import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import database
from fastapi.testclient import TestClient
from services.password_reset import build_reset_url, send_password_reset_email


class PasswordResetDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "webpanel.db"
        self.database_patch = patch.object(database, "DB_PATH", self.database_path)
        self.database_patch.start()
        database.init_db()
        database.set_user_email("admin", "admin@example.test")

    def tearDown(self):
        self.database_patch.stop()
        self.temporary.cleanup()

    def test_reset_token_is_one_time_and_updates_password(self):
        token = "t" * 43
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(
            database.create_password_reset_token("ADMIN@example.test", token_hash)["username"],
            "admin",
        )

        username = database.consume_password_reset_token(
            token_hash, database.hash_password("NewStrongPassword123!")
        )

        self.assertEqual(username, "admin")
        self.assertTrue(
            database.verify_password(
                "NewStrongPassword123!", database.get_user("admin")["password"]
            )
        )
        self.assertIsNone(
            database.consume_password_reset_token(
                token_hash, database.hash_password("AnotherStrongPassword123!")
            )
        )

    def test_reset_token_expires(self):
        token_hash = hashlib.sha256(b"expired-token").hexdigest()
        database.create_password_reset_token("admin@example.test", token_hash)
        connection = database.get_db()
        try:
            connection.execute(
                "UPDATE password_reset_tokens SET expires_at = datetime('now', '-1 minute') "
                "WHERE token_hash = ?",
                (token_hash,),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertIsNone(
            database.consume_password_reset_token(
                token_hash, database.hash_password("NewStrongPassword123!")
            )
        )

    def test_reset_requests_are_rate_limited_per_account(self):
        first_hash = hashlib.sha256(b"first-token").hexdigest()
        second_hash = hashlib.sha256(b"second-token").hexdigest()

        self.assertIsNotNone(
            database.create_password_reset_token("admin@example.test", first_hash)
        )
        self.assertIsNone(
            database.create_password_reset_token("admin@example.test", second_hash)
        )


class PasswordResetEmailTests(unittest.TestCase):
    def test_reset_url_requires_https(self):
        with patch.dict(os.environ, {"WEBPANEL_PUBLIC_URL": "http://panel.example.test"}):
            with self.assertRaises(ValueError):
                build_reset_url("token")

    def test_sends_reset_link_using_starttls(self):
        with patch.dict(
            os.environ,
            {
                "WEBPANEL_SMTP_HOST": "smtp.example.test",
                "WEBPANEL_SMTP_PORT": "587",
                "WEBPANEL_SMTP_USER": "panel@example.test",
                "WEBPANEL_SMTP_PASSWORD": "smtp-secret",
                "WEBPANEL_SMTP_FROM": "WebPanel <panel@example.test>",
                "WEBPANEL_SMTP_STARTTLS": "true",
            },
            clear=True,
        ), patch("services.password_reset.smtplib.SMTP") as smtp_factory:
            server = smtp_factory.return_value.__enter__.return_value

            send_password_reset_email(
                "user@example.test", "https://panel.example.test/reset-password?token=abc"
            )

        server.starttls.assert_called_once()
        server.login.assert_called_once_with("panel@example.test", "smtp-secret")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "user@example.test")
        self.assertIn("token=abc", message.get_content())


class PasswordResetRouteTests(unittest.TestCase):
    def test_request_and_consume_reset_link(self):
        from main import app

        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "webpanel.db"
            with patch.object(database, "DB_PATH", database_path), patch.dict(
                os.environ,
                {
                    "WEBPANEL_PUBLIC_URL": "https://panel.example.test",
                    "WEBPANEL_SMTP_HOST": "smtp.example.test",
                    "WEBPANEL_SMTP_PORT": "587",
                    "WEBPANEL_SMTP_FROM": "WebPanel <panel@example.test>",
                    "WEBPANEL_SMTP_STARTTLS": "true",
                },
                clear=True,
            ), patch("services.password_reset.smtplib.SMTP") as smtp_factory:
                with TestClient(app) as client:
                    database.set_user_email("admin", "admin@example.test")
                    forgot_page = client.get("/forgot-password")
                    csrf_token = re.search(
                        r'name="csrf_token" value="([^"]+)"', forgot_page.text
                    ).group(1)
                    response = client.post(
                        "/forgot-password",
                        data={"email": "admin@example.test", "csrf_token": csrf_token},
                        follow_redirects=False,
                    )

                    self.assertEqual(response.status_code, 302)
                    message = smtp_factory.return_value.__enter__.return_value.send_message.call_args.args[0]
                    reset_url = re.search(r"https://\S+", message.get_content()).group(0)
                    reset_path = urlsplit(reset_url).path + "?" + urlsplit(reset_url).query
                    reset_page = client.get(reset_path)
                    reset_csrf = re.search(
                        r'name="csrf_token" value="([^"]+)"', reset_page.text
                    ).group(1)
                    reset_token = re.search(r"[?&]token=([^&]+)", reset_url).group(1)
                    result = client.post(
                        "/reset-password",
                        data={
                            "csrf_token": reset_csrf,
                            "token": reset_token,
                            "new_password": "NewStrongPassword123!",
                            "confirm_password": "NewStrongPassword123!",
                        },
                        follow_redirects=False,
                    )

                    self.assertEqual(result.status_code, 302)
                    self.assertEqual(result.headers["location"], "/login")
                    self.assertTrue(
                        database.verify_password(
                            "NewStrongPassword123!",
                            database.get_user("admin")["password"],
                        )
                    )


if __name__ == "__main__":
    unittest.main()
