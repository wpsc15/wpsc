import asyncio
import subprocess
import unittest
from unittest.mock import patch

from starlette.requests import Request

from routers import php_router
from services import php_svc


def make_request(path="/php", query_string=b""):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query_string,
            "headers": [],
            "session": {"user": "admin"},
            "client": ("127.0.0.1", 12345),
        }
    )


class PhpManagerTests(unittest.TestCase):
    def test_manager_renders_all_reference_sections(self):
        with (
            patch.object(php_router, "get_installed_php_versions", return_value=["8.2"]),
            patch.object(php_router, "get_current_php_version", return_value="8.2.10"),
            patch.object(php_router, "get_php_ini_path", return_value=""),
            patch.object(php_router, "get_php_modules", return_value=["Core", "PDO"]),
            patch.object(php_router, "check_phpmyadmin", return_value={"installed": False}),
            patch.object(php_router, "service_status", return_value="active"),
            patch.object(php_router, "get_csrf_token", return_value="csrf"),
        ):
            response = asyncio.run(php_router.php_page(make_request()))

        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        for label in (
            "Service",
            "Install extensions",
            "Configuration",
            "Limit of upload",
            "Limit of timeout",
            "Configuration file",
            "FPM profile",
            "Disabled functions",
            "Optimization",
            "Load average",
            "Session configuration",
            "Logs",
            "Slow log",
            "phpinfo",
        ):
            with self.subTest(label=label):
                self.assertIn(label, html)

    def test_extension_catalog_only_offers_fixed_apt_packages(self):
        with (
            patch.object(php_svc, "get_php_modules", return_value=["Core", "PDO", "opcache"]),
            patch.object(php_svc.shutil, "which", return_value="/usr/bin/apt-cache"),
            patch.object(
                php_svc.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "package | version | repo", ""),
            ) as run,
        ):
            extensions = php_svc.get_php_extension_catalog("8.2")

        self.assertTrue(next(item for item in extensions if item["name"] == "opcache")["installed"])
        self.assertTrue(all(item["package"].startswith("php8.2-") for item in extensions))
        self.assertEqual(run.call_count, len(php_svc.PHP_EXTENSION_ALLOWLIST))

    def test_limit_form_renders_values_and_csrf_token(self):
        request = make_request(query_string=b"version=8.2&section=limit-upload")
        with (
            patch.object(php_router, "get_installed_php_versions", return_value=["8.2"]),
            patch.object(php_router, "get_current_php_version", return_value="8.2.10"),
            patch.object(php_router, "get_php_ini_path", return_value=""),
            patch.object(php_router, "get_php_modules", return_value=[]),
            patch.object(php_router, "check_phpmyadmin", return_value={"installed": False}),
            patch.object(php_router, "service_status", return_value="active"),
            patch.object(php_router, "get_csrf_token", return_value="csrf-token"),
        ):
            response = asyncio.run(
                php_router.php_page(request, version="8.2", section="limit-upload")
            )
        html = response.body.decode()
        self.assertIn("Maximum uploaded file size", html)
        self.assertIn('value="2M"', html)
        self.assertIn('name="csrf_token" value="csrf-token"', html)

    def test_invalid_extension_and_php_version_are_rejected(self):
        with patch.object(php_svc.subprocess, "run") as run:
            ok, message = php_svc.install_php_extension("8.2", "shell_exec")
        self.assertFalse(ok)
        self.assertIn("supported PHP extension", message)
        run.assert_not_called()
        with self.assertRaises(ValueError):
            php_svc.get_php_extension_catalog("8.2;id")

    def test_ini_setting_route_rejects_unknown_key(self):
        request = make_request()
        with (
            patch.object(php_router, "require_valid_csrf"),
            patch.object(php_router, "get_installed_php_versions", return_value=["8.2"]),
        ):
            response = asyncio.run(
                php_router.save_php_setting(
                    request,
                    version="8.2",
                    section="configuration",
                    key="auto_prepend_file",
                    value="/tmp/payload.php",
                    csrf_token="csrf",
                )
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("supported PHP setting", request.session["flash"]["msg"])


if __name__ == "__main__":
    unittest.main()
