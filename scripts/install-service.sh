#!/usr/bin/env bash
# Install tesserae-pi-bin-client as a systemd service.
#
# Run as root (or via sudo) ON THE PI after `pip install .` has put the
# tesserae-pi-bin-client binary on PATH (default /usr/local/bin).
#
# The unit is templated for the user that will run the daemon — pick the
# account that has membership in the `gpio` and `spi` groups. The default
# `pi` user typically does.
set -euo pipefail

UNIT_NAME="tesserae-pi-bin-client.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_TEMPLATE="${SCRIPT_DIR}/tesserae-pi-bin-client.service.in"
UNIT_DEST="/etc/systemd/system/${UNIT_NAME}"

SERVICE_USER="${1:-${SUDO_USER:-pi}}"

if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
    echo "missing unit template: ${UNIT_TEMPLATE}" >&2
    exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
    echo "this script must be run as root (try: sudo $0 [user])" >&2
    exit 1
fi
if ! command -v tesserae-pi-bin-client >/dev/null 2>&1; then
    echo "tesserae-pi-bin-client binary not found on PATH" >&2
    echo "install it first: pip install ." >&2
    exit 1
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "user ${SERVICE_USER} does not exist" >&2
    exit 1
fi

echo "installing ${UNIT_NAME} for user ${SERVICE_USER}"
sed "s|@USER@|${SERVICE_USER}|g" "${UNIT_TEMPLATE}" > "${UNIT_DEST}"
chmod 0644 "${UNIT_DEST}"

systemctl daemon-reload
systemctl enable "${UNIT_NAME}"
systemctl restart "${UNIT_NAME}"
systemctl --no-pager status "${UNIT_NAME}" || true
