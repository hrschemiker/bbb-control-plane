#!/usr/bin/env bash
set -Eeuo pipefail

unit=bcp-provision.service
state=/var/lib/bcp/provision/state

if systemctl is-active --quiet "$unit"; then
  echo '[bcp] attaching to the provisioning process already running on the server'
else
  systemctl reset-failed "$unit" 2>/dev/null || true
  echo '[bcp] starting a server-managed provisioning process'
  systemd-run --unit=bcp-provision --service-type=exec --property=TimeoutStartSec=8h \
    /usr/bin/bash /opt/bbb-control-plane/source/provision/install.sh
fi

journalctl -fu "$unit" -o cat --no-pager &
journal_pid=$!
trap 'kill "$journal_pid" 2>/dev/null || true' EXIT

while true; do
  unit_state=$(systemctl is-active "$unit" 2>/dev/null || true)
  [ "$unit_state" = active ] || [ "$unit_state" = activating ] || break
  current=$(cat "$state" 2>/dev/null || printf starting)
  printf '[bcp] HEARTBEAT: phase=%s time=%s\n' "$current" "$(date -u +%H:%M:%S)"
  sleep 15
done

kill "$journal_pid" 2>/dev/null || true
wait "$journal_pid" 2>/dev/null || true
status=$(systemctl show "$unit" -p ExecMainStatus --value 2>/dev/null || printf 1)
[ "$status" = 0 ] || {
  echo "[bcp] provisioning service failed with status $status" >&2
  journalctl -u "$unit" -n 120 -o cat --no-pager
  exit "${status:-1}"
}
echo '[bcp] server-managed provisioning process finished successfully'
