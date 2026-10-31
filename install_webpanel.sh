#!/usr/bin/env bash
# ==============================================================================
# WebPanel - Linux Web Hosting Control Panel Installer
# Supports Ubuntu, Debian, AlmaLinux, Rocky Linux, CentOS Stream, and Fedora.
# ==============================================================================

set -Eeuo pipefail

if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    BLUE='\033[0;34m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    BLUE=''
    YELLOW=''
    CYAN=''
    BOLD=''
    NC=''
fi

INSTALL_DIR="/opt/webpanel"
SERVICE_FILE="/etc/systemd/system/webpanel.service"
CLI_LINK="/usr/local/bin/wpsc"
PORT=8080
TOTAL_STEPS=6

print_banner() {
    printf '\n'
    printf '%b\n' "${GREEN}  __      __          _     ____                  _ ${NC}"
    printf '%b\n' "${GREEN}  \ \    / /__  _ __ | |   |  _ \ __ _ _ __   ___| |${NC}"
    printf '%b\n' "${GREEN}   \ \/\/ / _ \| '_ \| |   | |_) / _\` | '_ \ / _ \ |${NC}"
    printf '%b\n' "${GREEN}    \_/\_/ (_) | |_) | |___|  __/ (_| | | | |  __/ |${NC}"
    printf '%b\n' "${GREEN}           \___/| .__/|_____|_|   \__,_|_| |_|\___|_|${NC}"
    printf '%b\n' "${GREEN}                |_|${NC}"
    printf '\n'
    printf '%b\n' "${BOLD}  Linux Web Hosting Control Panel${NC}"
    printf '%b\n' "  Apache  |  MySQL  |  PHP  |  phpMyAdmin"
    printf '%s\n' "  ----------------------------------------------------------------"
    printf '\n'
}

step() {
    printf '\n%b\n' "${BLUE}${BOLD}  [$1/$TOTAL_STEPS] $2${NC}"
}

success() {
    printf '%b\n' "${GREEN}  [OK]${NC} $*"
}

info() {
    printf '%b\n' "${CYAN}  [..]${NC} $*"
}

warn() {
    printf '%b\n' "${YELLOW}  [!]${NC} $*"
}

fail() {
    printf '%b\n' "${RED}  [ERROR]${NC} $*" >&2
}

on_error() {
    local exit_code=$?
    trap - ERR
    fail "Installation failed on line $1 (exit code $exit_code)."
    printf '%s\n' "  Review the command output above, correct the issue, then run the installer again." >&2
    exit "$exit_code"
}
trap 'on_error "$LINENO"' ERR

print_banner

step 1 "Checking permissions and server"
if [[ "$(id -u)" -ne 0 ]]; then
    fail "Run the installer as root: sudo bash install.sh"
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    fail "Cannot identify this Linux distribution (/etc/os-release is missing)."
    exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
OS_NAME="${PRETTY_NAME:-${NAME:-Linux}}"
info "Operating system: ${OS_NAME}"

if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemd is required to install and manage the WebPanel service."
    exit 1
fi
success "Root access and systemd are available."

step 2 "Installing system prerequisites"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    info "Refreshing apt package metadata..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv curl libffi-dev procps certbot python3-certbot-apache
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 python3-pip curl gcc procps-ng
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q python3 python3-pip curl gcc procps-ng
else
    fail "No supported package manager found (apt-get, dnf, or yum)."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    fail "Python 3 was not installed successfully."
    exit 1
fi
python_version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
if [[ "$(printf '%s\n' 3.10 "$python_version" | sort -V | head -n1)" != "3.10" ]]; then
    fail "Python 3.10 or newer is required; found ${python_version}."
    exit 1
fi
success "Python ${python_version} is ready."

step 3 "Deploying WebPanel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "${SCRIPT_DIR}/requirements.txt" || ! -f "${SCRIPT_DIR}/main.py" ]]; then
    fail "Run install_webpanel.sh from the WebPanel project directory; main.py or requirements.txt is missing."
    exit 1
fi

mkdir -p "$INSTALL_DIR"
if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
    tar -C "$SCRIPT_DIR" \
        --exclude='./.venv' \
        --exclude='./venv' \
        --exclude='./__pycache__' \
        --exclude='*/__pycache__' \
        --exclude='./.pytest_cache' \
        --exclude='*/.pytest_cache' \
        --exclude='./webpanel.db' \
        --exclude='./database_backups' \
        -cf - . | tar -C "$INSTALL_DIR" -xf -
fi
success "Application files are in ${INSTALL_DIR}."

if [[ -e "$CLI_LINK" && ! -L "$CLI_LINK" ]]; then
    fail "${CLI_LINK} already exists and is not a symbolic link; move it before installing WebPanel CLI."
    exit 1
fi
if [[ -L "$CLI_LINK" && "$(readlink -f "$CLI_LINK")" != "${INSTALL_DIR}/wpsc" ]]; then
    fail "${CLI_LINK} points to a different file; remove or move it before installing WebPanel CLI."
    exit 1
fi
sed -i 's/\r$//' "${INSTALL_DIR}/wpsc"
chmod 0755 "${INSTALL_DIR}/wpsc"
ln -sfn "${INSTALL_DIR}/wpsc" "$CLI_LINK"
success "Terminal command installed: wpsc"

step 4 "Preparing the Python environment"
if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
    python3 -m venv "${INSTALL_DIR}/venv"
fi
"${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade pip -q
success "Virtual environment is ready."

step 5 "Installing WebPanel packages"
"${INSTALL_DIR}/venv/bin/python" -m pip install -r "${INSTALL_DIR}/requirements.txt" -q
success "Application dependencies are installed."

step 6 "Configuring and starting the service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=WebPanel - Linux Web Hosting Control Panel
After=network.target apache2.service httpd.service mysql.service mariadb.service

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/main.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/webpanel.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable webpanel.service >/dev/null
systemctl restart webpanel.service
if ! systemctl is-active --quiet webpanel.service; then
    fail "The WebPanel service did not start. Check: journalctl -u webpanel -n 50"
    exit 1
fi
success "WebPanel is enabled and running."

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SERVER_IP="${SERVER_IP:-localhost}"

printf '\n'
printf '%s\n' "  =================================================================="
printf '%b\n' "  ${GREEN}${BOLD}WebPanel installation complete${NC}"
printf '%s\n' "  =================================================================="
printf '\n'
printf '%b\n' "  ${BOLD}Panel access${NC}"
printf '  URL        : %s\n' "http://${SERVER_IP}:${PORT}"
printf '  Local URL  : %s\n' "http://localhost:${PORT}"
printf '\n'
printf '%b\n' "  ${BOLD}Initial login${NC}"
printf '  Username   : %s\n' "admin"
printf '  Password   : %s\n' "admin123"
warn "Change the default password after your first login."
warn "Open TCP port ${PORT} in your firewall. Use HTTPS before exposing the panel publicly."
printf '\n'
printf '%b\n' "  ${BOLD}Service management${NC}"
printf '  Status     : %s\n' "systemctl status webpanel"
printf '  Restart    : %s\n' "systemctl restart webpanel"
printf '  Live logs  : %s\n' "journalctl -u webpanel -f"
printf '  CLI        : %s\n' "wpsc help"
printf '\n'
