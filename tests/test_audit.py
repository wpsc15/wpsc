import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from routers import audit


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.executed = []

    def execute(self, query, parameters=()):
        self.executed.append((query, parameters))
        if "COUNT(*)" in query:
            return self
        return self

    def commit(self):
        pass

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [{
            "id": 1,
            "user": "admin",
            "action": "LOGIN",
            "detail": "Successful sign in",
            "ip": "127.0.0.1",
            "created_at": "2026-10-31 04:00:00",
        }]

    def close(self):
        self.closed = True


class ActivityLogTests(unittest.TestCase):
    def test_activity_page_has_readable_entries_and_delete_action(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/audit",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        connection = FakeConnection()
        with patch.object(audit, "get_db", return_value=connection):
            response = asyncio.run(audit.audit_page(request, page=1))

        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Successful sign in", html)
        self.assertIn("Clear activity log", html)
        self.assertIn("Delete all recorded activity history", html)
        self.assertIn('name="csrf_token"', html)
        self.assertTrue(connection.closed)
        stylesheet = Path(__file__).parent.parent / "static" / "panel.css"
        css = stylesheet.read_text(encoding="utf-8")
        self.assertIn(".audit-table td {", css)
        self.assertIn(".panel-content .logs-output", css)

    def test_clear_action_deletes_existing_rows_without_creating_a_new_entry(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/audit/clear",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin", "_csrf_token": "token"},
                "client": ("127.0.0.1", 12345),
            }
        )
        connection = FakeConnection()
        with patch.object(audit, "get_db", return_value=connection):
            response = asyncio.run(
                audit.clear_audit_log(request, csrf_token="token")
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/audit")
        self.assertTrue(any("DELETE FROM audit_log" in query for query, _ in connection.executed))
        self.assertEqual(request.session["flash"]["type"], "success")
        self.assertEqual(request.session["flash"]["msg"], "Activity log deleted.")
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
