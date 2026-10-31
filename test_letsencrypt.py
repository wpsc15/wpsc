import unittest
from unittest.mock import patch

from services.letsencrypt import issue_apache_certificate


class LetsEncryptTests(unittest.TestCase):
    @patch("services.letsencrypt.subprocess.run")
    @patch("services.letsencrypt.shutil.which", return_value="/usr/bin/certbot")
    @patch("services.letsencrypt.os.geteuid", return_value=0, create=True)
    def test_issues_and_deploys_certificate_with_apache_plugin(
        self, geteuid, which, run
    ):
        run.return_value.returncode = 0

        issue_apache_certificate("example.test", "admin@example.test")

        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["certbot", "--apache"])
        self.assertIn("--redirect", command)
        self.assertEqual(command[command.index("--email") + 1], "admin@example.test")
        self.assertEqual(command[command.index("--domains") + 1], "example.test")
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    @patch("services.letsencrypt.subprocess.run")
    @patch("services.letsencrypt.shutil.which", return_value="/usr/bin/certbot")
    @patch("services.letsencrypt.os.geteuid", return_value=0, create=True)
    def test_reports_certbot_failure(self, geteuid, which, run):
        run.return_value.returncode = 1
        run.return_value.stderr = "DNS problem: domain does not resolve to this server"
        with self.assertRaisesRegex(RuntimeError, "DNS problem"):
            issue_apache_certificate("example.test", "admin@example.test")

    @patch("services.letsencrypt.shutil.which", return_value="/usr/bin/certbot")
    @patch("services.letsencrypt.os.geteuid", return_value=0, create=True)
    def test_rejects_invalid_email_before_running_certbot(self, geteuid, which):
        with patch("services.letsencrypt.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "valid email"):
                issue_apache_certificate("example.test", "not-an-email")
        run.assert_not_called()

    @patch("services.letsencrypt.shutil.which", return_value=None)
    @patch("services.letsencrypt.os.geteuid", return_value=0, create=True)
    def test_reports_missing_certbot(self, geteuid, which):
        with self.assertRaisesRegex(RuntimeError, "Certbot is not installed"):
            issue_apache_certificate("example.test", "admin@example.test")

    @patch("services.letsencrypt.shutil.which", return_value="/usr/bin/certbot")
    @patch("services.letsencrypt.os.geteuid", return_value=1000, create=True)
    def test_requires_root(self, geteuid, which):
        with self.assertRaises(PermissionError):
            issue_apache_certificate("example.test", "admin@example.test")

    def test_rejects_command_argument_injection_domain(self):
        with self.assertRaises(ValueError):
            issue_apache_certificate("--domains=attacker.test", "admin@example.test")


if __name__ == "__main__":
    unittest.main()
