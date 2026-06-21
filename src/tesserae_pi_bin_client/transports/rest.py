"""REST transport — HTTP polling against the Tesserae server's /api/v1.

The wake loop is one round-trip per cycle: GET /device/<id>/frame, paint if
the ETag changed, POST /device/<id>/status, sleep for next_poll_s. Before
the first wake cycle the daemon either claims an admin-pressed Register row
via POST /device/discover (the recommended flow — zero typing) or, if a
pairing code is present, presents it to POST /device/register.

Field names, header conventions, and the dual auth-header pattern follow
the sibling ESP32 firmware (tesserae-device-photopainter-7.3-bin) so the
server sees a consistent contract across firmware kinds.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests

from ..config import Config, RestConfig, save_config, with_rest_updates
from ..heartbeat import Status
from ..mqtt_loop import PaintFn

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
HTTP_TIMEOUT_S = 15.0

# Clamp range borrowed from the photopainter — server can suggest very long
# or very short intervals, but we cap so a misconfigured server can't pin the
# daemon at sub-second polling or month-long silence.
POLL_INTERVAL_MIN_S = 30
POLL_INTERVAL_MAX_S = 7 * 24 * 60 * 60  # one week

DISCOVER_DEFAULT_RETRY_S = 30
REGISTER_DEFAULT_RETRY_AFTER_S = 30
REGISTER_RETRY_AFTER_CAP_S = 60
TRANSIENT_FAILURE_BACKOFF_S = 30


def _mac_address() -> str:
    """Lowercase 12-char hex with no separators (matches photopainter format).

    uuid.getnode() returns a synthesized MAC (with the multicast bit set)
    when no real interface is available — we still send it as a stable id.
    """
    return f"{uuid.getnode():012x}"


def _device_info(cfg: Config, status: Status) -> dict[str, Any]:
    """Body shared by /discover and /register: identifies this firmware."""
    return {
        "device_id": cfg.mqtt.device_id,
        "kind": status.kind,
        "panel_w": status.panel_w,
        "panel_h": status.panel_h,
        "fw_version": status.fw_version,
        "mac": _mac_address(),
    }


def _auth_headers(token: str) -> dict[str, str]:
    """Both header forms — photopainter sends both as a defensive fallback
    against HTTP-client variants that strip the standard Authorization
    header. Cheap, no downside."""
    return {
        "Authorization": f"Bearer {token}",
        "X-Tesserae-Token": token,
    }


def _clamp_poll(value: int) -> int:
    return max(POLL_INTERVAL_MIN_S, min(POLL_INTERVAL_MAX_S, value))


def _log_server_time(server_time: Any) -> None:
    """Log clock skew if the server's `server_time` (float seconds) diverges
    from local time by more than a minute. We don't actually settimeofday on
    a Pi — NTP runs as root and owns the clock; we'd just be racing it."""
    if not isinstance(server_time, (int, float)):
        return
    skew = float(server_time) - time.time()
    if abs(skew) > 60:
        log.warning(
            "server_time skew=%+ds (server=%.3f local=%.3f); check NTP",
            int(skew),
            float(server_time),
            time.time(),
        )


class RestClient:
    """Thin wrapper over requests.Session — one place that builds URLs,
    sets headers, and decodes JSON. Each call returns a (status_code, body)
    pair so callers can branch on HTTP semantics without raising for non-2xx."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session | None = None,
        timeout_s: float = HTTP_TIMEOUT_S,
    ) -> None:
        self._base = base_url.rstrip("/") + API_PREFIX
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout_s

    def _url(self, path: str) -> str:
        return self._base + path

    def discover(self, body: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        resp = self._session.post(
            self._url("/device/discover"),
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=self._timeout,
            allow_redirects=False,
        )
        return resp.status_code, _maybe_json(resp)

    def register(
        self, body: dict[str, Any], pairing_code: str
    ) -> tuple[int, dict[str, Any] | None, int | None]:
        """Returns (status_code, body_json, retry_after_s) — Retry-After only
        populated on 429."""
        resp = self._session.post(
            self._url("/device/register"),
            data=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "X-Pairing-Code": pairing_code,
            },
            timeout=self._timeout,
            allow_redirects=False,
        )
        retry_after: int | None = None
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "")
            try:
                retry_after = int(ra)
            except (TypeError, ValueError):
                retry_after = None
        return resp.status_code, _maybe_json(resp), retry_after

    def get_frame(
        self, device_id: str, token: str, if_none_match: str = ""
    ) -> tuple[int, dict[str, Any] | None, str]:
        """Returns (status_code, body_json_or_None, etag_or_empty).

        ETag is captured verbatim from the response header — including
        surrounding quotes if the server sent them. Pass it back unmodified
        in the next If-None-Match for the 304 short-circuit to work.
        """
        headers = _auth_headers(token)
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        resp = self._session.get(
            self._url(f"/device/{device_id}/frame"),
            headers=headers,
            timeout=self._timeout,
            allow_redirects=False,
        )
        etag = resp.headers.get("ETag", "") or ""
        return resp.status_code, _maybe_json(resp), etag

    def post_status(
        self, device_id: str, token: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any] | None]:
        headers = _auth_headers(token)
        headers["Content-Type"] = "application/json"
        resp = self._session.post(
            self._url(f"/device/{device_id}/status"),
            data=json.dumps(body, default=_json_default),
            headers=headers,
            timeout=self._timeout,
            allow_redirects=False,
        )
        return resp.status_code, _maybe_json(resp)

    def download(self, url: str, max_bytes: int, timeout_s: float) -> bytes:
        """Same contract as mqtt_loop.http_download — refuses oversize bodies."""
        resp = self._session.get(
            url, timeout=timeout_s, allow_redirects=True, stream=True
        )
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise ValueError(
                        f"server reports {declared} bytes; "
                        f"exceeds max_frame_bytes={max_bytes}"
                    )
            except ValueError:
                raise
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"frame larger than max_frame_bytes={max_bytes}")
            chunks.append(chunk)
        return b"".join(chunks)


def _json_default(obj: Any) -> Any:
    """Json fallback for stray non-JSON-native values in status.payload()."""
    return str(obj)


def _maybe_json(resp: requests.Response) -> dict[str, Any] | None:
    """Best-effort JSON decode. Returns None for empty bodies (204) or
    non-JSON responses (e.g. an upstream proxy's HTML error page)."""
    if not resp.content:
        return None
    try:
        obj = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if isinstance(obj, dict):
        return obj
    return None


def _claim_via_discover(
    client: RestClient,
    cfg: Config,
    status: Status,
    shutdown: threading.Event,
    config_path: Path,
) -> Config | None:
    """Loop POST /discover until the admin clicks Register on the server.

    Returns the updated Config (with device_token and possibly a new canonical
    device_id) once claimed, or None if shutdown fires first.
    """
    while not shutdown.is_set():
        try:
            code, body = client.discover(_device_info(cfg, status))
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.warning(
                "discover failed (%s: %s); retrying in %ds",
                type(exc).__name__, exc, TRANSIENT_FAILURE_BACKOFF_S,
            )
            shutdown.wait(TRANSIENT_FAILURE_BACKOFF_S)
            continue

        if code >= 500 or body is None:
            log.warning("discover returned %d; retrying in %ds", code, TRANSIENT_FAILURE_BACKOFF_S)
            shutdown.wait(TRANSIENT_FAILURE_BACKOFF_S)
            continue

        if body.get("registered"):
            token = body.get("device_token")
            if not isinstance(token, str) or not token:
                log.error("discover registered=true but no device_token in body; retrying")
                shutdown.wait(TRANSIENT_FAILURE_BACKOFF_S)
                continue
            # Adopt the server's canonical id — it may differ from what we
            # sent (renamed/stale local id). Using the wrong id against a
            # MAC-bound token gives 403 and an endless re-register loop.
            canonical_id = body.get("device_id") or cfg.mqtt.device_id
            new_cfg = with_rest_updates(cfg, device_token=token, pairing_code="")
            if canonical_id != cfg.mqtt.device_id:
                log.info(
                    "adopting server-canonical device_id=%s (was %s)",
                    canonical_id, cfg.mqtt.device_id,
                )
                new_cfg = replace(
                    new_cfg, mqtt=replace(new_cfg.mqtt, device_id=canonical_id)
                )
            save_config(new_cfg, config_path)
            _log_server_time(body.get("server_time"))
            log.info("registered via discover as device_id=%s", canonical_id)
            return new_cfg

        retry = body.get("retry_after_s", DISCOVER_DEFAULT_RETRY_S)
        if not isinstance(retry, int) or retry <= 0:
            retry = DISCOVER_DEFAULT_RETRY_S
        log.info(
            "not registered yet — admin needs to click Register on the server "
            "(retrying in %ds)", retry,
        )
        shutdown.wait(retry)
    return None


def _claim_via_register(
    client: RestClient,
    cfg: Config,
    status: Status,
    shutdown: threading.Event,
    config_path: Path,
) -> Config | None:
    """POST /register with the configured pairing code. Strict-gating
    alternative to the discover loop — admin must mint and type a code.

    Returns the updated Config on success. Exits the process on terminal
    pairing-code errors (403/400). Returns None if shutdown fires first.
    """
    pairing_code = cfg.rest.pairing_code
    while not shutdown.is_set():
        try:
            code, body, retry_after = client.register(
                _device_info(cfg, status), pairing_code
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.warning(
                "register failed (%s: %s); retrying in %ds",
                type(exc).__name__, exc, TRANSIENT_FAILURE_BACKOFF_S,
            )
            shutdown.wait(TRANSIENT_FAILURE_BACKOFF_S)
            continue

        if code in (200, 201) and body is not None:
            token = body.get("device_token")
            if not isinstance(token, str) or not token:
                log.error("register %d but no device_token; aborting", code)
                raise SystemExit(2)
            canonical_id = body.get("device_id") or cfg.mqtt.device_id
            new_cfg = with_rest_updates(cfg, device_token=token, pairing_code="")
            if canonical_id != cfg.mqtt.device_id:
                new_cfg = replace(
                    new_cfg, mqtt=replace(new_cfg.mqtt, device_id=canonical_id)
                )
            save_config(new_cfg, config_path)
            _log_server_time(body.get("server_time"))
            reused = body.get("reused_existing", False)
            log.info(
                "registered via pairing code as device_id=%s (%s)",
                canonical_id, "reused existing" if reused else "newly created",
            )
            return new_cfg

        if code == 403:
            log.error("register 403: pairing code invalid or expired — generate a fresh one")
            raise SystemExit(2)
        if code == 400:
            log.error("register 400: bad body (body=%r)", body)
            raise SystemExit(2)
        if code == 429:
            wait_s = retry_after if retry_after is not None else REGISTER_DEFAULT_RETRY_AFTER_S
            wait_s = min(max(wait_s, 1), REGISTER_RETRY_AFTER_CAP_S)
            log.warning(
                "register 429 rate-limited; honoring Retry-After=%ds", wait_s,
            )
            shutdown.wait(wait_s)
            continue
        log.warning(
            "register: unexpected status %d (body=%r); retrying in %ds",
            code, body, TRANSIENT_FAILURE_BACKOFF_S,
        )
        shutdown.wait(TRANSIENT_FAILURE_BACKOFF_S)
    return None


def _apply_status_response(
    cfg: Config,
    body: dict[str, Any] | None,
    config_path: Path,
) -> tuple[Config, int]:
    """Merge the /status response into local config and return the next-poll
    interval (clamped). Server can push `config.sleep_interval_s` (durable —
    we persist it) and a one-shot `next_poll_s` (transient — used only for
    this cycle's sleep)."""
    next_poll = cfg.rest.poll_interval_s
    if not isinstance(body, dict):
        return cfg, _clamp_poll(next_poll)

    _log_server_time(body.get("server_time"))

    srv_cfg = body.get("config")
    if isinstance(srv_cfg, dict):
        sis = srv_cfg.get("sleep_interval_s")
        if isinstance(sis, int) and POLL_INTERVAL_MIN_S <= sis <= POLL_INTERVAL_MAX_S:
            if sis != cfg.rest.poll_interval_s:
                log.info(
                    "server config push: poll_interval_s %d -> %d",
                    cfg.rest.poll_interval_s, sis,
                )
                cfg = with_rest_updates(cfg, poll_interval_s=sis)
                save_config(cfg, config_path)
            next_poll = sis

    nps = body.get("next_poll_s")
    if isinstance(nps, (int, float)):
        next_poll = int(nps)

    return cfg, _clamp_poll(next_poll)


def run(
    config: Config,
    status: Status,
    paint_fn: PaintFn,
    shutdown: threading.Event,
    config_path: Path,
) -> int:
    """REST wake loop. Returns process exit code (0 = clean shutdown)."""
    if not config.rest.server_url:
        log.error("transport_mode=rest but [rest].server_url is empty")
        return 2

    session = requests.Session()
    client = RestClient(config.rest.server_url, session=session)

    # 1. Acquire a device_token if we don't have one.
    if not config.rest.device_token:
        if config.rest.pairing_code:
            log.info("no device_token — registering with pairing code")
            updated = _claim_via_register(client, config, status, shutdown, config_path)
        else:
            log.info(
                "no device_token — entering discover loop "
                "(admin must click Register on Settings -> Devices)"
            )
            updated = _claim_via_discover(client, config, status, shutdown, config_path)
        if updated is None:
            # shutdown fired during claim
            return 0
        config = updated

    # 2. Wake loop.
    while not shutdown.is_set():
        config, next_poll, should_exit = _wake_cycle(
            client, config, status, paint_fn, config_path
        )
        if should_exit:
            return 2
        log.info("sleeping %ds until next wake", next_poll)
        if shutdown.wait(next_poll):
            break
    return 0


def _wake_cycle(
    client: RestClient,
    config: Config,
    status: Status,
    paint_fn: PaintFn,
    config_path: Path,
) -> tuple[Config, int, bool]:
    """One iteration of the wake loop: frame GET, optional paint, status POST.

    Returns (config, next_poll_s, should_exit). config is rebound if the
    cycle persisted a new etag, a server-pushed poll interval, or wiped a
    bad token; should_exit is True on terminal auth failure.
    """
    device_id = config.mqtt.device_id
    token = config.rest.device_token

    # --- frame GET ---
    try:
        code, body, etag = client.get_frame(
            device_id, token, if_none_match=config.rest.last_frame_etag
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        log.warning(
            "frame GET failed (%s: %s); sleeping fallback",
            type(exc).__name__, exc,
        )
        status.state = "error"
        status.last_error = f"{type(exc).__name__}: {exc}"
        return config, config.rest.poll_interval_s, False

    if code == 401:
        log.error(
            "frame GET 401: token invalid — wiping device_token, re-pair to recover"
        )
        config = with_rest_updates(config, device_token="")
        save_config(config, config_path)
        return config, 0, True
    if code == 403:
        log.error(
            "frame GET 403: token not valid for this device (stale device_id?); "
            "re-pair to recover"
        )
        return config, 0, True
    if code >= 500:
        log.warning("frame GET %d; sleeping fallback", code)
        status.state = "error"
        return config, config.rest.poll_interval_s, False

    if code == 304:
        log.info("frame unchanged (304)")
        status.state = "idle"
        status.last_error = None
    elif code == 204:
        log.info("no frame rendered yet; nothing to paint")
        status.state = "idle"
        status.last_error = None
    elif code == 200 and isinstance(body, dict):
        url = body.get("url")
        if not isinstance(url, str) or not url:
            log.warning("frame GET 200 but no url in body=%r", body)
            return config, config.rest.poll_interval_s, False
        render_id = body.get("render_id")
        log.info("painting render_id=%s url=%s", render_id, url)
        status.state = "rendering"
        try:
            packed = client.download(
                url,
                max_bytes=config.http.max_frame_bytes,
                timeout_s=float(config.http.download_timeout_s),
            )
            paint_fn(packed, config.panel.model)
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.HTTPError,
            ValueError,
            OSError,
        ) as exc:
            log.warning("download/paint failed: %s", exc)
            status.state = "error"
            status.last_error = f"{type(exc).__name__}: {exc}"
            return config, config.rest.poll_interval_s, False
        status.state = "idle"
        status.last_error = None
        status.last_paint_at = time.time()
        if isinstance(render_id, str):
            status.last_digest = render_id
        if etag and etag != config.rest.last_frame_etag:
            config = with_rest_updates(config, last_frame_etag=etag)
            save_config(config, config_path)
    else:
        log.warning("frame GET unexpected status=%d body=%r", code, body)

    # --- status POST (always send so server last-seen advances) ---
    try:
        post_code, post_body = client.post_status(device_id, token, status.payload())
    except (requests.ConnectionError, requests.Timeout) as exc:
        log.warning("status POST failed (%s: %s)", type(exc).__name__, exc)
        return config, config.rest.poll_interval_s, False

    if post_code == 401:
        log.error("status POST 401: token invalid — wiping device_token, re-pair to recover")
        config = with_rest_updates(config, device_token="")
        save_config(config, config_path)
        return config, 0, True
    if post_code >= 500:
        log.warning("status POST %d; using fallback poll interval", post_code)
        return config, config.rest.poll_interval_s, False
    if post_code not in (200, 201, 204):
        log.warning("status POST unexpected status=%d body=%r", post_code, post_body)
        return config, config.rest.poll_interval_s, False

    config, next_poll = _apply_status_response(config, post_body, config_path)
    return config, next_poll, False


__all__ = [
    "API_PREFIX",
    "POLL_INTERVAL_MIN_S",
    "POLL_INTERVAL_MAX_S",
    "RestClient",
    "RestConfig",
    "run",
]
