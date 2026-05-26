#!/usr/bin/env bash
# One-shot installer for tesserae-pi-bin-client on a fresh Raspberry Pi OS.
#
# Run this AS YOUR NORMAL USER (NOT root, not via sudo). The script calls
# sudo internally for the privileged bits (apt, raspi-config, usermod, ln,
# systemd unit install) and runs the unprivileged bits (venv, pip install)
# as the invoking user — so the venv ends up in that user's home and the
# service runs as them too.
#
# What it does, idempotently:
#   1. apt-get install build + runtime prerequisites
#   2. raspi-config nonint do_spi 0   (enable SPI bus)
#   3. usermod -aG gpio,spi $USER     (group membership for HAT access)
#   4. python3 -m venv .venv          (in the repo directory)
#   5. .venv/bin/pip install -e .     (project + inky[rpi])
#   6. .venv/bin/python -c "...load_config()"    (materialize the default
#                                                  config file in ~/.config)
#   7. ln -sf .venv/bin/tesserae-pi-bin-client /usr/local/bin/...
#   8. scripts/install-service.sh $USER          (systemd unit + enable + start,
#                                                  unless --no-service)
#
# The group change in step 3 only takes effect on the user's next login.
# If you've just been added to gpio/spi, log out + back in (or reboot)
# before running --paint-test or relying on the service.

set -euo pipefail

INSTALL_SERVICE=true
RUN_PAINT_TEST=false
SKIP_APT=false
SERVICE_USER="${USER:-$(id -un)}"

usage() {
    cat <<USAGE
usage: $0 [--no-service] [--paint-test] [--skip-apt] [--user USER]

  --no-service   don't install the systemd unit (steps 1-7 only)
  --paint-test   run --paint-test after install (requires fresh login if
                 the gpio/spi groups were just added)
  --skip-apt     skip apt-get update + install (assume packages are present)
  --user USER    user the systemd unit will run as (default: \$USER)
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) INSTALL_SERVICE=false; shift ;;
        --paint-test) RUN_PAINT_TEST=true; shift ;;
        --skip-apt) SKIP_APT=true; shift ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
BIN_LINK="/usr/local/bin/tesserae-pi-bin-client"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "error: run as your normal user, NOT root or via sudo." >&2
    echo "       (the script invokes sudo internally where needed)" >&2
    exit 1
fi

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
    echo "error: ${REPO_DIR}/pyproject.toml not found — wrong repo layout?" >&2
    exit 1
fi

is_rpi=false
if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    is_rpi=true
fi
if ! $is_rpi; then
    echo "warning: this doesn't look like a Raspberry Pi — continuing anyway."
    echo "         (SPI enable + group add will probably be no-ops on a normal Linux box.)"
fi

echo "==> caching sudo credentials"
sudo -v

# ----- 1. apt -----
if $SKIP_APT; then
    echo "==> skipping apt (--skip-apt)"
else
    echo "==> apt-get update + install"
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends \
        git \
        python3 \
        python3-venv \
        python3-pip \
        libopenjp2-7 \
        libtiff6
fi

# ----- 2. SPI -----
if $is_rpi && command -v raspi-config >/dev/null 2>&1; then
    echo "==> enabling SPI via raspi-config"
    sudo raspi-config nonint do_spi 0
else
    echo "==> skipping SPI enable (no raspi-config / not on a Pi)"
fi

# ----- 3. groups -----
needs_relogin=false
for group in gpio spi; do
    if ! getent group "$group" >/dev/null 2>&1; then
        echo "==> group $group does not exist on this system; skipping"
        continue
    fi
    if id -nG "$USER" | tr ' ' '\n' | grep -qx "$group"; then
        echo "==> $USER already in $group"
    else
        echo "==> adding $USER to $group"
        sudo usermod -aG "$group" "$USER"
        needs_relogin=true
    fi
done

# ----- 4-5. venv + pip install -----
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
echo "==> upgrading pip in venv"
"$VENV_DIR/bin/pip" install -q --upgrade pip
echo "==> installing package (this pulls inky[rpi] and can take a few minutes)"
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

# ----- 6. config file -----
echo "==> materialising default config"
"$VENV_DIR/bin/python" -c \
    "from tesserae_pi_bin_client.config import load_config, DEFAULT_CONFIG_PATH; load_config(); print(f'    {DEFAULT_CONFIG_PATH}')"

# ----- 7. /usr/local/bin symlink -----
echo "==> linking $BIN_LINK -> $VENV_DIR/bin/tesserae-pi-bin-client"
sudo ln -sf "$VENV_DIR/bin/tesserae-pi-bin-client" "$BIN_LINK"

# ----- 8. systemd unit (optional) -----
if $INSTALL_SERVICE; then
    echo "==> installing systemd unit (user=$SERVICE_USER)"
    sudo "$REPO_DIR/scripts/install-service.sh" "$SERVICE_USER"
else
    echo "==> skipping systemd unit install (--no-service)"
fi

# ----- 9. optional paint test -----
if $RUN_PAINT_TEST; then
    if $needs_relogin; then
        echo "==> NOT running --paint-test — gpio/spi groups were just added"
        echo "    and won't take effect until you log out + back in."
    else
        echo "==> running --paint-test"
        "$VENV_DIR/bin/tesserae-pi-bin-client" --paint-test || \
            echo "    (paint-test failed — see logs above)"
    fi
fi

echo
echo "================================================================"
echo "  install complete"
echo "================================================================"
echo
echo "  config:  edit ~/.config/tesserae-pi-bin-client/config.toml"
echo "           (set [mqtt].host to your broker, [panel].model to your panel)"
echo
if $needs_relogin; then
    echo "  groups:  $USER was added to gpio/spi — LOG OUT + BACK IN (or reboot)"
    echo "           before running --paint-test or relying on the service."
    echo
fi
if $INSTALL_SERVICE; then
    echo "  service: sudo systemctl status tesserae-pi-bin-client"
    echo "           sudo journalctl -u tesserae-pi-bin-client -f"
    echo "           sudo systemctl restart tesserae-pi-bin-client  # after editing config"
else
    echo "  service: not installed (re-run without --no-service to install)"
fi
echo
echo "  manual:  tesserae-pi-bin-client --paint-test   # paints a 6-colour stripe"
echo "           tesserae-pi-bin-client                # run in foreground"
echo
