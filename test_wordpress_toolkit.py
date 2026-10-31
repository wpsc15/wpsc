import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import wordpress


class WordPressArchiveTests(unittest.TestCase):
    @staticmethod
    def wordpress_download() -> bytes:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            files = {
                "wordpress/wp-includes/version.php": b"$wp_version = '6.8.1';",
                "wordpress/wp-config-sample.php": (
                    b"define('DB_NAME', 'database_name_here');\n"
                    b"define('DB_USER', 'username_here');\n"
                    b"define('DB_PASSWORD', 'password_here');\n"
                    b"define('DB_HOST', 'localhost');\n"
                    b"define('AUTH_KEY', 'put your unique phrase here');\n"
                ),
            }
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return payload.getvalue()

    def test_extracts_only_wordpress_regular_files(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            data = b"<?php $wp_version = '6.8.1';"
            member = tarfile.TarInfo("wordpress/wp-includes/version.php")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            wordpress._extract_wordpress(payload.getvalue(), destination)
            self.assertIn("6.8.1", (destination / "wp-includes/version.php").read_text())

    def test_rejects_archive_path_traversal(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            data = b"unsafe"
            member = tarfile.TarInfo("wordpress/../../escape.php")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                wordpress._extract_wordpress(payload.getvalue(), Path(temporary))

    def test_wp_config_uses_unique_salts_and_database_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "wp-config-sample.php").write_text(
                "\n".join(
                    (
                        "define( 'DB_NAME', 'database_name_here' );",
                        "define( 'DB_USER', 'username_here' );",
                        "define( 'DB_PASSWORD', 'password_here' );",
                        "define( 'DB_HOST', 'localhost' );",
                        "define( 'AUTH_KEY', 'put your unique phrase here' );",
                        "define( 'SECURE_AUTH_KEY', 'put your unique phrase here' );",
                    )
                )
            )
            result = wordpress._make_wp_config(root, "wp_db", "wp_user", "secret-password")

        self.assertIn("'wp_db'", result)
        self.assertIn("'wp_user'", result)
        self.assertIn("'secret-password'", result)
        auth_key = result.split("AUTH_KEY", 1)[1].splitlines()[0]
        secure_key = result.split("SECURE_AUTH_KEY", 1)[1].splitlines()[0]
        self.assertNotEqual(auth_key, secure_key)
        self.assertNotIn("put your unique phrase here", result)

    def test_reads_database_config_without_evaluating_php(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "wp-config.php"
            config.write_text(
                "define('DB_NAME', 'wp_db');\n"
                "define('DB_USER', 'wp_user');\n"
                "define('DB_PASSWORD', 'p\\'ass');\n"
                "define('DB_HOST', 'localhost:3307');\n"
            )
            values = wordpress._read_database_config(config)
        self.assertEqual(values, ("wp_db", "wp_user", "p'ass", "localhost:3307"))

    def test_backup_contains_database_and_wordpress_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "site"
            (root / "wp-content").mkdir(parents=True)
            (root / "wp-content" / "test.txt").write_text("content")
            (root / "wp-config.php").write_text(
                "define('DB_NAME', 'wp_db');"
                "define('DB_USER', 'wp_user');"
                "define('DB_PASSWORD', 'secret');"
                "define('DB_HOST', 'localhost');"
            )
            backup_root = base / "backups"
            site = {"root": str(root), "domain": "example.test"}
            run_result = type("Result", (), {"returncode": 0, "stderr": b""})()
            chmod_calls = []
            with (
                patch.object(wordpress, "WEB_ROOT", base),
                patch.object(wordpress, "BACKUP_ROOT", backup_root),
                patch.object(wordpress.shutil, "which", return_value="/usr/bin/mysqldump"),
                patch.object(wordpress.subprocess, "run", return_value=run_result) as run,
                patch.object(wordpress.os, "chmod", side_effect=lambda *args: chmod_calls.append(args)),
            ):
                def write_dump(*args, **kwargs):
                    kwargs["stdout"].write(b"-- test SQL dump")
                    return run_result

                run.side_effect = write_dump
                backup = wordpress.create_wordpress_backup(site)

            self.assertIn((backup, 0o600), chmod_calls)
            with tarfile.open(backup, "r:gz") as archive:
                self.assertEqual(set(archive.getnames()), {
                    "database.sql",
                    "wp-config.php",
                    "wp-content",
                    "wp-content/test.txt",
                })
                self.assertEqual(archive.extractfile("database.sql").read(), b"-- test SQL dump")

    def test_backup_lookup_rejects_traversal(self):
        with self.assertRaises(FileNotFoundError):
            wordpress.find_wordpress_backup("../outside.tar.gz")

    def test_install_creates_site_database_and_scoped_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_is_dir = Path.is_dir

            def is_dir(path):
                if path == Path("/etc/apache2/sites-available"):
                    return True
                return original_is_dir(path)

            with (
                patch.object(wordpress, "WEB_ROOT", root),
                patch.object(wordpress.os, "geteuid", return_value=0, create=True),
                patch.object(Path, "is_dir", is_dir),
                patch.object(wordpress.shutil, "which", return_value="/usr/bin/tool"),
                patch.object(wordpress, "_download_wordpress", return_value=self.wordpress_download()),
                patch.object(wordpress, "create_database", return_value=(True, "")) as create_db,
                patch.object(wordpress, "create_user", return_value=(True, "")) as create_account,
                patch.object(wordpress, "add_website", return_value=7),
                patch.object(
                    wordpress,
                    "create_apache_vhost",
                    return_value={
                        "domain": "example.test",
                        "vhost_path": "/etc/apache2/sites-available/example.test.conf",
                        "document_root": str(root / "example.test"),
                    },
                ),
                patch.object(wordpress, "set_website_vhost"),
                patch.object(wordpress, "_set_wordpress_ownership"),
                patch.object(
                    wordpress,
                    "_initialize_wordpress",
                    return_value={"admin_username": "wpadmin_test", "admin_password": "test-secret"},
                ) as initialize,
            ):
                result = wordpress.install_wordpress(
                    "example.test",
                    "root-secret",
                    "Example Site",
                    "admin@example.test",
                )

            self.assertEqual(result["url"], "http://example.test/wp-login.php")
            self.assertTrue(result["admin_username"].startswith("wpadmin_"))
            self.assertTrue(result["admin_password"])
            self.assertTrue((root / "example.test" / "wp-config.php").is_file())
            self.assertFalse((root / "example.test" / "index.html").exists())
            initialize.assert_called_once_with(
                root / "example.test",
                "Example Site",
                "admin@example.test",
                "en_US",
                "",
            )
            create_db.assert_called_once()
            self.assertEqual(create_account.call_args.kwargs["database"].startswith("wp_"), True)

    def test_install_requires_root(self):
        with (
            patch.object(wordpress.os, "geteuid", return_value=1000, create=True),
            self.assertRaisesRegex(PermissionError, "root"),
        ):
            wordpress.install_wordpress("example.test")

    def test_initializer_passes_site_metadata_and_keeps_secret_in_private_file(self):
        observed = {}
        chmod_calls = []

        def run(command, **kwargs):
            settings_path = Path(command[2])
            script_path = Path(command[1])
            observed["settings"] = json.loads(settings_path.read_text())
            observed["script"] = script_path.read_text()
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(wordpress.subprocess, "run", side_effect=run),
            patch.object(wordpress.shutil, "which", return_value="/usr/bin/php"),
            patch.object(wordpress.os, "chmod", side_effect=lambda *args: chmod_calls.append(args)),
            patch.object(wordpress.secrets, "token_hex", return_value="abc123"),
            patch.object(wordpress.secrets, "token_urlsafe", return_value="generated-password"),
        ):
            credentials = wordpress._initialize_wordpress(
                Path("/var/www/example.test"),
                "Example Site",
                "admin@example.test",
                "fr_FR",
            )

        self.assertEqual(observed["settings"]["title"], "Example Site")
        self.assertEqual(observed["settings"]["email"], "admin@example.test")
        self.assertEqual(observed["settings"]["language"], "fr_FR")
        self.assertEqual(observed["settings"]["password"], "generated-password")
        self.assertEqual([mode for _, mode in chmod_calls[-2:]], [0o600, 0o600])
        self.assertIn("wp_install(", observed["script"])
        self.assertEqual(credentials["admin_username"], "wpadmin_abc123")


if __name__ == "__main__":
    unittest.main()
