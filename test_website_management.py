import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.websites import discover_wordpress_sites


class WordpressDiscoveryTests(unittest.TestCase):
    def test_discovers_wordpress_and_matches_monitored_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            web_root = Path(temporary)
            root = web_root / "example.test"
            (root / "wp-includes").mkdir(parents=True)
            (root / "wp-admin").mkdir()
            (root / "wp-content" / "plugins" / "hello-dolly").mkdir(parents=True)
            (root / "wp-content" / "plugins" / "seo-tool").mkdir()
            (root / "wp-content" / "themes" / "sample").mkdir(parents=True)
            (root / "wp-config.php").write_text("<?php")
            (root / "wp-includes" / "version.php").write_text("$wp_version = '6.8.1';")
            monitored = [{
                "name": "Example",
                "url": "https://example.test",
                "vhost_domain": "example.test",
                "document_root": str(root),
            }]

            sites = discover_wordpress_sites(monitored, web_root)

        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["version"], "6.8.1")
        self.assertEqual(sites[0]["plugin_count"], 2)
        self.assertEqual(sites[0]["theme_count"], 1)
        self.assertEqual(sites[0]["admin_url"], "https://example.test/wp-admin/")
        self.assertTrue(sites[0]["monitored"])

    def test_ignores_invalid_install_and_symlinked_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            web_root = Path(temporary)
            missing_version = web_root / "missing-version"
            (missing_version / "wp-includes").mkdir(parents=True)
            (missing_version / "wp-config.php").write_text("<?php")
            (missing_version / "wp-includes" / "version.php").write_text("not WordPress")
            symlink_root = web_root / "symlink-config"
            (symlink_root / "wp-includes").mkdir(parents=True)
            (symlink_root / "wp-includes" / "version.php").write_text("$wp_version = '6.0';")
            try:
                (symlink_root / "wp-config.php").symlink_to(missing_version / "wp-config.php")
            except OSError as exc:
                self.skipTest(f"Symlink creation is unavailable: {exc}")

            sites = discover_wordpress_sites([], web_root)

        self.assertEqual(sites, [])


class WebsiteTemplateTests(unittest.TestCase):
    def test_website_and_wordpress_templates_compile(self):
        from fastapi.templating import Jinja2Templates

        templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
        for name in ("websites.html", "wp_toolkit.html"):
            with self.subTest(template=name):
                templates.env.get_template(name)

    def test_website_page_renders_reference_columns_and_wp_nav(self):
        from starlette.requests import Request
        from routers import websites as website_router

        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/websites",
            "query_string": b"",
            "headers": [],
            "session": {"user": "admin"},
            "client": ("127.0.0.1", 12345),
        })
        site = {
            "id": 1,
            "name": "Example",
            "url": "https://example.test",
            "vhost_path": "/etc/apache2/sites-available/example.test.conf",
            "vhost_domain": "example.test",
            "document_root": "/var/www/example.test",
            "is_up": 1,
            "status_code": 200,
            "response_time_ms": 38,
            "last_checked": "2026-10-30 12:00:00",
            "error": "",
        }
        with (
            patch.object(website_router, "list_websites", return_value=[site]),
            patch.object(website_router, "service_status", return_value="active"),
        ):
            response = asyncio.run(website_router.websites_page(request))
        html = response.body.decode()
        for expected in (
            "Backup / restore",
            "Quick actions",
            "URL scheme",
            "Requests",
            "WAF",
            "WP Toolkit",
            "Example",
            "data-site-open",
            "Site modification",
            "Domain Manager",
            "data-site-config-link",
            "Apply Let's Encrypt",
            "confirm_domain",
            "/etc/letsencrypt/",
        ):
            self.assertIn(expected, html)

    def test_ssl_route_requires_confirmation_and_targets_saved_vhost(self):
        from routers import websites as website_router
        from starlette.requests import Request

        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "example.test.conf"
            config_path.write_text("<VirtualHost *:80></VirtualHost>")
            site = {
                "id": 7,
                "vhost_path": str(config_path),
                "vhost_domain": "example.test",
            }
            request = Request({
                "type": "http",
                "method": "POST",
                "path": "/websites/7/ssl",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            })
            with (
                patch.object(website_router, "list_websites", return_value=[site]),
                patch.object(website_router, "get_apache_vhost", return_value=(str(config_path), "")),
                patch.object(website_router, "issue_apache_certificate") as issue,
                patch.object(website_router, "require_valid_csrf"),
                patch.object(website_router, "log_action"),
            ):
                response = asyncio.run(website_router.install_website_ssl(
                    request,
                    website_id=7,
                    email="admin@example.test",
                    confirm_domain=True,
                    csrf_token="token",
                ))

            self.assertEqual(response.status_code, 302)
            self.assertEqual(request.session["flash"]["type"], "success")
            issue.assert_called_once_with("example.test", "admin@example.test")

            request = Request({
                "type": "http",
                "method": "POST",
                "path": "/websites/7/ssl",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            })
            with (
                patch.object(website_router, "list_websites", return_value=[site]),
                patch.object(website_router, "issue_apache_certificate") as issue,
                patch.object(website_router, "require_valid_csrf"),
                patch.object(website_router, "log_action"),
            ):
                response = asyncio.run(website_router.install_website_ssl(
                    request,
                    website_id=7,
                    email="admin@example.test",
                    confirm_domain=False,
                    csrf_token="token",
                ))

            self.assertEqual(response.status_code, 302)
            self.assertEqual(request.session["flash"]["type"], "error")
            self.assertIn("Confirm that the domain", request.session["flash"]["msg"])
            issue.assert_not_called()

    def test_wp_toolkit_page_renders_local_management_tools(self):
        from routers import wp_toolkit
        from starlette.requests import Request

        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/wp-toolkit",
            "query_string": b"",
            "headers": [],
            "session": {"user": "admin"},
            "client": ("127.0.0.1", 12345),
        })
        site = {
            "name": "Example",
            "domain": "example.test",
            "root": "/var/www/example.test",
            "version": "6.8.1",
            "plugin_count": 2,
            "theme_count": 1,
            "admin_url": "https://example.test/wp-admin/",
            "monitored": True,
        }
        with (
            patch.object(wp_toolkit, "list_websites", return_value=[]),
            patch.object(wp_toolkit, "discover_wordpress_sites", return_value=[site]),
        ):
            response = asyncio.run(wp_toolkit.wordpress_toolkit_page(request))
        html = response.body.decode()
        for expected in (
            "6.8.1",
            "/var/www/example.test",
            "Login",
            "Add WordPress",
            "Migrate Site",
            "Data Copy",
            "Statistics",
            "Website Title",
            "Email",
            "PHP version",
            "WP version",
            "Language",
            "Apply for SSL",
        ):
            self.assertIn(expected, html)


if __name__ == "__main__":
    unittest.main()
