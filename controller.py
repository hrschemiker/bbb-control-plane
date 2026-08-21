#!/usr/bin/env python3
"""Local GUI controller for deterministic node provisioning."""
from __future__ import annotations

import ipaddress
import ctypes
import json
import os
import queue
import re
import secrets
import shlex
import socket
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import paramiko

try:
    import keyring
except ImportError:
    keyring = None

ROOT = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".bbb-control-plane"
PROFILE_FILE = STATE_DIR / "profile.json"
KEYRING_SERVICE = "bbb-control-plane"
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

    def run(self, command: str, timeout: int = 1800, emit=None) -> tuple[int, str]:
        _stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if emit is None:
            output = stdout.read().decode(errors="replace") + stderr.read().decode(errors="replace")
            return stdout.channel.recv_exit_status(), output
        channel = stdout.channel
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                emit(channel.recv(4096).decode(errors="replace"))
            if channel.recv_stderr_ready():
                emit(channel.recv_stderr(4096).decode(errors="replace"))
            time.sleep(0.08)
        return channel.recv_exit_status(), ""

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
        self.geometry("1020x820")
        self.minsize(900, 700)
        self._load_font()
        self._configure_style()
        self.events: queue.Queue[object] = queue.Queue()
        self.values = {k: tk.StringVar() for k in (
            "host", "port", "user", "key", "password", "hostname", "email",
            "wordpress", "bot_token", "api_id", "api_hash", "archive_chat_id",
            "admin_name", "admin_email", "admin_password",
        )}
        self.values["port"].set("22")
        self.values["user"].set("root")
        self.values["admin_name"].set("Administrator")
        self.status = tk.StringVar(value="READY")
        self.action_buttons: list[ttk.Button] = []
        self._build()
        self._load_profile()
        self.after(100, self._drain)

    def _load_font(self):
        font_path = ROOT / "assets" / "VT323-Regular.ttf"
        if os.name == "nt" and font_path.is_file():
            try:
                ctypes.windll.gdi32.AddFontResourceExW(str(font_path), 0x10, 0)
            except Exception:
                pass

    def _configure_style(self):
        style = ttk.Style(self)
        self.option_add("*Font", ("VT323", 15))
        for name in ("TLabel", "TButton", "TEntry", "TCheckbutton", "TNotebook.Tab"):
            style.configure(name, font=("VT323", 15))
        style.configure("Title.TLabel", font=("VT323", 24))
        style.configure("Status.TLabel", font=("VT323", 16))

    def _build(self):
        box = ttk.Frame(self, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="MEDIA NODE CONTROL PLANE", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        self.tabs = ttk.Notebook(box)
        self.tabs.pack(fill="both", expand=True)
        setup = ttk.Frame(self.tabs, padding=14)
        manage = ttk.Frame(self.tabs, padding=14)
        logs = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(setup, text="SETUP")
        self.tabs.add(manage, text="MANAGEMENT")
        self.tabs.add(logs, text="LOGS")
        fields = [
            ("Server IPv4", "host"), ("SSH port", "port"), ("SSH user", "user"),
            ("SSH private key, optional", "key"), ("Temporary SSH password, optional", "password"),
            ("Media hostname", "hostname"), ("Certificate email", "email"),
            ("WordPress URL", "wordpress"), ("Telegram bot token", "bot_token"),
            ("Telegram API ID", "api_id"), ("Telegram API hash", "api_hash"),
            ("Telegram archive channel ID", "archive_chat_id"),
            ("Greenlight admin name", "admin_name"), ("Greenlight admin email", "admin_email"),
            ("Greenlight admin password, auto if empty", "admin_password"),
        ]
        for row, (label, key) in enumerate(fields):
            ttk.Label(setup, text=label).grid(row=row, column=0, sticky="w", pady=4)
            hidden = key in {"password", "bot_token", "api_hash", "admin_password"}
            ttk.Entry(setup, textvariable=self.values[key], width=68, show="*" if hidden else "").grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(setup, text="SELECT SSH KEY", command=lambda: self.values["key"].set(filedialog.askopenfilename())).grid(row=3, column=2, padx=6)
        actions = ttk.Frame(setup)
        actions.grid(row=len(fields), column=0, columnspan=3, sticky="w", pady=12)
        for text, action in (("PREFLIGHT", "preflight"), ("PROVISION", "provision")):
            button = ttk.Button(actions, text=text, command=lambda a=action: self._start(a))
            button.pack(side="left", padx=(0, 8)); self.action_buttons.append(button)
        setup.columnconfigure(1, weight=1)

        ttk.Label(manage, text="WEB CONSOLES", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Button(manage, text="OPEN GREENLIGHT", command=lambda: self._open_web("")).grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        ttk.Button(manage, text="OPEN ADMIN PANEL", command=lambda: self._open_web("/admin")).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(manage, text="SERVER OPERATIONS", style="Title.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(18, 8))
        management = (("HEALTH", "health"), ("REPAIR", "repair"), ("RESTART BBB", "restart"),
                      ("START BBB", "start"), ("STOP BBB", "stop"), ("RECORDING QUEUE", "queue"),
                      ("SERVICE LOGS", "logs"))
        for index, (text, action) in enumerate(management):
            button = ttk.Button(manage, text=text, command=lambda a=action: self._confirm_action(a))
            button.grid(row=3 + index // 3, column=index % 3, padx=5, pady=5, sticky="ew")
            self.action_buttons.append(button)
        for column in range(3): manage.columnconfigure(column, weight=1)

        toolbar = ttk.Frame(logs); toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="COPY LOG", command=self._copy_log).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="SAVE LOG", command=self._save_log).pack(side="left")
        ttk.Button(toolbar, text="CLEAR", command=self._clear_log).pack(side="left", padx=8)
        self.log = tk.Text(logs, height=28, wrap="word", state="disabled", font=("VT323", 14))
        self.log.pack(fill="both", expand=True)

        progress_box = ttk.Frame(box); progress_box.pack(fill="x", pady=(10, 4))
        self.progress = ttk.Progressbar(progress_box, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_box, textvariable=self.status, style="Status.TLabel", width=18).pack(side="left", padx=10)
        footer = ttk.Frame(box); footer.pack(fill="x")
        ttk.Label(footer, text="Made with <3 by").pack(side="left")
        author = ttk.Label(footer, text="Hamidreza", foreground="#2563eb", cursor="hand2")
        author.pack(side="left", padx=4)
        author.bind("<Button-1>", lambda _event: webbrowser.open("https://github.com/hrschemiker"))

    def _open_web(self, suffix: str):
        hostname = self.values["hostname"].get().strip()
        if not hostname:
            messagebox.showerror("Missing hostname", "Enter the media hostname first")
            return
        webbrowser.open(f"https://{hostname}{suffix}")

    def _copy_log(self):
        content = self.log.get("1.0", "end-1c")
        self.clipboard_clear(); self.clipboard_append(content)
        self.status.set("LOG COPIED")

    def _save_log(self):
        target = filedialog.asksaveasfilename(defaultextension=".log", filetypes=[("Log files", "*.log"), ("All files", "*")])
        if target:
            Path(target).write_text(self.log.get("1.0", "end-1c"), encoding="utf-8")
            self.status.set("LOG SAVED")

    def _clear_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")

    def _secret(self, key: str, value: str | None = None) -> str:
        if keyring is None:
            return value or ""
        account = f"{self.values['hostname'].get().strip() or 'default'}:{key}"
        try:
            if value is not None: keyring.set_password(KEYRING_SERVICE, account, value)
            return keyring.get_password(KEYRING_SERVICE, account) or ""
        except Exception:
            return value or ""

    def _save_profile(self):
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        secret_keys = {"password", "bot_token", "api_hash", "admin_password"}
        data = {key: value.get().strip() for key, value in self.values.items() if key not in secret_keys}
        PROFILE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8"); PROFILE_FILE.chmod(0o600)
        for key in secret_keys:
            value = self.values[key].get().strip()
            if value: self._secret(key, value)

    def _load_profile(self):
        if PROFILE_FILE.is_file():
            try:
                for key, value in json.loads(PROFILE_FILE.read_text(encoding="utf-8")).items():
                    if key in self.values: self.values[key].set(value)
            except Exception:
                pass
        for key in ("password", "bot_token", "api_hash", "admin_password"):
            value = self._secret(key)
            if value: self.values[key].set(value)

    def _confirm_action(self, action: str):
        if action == "stop" and not messagebox.askyesno("Stop BigBlueButton", "Stop all BigBlueButton services now?"):
            return
        self._start(action)

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
        private_dir = Path.home() / ".bbb-control-plane"
        private_file = private_dir / f"{values['hostname']}.env"
        previous: dict[str, str] = {}
        if private_file.is_file():
            for line in private_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1); previous[key] = value
        secret = previous.get("BRIDGE_SHARED_SECRET") or secrets.token_hex(32)
        admin_email = values["admin_email"] or values["email"]
        admin_password = values["admin_password"] or previous.get("GREENLIGHT_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        lines = {
            "BBB_HOSTNAME": values["hostname"], "LETSENCRYPT_EMAIL": values["email"],
            "WORDPRESS_URL": wordpress, "BRIDGE_SHARED_SECRET": secret,
            "TELEGRAM_BOT_TOKEN": clean_value("Telegram bot token", values["bot_token"]),
            "TELEGRAM_API_ID": api_id,
            "TELEGRAM_API_HASH": clean_value("Telegram API hash", values["api_hash"]),
            "TELEGRAM_ARCHIVE_CHAT_ID": channel_id,
            "GREENLIGHT_ADMIN_NAME": clean_value("Greenlight admin name", values["admin_name"]),
            "GREENLIGHT_ADMIN_EMAIL": clean_value("Greenlight admin email", admin_email),
            "GREENLIGHT_ADMIN_PASSWORD": clean_value("Greenlight admin password", admin_password),
            "RAW_RETENTION_DAYS": "7",
            "PRESENTATION_RETENTION_DAYS": "30", "LOCAL_VIDEO_RETENTION_DAYS": "3",
            "MIN_FREE_GB": "30", "MAX_UPLOAD_MIB": "1900", "VIDEO_CRF": "23",
            "VIDEO_HEIGHT": "720",
        }
        payload = "\n".join(f"{key}={clean_value(key, value)}" for key, value in lines.items()) + "\n"
        private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_file.write_text(payload, encoding="utf-8")
        private_file.chmod(0o600)
        self._secret("admin_password", admin_password)
        return payload, private_file

    def _start(self, action: str):
        if action == "provision":
            if not self.values["admin_email"].get().strip():
                self.values["admin_email"].set(self.values["email"].get().strip())
            if not self.values["admin_password"].get().strip():
                self.values["admin_password"].set(secrets.token_urlsafe(18))
        self._save_profile()
        self.status.set(action.upper())
        self.progress.start(12)
        for button in self.action_buttons: button.configure(state="disabled")
        self.tabs.select(2)
        self.events.put(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {action.upper()} started\n")
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
                    code, out = ssh.run("sudo install -m 0600 /tmp/bcp.env /etc/bbb-control-plane.env && rm -f /tmp/bcp.env && sudo bash /opt/bbb-control-plane/source/provision/install.sh", emit=self.events.put)
                    self.events.put(f"Private recovery settings saved at {private_file}\n")
                else:
                    code, out = ssh.run(f"sudo /usr/local/sbin/bcpctl {shlex.quote(action)}", emit=self.events.put)
                self.events.put(out)
                if code:
                    raise ControllerError(f"Operation exited with status {code}")
                self.events.put("Operation completed successfully.\n")
            finally:
                ssh.close()
        except Exception as exc:
            self.events.put(f"ERROR: {exc}\n")
        finally:
            self.events.put(("done", action))

    def _drain(self):
        while not self.events.empty():
            msg = self.events.get_nowait()
            if isinstance(msg, tuple) and msg[0] == "done":
                self.progress.stop(); self.status.set("READY")
                for button in self.action_buttons: button.configure(state="normal")
                continue
            self.log.configure(state="normal"); self.log.insert("end", str(msg)); self.log.see("end"); self.log.configure(state="disabled")
        self.after(100, self._drain)


if __name__ == "__main__":
    App().mainloop()
