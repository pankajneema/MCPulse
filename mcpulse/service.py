"""User-scoped service definitions. Never install a system/root service."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import xml.etree.ElementTree as ET

from .config import private_write

LABEL = "dev.mcpulse.daemon"


def command():
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "run_mcpulse.py"), "start"]


def definition(platform, directory, user_home=None):
    user_home = user_home or Path.home()
    args = command()
    if platform == "darwin":
        path = user_home / "Library/LaunchAgents" / (LABEL + ".plist")
        body = plistlib.dumps({"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
                              "KeepAlive": {"SuccessfulExit": False}, "EnvironmentVariables": {"MCPULSE_HOME": str(directory)},
                              "StandardOutPath": str(directory / "service.log"), "StandardErrorPath": str(directory / "service-error.log")}).decode()
        return path, body
    if platform.startswith("linux"):
        def quote(value):
            if "\n" in value or "\r" in value:
                raise ValueError("Service paths cannot contain newlines")
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
        path = user_home / ".config/systemd/user/mcpulse.service"
        body = ("[Unit]\nDescription=MCPulse user daemon\nAfter=network-online.target\n\n[Service]\nType=simple\n"
                + "ExecStart=" + " ".join(map(quote, args)) + "\nEnvironment=" + quote("MCPULSE_HOME=" + str(directory))
                + "\nRestart=on-failure\nRestartSec=3\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
        return path, body
    if platform == "win32":
        path = directory / "task.xml"
        ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ET.register_namespace("", ns)
        task = ET.Element("Task", {"version": "1.2", "xmlns": ns})
        triggers = ET.SubElement(task, "Triggers")
        ET.SubElement(ET.SubElement(triggers, "LogonTrigger"), "Enabled").text = "true"
        principals = ET.SubElement(task, "Principals")
        principal = ET.SubElement(principals, "Principal", {"id": "Author"})
        ET.SubElement(principal, "LogonType").text = "InteractiveToken"
        ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
        settings = ET.SubElement(task, "Settings")
        ET.SubElement(settings, "ExecutionTimeLimit").text = "PT0S"
        actions = ET.SubElement(task, "Actions", {"Context": "Author"})
        execute = ET.SubElement(actions, "Exec")
        ET.SubElement(execute, "Command").text = args[0]
        ET.SubElement(execute, "Arguments").text = subprocess.list2cmdline([args[1], "--home", str(directory), "start"])
        return path, ET.tostring(task, encoding="unicode")
    raise ValueError("Unsupported service platform")


def manage(action, directory):
    path, body = definition(sys.platform, directory)
    if action == "show":
        return {"path": str(path), "definition": body}
    if action == "install":
        private_write(path, body)
    if sys.platform == "darwin":
        domain = "gui/" + str(os.getuid())
        if action == "install":
            subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
            cmd = ["launchctl", "bootstrap", domain, str(path)]
        else:
            cmd = ["launchctl", "bootout", domain + "/" + LABEL]
    elif sys.platform.startswith("linux"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        cmd = ["systemctl", "--user", "enable" if action == "install" else "disable", "--now", "mcpulse.service"]
    else:
        cmd = ["schtasks", "/Create", "/TN", "MCPulse", "/XML", str(path), "/F"] if action == "install" else ["schtasks", "/Delete", "/TN", "MCPulse", "/F"]
    subprocess.run(cmd, check=True, capture_output=True)
    if action == "uninstall":
        path.unlink(missing_ok=True)
    return {"service": action, "path": str(path)}
