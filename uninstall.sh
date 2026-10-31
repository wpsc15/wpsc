#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/webpanel"
SERVICE_FILE="/etc/systemd/system/webpanel.service"

if [[ "$(id -u)" -ne 0 ]]; then
    printf '%s\n' "Run the uninstaller as root: sudo bash uninstall.sh" >&2
    exit 1
fi

if [[ "${1:-}" != "--yes" ]]; then
    printf '%s\n' \
        "This removes the WebPanel service and ${INSTALL_DIR}," \
        "including its SQLite account, audit log, inventory, and app-local backups." \
        "WebPanel-managed cron entries will also be removed." \
        "It does not remove OS packages, hosted websites, Apache configs, SSL certificates," \
        "MySQL databases, or WordPress backups under /var/backups/webpanel/wordpress."
    read -r -p "Type 'yes' to continue: " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        printf '%s\n' "Uninstall cancelled."
        exit 0
    fi
fi

if command -v systemctl >/dev/null 2>&1; then
    if [[ -f "$SERVICE_FILE" ]]; then
        systemctl disable --now webpanel.service
        rm -f -- "$SERVICE_FILE"
        systemctl daemon-reload
    else
        printf '%s\n' "WebPanel systemd unit not found; continuing with file cleanup."
    fi
else
    printf '%s\n' "systemctl not found; skipping service cleanup."
fi

if [[ -L /usr/local/bin/wpsc && "$(readlink -f /usr/local/bin/wpsc)" == "${INSTALL_DIR}/wpsc" ]]; then
    rm -f -- /usr/local/bin/wpsc
fi

if command -v crontab >/dev/null 2>&1; then
    crontab_file="$(mktemp)"
    error_file="$(mktemp)"
    trap 'rm -f -- "$crontab_file" "$error_file"' EXIT

    if crontab -l >"$crontab_file" 2>"$error_file"; then
        filtered_file="$(mktemp)"
        trap 'rm -f -- "$crontab_file" "$error_file" "$filtered_file"' EXIT
        awk '
            /^# webpanel-job:/ && length($0) == 51 && substr($0, 16) ~ /^[0-9a-f-]+$/ { getline; next }
            { print }
        ' "$crontab_file" >"$filtered_file"
        crontab "$filtered_file"
        rm -f -- "$crontab_file" "$error_file" "$filtered_file"
        trap - EXIT
    elif grep -qi 'no crontab for' "$error_file"; then
        rm -f -- "$crontab_file" "$error_file"
        trap - EXIT
    else
        cat "$error_file" >&2
        printf '%s\n' "Could not safely update the root crontab; stopping uninstall." >&2
        exit 1
    fi
else
    printf '%s\n' "crontab not found; no managed cron entries were removed."
fi

if [[ -d "$INSTALL_DIR" && "$INSTALL_DIR" != "/" ]]; then
    rm -rf -- "$INSTALL_DIR"
fi

printf '%s\n' "WebPanel has been removed."
