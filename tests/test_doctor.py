import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcpulse.cli import bridge, doctor, main
from mcpulse.config import initialize, private_write
from mcpulse.hooks import EVENTS, check_installation, install


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'private-config'
        self.settings = self.root / 'project' / '.claude' / 'settings.local.json'
        self.config = initialize(self.home)
        self.bridge = bridge(self.home)

    def check(self, config=None):
        return check_installation(self.settings, config or self.config, self.bridge)

    def test_both_transports_validate_without_exposing_credentials(self):
        for command in (None, self.bridge):
            with self.subTest(command=bool(command)):
                install(self.settings, self.config, bridge=command)
                before = self.settings.read_bytes()
                result = self.check()
                self.assertTrue(result['ok'])
                self.assertNotIn(self.config['secret'], json.dumps(result))
                self.assertEqual(self.settings.read_bytes(), before)

    def test_stale_connection_targets_are_detected(self):
        install(self.settings, self.config)
        changed = {**self.config, 'secret':'ab'*32, 'port':8818}
        self.assertEqual(self.check(changed)['mismatched_events'], list(EVENTS))
        install(self.settings, self.config, bridge=self.bridge)
        result = check_installation(self.settings, self.config, [*self.bridge, '--wrong-home'])
        self.assertEqual(result['mismatched_events'], list(EVENTS))

    def test_total_count_cannot_hide_missing_and_duplicate_events(self):
        install(self.settings, self.config)
        settings = json.loads(self.settings.read_text())
        settings['hooks']['PermissionRequest'] *= 2
        del settings['hooks']['Stop']
        private_write(self.settings, json.dumps(settings))
        result = self.check()
        self.assertFalse(result['ok'])
        self.assertEqual(result['missing_events'], ['Stop'])
        self.assertEqual(result['mismatched_events'], ['PermissionRequest'])

    def test_wrong_matcher_and_async_permission_hook_are_detected(self):
        install(self.settings, self.config)
        settings = json.loads(self.settings.read_text())
        settings['hooks']['PreToolUse'][0]['matcher'] = 'Bash'
        settings['hooks']['PermissionRequest'][0]['hooks'][0]['async'] = True
        private_write(self.settings, json.dumps(settings))
        self.assertEqual(self.check()['mismatched_events'], ['PermissionRequest','PreToolUse'])

    def test_disabled_missing_and_malformed_settings(self):
        self.assertFalse(self.check()['ok'])
        for value in ('not JSON', '[]', '{"disableAllHooks":true}', '{"hooks":{"Stop":[null]}}'):
            with self.subTest(value=value):
                private_write(self.settings, value)
                self.assertFalse(self.check()['ok'])
                self.assertEqual(self.settings.read_text(), value)

    def test_missing_config_produces_actionable_read_only_report(self):
        directory = self.root / 'not-created'
        output = io.StringIO()
        with patch.dict('os.environ', {'MCPULSE_HOME':str(directory)}), contextlib.redirect_stdout(output):
            code = main(['doctor', '--strict'])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result[0]['fix'], 'python -m mcpulse setup')
        self.assertFalse(directory.exists())

    def test_selected_project_and_strict_exit_ignore_optional_sensor(self):
        install(self.settings, self.config, bridge=self.bridge)
        with patch('mcpulse.cli.api', return_value={'presence':'away','channels':{}}), patch('mcpulse.cli.idle_seconds', return_value=None):
            findings = doctor(self.config, self.home, self.settings)
            hook = next(row for row in findings if row['check']=='Claude hooks')
            self.assertTrue(hook['ok'])
            self.assertEqual(hook['settings'],str(self.settings))
            with patch.dict('os.environ', {'MCPULSE_HOME':str(self.home)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['doctor','--strict','--settings',str(self.settings)]), 0)
            with patch('mcpulse.cli.api', side_effect=ConnectionError), patch.dict('os.environ', {'MCPULSE_HOME':str(self.home)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['doctor','--strict','--settings',str(self.settings)]), 1)
                self.assertEqual(main(['doctor','--settings',str(self.settings)]), 0)
