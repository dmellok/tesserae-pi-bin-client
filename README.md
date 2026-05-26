# tesserae-pi-bin-client

A headless Raspberry Pi daemon that subscribes to a [Tesserae] server over MQTT,
downloads pre-packed 4-bpp `.bin` frames, and paints them onto a Pimoroni
[Inky Impression] (Spectra 6 / Waveshare E6) e-ink panel.

It is the consumer end of the Tesserae render pipeline:

```
Tesserae server  ──▶  MQTT broker  ──▶  tesserae-pi-bin-client  ──▶  Inky panel
   (renders +                          (this project, on the Pi)        (SPI)
    packs to 4-bpp)
```

The server does all the dithering, palette mapping, and pixel packing. This
client just downloads bytes and pushes them at the panel — no PIL involved on
the paint path.

## Status

Pre-release; tested against the API of `inky==2.4.0`.

## Hardware

- Raspberry Pi running Raspberry Pi OS Bookworm or later
- Pimoroni Inky Impression — 4" (640×400), 5.7" (600×448), 7.3" (800×480), or
  13.3" (1600×1200). The model is auto-detected via the HAT EEPROM; the
  `panel.model` config field is a fallback + sanity-check.

## Install (on the Pi)

```bash
# OS prerequisites
sudo apt update
sudo apt install -y python3-venv python3-pip git
sudo raspi-config nonint do_spi 0     # enable SPI

# Required group membership for the inky lib
sudo usermod -aG gpio,spi "$USER"
# log out and back in for groups to take effect

# Clone and install
git clone https://github.com/dmellok/tesserae-pi-bin-client.git
cd tesserae-pi-bin-client
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Smoke-test the hardware: paints a six-colour stripe pattern, no MQTT needed.
tesserae-pi-bin-client --paint-test
```

On first run the daemon writes a default config to
`~/.config/tesserae-pi-bin-client/config.toml`. Edit the `[mqtt]` host to point
at your broker, then start it:

```bash
tesserae-pi-bin-client
```

### systemd service

Once `tesserae-pi-bin-client` is on `PATH` (e.g. via `pip install .` system-wide,
or by symlinking from your venv into `/usr/local/bin`):

```bash
sudo ./scripts/install-service.sh           # uses $SUDO_USER
sudo ./scripts/install-service.sh pi        # explicit user
sudo journalctl -u tesserae-pi-bin-client -f
```

The unit runs as the user you specify and grants them `gpio` and `spi`
supplementary groups. It restarts on failure with a 5-second delay and logs to
the journal.

## Configuration

Defaults live at `~/.config/tesserae-pi-bin-client/config.toml`:

```toml
[mqtt]
host = "192.168.1.10"
port = 1883
username = ""        # optional
password = ""        # optional
client_id = "pi-impression-1"
keepalive = 60

[panel]
model = "inky_13_3"  # inky_4 | inky_5_7 | inky_7_3 | inky_13_3

[http]
download_timeout_s = 30
max_frame_bytes = 16000000

[logging]
level = "INFO"
```

Pass `--config /path/to/config.toml` to override.

## MQTT contract

### Subscribes

Topic `tesserae/pi/frame/bin`, payload (not retained):

```json
{ "url": "http://192.168.1.10:8000/renders/<digest>.bin" }
```

The client downloads the URL, validates the byte count matches
`panel_w * panel_h / 2`, and paints. Duplicate-digest frames are skipped.

### Publishes (retained)

Topic `tesserae/pi/status`:

```json
{
  "state": "idle",
  "last_paint_at": 1734567890.123,
  "last_error": null,
  "last_digest": "3f7a91b2c4e5d6f8",
  "uptime_s": 3601,
  "fw_version": "0.1.0",
  "panel": "inky_13_3"
}
```

Heartbeat publishes every 60 s and immediately on state change. A retained
last-will of `{"state":"offline"}` is set on connect.

## Wire format (the `.bin` files)

Headerless, no magic bytes, no length prefix:

- Length is exactly `panel_w * panel_h / 2`.
- Scanline-order, two pixels per byte: high nibble is the even column, low
  nibble the odd.
- Nibble values index the Waveshare E6 palette: `0=black 1=white 2=yellow
  3=red 5=blue 6=green`. `0x4` is reserved by the firmware and is never
  written by Tesserae; the unpack helper renders it as black if it ever shows
  up.

Reference sizes:

| Panel | Pixels | Buffer |
|-------|--------|--------|
| inky_4    | 640×400   | 128 000 B |
| inky_5_7  | 600×448   | 134 400 B |
| inky_7_3  | 800×480   | 192 000 B |
| inky_13_3 | 1600×1200 | 960 000 B |

## Private-API dependency — read this before bumping `inky`

The paint path bypasses `inky`'s public `set_image(pil)` API. That method
PIL-loads our buffer and re-quantises it to the panel palette — wasted CPU,
since Tesserae has already done that work server-side.

In `inky==2.4.0`, `panel.buf` is a 2-D `numpy.uint8` array of
**one byte per pixel**; `panel.show()` flips/rotates, packs pairs of pixels
into nibbles, and calls the private `panel._update(packed_list)` to push over
SPI. We download bytes that are *already* packed nibble pairs, so we skip
`show()` entirely and call `_update()` directly. See [paint.py] for the
relevant comment.

Consequences:

- The server is responsible for baking `h_flip` / `v_flip` / `rotation` into
  the `.bin`. The client no longer applies those transforms.
- `inky` is **pinned to an exact version** in `pyproject.toml`. Pimoroni could
  rename `_update` in a minor release. If you bump the pin:
  1. Re-confirm that `panel._update(list_of_packed_ints)` still pushes the
     correct Spectra-6 init+refresh sequence.
  2. Test on real hardware — there is no software-only way to catch a
     palette-order or pack-order regression.

## Development

```bash
pip install -e '.[dev]'
pytest                                  # tests use fakes — no broker, no panel
ruff check .
mypy src/                               # strict on contract modules
```

The tests cover: unpack/pack round-trips, malformed-length rejection, config
parsing, frame-payload parsing, the message handler with a stub dispatcher,
and the dispatcher with fake download + paint. No test touches real hardware
or a real broker.

## Troubleshooting

- **Panel stays blank, no logs.** Is the daemon running? `systemctl status
  tesserae-pi-bin-client`. Check `journalctl -u tesserae-pi-bin-client`.
- **Daemon connects but never paints.** Confirm the broker is reachable from
  the Pi (`mosquitto_sub -h <host> -t 'tesserae/pi/frame/bin'`). Confirm the
  URL in a message is reachable from the Pi (`curl -I <url>`).
- **"frame is N bytes; expected M" errors.** The configured `panel.model`
  doesn't match the panel that Tesserae is rendering for. Make them agree.
- **Permission errors opening SPI.** The user running the daemon needs
  membership in the `gpio` and `spi` groups. Re-check, then log out and back
  in.
- **`--paint-test` fails to import inky.** You're not on a Pi or `inky[rpi]`
  didn't install its RPi-specific deps; that's expected off-Pi.

## License

MIT — see [LICENSE](LICENSE).

[Tesserae]: https://github.com/dmellok/tesserae
[Inky Impression]: https://shop.pimoroni.com/products/inky-impression
[paint.py]: src/tesserae_pi_bin_client/paint.py
