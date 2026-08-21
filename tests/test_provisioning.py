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


if __name__ == "__main__":
    unittest.main()
