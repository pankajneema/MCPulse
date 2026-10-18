"""Opt-in desktop alerts through the operating system's notification tools."""
import html
import os
from pathlib import PurePosixPath
import shutil
import subprocess
import sys

from .channels import Delivery
from .core import preview

MAC_SCRIPT = '''on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run'''

WINDOWS_SCRIPT = '''$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notice = New-Object System.Windows.Forms.NotifyIcon
try {
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Visible = $true
    $notice.ShowBalloonTip(5000, $env:MCPULSE_NOTICE_TITLE, $env:MCPULSE_NOTICE_BODY, [System.Windows.Forms.ToolTipIcon]::Info)
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.ElapsedMilliseconds -lt 5000) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
} finally {
    $notice.Dispose()
}'''


def notify(title, body):
    """Submit an alert; success does not prove the OS displayed it."""
    env = dict(os.environ)
    if sys.platform == 'darwin':
        command = ['/usr/bin/osascript', '-e', MAC_SCRIPT, title, body]
    elif sys.platform == 'win32':
        executable = shutil.which('powershell.exe')
        if not executable:
            raise RuntimeError('Desktop notifications require Windows PowerShell')
        # Payload stays in environment data, never in executable script text.
        env.update(MCPULSE_NOTICE_TITLE=title, MCPULSE_NOTICE_BODY=body)
        command = [executable, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', WINDOWS_SCRIPT]
    elif sys.platform.startswith('linux'):
        executable = shutil.which('notify-send')
        if not executable or not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
            raise RuntimeError('Desktop notifications require notify-send and a graphical session')
        command = [executable, '--app-name=MCPulse', '--expire-time=5000', '--', title, html.escape(body)]
    else:
        raise RuntimeError('Desktop notifications are unsupported on this platform')
    try:
        subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Desktop notification failed; check the graphical session and notification settings') from None


class Desktop(Delivery):
    def __init__(self, broker):
        super().__init__(broker, {}, 'desktop')

    def send(self, card):
        title = 'MCPulse · ' + {'permission':'Approval needed', 'question':'Your answer is needed',
                               'finished':'Agent finished', 'notification':'Session update'}.get(card['kind'], 'Request waiting')
        project = PurePosixPath(card['project'].replace('\\', '/')).name or 'Project'
        # Notifications may appear on a lock screen: omit commands, questions and replies.
        body = ('Agent: ' + preview(card.get('agent', 'Unknown agent'), 60)
                + '\nProject: ' + preview(project, 60)
                + '\nSession: ' + preview(card['session'], 60)
                + '\nRequest: ' + card['id']
                + '\nOpen MCPulse dashboard to review.')
        notify(title, body)
