import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProvisioningSafetyTests(unittest.TestCase):
    def test_recording_storage_is_never_removed(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("rm -rf /var/bigbluebutton", script)
        self.assertIn("recordings under /var/bigbluebutton were preserved", script)

    def test_recovery_has_resume_repair_backup_and_final_retry(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        for requirement in ("bbb_core_healthy", "greenlight_healthy", "repair_packages", "backup_before_cleanup", "run_upstream_installer 3"):
            self.assertIn(requirement, script)

    def test_server_managed_launcher_has_heartbeat(self):
        launcher = (ROOT / "provision" / "launch.sh").read_text(encoding="utf-8")
        self.assertIn("systemd-run", launcher)
        self.assertIn("HEARTBEAT", launcher)

    def test_nginx_variables_survive_template_rendering(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        template = (ROOT / "provision" / "telegram-gateway.nginx.in").read_text(encoding="utf-8")
        self.assertIn("envsubst '${BRIDGE_SHARED_SECRET}'", script)
        self.assertIn("$http_x_bcp_gateway_secret", template)

    def test_greenlight_database_is_repaired_before_admin_creation(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("bundle exec rails db:prepare", script)
        self.assertLess(script.index("greenlight_database_ready || prepare_greenlight_database"), script.index("create_or_promote_greenlight_admin\n"))

    def test_failed_services_emit_diagnostics(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        self.assertIn('journalctl --no-pager -u "$service_name" -n 160', script)

    def test_sfu_scheduler_repair_is_guarded_by_journal_signature(self):
        control = (ROOT / "provision" / "bcpctl").read_text(encoding="utf-8")
        self.assertIn("214/SETSCHEDULER", control)
        self.assertIn("CPUSchedulingPolicy=other", control)
        self.assertIn("systemctl is-active --quiet bbb-webrtc-sfu.service", control)

    def test_worker_is_disabled_until_telegram_migration(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl disable --now bcp-worker", script)

    def test_provision_log_secrets_are_redacted(self):
        script = (ROOT / "provision" / "install.sh").read_text(encoding="utf-8")
        self.assertIn('text.replace(secret, "[REDACTED]")', script)
        self.assertIn('admin:create[$GREENLIGHT_ADMIN_NAME,$GREENLIGHT_ADMIN_EMAIL,$GREENLIGHT_ADMIN_PASSWORD]" >/dev/null 2>&1', script)


if __name__ == "__main__":
    unittest.main()
