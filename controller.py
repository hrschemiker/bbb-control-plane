#!/usr/bin/env python3
"""Local GUI controller for deterministic node provisioning."""
from __future__ import annotations

import ipaddress
import os
import queue
import re
import shlex
import socket
import threading
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


class SSH:
    def __init__(self, host: str, port: int, user: str, key_path: str, password: str):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        known = Path.home() / ".ssh" / "known_hosts"
        if known.exists():
            self.client.load_host_keys(str(known))
        known.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.client.connect(host, port=port, username=user, key_filename=key_path or None, password=password or None, timeout=20)
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
        self.geometry("880x680")
        self.events: queue.Queue[str] = queue.Queue()
        self.values = {k: tk.StringVar() for k in ("host", "port", "user", "key", "password", "hostname", "email", "env")}
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
            ("Media hostname", "hostname"), ("Certificate email", "email"), ("Private environment file", "env"),
        ]
        for row, (label, key) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(box, textvariable=self.values[key], width=72, show="*" if key == "password" else "").grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(box, text="Select SSH key", command=lambda: self.values["key"].set(filedialog.askopenfilename())).grid(row=3, column=2)
        ttk.Button(box, text="Select environment", command=lambda: self.values["env"].set(filedialog.askopenfilename())).grid(row=7, column=2)
        actions = ttk.Frame(box)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", pady=14)
        for text, action in (("Preflight", "preflight"), ("Provision", "provision"), ("Health", "health"), ("Repair", "repair")):
            ttk.Button(actions, text=text, command=lambda a=action: self._start(a)).pack(side="left", padx=5)
        self.log = tk.Text(box, height=24, wrap="word", state="disabled")
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew")
        box.columnconfigure(1, weight=1)
        box.rowconfigure(9, weight=1)

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
                    if not Path(v["env"]).is_file():
                        raise ControllerError("Select a completed private environment file")
                    ssh.put_tree(ROOT, "/opt/bbb-control-plane/source")
                    sftp = ssh.client.open_sftp()
                    sftp.put(v["env"], "/tmp/bcp.env")
                    sftp.close()
                    code, out = ssh.run("sudo install -m 0600 /tmp/bcp.env /etc/bbb-control-plane.env && rm -f /tmp/bcp.env && sudo bash /opt/bbb-control-plane/source/provision/install.sh")
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
