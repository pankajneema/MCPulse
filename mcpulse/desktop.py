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


def get_backend():
    """Read-only capability check; never prompts for OS permissions."""
    if sys.platform == 'darwin':
        name, executable = 'macOS notifications', shutil.which('/usr/bin/osascript')
    elif sys.platform == 'win32':
        name, executable = 'Windows notification icon', shutil.which('powershell.exe')
    elif sys.platform.startswith('linux'):
        name, executable = 'Linux notifications', shutil.which('notify-send')
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise RuntimeError('Desktop notifications require a graphical session (DISPLAY or WAYLAND_DISPLAY)')
    else:
        raise RuntimeError('Desktop notifications are unsupported on this platform')
    if not executable:
        raise RuntimeError({'darwin':'Desktop notifications require osascript',
                            'win32':'Desktop notifications require Windows PowerShell'}.get(sys.platform, 'Desktop notifications require notify-send'))
    return name, executable


def status():
    result = {'platform':sys.platform, 'available':False,
              'note':'Availability does not verify notification permissions or that a popup will be displayed.'}
    try:
        name, executable = get_backend()
        result.update(available=True, backend=name, executable=executable)
    except RuntimeError as error:
        result['detail'] = str(error)
    return result


def notify(title, body):
    """Submit an alert; success does not prove the OS displayed it."""
    _, executable = get_backend()
    env = dict(os.environ)
    if sys.platform == 'darwin':
        command = [executable, '-e', MAC_SCRIPT, title, body]
    elif sys.platform == 'win32':
        # Payload stays in environment data, never in executable script text.
        env.update(MCPULSE_NOTICE_TITLE=title, MCPULSE_NOTICE_BODY=body)
        command = [executable, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', WINDOWS_SCRIPT]
    else:
        command = [executable, '--app-name=MCPulse', '--expire-time=5000', '--', title, html.escape(body)]
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
