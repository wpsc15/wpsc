#!/usr/bin/env bash
set -euo pipefail

apache_version=""
mysql_version=""
php_version=""
application="stack"
domain="localhost"

while (($#)); do
    case "$1" in
        --apache-version) apache_version="${2:?Missing Apache package version}"; shift 2 ;;
        --mysql-version) mysql_version="${2:?Missing MySQL package version}"; shift 2 ;;
        --php-version) php_version="${2:?Missing PHP version}"; shift 2 ;;
        --application) application="${2:?Missing application choice}"; shift 2 ;;
        --domain) domain="${2:?Missing domain}"; shift 2 ;;
        *) echo "[ERROR] Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ "$(id -u)" -ne 0 ]]; then
    echo "[ERROR] Run this installer as root." >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]] || ! . /etc/os-release || [[ "$ID" != "debian" && "$ID" != "ubuntu" ]]; then
    echo "[ERROR] Only Debian and Ubuntu are supported." >&2
    exit 1
fi
if [[ ! "$apache_version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]] ||
   [[ ! "$mysql_version" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]] ||
   [[ ! "$php_version" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "[ERROR] Invalid package version selection." >&2
    exit 2
fi
if [[ "$application" != "stack" && "$application" != "wordpress" ]]; then
    echo "[ERROR] Unsupported application choice." >&2
    exit 2
fi
if [[ ${#domain} -gt 253 || ! "$domain" =~ ^(localhost|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$ ]]; then
    echo "[ERROR] Invalid domain name." >&2
    exit 2
fi
if [[ "$application" == "wordpress" ]] && [[ "$(printf '%s\n' "$php_version" 7.4 | sort -V | head -n1)" != "7.4" ]]; then
    echo "[ERROR] WordPress requires PHP 7.4 or newer." >&2
    exit 2
fi
if [[ "$application" == "wordpress" ]]; then
    apache_upstream="${apache_version#*:}"
    apache_upstream="${apache_upstream%%-*}"
    mysql_upstream="${mysql_version#*:}"
    mysql_upstream="${mysql_upstream%%-*}"
    IFS=. read -r mysql_major mysql_minor _ <<< "$mysql_upstream"
    if ! dpkg --compare-versions "$apache_upstream" ge 2.4; then
        echo "[ERROR] WordPress requires Apache 2.4 or newer." >&2
        exit 2
    fi
    if ! { (( mysql_major >= 8 && mysql_major < 10 )) ||
           (( mysql_major >= 10 && mysql_minor >= 6 )); }; then
        echo "[ERROR] WordPress requires MySQL 8.0+ or MariaDB 10.6+." >&2
        exit 2
    fi
fi
if [[ "$application" == "wordpress" && -e /var/www/wordpress ]]; then
    echo "[ERROR] /var/www/wordpress already exists; refusing to overwrite it." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
echo "[1/4] Refreshing apt package lists..."
apt-get update

echo "[2/4] Installing Apache ${apache_version}, MySQL ${mysql_version}, and PHP ${php_version}..."
apt-get install -y --no-install-recommends \
    "apache2=${apache_version}" \
    "mysql-server=${mysql_version}" \
    "php${php_version}" \
    "php${php_version}-cli" \
    "php${php_version}-fpm" \
    "php${php_version}-mysql" \
    "php${php_version}-curl" \
    "php${php_version}-xml" \
    "php${php_version}-mbstring" \
    "php${php_version}-zip" \
    "php${php_version}-gd" \
    ca-certificates curl

systemctl enable --now apache2
systemctl enable --now mysql
systemctl enable --now "php${php_version}-fpm"

if [[ "$application" == "wordpress" ]]; then
    echo "[3/4] Installing WordPress from wordpress.org..."
    archive="$(mktemp)"
    trap 'rm -f "$archive"' EXIT
    curl --fail --location --silent --show-error \
        --proto '=https' --tlsv1.2 \
        https://wordpress.org/latest.tar.gz --output "$archive"
    tar -xzf "$archive" -C /var/www

    db_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
    mysql --protocol=socket <<SQL
CREATE DATABASE webpanel_wp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'webpanel_wp'@'localhost' IDENTIFIED BY '${db_password}';
GRANT ALL PRIVILEGES ON webpanel_wp.* TO 'webpanel_wp'@'localhost';
FLUSH PRIVILEGES;
SQL

    cp /var/www/wordpress/wp-config-sample.php /var/www/wordpress/wp-config.php
    sed -i \
        -e "s/database_name_here/webpanel_wp/" \
        -e "s/username_here/webpanel_wp/" \
        -e "s/password_here/${db_password}/" \
        /var/www/wordpress/wp-config.php
    python3 - /var/www/wordpress/wp-config.php <<'PY'
import re
import secrets
import sys

path = sys.argv[1]
content = open(path, encoding="utf-8").read()
keys = (
    "AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY",
    "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT",
)
for key in keys:
    pattern = rf"(define\(\s*'{key}'\s*,\s*)'put your unique phrase here'(\s*\);)"
    content, count = re.subn(
        pattern,
        lambda match: f"{match.group(1)}'{secrets.token_urlsafe(48)}'{match.group(2)}",
        content,
    )
    if count != 1:
        raise SystemExit(f"Could not configure WordPress secret {key}")
with open(path, "w", encoding="utf-8") as config:
    config.write(content)
PY

    cat > /etc/apache2/sites-available/webpanel-wordpress.conf <<EOF
<VirtualHost *:80>
    ServerName ${domain}
    DocumentRoot /var/www/wordpress
    <FilesMatch \.php$>
        SetHandler "proxy:unix:/run/php/php${php_version}-fpm.sock|fcgi://localhost"
    </FilesMatch>
    <Directory /var/www/wordpress>
        AllowOverride All
        Require all granted
    </Directory>
    ErrorLog \${APACHE_LOG_DIR}/webpanel-wordpress-error.log
    CustomLog \${APACHE_LOG_DIR}/webpanel-wordpress-access.log combined
</VirtualHost>
EOF
    a2enmod rewrite
    a2enmod proxy_fcgi setenvif
    a2ensite webpanel-wordpress.conf
    chown -R www-data:www-data /var/www/wordpress
    chmod 640 /var/www/wordpress/wp-config.php
    echo "[4/4] Validating Apache configuration and reloading..."
    apache2ctl configtest
    systemctl reload apache2
    echo "[DONE] WordPress files are installed at /var/www/wordpress."
    echo "[DONE] Open http://${domain}/ to complete WordPress setup."
else
    echo "[3/4] Installing the selected PHP runtime and system services..."
    echo "[4/4] LAMP stack installation completed. Configure an Apache virtual host to use PHP-FPM at /run/php/php${php_version}-fpm.sock."
fi
