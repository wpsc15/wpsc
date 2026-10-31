import asyncio
import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from starlette.requests import Request
from types import SimpleNamespace
from unittest.mock import patch

from routers import cron as cron_router
from routers import docker as docker_router
from services import (
    cron_jobs,
    docker_manager,
    firewall,
    server_logs,
    stack_installer,
    web_security,
)


class CronScheduleTests(unittest.TestCase):
    def test_valid_schedules(self):
        for schedule in ("0 2 * * *", "*/15 8-17 * * 1-5", "0 0 1,15 * *"):
            with self.subTest(schedule=schedule):
                self.assertEqual(cron_jobs.validate_schedule(schedule), schedule)

    def test_invalid_schedules(self):
        for schedule in (
            "60 * * * *",
            "* * * *",
            "*;id * * * *",
            "5-1 * * * *",
            "* * * * *\n0 0 * * *",
            "0" * 65,
        ):
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                cron_jobs.validate_schedule(schedule)

    def test_managed_jobs_preserve_unmanaged_entries(self):
        job_id = str(uuid.uuid4())
        spool = ["MAILTO=root\n0 4 * * * /usr/local/bin/keep.sh\n"]
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(cron_jobs, "LOCK_PATH", Path(temp_dir) / "lock"),
                patch.object(cron_jobs.os, "geteuid", return_value=0, create=True),
                patch.object(cron_jobs, "_read_crontab", side_effect=lambda: spool[0]),
                patch.object(
                    cron_jobs,
                    "_write_crontab",
                    side_effect=lambda value: spool.__setitem__(0, value),
                ),
            ):
                added_id = cron_jobs.add_managed_job("*/15 * * * *", "/usr/bin/true")
                jobs = cron_jobs.list_managed_jobs()
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["id"], added_id)
                cron_jobs.remove_managed_job(added_id)
        self.assertIn("0 4 * * * /usr/local/bin/keep.sh", spool[0])
        self.assertNotIn("webpanel-job:", spool[0])


class CronRouterTests(unittest.TestCase):
    @staticmethod
    def make_request():
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/cron/add",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )

    def test_schedule_dropdown_and_custom_expression_are_rendered(self):
        request = self.make_request()
        with (
            patch.object(cron_router, "list_managed_jobs", return_value=[]),
            patch.object(cron_router, "get_csrf_token", return_value="token"),
        ):
            response = asyncio.run(cron_router.cron_page(request))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="cron-schedule-preset"', response.body)
        self.assertIn(b"Weekly on Sunday at 02:00", response.body)
        self.assertIn(b'name="custom_schedule"', response.body)

    def test_custom_schedule_is_passed_to_existing_validation(self):
        with (
            patch.object(cron_router, "require_valid_csrf"),
            patch.object(cron_router, "add_managed_job", return_value="job-id") as add_job,
            patch.object(cron_router, "log_action"),
        ):
            response = asyncio.run(
                cron_router.cron_add(
                    self.make_request(),
                    schedule="custom",
                    command="/usr/local/bin/backup.sh",
                    csrf_token="token",
                    custom_schedule="15 3 * * 1",
                )
            )

        self.assertEqual(response.status_code, 302)
        add_job.assert_called_once_with("15 3 * * 1", "/usr/local/bin/backup.sh")


class FirewallTests(unittest.TestCase):
    def test_rule_arguments_are_validated_and_passed_without_a_shell(self):
        with (
            patch.object(firewall.shutil, "which", return_value="/usr/sbin/ufw"),
            patch.object(firewall.os, "geteuid", return_value=0, create=True),
            patch.object(
                firewall,
                "_run_ufw",
                return_value=subprocess.CompletedProcess([], 0, "Rule added", ""),
            ) as run_ufw,
        ):
            self.assertTrue(firewall.change_firewall_rule("allow", "8080", "tcp")[0])
            self.assertEqual(run_ufw.call_args.args, ("allow", "8080/tcp"))
            self.assertFalse(firewall.change_firewall_rule("allow", "22;id", "tcp")[0])
            self.assertFalse(firewall.change_firewall_rule("allow", "22", "icmp")[0])

    def test_active_firewall_cannot_be_disabled(self):
        with (
            patch.object(firewall.shutil, "which", return_value="/usr/sbin/ufw"),
            patch.object(firewall.os, "geteuid", return_value=0, create=True),
            patch.object(
                firewall,
                "_run_ufw",
                return_value=subprocess.CompletedProcess(
                    [], 0, "Status: active", ""
                ),
            ),
        ):
            ok, message = firewall.set_firewall_state("disable")
        self.assertFalse(ok)
        self.assertIn("blocked", message)


class DockerTests(unittest.TestCase):
    def test_docker_page_renders_with_installed_engine(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/docker",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        with (
            patch.object(docker_router, "list_containers", return_value=[]),
            patch.object(docker_router, "get_docker_options", return_value={
                "supported": True,
                "versions": ["29.1.3"],
                "error": "",
            }),
            patch.object(docker_router, "get_install_status", return_value={"status": "idle"}),
            patch.object(docker_router.shutil, "which", return_value="/usr/bin/docker"),
        ):
            response = asyncio.run(docker_router.docker_page(request))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Docker Engine", response.body)
        self.assertIn(b"Start engine", response.body)

    def test_docker_page_renders_container_status(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/docker",
                "query_string": b"",
                "headers": [],
                "session": {"user": "admin"},
                "client": ("127.0.0.1", 12345),
            }
        )
        container = {
            "id": "a" * 64,
            "name": "sample",
            "image": "example:latest",
            "status": "Up 3 minutes",
            "ports": "8080/tcp",
        }
        with (
            patch.object(docker_router, "list_containers", return_value=[container]),
            patch.object(docker_router, "get_docker_options", return_value={
                "supported": True,
                "versions": [],
                "error": "",
            }),
            patch.object(docker_router, "get_install_status", return_value={"status": "idle"}),
            patch.object(docker_router.shutil, "which", return_value="/usr/bin/docker"),
        ):
            response = asyncio.run(docker_router.docker_page(request))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"service-status-active", response.body)
        self.assertIn(b"sample", response.body)

    def test_container_action_requires_a_listed_id_and_passes_fixed_args(self):
        container_id = "a" * 64
        listing = json.dumps({"ID": container_id})
        with (
            patch.object(docker_manager, "list_containers", return_value=[{"id": container_id}]),
            patch.object(docker_manager.os, "geteuid", return_value=0, create=True),
            patch.object(
                docker_manager,
                "_run_docker",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run_docker,
        ):
            self.assertTrue(docker_manager.control_container(container_id, "restart")[0])
            self.assertEqual(run_docker.call_args.args, ("restart", container_id))
            self.assertFalse(docker_manager.control_container(container_id + ";id", "restart")[0])
            self.assertFalse(docker_manager.control_container(container_id, "rm")[0])

        with (
            patch.object(docker_manager.shutil, "which", return_value="docker"),
            patch.object(
                docker_manager,
                "_run_docker",
                return_value=subprocess.CompletedProcess([], 0, listing, ""),
            ),
        ):
            self.assertEqual(docker_manager.list_containers()[0]["id"], container_id)

    def test_docker_service_control_uses_fixed_service_and_allowed_actions(self):
        with (
            patch.object(docker_manager.os, "geteuid", return_value=0, create=True),
            patch.object(docker_manager.shutil, "which", return_value="/usr/bin/systemctl"),
            patch.object(
                docker_manager.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run_systemctl,
        ):
            self.assertTrue(docker_manager.control_docker_service("start")[0])
            self.assertEqual(run_systemctl.call_args.args[0], ["/usr/bin/systemctl", "start", "docker"])
            self.assertFalse(docker_manager.control_docker_service("enable")[0])

    def test_docker_install_validation_only_accepts_repository_versions(self):
        with (
            patch.object(
                stack_installer,
                "get_docker_options",
                return_value={"supported": True, "versions": ["24.0.7-0ubuntu2"], "error": ""},
            ),
            patch.object(stack_installer, "get_stack_options") as get_stack_options,
        ):
            self.assertIsNone(
                stack_installer.validate_install_request("docker", "24.0.7-0ubuntu2")
            )
            self.assertIn(
                "available Docker",
                stack_installer.validate_install_request("docker", "arbitrary"),
            )
        get_stack_options.assert_not_called()

    def test_docker_options_use_the_os_package_repository(self):
        with (
            patch.object(stack_installer, "_supported_os_id", return_value="ubuntu"),
            patch.object(
                stack_installer,
                "_package_versions",
                return_value=["29.1.3-0ubuntu3"],
            ) as package_versions,
        ):
            options = stack_installer.get_docker_options()
        self.assertEqual(options["versions"], ["29.1.3-0ubuntu3"])
        package_versions.assert_called_once_with("docker.io")

    def test_docker_installer_is_an_allowlisted_component(self):
        script = Path(stack_installer.SCRIPT_PATH).read_text(encoding="utf-8")
        self.assertIn('docker)\n', script)
        self.assertIn('systemctl enable --now docker', script)
        self.assertIn('"docker.io=${version}"', script)

    def test_app_catalog_reports_installed_versions_and_supported_packages(self):
        installed = (
            "apache2\tinstall ok installed\t2.4.58\n"
            "php8.2-fpm:amd64\tinstall ok installed\t8.2.10\n"
            "docker.io\tinstall ok installed\t24.0.7\n"
            "nginx\tinstall ok installed\t1.24\n"
        )
        options = {
            "apache": ["2.4.58"],
            "mysql": ["8.0.36"],
            "php": ["8.2", "8.3"],
            "phpmyadmin": ["5.2.1"],
            "nginx": ["1.26"],
            "redis": ["7.0"],
            "nodejs": ["20.0"],
            "git": ["2.40"],
            "composer": ["2.6"],
            "certbot": ["2.6"],
            "memcached": ["1.6"],
            "htop": ["3.2"],
            "fail2ban": ["1.0"],
            "unzip": ["6.0"],
            "postgresql": ["16+"],
            "sqlite": ["3.45"],
            "supervisor": ["4.2"],
            "rabbitmq": ["3.12"],
            "varnish": ["7.4"],
            "haproxy": ["2.8"],
            "java": ["17"],
            "go": ["2:1.22"],
            "ruby": ["3.1"],
            "rsync": ["3.2"],
            "curl": ["8.5"],
            "wget": ["1.21"],
            "nano": ["7.2"],
            "vim": ["9.0"],
            "clamav": ["1.0"],
        }
        docker_options = {"supported": True, "versions": ["24.0.7"]}
        with (
            patch.object(stack_installer.shutil, "which", return_value="/usr/bin/dpkg-query"),
            patch.object(stack_installer, "_version_is_newer", side_effect=lambda candidate, installed: candidate != installed),
            patch.object(
                stack_installer.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, installed, ""),
            ),
        ):
            apps = stack_installer.get_app_catalog(options, docker_options)

        by_component = {app["component"]: app for app in apps}
        self.assertEqual(len(apps), 30)
        self.assertTrue(by_component["apache"]["installed"])
        self.assertEqual(by_component["apache"]["installed_version"], "2.4.58")
        self.assertFalse(by_component["apache"]["update_available"])
        self.assertTrue(by_component["nginx"]["update_available"])
        self.assertEqual(by_component["nginx"]["update_version"], "1.26")
        self.assertFalse(by_component["mysql"]["installed"])
        self.assertTrue(by_component["php"]["installed"])
        self.assertEqual(by_component["php"]["installed_version"], "8.2")
        self.assertTrue(by_component["docker"]["installed"])
        self.assertEqual(by_component["phpmyadmin"]["category"], "Tools")
        for component in stack_installer.ADDITIONAL_PACKAGE_COMPONENTS:
            self.assertTrue(by_component[component]["available"])
            self.assertTrue(by_component[component]["config_url"])
        for app in apps:
            with self.subTest(component=app["component"]):
                self.assertTrue(app["official_url"].startswith("https://"))

    def test_app_catalog_does_not_offer_docker_on_unsupported_system(self):
        with (
            patch.object(stack_installer.shutil, "which", return_value=None),
        ):
            apps = stack_installer.get_app_catalog(
                {"apache": [], "mysql": [], "php": [], "phpmyadmin": []},
                {"supported": False, "versions": []},
            )
        docker = next(app for app in apps if app["component"] == "docker")
        self.assertFalse(docker["available"])
        self.assertEqual(docker["versions"], [])

    def test_additional_package_installs_are_allowlisted_and_repository_validated(self):
        with patch.object(
            stack_installer,
            "get_stack_options",
            return_value={
                "supported": True,
                "redis": ["5:6.0.16-1ubuntu1.2"],
            },
        ):
            self.assertIsNone(
                stack_installer.validate_install_request("redis", "5:6.0.16-1ubuntu1.2")
            )
            self.assertIsNotNone(
                stack_installer.validate_install_request("redis", "arbitrary")
            )
            self.assertIsNotNone(
                stack_installer.validate_install_request("systemd", "1")
            )

        script = Path(stack_installer.SCRIPT_PATH).read_text(encoding="utf-8")
        for component in stack_installer.ADDITIONAL_PACKAGE_COMPONENTS:
            with self.subTest(component=component):
                self.assertIn(component, stack_installer.PACKAGE_COMPONENTS)
                self.assertIn(component, script)


class LogAndCsrfTests(unittest.TestCase):
    def test_logs_are_limited_to_allowlisted_units_and_bounded_lines(self):
        with (
            patch.object(
                server_logs,
                "available_log_services",
                return_value=[{"unit": "cron.service"}],
            ),
            patch.object(server_logs.shutil, "which", return_value="journalctl"),
            patch.object(
                server_logs.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "sample", ""),
            ) as journal,
        ):
            self.assertEqual(server_logs.read_service_log("cron.service"), "sample")
            args = journal.call_args.args[0]
            self.assertIn("200", args)
            with self.assertRaises(ValueError):
                server_logs.read_service_log("../../etc/passwd")

    def test_csrf_rejects_missing_or_wrong_tokens(self):
        request = SimpleNamespace(session={})
        token = web_security.get_csrf_token(request)
        web_security.require_valid_csrf(request, token)
        with self.assertRaises(Exception) as context:
            web_security.require_valid_csrf(request, "wrong")
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
