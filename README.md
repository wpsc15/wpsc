# 🌐 WebPanel — Linux Web Hosting Control Panel

A lightweight, modern web-based hosting control panel for Linux servers designed similarly to **aaPanel**. Built with **FastAPI**, **Alpine.js**, **Tailwind CSS**, and **Chart.js** to manage **Apache**, **MySQL / MariaDB**, **PHP**, and **phpMyAdmin** directly through a browser.

---

## 🚀 Key Features

### 1. 🔐 Web-Based Authentication
* **Cookie-Based Sessions**: Secure signed HTTP session cookies (`itsdangerous`).
* **Bcrypt Password Encryption**: Industry-standard password hashing.
* **Audit Logging**: Tracks login attempts, service actions, database modifications, and IP addresses in SQLite.
* **Default Credentials**: `admin` / `admin123` (change on initial setup).

### 2. 📊 Real-Time Server Monitoring
* **Live System Metrics**: CPU %, RAM %, Disk %, and Load Averages ($1$, $5$, $15$ mins).
* **Live WebSocket Streaming**: Stats stream to the browser every 2 seconds via `/ws/stats`.
* **Dynamic Visual Charts**: Real-time interactive CPU & RAM usage history using Chart.js.
* **Service Badges**: Instant active/inactive visual status for Apache and MySQL.
* **Hosting dashboard**: aaPanel-inspired light dashboard with a dark navigation rail, live resource meters, service overview, software shortcuts, and responsive layouts.
* **Service manager**: Discover and control installed Apache, Nginx, MySQL/MariaDB, PHP-FPM, Docker, FTP, mail, Redis, DNS, firewall, and scheduled-task services through a restricted systemd service allowlist.
* **Server logs**: Read the latest 200 journal entries for installed supported services.
* **Scheduled jobs**: Add and remove WebPanel-owned root cron jobs while preserving other crontab entries. Jobs run as root; only create jobs using trusted commands.
* **Firewall manager**: Inspect UFW status and rules, add/remove specific TCP and UDP port rules, and enable UFW. Disabling an active firewall is intentionally blocked in the panel.
* **Docker manager**: List the latest 100 containers, manage container lifecycle, and inspect bounded recent logs by container ID.
* **Account settings**: Change the signed-in administrator password after verifying the current password.
* **Email password recovery**: Request a single-use, 30-minute password reset link by email. Recovery email addresses are managed in account settings and account creation.
* **Activity log**: Browse recent recorded administrative events using bounded pagination.
* **Protected admin forms**: Session-bound CSRF tokens are required for new system-changing service, Docker, firewall, cron, and account actions.

### 3. 🌍 Apache Web Server Control
* **Lifecycle Management**: Start, Stop, Restart, and Graceful Reload (`systemctl`).
* **Virtual Host Overview**: View all active/enabled and available Apache virtual hosts.
* **Configuration Editor**: Edit the server's main Apache configuration and managed virtual hosts. Each save makes a backup, validates Apache configuration, then reloads the service.
* **Live Log Viewer**: Inspect recent lines from Apache's `error.log` and `access.log` with one click.

### 4. 🗄️ MySQL / MariaDB Database Control
* **Service Control**: Start, Stop, and Restart MySQL / MariaDB.
* **Database inventory**: Open the Database Manager from the left navigation. Search, filter, and paginate databases; view table counts, estimated size, default collation, database-scoped accounts, and available actions in a compact management table. System schemas are excluded from the default view and protected from deletion.
* **Database management**: Create and delete databases with safety confirmations; grant or revoke one account's access to one selected database.
* **User management**: View existing users and hosts, create database-scoped logins or explicitly selected full database administrators, rotate passwords (shown once), and delete non-system users.
* **Strong phpMyAdmin login**: Generate a 24-character cryptographically random password for a named MySQL login usable from both phpMyAdmin and MySQL clients. Credentials are shown once. Ubuntu's socket-authenticated `root` account is left unchanged.
* **Session Auth**: Keeps a supplied root credential in server memory keyed by a random signed-session token (24-hour idle timeout); the password itself is not stored in the browser cookie.
* **Credential display**: Newly created database credentials are shown once in the immediate result; MySQL does not permit plaintext passwords for existing accounts to be retrieved.
* **Backup and restore**: Create/download a full SQL dump or a single-database dump, restore trusted `.sql` files (up to 256 MiB), and re-download saved backups. Local backups are saved in `database_backups/` with restrictive permissions.
* **Limitations**: Backups are manual; automatic schedules, a recycle bin, and storage quotas are not configured. Passwords cannot be retrieved in plaintext; reset rotates them instead.
* **Scope**: These database tools support MySQL/MariaDB only. Other database engines and aaPanel-specific paid integrations are not included.

### 5. 🐘 PHP Multi-Version & Configuration Management
* Open PHP settings from the App Store; PHP is intentionally not a separate item in the left navigation.
* **Multi-Version Detection**: Automatically detects installed PHP versions (e.g. PHP 8.3, 8.2, 8.1, 7.4).
* **PHP settings workspace**: Dedicated sections for service controls, repository-backed extensions, common configuration, upload and timeout limits, php.ini, FPM pool profiles, disabled functions, OPcache, server load, sessions, logs, slow logs, and PHP information.
* **Extension installation**: Installs only a fixed set of PHP extensions from the configured Debian/Ubuntu repositories; the matching PHP-FPM service is restarted after installation.
* **Full php.ini Editor**:
  * `upload_max_filesize`
  * `post_max_size`
  * `max_execution_time`
  * `max_input_time`
  * `memory_limit`
  * `display_errors`
  * `date.timezone`
  * `max_file_uploads`
* **Automated Service Reload**: Automatically applies changes and restarts `php-fpm` upon saving.
* **PHP-FPM controls**: View the selected PHP-FPM service status and start, stop, restart, or reload it from the PHP settings workspace.
* **Protected config saves**: PHP settings and pool profile saves keep timestamped backups, validate before applying, and restore the original if validation or service restart fails.
* **Module Inspector**: Visual badges displaying all compiled and loaded PHP modules.

### 6. 🗃️ phpMyAdmin Integration
* **Automatic Detection**: Detects phpMyAdmin installation paths (`/usr/share/phpmyadmin`, etc.).
* **One-Click Apache Toggle**: Enable (`a2enconf phpmyadmin`) or disable (`a2disconf phpmyadmin`) access via Apache directly from the UI.
* **App Store installer**: Install phpMyAdmin from the App Store, selecting an available repository version. Apache integration is enabled when its package configuration is available.
* **Configuration Editor**: Edit phpMyAdmin's PHP configuration with a backup and PHP syntax check.
* **Direct Web Access**: Launch phpMyAdmin in a new tab when active and sign in with a MySQL account created on the MySQL page.
* **PHP-page shortcut**: Open phpMyAdmin directly from the PHP page when Apache integration is enabled.
* **Setup Guide**: Built-in terminal commands for Debian/Ubuntu and RHEL/CentOS systems if not installed.

### 7. 🌍 Website Monitoring
* Maintain a compact website inventory with Apache virtual-host links, search, site-type filters, and online/offline totals.
* Add up to 100 HTTP or HTTPS websites to a persistent monitor list; optionally create an Apache HTTP virtual host.
* Background checks every 60 seconds report availability, HTTP status, response time, and last check time.
* Manually trigger a check or remove a website from monitoring.
* **WP Toolkit** provides aaPanel-style local site creation: domain, site title, admin email, active PHP-FPM selection, WordPress stable release, and locale. It downloads official WordPress files, creates a scoped database/account and Apache vhost, initializes WordPress, and displays the generated administrator credentials once.
* View local installation/version/status/plugin/theme statistics and create private site backups containing the database, `wp-content`, and `wp-config.php`. Remote management, migration, restore, WordPress updates, batch operations, and plugin/theme sets are not implemented and are clearly marked unavailable.
* Installation requires WebPanel to run as root, Apache/PHP/MySQL to be installed, accepted MySQL credentials, an active PHP-FPM socket for per-site PHP selection, and outbound HTTPS access to `wordpress.org`. Point DNS to the server before installation; configure TLS separately.

### 8. 📁 Web-root File Manager
* Browse `/var/www` by default, create directories, upload/download files, edit UTF-8 text files up to 1 MiB, and delete files or empty directories. Set `WEBPANEL_WEB_ROOT` to use a different web root (for example, a user-owned directory for local WSL development).
* Access is confined to `/var/www`; path traversal, symlinks, and hard-linked files are blocked. Uploads are limited to 10 MiB.

### 9. 📦 App Store
* Search and filter the supported app catalog by installed state, deployment software, and tools.
* View installed versions, available repository versions, package locations, and links to the related settings pages.
* Install 30 repository-backed applications, including Apache, Nginx, MySQL, PostgreSQL, SQLite, PHP + PHP-FPM, Node.js, Redis, RabbitMQ, Docker, phpMyAdmin, Certbot, Supervisor, Varnish, HAProxy, Java, Go, Ruby, and common server tools, when available in configured Debian/Ubuntu repositories.
* Nginx and HAProxy may conflict with Apache on ports 80/443; this is surfaced in their app descriptions and installer output. Fail2ban is installed and started but requires jail configuration; ClamAV needs updated virus definitions before scanning.
* Third-party plug-ins, paid software, and in-panel App Store uninstall actions are not offered.
* Each install opens a popup WebPanel console with live installer output; only one apt operation runs at a time.
* PHP installs PHP-FPM and common extensions. If Apache is installed without an active PHP-FPM config, the selected version is connected; an existing PHP version configuration is left unchanged.
* Docker installs `docker.io` from the configured OS repositories, enables and starts the Docker service, and installs Compose v2 when available in those repositories. Container start/stop/restart and bounded logs are available from the Docker Manager.
* Does not add third-party package repositories. The installer requires WebPanel to run as root and currently supports Debian and Ubuntu only. phpMyAdmin's internal database configuration is disabled; use an existing MySQL account to sign in.

### 10. 🌐 Domain websites and Apache virtual hosts
* Add a domain to monitoring and optionally create/enable an Apache virtual-host file at `/etc/apache2/sites-available/<domain>.conf`.
* Each new virtual host uses `/var/www/<domain>` as its document root and is validated before Apache reload.
* Generated vhosts serve HTTP on port 80. From a site's SSL panel, root-run deployments with Certbot and its Apache plugin can request a Let's Encrypt certificate, configure Apache HTTPS, and redirect HTTP to HTTPS. The domain must resolve to this server, and inbound ports 80 and 443 must be reachable. Certbot keeps certificates and private keys under `/etc/letsencrypt/`, outside the public document root; never copy private keys into the website directory. Automatic renewal depends on the operating system's Certbot renewal timer. Debian/Ubuntu installation includes Certbot and its Apache plugin.

---

## 📁 Project Architecture

```
/opt/webpanel/
├── main.py                     # FastAPI application entrypoint & startup
├── config.py                   # Global settings, paths, secret keys
├── database.py                 # SQLite database setup, users & audit logs
├── dependencies.py             # Auth dependencies & middleware
├── requirements.txt            # Python dependencies
├── wpsc.tar.gz                 # Source bundle used by the bootstrap installer
├── install.sh                  # Bootstrap installer for the release bundle
├── install_webpanel.sh         # Full Linux setup and service installer
├── uninstall.sh                # WebPanel service and files removal script
├── install_component.sh        # Individual Debian/Ubuntu package installation script
├── services/
│   ├── system.py               # psutil monitoring, shell command runner, systemctl
│   ├── system_services.py      # Allowlisted systemd service discovery and controls
│   ├── server_logs.py          # Allowlisted journal reads
│   ├── cron_jobs.py            # WebPanel-owned root crontab entries
│   ├── firewall.py             # Constrained UFW operations
│   ├── docker_manager.py       # Validated Docker container controls and logs
│   ├── web_security.py         # Session-bound CSRF protection for admin actions
│   ├── apache.py               # Apache service actions, vhosts, logs, config
│   ├── mysql_svc.py            # MySQL database & user operations
│   ├── php_svc.py              # PHP version detection, php.ini parser, phpMyAdmin
│   ├── websites.py             # Website monitoring, HTTP checks, WordPress discovery
│   ├── letsencrypt.py          # Certbot Apache certificate issuance and deployment
│   ├── wordpress.py            # WordPress install, database setup, and site backups
│   ├── domains.py              # ASCII domain-name validation
│   ├── file_manager.py         # Sandboxed /var/www file operations
│   └── stack_installer.py      # Debian/Ubuntu package version discovery and install jobs
├── routers/
│   ├── auth.py                 # Login, logout, session management
│   ├── dashboard.py            # Overview dashboard controller
│   ├── apache.py               # Apache management routes
│   ├── mysql_router.py         # Database management routes
│   ├── php_router.py           # PHP settings & phpMyAdmin routes
│   ├── monitor.py              # WebSocket live streaming endpoint
│   ├── websites.py             # Website monitoring routes
│   ├── wp_toolkit.py           # WordPress install, backup, and inventory routes
│   ├── files.py                # Web-root file manager routes
│   ├── stack.py                # App Store and package installer routes
│   ├── docker.py               # Docker container manager
│   ├── settings.py             # Administrator account settings
│   ├── audit.py                # Activity log browser and history deletion
│   ├── services.py             # System service controls
│   ├── logs.py                 # System journal viewer and clearing
│   ├── cron.py                 # WebPanel-managed scheduled jobs
│   └── firewall.py             # Restricted UFW status and rule management
├── templates/
│   ├── base.html               # Responsive layout and navigation
│   ├── login.html              # Standalone login page
│   ├── dashboard.html          # Stats cards, charts, quick actions
│   ├── apache.html             # Apache control, vhosts, logs
│   ├── apache_config.html      # Apache configuration editor
│   ├── mysql.html              # Database and user management
│   ├── php.html                # PHP settings and modules
│   ├── phpmyadmin.html         # phpMyAdmin status, config, and launch
│   ├── websites.html           # Website monitoring and virtual hosts
│   ├── wp_toolkit.html         # WordPress management, backup, and statistics UI
│   ├── files.html              # Sandboxed web-root file manager
│   ├── stack.html              # App Store
│   ├── docker.html             # Docker container manager
│   ├── settings.html           # Administrator account settings
│   ├── audit.html              # Paginated activity log
│   ├── services.html           # Installed service manager
│   ├── logs.html               # System journal viewer
│   ├── cron.html               # WebPanel-owned scheduled tasks
│   ├── firewall.html           # UFW firewall status and rules
│   ├── installer_terminal.html # Popup package-install output console
│   └── mysql_config.html       # Read-only MySQL configuration view
└── tests/
    └── test_admin_tools.py     # Validation and safety tests for admin modules
```

---

## ⚡ Quick Installation (Linux Server)

### 1. Transfer the release files
Copy both `install.sh` and `wpsc.tar.gz` to a Linux server with `scp`:
```bash
scp install.sh wpsc.tar.gz root@your-server-ip:/tmp/
ssh root@your-server-ip
cd /tmp
```

### 2. Extract and install
Run the bootstrap installer as root. It extracts `wpsc.tar.gz` to a temporary directory and starts the full installer:
```bash
sudo bash install.sh
```

The script will:
1. Check root access, identify the Linux distribution, and verify that systemd is available.
2. Install Python 3 and the system prerequisites (Python 3.10 or newer is required).
3. Deploy the application to `/opt/webpanel` and prepare its virtual environment.
4. Install the Python dependencies and create the `webpanel.service` systemd service.
5. Start the service and verify that it is running.

The installer prints the panel URL, default login, and service-management commands when setup completes. Change the default password after signing in, allow TCP port `8080` through your firewall, and configure HTTPS before exposing the panel publicly.

The installer supports Linux servers with systemd and `apt-get`, `dnf`, or `yum`; Python 3.10 or newer is required. The `wpsc.tar.gz` bundle contains the application source, templates, static assets, tests, and setup scripts. It excludes local virtual environments, caches, and the development SQLite database.

### Uninstall
Run this on the installed server:
```bash
sudo bash /opt/webpanel/uninstall.sh
```
The uninstaller asks for confirmation, disables the WebPanel service, removes WebPanel-tagged root cron jobs, and deletes `/opt/webpanel` (including its SQLite account/audit data and app-local backups). It leaves system packages, hosted website files and Apache configuration, SSL certificates, MySQL databases, and WordPress backups under `/var/backups/webpanel/wordpress` untouched. To skip the confirmation prompt, use `sudo bash /opt/webpanel/uninstall.sh --yes`.

---

## 🛠️ Manual Installation

If you prefer to set up manually without `install.sh`:

```bash
# 1. Install prerequisites (Debian/Ubuntu)
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# 2. Copy files to /opt/webpanel
sudo mkdir -p /opt/webpanel
sudo cp -r ./* /opt/webpanel/
cd /opt/webpanel

# 3. Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Run the panel
python3 main.py
```

---

## 🔑 Default Credentials & Security

* **URL**: `http://<your-server-ip>:8080`
* **Default Username**: `admin`
* **Default Password**: `admin123`

### Change Password
You can change the admin password at any time using Python:
```bash
cd /opt/webpanel
venv/bin/python -c "from database import get_db, hash_password; conn=get_db(); conn.execute('UPDATE users SET password=? WHERE username=?', (hash_password('YOUR_NEW_PASSWORD'), 'admin')); conn.commit(); print('Password updated!')"
```

### Configure email password recovery

Password recovery requires an SMTP account and the panel's canonical HTTPS URL. Add these variables to `/etc/webpanel.env` on Linux (keep the file root-readable only), then restart WebPanel:

```ini
WEBPANEL_PUBLIC_URL=https://panel.example.com
WEBPANEL_SMTP_HOST=smtp.example.com
WEBPANEL_SMTP_PORT=587
WEBPANEL_SMTP_USER=webpanel@example.com
WEBPANEL_SMTP_PASSWORD=replace-with-an-smtp-app-password
WEBPANEL_SMTP_FROM=WebPanel <webpanel@example.com>
WEBPANEL_SMTP_STARTTLS=true
```

Port `465` uses implicit TLS automatically. For other ports, STARTTLS is enabled by default; set `WEBPANEL_SMTP_STARTTLS=false` only when connecting to a trusted SMTP relay that does not support TLS. `WEBPANEL_SMTP_USER` and `WEBPANEL_SMTP_PASSWORD` may both be omitted for an authenticated network relay.

The installer configures the service to read `/etc/webpanel.env`. On an existing installation, ensure its systemd unit includes `EnvironmentFile=-/etc/webpanel.env`, then run `sudo systemctl daemon-reload && sudo systemctl restart webpanel`. Users must save a recovery email in Settings; newly created accounts require one. Reset requests return the same response whether the email is registered or not.

---

## 🔧 Managing the Background Service

Use the short `wpsc` command for common terminal operations:

```bash
wpsc                 # Interactive menu
wpsc status
wpsc start
wpsc stop
wpsc restart
wpsc repair          # Check and repair common issues
wpsc logs            # Last 50 service log lines
wpsc logs 200        # Last 200 lines
wpsc follow          # Follow live logs
wpsc help
```

The service control and repair commands use `sudo` when not run as root. The interactive stop action asks for confirmation. `wpsc reload` restarts WebPanel because the application does not implement a reload signal.

`wpsc repair` checks required files, Python dependencies and source syntax, validates the SQLite database read-only, reloads systemd, restarts the service, and checks `/login`. If dependencies are inconsistent it attempts to restore them from `requirements.txt`. It does not overwrite application source or attempt to repair a corrupt database; those cases are reported for manual recovery.

| Task | Command |
|---|---|
| Check service status | `systemctl status webpanel` |
| Restart WebPanel | `sudo systemctl restart webpanel` |
| Stop WebPanel | `sudo systemctl stop webpanel` |
| View live logs | `journalctl -u webpanel -f` |

---

## 🛡️ Firewall Configuration

Make sure port `8080` is open on your firewall:

* **UFW (Ubuntu/Debian)**:
  ```bash
  sudo ufw allow 8080/tcp
  sudo ufw reload
  ```
* **Firewalld (CentOS/AlmaLinux)**:
  ```bash
  sudo firewall-cmd --permanent --add-port=8080/tcp
  sudo firewall-cmd --reload
  ```
