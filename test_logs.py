import asyncio
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.requests import Request

from routers import logs
from services import server_logs


class ServerLogsPageTests(unittest.TestCase):
    def test_log_view_offers_server_journal_deletion(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/logs",
                "query_string": b"unit=apache2.service",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        with (
            patch.object(logs, "available_log_services", return_value=[
                {"unit": "apache2.service", "name": "Apache", "category": "Web server", "status": "active"},
            ]),
            patch.object(logs, "read_service_log", return_value="sample journal entry"),
        ):
            response = asyncio.run(logs.logs_page(request, unit="apache2.service"))

        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("sample journal entry", html)
        self.assertIn(">Clear logs", html)
        self.assertIn("existing journal history for all services", html)
        self.assertIn('name="csrf_token"', html)
        stylesheet = Path(__file__).parent.parent / "static" / "panel.css"
        self.assertIn(".panel-content .logs-output", stylesheet.read_text(encoding="utf-8"))

    def test_clear_action_deletes_journal_and_reports_success(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/logs/clear",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin", "_csrf_token": "token"},
                "client": ("127.0.0.1", 12345),
            }
        )
        with (
            patch.object(logs, "clear_server_journal") as clear_journal,
            patch.object(logs, "log_action") as log_action,
        ):
            response = asyncio.run(
                logs.clear_logs(request, unit="apache2.service", csrf_token="token")
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/logs")
        clear_journal.assert_called_once_with("apache2.service")
        log_action.assert_called_once()
        self.assertEqual(request.session["flash"]["type"], "success")

    def test_clear_server_journal_rotates_and_vacuums_journal(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with (
            patch.object(server_logs, "available_log_services", return_value=[
                {"unit": "apache2.service"},
            ]),
            patch.object(server_logs.shutil, "which", return_value="/usr/bin/journalctl"),
            patch.object(server_logs.subprocess, "run", return_value=completed) as run,
            patch.object(server_logs, "_service_log_patterns", return_value=()),
        ):
            server_logs.clear_server_journal("apache2.service")

        run.assert_called_once_with(
            ["journalctl", "--rotate", "--vacuum-size=1"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )

    def test_service_log_clear_truncates_files_but_preserves_shared_logs(self):
        with TemporaryDirectory() as temp_dir:
            dedicated_log = Path(temp_dir) / "service.log"
            rotated_log = Path(temp_dir) / "service.log.1.gz"
            shared_log = Path(temp_dir) / "syslog"
            dedicated_log.write_text("service history", encoding="utf-8")
            rotated_log.write_bytes(b"compressed history")
            shared_log.write_text("shared history", encoding="utf-8")
            with patch.object(
                server_logs,
                "_service_log_patterns",
                return_value=(
                    (str(Path(temp_dir) / "service.log*"), None),
                    (str(shared_log), r"service"),
                ),
            ):
                server_logs._clear_service_file_logs("apache2.service")

            self.assertEqual(dedicated_log.read_bytes(), b"")
            self.assertFalse(rotated_log.exists())
            self.assertEqual(shared_log.read_text(encoding="utf-8"), "shared history")

    def test_service_file_logs_are_used_when_the_journal_is_empty(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "service.log"
            log_path.write_text(
                "wanted service event\nunrelated event\nwanted second event\n",
                encoding="utf-8",
            )
            result = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            with (
                patch.object(server_logs, "available_log_services", return_value=[
                    {"unit": "apache2.service"},
                ]),
                patch.object(server_logs.shutil, "which", return_value="/usr/bin/journalctl"),
                patch.object(server_logs.subprocess, "run", return_value=result),
                patch.object(
                    server_logs,
                    "_service_log_patterns",
                    return_value=((str(log_path), r"wanted"),),
                ),
            ):
                output = server_logs.read_service_log("apache2.service")

        self.assertIn("wanted service event", output)
        self.assertIn("wanted second event", output)
        self.assertNotIn("unrelated event", output)


if __name__ == "__main__":
    unittest.main()
