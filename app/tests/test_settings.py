"""Settings (META_API_KEY via local page) + example caption tests.

Patches webserver.DOTENV to a tmp file: the real app/.env is never touched.
"""
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import unittest

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR / "web"))
import server as webserver  # noqa: E402


class SettingsKeyFileTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="settings-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.tmp = Path(self.tmpdir) / ".env"
        self._old_dotenv = webserver.DOTENV
        webserver.DOTENV = self.tmp
        self._old_env = os.environ.pop("META_API_KEY", None)

    def tearDown(self):
        webserver.DOTENV = self._old_dotenv
        if self._old_env is not None:
            os.environ["META_API_KEY"] = self._old_env
        elif "META_API_KEY" in os.environ:
            del os.environ["META_API_KEY"]

    def test_save_masked_and_0600(self):
        webserver.write_dotenv_key("sk-test-fake-key-1234")
        mode = stat.S_IMODE(os.stat(self.tmp).st_mode)
        self.assertEqual(oct(mode), "0o600", "app/.env must be 0600")
        self.assertEqual(webserver.masked_last4(), "****1234")

    def test_save_rejects_empty(self):
        with self.assertRaises(ValueError):
            webserver.write_dotenv_key("   ")
        self.assertFalse(self.tmp.exists())

    def test_effective_immediate_no_restart(self):
        webserver.write_dotenv_key("sk-fake-immediate-9999")
        self.assertEqual(webserver.effective_meta_key(), "sk-fake-immediate-9999")

    def test_env_var_fallback(self):
        os.environ["META_API_KEY"] = "sk-env-fake-0007"
        self.assertEqual(webserver.masked_last4(), "****0007")

    def test_clear_removes_key(self):
        webserver.write_dotenv_key("sk-fake-clear-5555")
        self.assertEqual(webserver.masked_last4(), "****5555")
        self.assertTrue(webserver.clear_dotenv_key())
        self.assertEqual(webserver.masked_last4(), "")
        self.assertEqual(webserver.effective_meta_key(), "")

    def test_clear_preserves_other_lines(self):
        self.tmp.write_text("OTHER=1\nMETA_API_KEY=sk-fake-x\n# c\n")
        webserver.clear_dotenv_key()
        body = self.tmp.read_text()
        self.assertNotIn("META_API_KEY", body)
        self.assertIn("OTHER=1", body)

    def test_mask_never_shows_full_key(self):
        webserver.write_dotenv_key("sk-test-fake-key-1234")
        self.assertNotIn("sk-test-fake-key-1234", webserver.masked_last4())


class SettingsGuardTests(unittest.TestCase):
    def test_post_loopback_origin_ok(self):
        self.assertTrue(webserver.settings_request_allowed({
            "Host": "127.0.0.1:18766", "Origin": "http://127.0.0.1:18766"}))

    def test_post_external_host_denied(self):
        self.assertFalse(webserver.settings_request_allowed({
            "Host": "evil.example", "Origin": "http://127.0.0.1:18766"}))

    def test_post_cross_origin_denied(self):
        self.assertFalse(webserver.settings_request_allowed({
            "Host": "127.0.0.1:18766", "Origin": "http://evil.example"}))

    def test_post_no_origin_denied(self):
        self.assertFalse(webserver.settings_request_allowed({"Host": "127.0.0.1:18766"}))


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="limits-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self._old_budget = webserver.BUDGET_FILE
        self._old_dotenv = webserver.DOTENV
        webserver.BUDGET_FILE = Path(self.tmpdir) / "budget.json"
        webserver.DOTENV = Path(self.tmpdir) / ".env"
        self._old_env = {k: os.environ.pop(k, None) for k in
                         ("WEB_MAX_CALLS_PER_DAY", "WEB_MAX_USD_PER_DAY", "META_API_KEY")}

    def tearDown(self):
        webserver.BUDGET_FILE = self._old_budget
        webserver.DOTENV = self._old_dotenv
        for k, v in self._old_env.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]

    def test_defaults(self):
        self.assertEqual(webserver.get_limits(),
                         {"max_calls": 20, "max_usd": 1.00, "model": "muse-spark-1.2"})

    def test_env_override(self):
        os.environ["WEB_MAX_CALLS_PER_DAY"] = "3"
        os.environ["WEB_MAX_USD_PER_DAY"] = "0.10"
        lim = webserver.get_limits()
        self.assertEqual((lim["max_calls"], lim["max_usd"]), (3, 0.10))
        self.assertEqual(lim["model"], "muse-spark-1.2")

    def test_dotenv_override(self):
        webserver.DOTENV.write_text("WEB_MAX_CALLS_PER_DAY=5\n")
        self.assertEqual(webserver.get_limits()["max_calls"], 5)

    def test_bad_values_ignored(self):
        os.environ["WEB_MAX_CALLS_PER_DAY"] = "x"
        os.environ["WEB_MAX_USD_PER_DAY"] = "-1"
        lim = webserver.get_limits()
        self.assertEqual((lim["max_calls"], lim["max_usd"]), (20, 1.00))

    def test_budget_missing_zeros(self):
        b = webserver.load_budget()
        self.assertEqual((b["paid_calls"], b["spend_usd"]), (0, 0.0))
        self.assertIn("date", b)

    def test_budget_stale_resets(self):
        webserver.BUDGET_FILE.write_text('{"date": "2000-01-01", "paid_calls": 9, "spend_usd": 9.9}')
        b = webserver.load_budget()
        self.assertEqual((b["paid_calls"], b["spend_usd"]), (0, 0.0))

    def test_budget_legacy_keeps_counts(self):
        webserver.BUDGET_FILE.write_text('{"paid_calls": 1, "spend_usd": 0.011}')
        b = webserver.load_budget()
        self.assertEqual((b["paid_calls"], b["spend_usd"]), (1, 0.011))

    def test_save_budget_stamps_date(self):
        webserver.save_budget({"paid_calls": 0, "spend_usd": 0.0})
        b = webserver.load_budget()
        self.assertIn("date", b)


class SettingsTemplateTests(unittest.TestCase):
    def test_csrf_field_present(self):
        self.assertIn('name="csrf"', webserver.HOME_TMPL)

    def test_no_test_connection_paid_probe(self):
        # No paid call may be used to test the key; template must not promise one.
        self.assertNotIn("Test connection", webserver.HOME_TMPL)

    def test_dialog_key_only_no_budget_controls(self):
        self.assertIn('id="dlgKeyForm"', webserver.HOME_TMPL)
        self.assertIn('id="dlgClearForm"', webserver.HOME_TMPL)
        self.assertNotIn("/settings/limits", webserver.HOME_TMPL)
        self.assertNotIn('name="max_calls"', webserver.HOME_TMPL)
        self.assertNotIn('name="max_usd"', webserver.HOME_TMPL)
        self.assertNotIn('name="model"', webserver.HOME_TMPL)

    def test_no_standalone_settings_page(self):
        self.assertFalse(hasattr(webserver, "SETTINGS_TMPL"))
        self.assertFalse(hasattr(webserver.Handler, "page_settings"))


if __name__ == "__main__":
    unittest.main()
