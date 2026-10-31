#!/usr/bin/env bash
set -euo pipefail

component=""
version=""
while (($#)); do
    case "$1" in
        --component) component="${2:?Missing component}"; shift 2 ;;
        --version) version="${2:?Missing version}"; shift 2 ;;
        *) echo "[ERROR] Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ "$(id -u)" -ne 0 ]]; then
    echo "[ERROR] Run WebPanel as root to install packages." >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]] || ! . /etc/os-release || [[ "$ID" != "debian" && "$ID" != "ubuntu" ]]; then
    echo "[ERROR] Only Debian and Ubuntu are supported." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
echo "[1/3] Updating apt package metadata..."
apt-get update

case "$component" in
    apache)
        if [[ ! "$version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]]; then
            echo "[ERROR] Invalid Apache package version." >&2
            exit 2
        fi
        echo "[2/3] Installing Apache package version ${version}..."
        apt-get install -y --allow-downgrades --no-install-recommends "apache2=${version}"
        echo "[3/3] Enabling Apache..."
        systemctl enable --now apache2
        apache2ctl configtest
        ;;
    mysql)
        if [[ ! "$version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]]; then
            echo "[ERROR] Invalid MySQL package version." >&2
            exit 2
        fi
        echo "[2/3] Installing MySQL server package version ${version}..."
        apt-get install -y --allow-downgrades --no-install-recommends "mysql-server=${version}"
        echo "[3/3] Enabling MySQL..."
        if systemctl cat mysql.service >/dev/null 2>&1; then
            systemctl enable --now mysql
        elif systemctl cat mariadb.service >/dev/null 2>&1; then
            systemctl enable --now mariadb
        else
            echo "[ERROR] MySQL or MariaDB systemd service was not installed." >&2
            exit 1
        fi
        ;;
    phpmyadmin)
        if [[ ! "$version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]]; then
            echo "[ERROR] Invalid phpMyAdmin package version." >&2
            exit 2
        fi
        echo "[2/3] Installing phpMyAdmin package version ${version}..."
        if command -v debconf-set-selections >/dev/null 2>&1; then
            printf '%s\n' \
                'phpmyadmin phpmyadmin/reconfigure-webserver multiselect apache2' \
                'phpmyadmin phpmyadmin/dbconfig-install boolean false' \
                | debconf-set-selections
        fi
        apt-get install -y --allow-downgrades --no-install-recommends "phpmyadmin=${version}"
        if command -v a2enconf >/dev/null 2>&1 && [[ -f /etc/apache2/conf-available/phpmyadmin.conf ]]; then
            echo "[3/3] Enabling phpMyAdmin in Apache..."
            a2enconf phpmyadmin
            apache2ctl configtest
            if systemctl is-active --quiet apache2; then
                systemctl reload apache2
            else
                systemctl enable --now apache2
            fi
        else
            echo "[3/3] phpMyAdmin installed; Apache configuration was not found."
        fi
        ;;
    docker)
        if [[ ! "$version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]]; then
            echo "[ERROR] Invalid Docker package version." >&2
            exit 2
        fi
        echo "[2/3] Installing Docker Engine ${version} from the configured OS repositories..."
        docker_packages=("docker.io=${version}")
        if apt-cache madison docker-compose-v2 | grep -q '|'; then
            docker_packages+=("docker-compose-v2")
        else
            echo "[INFO] Docker Compose v2 is not available in the configured repositories; continuing without it."
        fi
        apt-get install -y --allow-downgrades --no-install-recommends "${docker_packages[@]}"
        echo "[3/3] Enabling and starting Docker..."
        systemctl enable --now docker
        docker info
        ;;
    php)
        if [[ ! "$version" =~ ^[0-9]+\.[0-9]+$ ]]; then
            echo "[ERROR] Invalid PHP major/minor version." >&2
            exit 2
        fi
        echo "[2/3] Installing PHP ${version}, FPM, and common extensions..."
        apt-get install -y --no-install-recommends \
            "php${version}" \
            "php${version}-cli" \
            "php${version}-fpm" \
            "php${version}-mysql" \
            "php${version}-curl" \
            "php${version}-xml" \
            "php${version}-mbstring" \
            "php${version}-zip" \
            "php${version}-gd"
        echo "[3/3] Enabling PHP-FPM..."
        systemctl enable --now "php${version}-fpm"
        if command -v apache2ctl >/dev/null 2>&1; then
            a2enmod proxy_fcgi setenvif
            shopt -s nullglob
            enabled_fpm_configs=(/etc/apache2/conf-enabled/php*-fpm.conf)
            if ((${#enabled_fpm_configs[@]} == 0)); then
                a2enconf "php${version}-fpm"
                apache2ctl configtest
                if systemctl is-active --quiet apache2; then
                    systemctl reload apache2
                fi
            else
                echo "[INFO] Existing PHP-FPM Apache configuration left unchanged."
                echo "[INFO] Review /etc/apache2/conf-enabled before switching PHP versions."
            fi
        fi
        ;;
    nginx|redis|nodejs|git|composer|certbot|memcached|htop|fail2ban|unzip|postgresql|sqlite|supervisor|rabbitmq|varnish|haproxy|java|go|ruby|rsync|curl|wget|nano|vim|clamav)
        case "$component" in
            nginx) package="nginx"; service="nginx" ;;
            redis) package="redis-server"; service="redis-server" ;;
            nodejs) package="nodejs"; service="" ;;
            git) package="git"; service="" ;;
            composer) package="composer"; service="" ;;
            certbot) package="certbot"; service="" ;;
            memcached) package="memcached"; service="memcached" ;;
            htop) package="htop"; service="" ;;
            fail2ban) package="fail2ban"; service="fail2ban" ;;
            unzip) package="unzip"; service="" ;;
            postgresql) package="postgresql"; service="postgresql" ;;
            sqlite) package="sqlite3"; service="" ;;
            supervisor) package="supervisor"; service="supervisor" ;;
            rabbitmq) package="rabbitmq-server"; service="rabbitmq-server" ;;
            varnish) package="varnish"; service="varnish" ;;
            haproxy) package="haproxy"; service="haproxy" ;;
            java) package="default-jre-headless"; service="" ;;
            go) package="golang-go"; service="" ;;
            ruby) package="ruby"; service="" ;;
            rsync) package="rsync"; service="" ;;
            curl) package="curl"; service="" ;;
            wget) package="wget"; service="" ;;
            nano) package="nano"; service="" ;;
            vim) package="vim-tiny"; service="" ;;
            clamav) package="clamav"; service="" ;;
        esac
        if [[ ! "$version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]]; then
            echo "[ERROR] Invalid ${component} package version." >&2
            exit 2
        fi
        echo "[2/3] Installing ${component} package version ${version} from the configured OS repositories..."
        apt-get install -y --allow-downgrades --no-install-recommends "${package}=${version}"
        if [[ "$component" == "certbot" ]] && apt-cache madison python3-certbot-apache | grep -q '|'; then
            echo "[INFO] Installing the available Apache integration for Certbot..."
            apt-get install -y --no-install-recommends python3-certbot-apache
        fi
        if [[ -n "$service" ]]; then
            echo "[3/3] Enabling and starting ${service}..."
            if [[ ( "$service" == "nginx" || "$service" == "haproxy" ) ]] && systemctl is-active --quiet apache2; then
                echo "[WARNING] Apache is active and may already use ports 80/443; ${service} may fail to start until ports are available."
                systemctl enable "$service"
                if systemctl start "$service"; then
                    echo "[INFO] ${service} started."
                else
                    echo "[ERROR] ${service} was installed but could not start. Check for port conflicts." >&2
                    exit 1
                fi
            else
                systemctl enable --now "$service"
            fi
        else
            echo "[3/3] ${component} is installed; no background service is required."
        fi
        ;;
    *)
        echo "[ERROR] Select a supported App Store component." >&2
        exit 2
        ;;
esac

echo "[DONE] ${component^^} ${version} installation completed."
