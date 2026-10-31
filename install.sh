#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/main.py" && -f "${SCRIPT_DIR}/install_webpanel.sh" ]]; then
    exec bash "${SCRIPT_DIR}/install_webpanel.sh" "$@"
fi

ARCHIVE="${SCRIPT_DIR}/wpsc.tar.gz"
if [[ ! -f "$ARCHIVE" ]]; then
    echo "[ERROR] wpsc.tar.gz was not found beside install.sh." >&2
    echo "Copy both release files to the server, then run: sudo bash install.sh" >&2
    exit 1
fi
if ! command -v tar >/dev/null 2>&1; then
    echo "[ERROR] tar is required to extract wpsc.tar.gz." >&2
    exit 1
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
PAYLOAD_DIR="${TEMP_DIR}/wpsc"
if [[ ! -f "${PAYLOAD_DIR}/main.py" ]]; then
    echo "[ERROR] The archive is missing the WebPanel application files." >&2
    exit 1
fi
if [[ ! -f "${PAYLOAD_DIR}/install_webpanel.sh" ]]; then
    echo "[ERROR] The archive is missing required WebPanel installation files." >&2
    exit 1
fi

bash "${PAYLOAD_DIR}/install_webpanel.sh" "$@"
