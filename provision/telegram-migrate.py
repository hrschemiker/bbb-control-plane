#!/usr/bin/env python3
"""Move a bot from the cloud Bot API to the local Bot API after bridge validation."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment value: {name}")
    return value


def request_json(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 30) -> dict:
    body = None if data is None else urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(result.get("description", f"request failed: {url}"))
    return result


def bridge_ready() -> None:
    secret = required("BRIDGE_SHARED_SECRET")
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{timestamp}.bridge-ready".encode(), hashlib.sha256).hexdigest()
    url = required("WORDPRESS_URL").rstrip("/") + "/wp-json/gtbp-bridge/v1/health"
    result = request_json(url, headers={"X-BCP-Timestamp": timestamp, "X-BCP-Signature": signature})
    if not result.get("gateway_configured") or not result.get("authenticated"):
        raise RuntimeError("WordPress transport bridge is not configured for the local gateway")


def main() -> None:
    token = required("TELEGRAM_BOT_TOKEN")
    cloud = f"https://api.telegram.org/bot{token}"
    local = f"http://127.0.0.1:8081/bot{token}"
    marker = "/var/lib/bcp/telegram-local.ready"
    if os.path.isfile(marker):
        request_json(local + "/getMe")
        print("[bcp] Telegram bot is already attached to the local Bot API")
        return

    bridge_ready()
    webhook = request_json(cloud + "/getWebhookInfo").get("result", {})
    webhook_url = str(webhook.get("url", "")).strip()
    request_json(cloud + "/logOut", data={})

    last_error: Exception | None = None
    for _attempt in range(30):
        try:
            request_json(local + "/getMe")
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    if last_error:
        raise RuntimeError(f"local Bot API did not accept the bot after cloud logout: {last_error}")

    if webhook_url:
        data = {"url": webhook_url}
        if webhook.get("max_connections"):
            data["max_connections"] = str(webhook["max_connections"])
        if webhook.get("allowed_updates"):
            data["allowed_updates"] = json.dumps(webhook["allowed_updates"], separators=(",", ":"))
        request_json(local + "/setWebhook", data=data)

    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(str(int(time.time())) + "\n")
    os.chmod(marker, 0o600)
    print("[bcp] Telegram bot migration completed")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"[bcp] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
