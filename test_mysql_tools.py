import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request
from unittest.mock import patch

from routers import mysql_router
from services import mysql_backup, mysql_svc


class MysqlTemplateTests(unittest.TestCase):
    def test_mysql_templates_compile(self):
        template_dir = Path(__file__).parent.parent / "templates"
        environment = Environment(loader=FileSystemLoader(template_dir))
        for name in (
            "base.html",
            "docker.html",
            "stack.html",
            "mysql.html",
            "mysql_backup.html",
            "mysql_config.html",
        ):
            with self.subTest(template=name):
                environment.get_template(name)

    def test_app_store_page_renders_supported_catalog(self):
        from routers import stack as stack_router

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/stack",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        options = {
            "supported": True,
            "error": "",
            "apache": ["2.4.58"],
            "mysql": ["8.0.36"],
            "php": ["8.2"],
            "phpmyadmin": ["5.2.1"],
        }
        docker_options = {"supported": True, "versions": ["24.0.7"]}
        apps = [
            {
                "component": "apache",
                "name": "Apache",
                "developer": "OS repository",
                "category": "Deployment",
                "icon": "fa-globe",
                "location": "/etc/apache2",
                "versions": ["2.4.58"],
                "installed": True,
                "installed_version": "2.4.58",
                "update_available": True,
                "update_version": "2.4.59",
                "available": True,
                "official_url": "https://httpd.apache.org/docs/",
            },
            {
                "component": "php",
                "name": "PHP + PHP-FPM",
                "developer": "OS repository",
                "category": "Deployment",
                "icon": "fa-php",
                "location": "/etc/php",
                "versions": ["8.2"],
                "installed": False,
                "installed_version": "",
                "available": True,
                "official_url": "https://www.php.net/manual/",
            },
        ]
        with (
            patch.object(stack_router, "get_stack_options", return_value=options),
            patch.object(stack_router, "get_docker_options", return_value=docker_options),
            patch.object(stack_router, "get_app_catalog", return_value=apps),
            patch.object(stack_router, "get_install_status", return_value={
                "id": "",
                "component": "",
                "version": "",
                "status": "idle",
                "output": "",
            }),
            patch.object(stack_router, "get_csrf_token", return_value="token"),
        ):
            response = asyncio.run(stack_router.stack_page(request))
        self.assertEqual(response.status_code, 200)
        for expected in (
            "App Store",
            "Official Apps",
            "Third-party Plug-ins",
            "Installed (1)",
            "Deployment",
            "Recently used",
            "Update App List",
            "List view",
            "Grid view",
            "Rows per page",
            "Official OS repository",
            "Setting",
            "Update",
            "Apache",
            "OS repository",
            "Open",
            "Official docs",
            "https://httpd.apache.org/docs/",
            "https://www.php.net/manual/",
            "Uninstall",
            "PHP + PHP-FPM",
        ):
            self.assertIn(expected.encode(), response.body)
        self.assertNotIn(b"<span>Apache</span>", response.body)
        self.assertNotIn(b"<span>PHP</span>", response.body)
        self.assertNotIn(b"Display on Home", response.body)

    def test_database_manager_renders_reference_columns_and_real_values(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/mysql",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        with (
            patch.object(mysql_router, "get_mysql_status", return_value={
                "status": "active",
                "running": True,
                "version": "MySQL 8.0",
            }),
            patch.object(mysql_router, "list_databases", return_value=["appdb"]),
            patch.object(mysql_router, "list_users", return_value=[]),
            patch.object(mysql_router, "list_database_details", return_value=[{
                "name": "appdb",
                "tables": 2,
                "size_bytes": 2048,
                "collation": "utf8mb4_general_ci",
            }]),
            patch.object(mysql_router, "list_database_grants", return_value={
                "appdb": [{"user": "appuser", "host": "localhost"}],
            }),
            patch.object(mysql_router, "check_phpmyadmin", return_value={"installed": False}),
        ):
            response = mysql_router._mysql_page_response(request)
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Database Manager",
            "Database name",
            "Username",
            "Password",
            "Quota",
            "Backup",
            "Location",
            "Operate",
            "appdb",
            "appuser",
            "Not readable",
            "Not set",
            "action=\"/mysql/reset-user-password\"",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected.encode(), response.body)


class MysqlInventoryTests(unittest.TestCase):
    def test_database_details_parse_size_table_count_and_collation(self):
        output = "app\t3\t2048\tutf8mb4_general_ci\n"
        with patch.object(mysql_svc, "_run_mysql", return_value=(0, output, "")):
            self.assertEqual(
                mysql_svc.list_database_details(),
                [
                    {
                        "name": "app",
                        "tables": 3,
                        "size_bytes": 2048,
                        "collation": "utf8mb4_general_ci",
                    }
                ],
            )

    def test_database_details_report_mysql_errors_and_bad_rows(self):
        with patch.object(mysql_svc, "_run_mysql", return_value=(1, "", "denied")):
            with self.assertRaisesRegex(RuntimeError, "denied"):
                mysql_svc.list_database_details()
        with patch.object(mysql_svc, "_run_mysql", return_value=(0, "bad row", "")):
            with self.assertRaisesRegex(RuntimeError, "invalid inventory row"):
                mysql_svc.list_database_details()


class MysqlPermissionTests(unittest.TestCase):
    def test_permission_change_is_limited_to_one_existing_database_and_user(self):
        with patch.object(
            mysql_svc,
            "_run_mysql",
            side_effect=[(0, "1\t1", ""), (0, "", "")],
        ) as run_mysql:
            ok, _ = mysql_svc.set_database_permission(
                "app", "webuser", "localhost", True
            )
        self.assertTrue(ok)
        self.assertIn("SELECT COUNT(*) FROM information_schema.SCHEMATA", run_mysql.call_args_list[0].args[0])
        self.assertEqual(
            run_mysql.call_args_list[1].args[0],
            "GRANT ALL PRIVILEGES ON `app`.* TO 'webuser'@'localhost';",
        )

    def test_permission_change_rejects_system_schema_and_invalid_account(self):
        with patch.object(mysql_svc, "_run_mysql") as run_mysql:
            self.assertFalse(
                mysql_svc.set_database_permission(
                    "mysql", "webuser", "localhost", True
                )[0]
            )
            self.assertFalse(
                mysql_svc.set_database_permission(
                    "app", "root", "localhost", True
                )[0]
            )
        run_mysql.assert_not_called()

    def test_password_reset_verifies_account_and_returns_generated_secret_once(self):
        with (
            patch.object(mysql_svc, "_run_mysql", side_effect=[(0, "1", ""), (0, "", "")]) as run_mysql,
            patch.object(mysql_svc.secrets, "token_urlsafe", return_value="strong-secret"),
        ):
            ok, _, password = mysql_svc.reset_user_password(
                "webuser", "localhost"
            )
        self.assertTrue(ok)
        self.assertEqual(password, "strong-secret")
        self.assertIn("ALTER USER 'webuser'@'localhost'", run_mysql.call_args_list[1].args[0])

    def test_protected_users_cannot_be_deleted_or_reset(self):
        with patch.object(mysql_svc, "_run_mysql") as run_mysql:
            self.assertFalse(mysql_svc.drop_user("root")[0])
            self.assertFalse(mysql_svc.reset_user_password("root", "localhost")[0])
        run_mysql.assert_not_called()


class MysqlDatabaseBackupTests(unittest.TestCase):
    def test_database_backup_uses_validated_database_and_private_file(self):
        def dump(args, **kwargs):
            kwargs["stdout"].write(b"SQL dump")
            return subprocess.CompletedProcess(args, 0, "", "")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(mysql_backup.subprocess, "run", side_effect=dump) as run:
                backup = mysql_backup.create_database_backup(
                    "app", "root-secret", Path(temp_dir)
                )
            self.assertTrue(backup.read_bytes())
            if os.name == "posix":
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertTrue(backup.name.startswith("mysql-db-app-"))
            self.assertIn("--databases=app", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["env"]["MYSQL_PWD"], "root-secret")
            self.assertEqual(mysql_backup.find_database_backup(backup.parent, backup.name), backup)
            self.assertEqual(mysql_backup.list_database_backups(backup.parent), [backup])

    def test_database_backup_rejects_invalid_name_before_running_dump(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(mysql_backup.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    mysql_backup.create_database_backup(
                        "app;DROP DATABASE mysql", "", Path(temp_dir)
                    )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
