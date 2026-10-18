import json
import os
from pathlib import Path
import tempfile
import unittest

from mcpulse.config import initialize, key, load, private_write
from mcpulse.hooks import install, uninstall


class ConfigHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = initialize(self.root / "config")
        self.settings = self.root / "claude" / "settings.json"

    def test_setup_is_idempotent_and_private(self):
        self.assertEqual(initialize(self.root / "config"), self.config)
        self.assertEqual(len(key(self.config)), 64)
        if os.name != "nt":
            self.assertEqual((self.root / "config").stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / "config" / "config.json").stat().st_mode & 0o777, 0o600)

    def test_bad_config_rejected(self):
        for field, value in (("port", 0), ("port", True), ("permission_timeout", -1),
                             ("presence", "invalid"), ("secret", "x" * 64), ("telegram_user_id", -1)):
            private_write(self.root / "config" / "config.json", json.dumps({**self.config, field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                load(self.root / "config")

    def test_install_idempotent_and_uninstall_byte_exact(self):
        original = '{ "custom": true, "hooks": {"PermissionRequest": [{"hooks": [{"type":"command", "command":"echo existing"}]}]} }\n'
        private_write(self.settings, original)
        install(self.settings, self.config)
        first = self.settings.read_bytes()
        install(self.settings, self.config)
        self.assertEqual(self.settings.read_bytes(), first)
        current = json.loads(first)
        self.assertEqual(len(current["hooks"]["PermissionRequest"]), 2)
        self.assertEqual(current["hooks"]["PermissionRequest"][1]["hooks"][0]["timeout"], 14460)
        uninstall(self.settings)
        self.assertEqual(self.settings.read_text(), original)

    def test_uninstall_preserves_later_user_edits(self):
        private_write(self.settings, '{"model":"original"}')
        install(self.settings, self.config)
        current = json.loads(self.settings.read_text())
        current["model"] = "changed"
        current["hooks"]["PermissionRequest"].append({"hooks": [{"type": "command", "command": "mine"}]})
        private_write(self.settings, json.dumps(current))
        uninstall(self.settings)
        current = json.loads(self.settings.read_text())
        self.assertEqual(current["model"], "changed")
        self.assertEqual(current["hooks"]["PermissionRequest"], [{"hooks": [{"type": "command", "command": "mine"}]}])

    def test_new_settings_removed_on_uninstall(self):
        install(self.settings, self.config)
        uninstall(self.settings)
        self.assertFalse(self.settings.exists())

    def test_key_rotation_replaces_only_owned_hooks(self):
        install(self.settings, self.config)
        updated = {**self.config, "port": 8818, "secret": "ab" * 32}
        install(self.settings, updated)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(len(settings["hooks"]["PermissionRequest"]), 1)
        hook = settings["hooks"]["PermissionRequest"][0]["hooks"][0]
        self.assertEqual(hook["url"], "http://127.0.0.1:8818/hook/PermissionRequest")
        self.assertEqual(hook["headers"]["x-mcpulse-key"], key(updated))

    def test_invalid_settings_untouched(self):
        private_write(self.settings, "not json")
        with self.assertRaises(ValueError):
            install(self.settings, self.config)
        self.assertEqual(self.settings.read_text(), "not json")
