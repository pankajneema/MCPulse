import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mcpulse.channels import configured_adapters
from mcpulse.cli import main
from mcpulse.config import initialize, load
from mcpulse.core import Broker
from mcpulse.desktop import Desktop, MAC_SCRIPT, WINDOWS_SCRIPT, notify
from tests.test_core import payload


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(':memory:', 'away', config={'context':False})
        self.addCleanup(self.broker.close)

    def test_setting_is_opt_in_boolean_and_listed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = initialize(root)
            self.assertIs(config['desktop_notifications'], False)
            self.assertEqual(configured_adapters(self.broker, config, root), [])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--home', str(root), 'config', 'set', 'desktop_notifications', 'true']), 0)
            config = load(root)
            self.assertIs(config['desktop_notifications'], True)
            self.assertIsInstance(configured_adapters(self.broker, config, root)[0], Desktop)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--home', str(root), 'channel', 'list']), 0)
            self.assertIs(json.loads(output.getvalue())['desktop'], True)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--home', str(root), 'config', 'set', 'desktop_notifications', 'yes']), 1)
            self.assertIs(load(root)['desktop_notifications'], True)

    def test_native_commands_keep_untrusted_text_out_of_scripts(self):
        title = 'MCPulse · Your answer is needed'
        body = 'Agent: "hello"; $(bad) `bad`\n<markup> & Unicode café'
        for platform in ('darwin', 'win32', 'linux'):
            with self.subTest(platform=platform), patch('mcpulse.desktop.sys.platform', platform), patch('mcpulse.desktop.shutil.which', return_value='/native/tool'), patch.dict(os.environ, {'DISPLAY':':0'}), patch('mcpulse.desktop.subprocess.run') as run:
                notify(title, body)
                command = run.call_args.args[0]
                kwargs = run.call_args.kwargs
                self.assertNotIn('shell', kwargs)
                self.assertTrue(kwargs['check'])
                self.assertEqual(kwargs['timeout'], 15)
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                if platform == 'darwin':
                    self.assertEqual(command, ['/usr/bin/osascript', '-e', MAC_SCRIPT, title, body])
                    self.assertNotIn(body, MAC_SCRIPT)
                elif platform == 'win32':
                    self.assertEqual(command[-1], WINDOWS_SCRIPT)
                    self.assertNotIn(body, WINDOWS_SCRIPT)
                    self.assertEqual(kwargs['env']['MCPULSE_NOTICE_BODY'], body)
                else:
                    self.assertEqual(command[-3], '--')
                    self.assertIn('&lt;markup&gt; &amp;', command[-1])

    def test_missing_desktop_or_tool_does_not_spawn(self):
        for platform, executable, graphical in (('linux', None, ':0'), ('linux', '/notify-send', ''), ('win32', None, ''), ('unsupported', None, '')):
            with self.subTest(platform=platform, executable=executable, graphical=graphical), patch('mcpulse.desktop.sys.platform', platform), patch('mcpulse.desktop.shutil.which', return_value=executable), patch.dict(os.environ, {'DISPLAY':graphical, 'WAYLAND_DISPLAY':''}, clear=True), patch('mcpulse.desktop.subprocess.run') as run:
                with self.assertRaises(RuntimeError):
                    notify('Title', 'Body')
                run.assert_not_called()

    def test_native_failure_is_generic_and_timeout_is_bounded(self):
        for failure in (FileNotFoundError('private path'), subprocess.CalledProcessError(1, ['private metadata']), subprocess.TimeoutExpired(['private metadata'], 15)):
            with self.subTest(failure=type(failure).__name__), patch('mcpulse.desktop.sys.platform', 'darwin'), patch('mcpulse.desktop.subprocess.run', side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, '^Desktop notification failed;') as caught:
                    notify('Title', 'Body')
                self.assertNotIn('private', str(caught.exception))

    def test_alert_is_metadata_only_deduplicated_and_never_answers(self):
        request = self.broker.open({**payload('echo password=hidden'), 'agent':'opencode'})
        desktop = Desktop(self.broker)
        with patch('mcpulse.desktop.notify') as send:
            desktop.sync_cards()
            desktop.sync_cards()
            # Stored reference survives an adapter restart.
            Desktop(self.broker).sync_cards()
            send.assert_called_once()
            title, body = send.call_args.args
        self.assertIn('Approval needed', title)
        for expected in ('Agent: OpenCode', 'Project: project', 'Session: test-session', request.id):
            self.assertIn(expected, body)
        self.assertNotIn('password', body)
        self.assertNotIn('/work/', body)
        self.assertEqual(request.state, 'OPEN')
        self.assertTrue(self.broker.message_ref('desktop', request.id))

    def test_failed_alert_retries_and_mute_only_suppresses_information(self):
        permission = self.broker.open(payload())
        info = self.broker.open({**payload(), 'hook_event_name':'MCP', 'kind':'notification', 'message':'Private details'})
        self.broker.muted_until = float('inf')
        desktop = Desktop(self.broker)
        with patch('mcpulse.desktop.notify', side_effect=RuntimeError('unavailable')) as send:
            desktop.sync_cards()
            send.assert_called_once()
        self.assertIsNone(self.broker.message_ref('desktop', permission.id))
        with patch('mcpulse.desktop.notify') as send:
            desktop.sync_cards()
            send.assert_called_once()
        self.assertTrue(self.broker.message_ref('desktop', permission.id))
        self.assertIsNone(self.broker.message_ref('desktop', info.id))
        self.assertEqual(permission.state, 'OPEN')
