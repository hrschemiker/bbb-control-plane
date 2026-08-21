#!/usr/bin/env python3
"""Local GUI controller for deterministic node provisioning."""
from __future__ import annotations

import ipaddress
import os
import queue
import re
import secrets
import shlex
import socket
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import paramiko

ROOT = Path(__file__).resolve().parent
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")


class ControllerError(RuntimeError):
    pass


def validate(host: str, hostname: str, email: str, key_path: str, password: str) -> None:
    ipaddress.ip_address(host)
    if not HOST_RE.match(hostname):
        raise ControllerError("Invalid fully qualified hostname")
    if "@" not in email or email.startswith("@"):
        raise ControllerError("Invalid certificate email")
    if not password and not Path(key_path).is_file():
        raise ControllerError("Provide an SSH private key or the temporary server password")


def clean_value(name: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise ControllerError(f"Invalid or missing {name}")
    return value


class SSH:
    def __init__(self, host: str, port: int, user: str, key_path: str, password: str):
        known = Path.home() / ".ssh" / "known_hosts"
        known.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            if known.exists():
                self.client.load_host_keys(str(known))
            try:
                self.client.connect(
                    host,
                    port=port,
                    username=user,
                    key_filename=key_path or None,
                    password=password or None,
                    timeout=30,
                    banner_timeout=120,
                    auth_timeout=60,
                    look_for_keys=False,
                    allow_agent=False,
                )
                break
            except Exception as exc:
                last_error = exc
                self.client.close()
                if attempt < 3:
                    time.sleep(5)
        else:
            raise ControllerError(f"SSH did not become ready after 3 attempts: {last_error}")
        self.client.save_host_keys(str(known))

    def run(self, command: str, timeout: int = 1800) -> tuple[int, str]:
        _stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        output = stdout.read().decode(errors="replace") + stderr.read().decode(errors="replace")
        return stdout.channel.recv_exit_status(), output

    def put_tree(self, local: Path, remote: str) -> None:
        sftp = self.client.open_sftp()
        self.run(f"sudo install -d -m 0755 {shlex.quote(remote)}")
        for path in local.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = path.relative_to(local).as_posix()
            target = f"{remote}/{rel}"
            parent = target.rsplit("/", 1)[0]
            self.run(f"sudo install -d -m 0755 {shlex.quote(parent)}")
            temp = f"/tmp/bcp-{os.getpid()}-{path.name}"
            sftp.put(str(path), temp)
            self.run(f"sudo install -m 0644 {shlex.quote(temp)} {shlex.quote(target)} && rm -f {shlex.quote(temp)}")
        sftp.close()

    def close(self) -> None:
        self.client.close()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Media Node Controller")
        self.geometry("940x780")
        self.events: queue.Queue[str] = queue.Queue()
        self.values = {k: tk.StringVar() for k in (
            "host", "port", "user", "key", "password", "hostname", "email",
            "wordpress", "bot_token", "api_id", "api_hash", "archive_chat_id",
        )}
        self.values["port"].set("22")
        self.values["user"].set("root")
        self._build()
        self.after(100, self._drain)

    def _build(self):
        box = ttk.Frame(self, padding=18)
        box.pack(fill="both", expand=True)
        fields = [
            ("Server IPv4", "host"), ("SSH port", "port"), ("SSH user", "user"),
            ("SSH private key, optional", "key"), ("Temporary SSH password, optional", "password"),
            ("Media hostname", "hostname"), ("Certificate email", "email"),
            ("WordPress URL", "wordpress"), ("Telegram bot token", "bot_token"),
            ("Telegram API ID", "api_id"), ("Telegram API hash", "api_hash"),
            ("Telegram archive channel ID", "archive_chat_id"),
        ]
        for row, (label, key) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=5)
            hidden = key in {"password", "bot_token", "api_hash"}
            ttk.Entry(box, textvariable=self.values[key], width=72, show="*" if hidden else "").grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(box, text="Select SSH key", command=lambda: self.values["key"].set(filedialog.askopenfilename())).grid(row=3, column=2)
        actions = ttk.Frame(box)
        actions.grid(row=len(fields), column=0, columnspan=3, sticky="w", pady=14)
        for text, action in (("Preflight", "preflight"), ("Provision", "provision"), ("Health", "health"), ("Repair", "repair")):
            ttk.Button(actions, text=text, command=lambda a=action: self._start(a)).pack(side="left", padx=5)
        self.log = tk.Text(box, height=24, wrap="word", state="disabled")
        self.log.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="nsew")
        box.columnconfigure(1, weight=1)
        box.rowconfigure(len(fields) + 1, weight=1)

    def _environment(self, values: dict[str, str]) -> tuple[str, Path]:
        wordpress = clean_value("WordPress URL", values["wordpress"]).rstrip("/")
        if not wordpress.startswith(("https://", "http://")):
            raise ControllerError("WordPress URL must begin with https://")
        api_id = clean_value("Telegram API ID", values["api_id"])
        if not api_id.isdigit():
            raise ControllerError("Telegram API ID must contain digits only")
        channel_id = clean_value("Telegram archive channel ID", values["archive_chat_id"])
        if not channel_id.lstrip("-").isdigit():
            raise ControllerError("Telegram archive channel ID must be numeric")
        secret = secrets.token_hex(32)
        lines = {
            "BBB_HOSTNAME": values["hostname"], "LETSENCRYPT_EMAIL": values["email"],
            "WORDPRESS_URL": wordpress, "BRIDGE_SHARED_SECRET": secret,
            "TELEGRAM_BOT_TOKEN": clean_value("Telegram bot token", values["bot_token"]),
            "TELEGRAM_API_ID": api_id,
            "TELEGRAM_API_HASH": clean_value("Telegram API hash", values["api_hash"]),
            "TELEGRAM_ARCHIVE_CHAT_ID": channel_id, "RAW_RETENTION_DAYS": "7",
            "PRESENTATION_RETENTION_DAYS": "30", "LOCAL_VIDEO_RETENTION_DAYS": "3",
            "MIN_FREE_GB": "30", "MAX_UPLOAD_MIB": "1900", "VIDEO_CRF": "23",
            "VIDEO_HEIGHT": "720",
        }
        payload = "\n".join(f"{key}={clean_value(key, value)}" for key, value in lines.items()) + "\n"
        private_dir = Path.home() / ".bbb-control-plane"
        private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_file = private_dir / f"{values['hostname']}.env"
        private_file.write_text(payload, encoding="utf-8")
        private_file.chmod(0o600)
        return payload, private_file

    def _start(self, action: str):
        threading.Thread(target=self._execute, args=(action,), daemon=True).start()

    def _execute(self, action: str):
        v = {k: x.get().strip() for k, x in self.values.items()}
        try:
            validate(v["host"], v["hostname"], v["email"], v["key"], v["password"])
            expected = socket.gethostbyname(v["hostname"])
            if expected != v["host"]:
                raise ControllerError(f"DNS resolves to {expected}, expected {v['host']}")
            ssh = SSH(v["host"], int(v["port"]), v["user"], v["key"], v["password"])
            try:
                if action == "preflight":
                    cmd = "bash -s -- --preflight"
                    script = (ROOT / "provision" / "install.sh").read_text()
                    _stdin, stdout, stderr = ssh.client.exec_command(cmd)
                    _stdin.write(script); _stdin.channel.shutdown_write()
                    out = stdout.read().decode() + stderr.read().decode()
                    code = stdout.channel.recv_exit_status()
                elif action == "provision":
                    payload, private_file = self._environment(v)
                    ssh.put_tree(ROOT, "/opt/bbb-control-plane/source")
                    sftp = ssh.client.open_sftp()
                    with sftp.file("/tmp/bcp.env", "w") as remote_env:
                        remote_env.write(payload)
                    sftp.close()
                    code, out = ssh.run("sudo install -m 0600 /tmp/bcp.env /etc/bbb-control-plane.env && rm -f /tmp/bcp.env && sudo bash /opt/bbb-control-plane/source/provision/install.sh")
                    self.events.put(f"Private recovery settings saved at {private_file}\n")
                else:
                    code, out = ssh.run(f"sudo /usr/local/sbin/bcpctl {shlex.quote(action)}")
                self.events.put(out)
                if code:
                    raise ControllerError(f"Operation exited with status {code}")
                self.events.put("Operation completed successfully.\n")
            finally:
                ssh.close()
        except Exception as exc:
            self.events.put(f"ERROR: {exc}\n")

    def _drain(self):
        while not self.events.empty():
            msg = self.events.get_nowait()
            self.log.configure(state="normal"); self.log.insert("end", msg); self.log.see("end"); self.log.configure(state="disabled")
        self.after(100, self._drain)


if __name__ == "__main__":
    App().mainloop()
