#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

STATE_DIR=/var/lib/bcp/provision
STATE_FILE=$STATE_DIR/state
LOG_FILE=/var/log/bcp-provision.log
PHASE=bootstrap

log(){ printf '[bcp] %s\n' "$*"; }
die(){ printf '[bcp] ERROR: %s\n' "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
phase(){ PHASE=$1; install -d -m 0750 "$STATE_DIR"; printf '%s\n' "$PHASE" > "$STATE_FILE"; log "PHASE: $PHASE"; }
on_error(){ code=$?; log "FAILED: phase=$PHASE line=${BASH_LINENO[0]} status=$code"; exit "$code"; }
trap on_error ERR

preflight(){
  need awk; need curl; need getent
  [ "$(id -u)" -eq 0 ] || die "run as root"
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 22.04 ] || die "Ubuntu 22.04 is required"
  [ "$(uname -m)" = x86_64 ] || die "x86_64 is required"
  cpu=$(getconf _NPROCESSORS_ONLN); mem=$(awk '/MemTotal/{print int($2/1024/1024)}' /proc/meminfo)
  [ "$cpu" -ge 8 ] || die "at least 8 CPU cores are required"
  [ "$mem" -ge 15 ] || die "at least 16 GB RAM is required"
  root_free=$(df -BG / | awk 'NR==2{gsub(/G/,"",$4);print $4}')
  [ "$root_free" -ge 120 ] || die "at least 120 GB free disk is required"
  log "preflight passed: cpu=$cpu ram=${mem}GiB free=${root_free}GiB"
}

if [ "${1:-}" = --preflight ]; then preflight; exit 0; fi
preflight
need flock
exec 9>/var/lock/bcp-provision.lock
flock -n 9 || die "another provisioning process is already running"
install -d -m 0750 "$STATE_DIR"
touch "$LOG_FILE"; chmod 0640 "$LOG_FILE"

[ -f /etc/bbb-control-plane.env ] || die "/etc/bbb-control-plane.env is missing"
set -a; . /etc/bbb-control-plane.env; set +a
for key in BBB_HOSTNAME LETSENCRYPT_EMAIL WORDPRESS_URL BRIDGE_SHARED_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_ARCHIVE_CHAT_ID GREENLIGHT_ADMIN_NAME GREENLIGHT_ADMIN_EMAIL GREENLIGHT_ADMIN_PASSWORD; do
  [ -n "${!key:-}" ] || die "$key is required"
done
python3 - "$LOG_FILE" "$GREENLIGHT_ADMIN_PASSWORD" "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_API_HASH" "$BRIDGE_SHARED_SECRET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
for secret in sys.argv[2:]:
    if secret:
        text = text.replace(secret, "[REDACTED]")
path.write_text(text, encoding="utf-8")
PY
exec > >(tee -a "$LOG_FILE") 2>&1
resolved=$(getent ahostsv4 "$BBB_HOSTNAME" | awk 'NR==1{print $1}')
public=$(curl -4fsS --max-time 10 https://api.ipify.org)
[ "$resolved" = "$public" ] || die "DNS $resolved does not match public IPv4 $public"
export DEBIAN_FRONTEND=noninteractive

wait_existing_installer(){
  phase waiting_for_previous_installer
  waited=0
  while pgrep -f '[b]bb-install.sh' >/dev/null 2>&1; do
    [ "$waited" -lt 7200 ] || die "previous BigBlueButton installer exceeded two hours"
    log "previous BigBlueButton installer is still active, waiting (${waited}s)"
    sleep 15; waited=$((waited+15))
  done
}

wait_for_apt(){
  phase waiting_for_package_manager
  waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; do
    [ "$waited" -lt 1800 ] || die "package manager remained busy for more than 30 minutes"
    log "package manager is busy, waiting (${waited}s)"
    sleep 15; waited=$((waited+15))
  done
}

repair_packages(){
  phase repairing_package_manager
  wait_for_apt
  dpkg --configure -a
  apt-get -f install -y
  apt-get update
}

bbb_packages_present(){
  dpkg-query -W -f='${binary:Package}\n' 2>/dev/null | grep -Eq '^(bigbluebutton|bbb-)'
}

bbb_core_healthy(){
  dpkg-query -W -f='${db:Status-Status}\n' bigbluebutton 2>/dev/null | grep -qx installed || return 1
  command -v bbb-conf >/dev/null 2>&1 || return 1
  bbb-conf --version >/dev/null 2>&1 || return 1
  [ -f /usr/local/bigbluebutton/core/scripts/bigbluebutton.yml ] || return 1
  grep -Fq "$BBB_HOSTNAME" /usr/local/bigbluebutton/core/scripts/bigbluebutton.yml || return 1
}

greenlight_healthy(){
  command -v docker >/dev/null 2>&1 || return 1
  [ -s /root/greenlight-v3/docker-compose.yml ] || return 1
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx greenlight-v3
}

greenlight_database_ready(){
  greenlight_healthy || return 1
  docker exec greenlight-v3 bundle exec rails runner 'ActiveRecord::Base.connection.execute("SELECT 1")' >/dev/null 2>&1
}

prepare_greenlight_database(){
  phase repairing_greenlight_database
  greenlight_healthy || die "Greenlight container is not running"
  attempt=1
  while [ "$attempt" -le 5 ]; do
    if docker exec greenlight-v3 bundle exec rails db:prepare; then
      greenlight_database_ready && return 0
    fi
    log "Greenlight database is not ready, retrying ($attempt/5)"
    sleep 10
    attempt=$((attempt+1))
  done
  docker logs --tail 120 greenlight-v3 2>&1 || true
  die "Greenlight database could not be prepared"
}

create_or_promote_greenlight_admin(){
  if docker exec greenlight-v3 bundle exec rake "admin:create[$GREENLIGHT_ADMIN_NAME,$GREENLIGHT_ADMIN_EMAIL,$GREENLIGHT_ADMIN_PASSWORD]" >/dev/null 2>&1; then
    log "Greenlight administrator created"
    return
  fi
  log "Administrator may already exist, ensuring the configured account has the administrator role"
  if docker exec greenlight-v3 bundle exec rake "user:set_admin_role[$GREENLIGHT_ADMIN_EMAIL]" >/dev/null 2>&1; then
    log "Greenlight administrator role verified"
    return
  fi
  die "Greenlight administrator could not be created or promoted"
}

start_required_service(){
  service_name=$1
  systemctl enable "$service_name"
  if systemctl restart "$service_name"; then return 0; fi
  systemctl --no-pager --full status "$service_name" || true
  journalctl --no-pager -u "$service_name" -n 160 || true
  die "$service_name failed to start"
}

backup_before_cleanup(){
  phase backing_up_partial_installation
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  backup=/var/backups/bcp/$stamp
  install -d -m 0700 "$backup"
  tar -czf "$backup/configuration.tar.gz" --ignore-failed-read /etc/bigbluebutton /etc/nginx/sites-available /etc/nginx/sites-enabled /root/greenlight-v3 2>/dev/null || true
  if [ -d /var/bigbluebutton ]; then
    find /var/bigbluebutton -mindepth 1 -maxdepth 2 -type d -printf '%p\n' > "$backup/recording-directories.txt" || true
  fi
  log "configuration backup created at $backup"
}

purge_partial_bbb_packages(){
  backup_before_cleanup
  phase cleaning_partial_bbb_packages
  if command -v bbb-conf >/dev/null 2>&1; then bbb-conf --stop || true; fi
  mapfile -t packages < <(dpkg-query -W -f='${binary:Package}\n' 2>/dev/null | sed 's/:amd64$//' | grep -E '^(bigbluebutton|bbb-)' | sort -u)
  if [ "${#packages[@]}" -gt 0 ]; then apt-get purge -y "${packages[@]}"; fi
  dpkg --configure -a
  apt-get -f install -y
  log "recordings under /var/bigbluebutton were preserved"
}

run_upstream_installer(){
  attempt=$1
  phase "installing_bigbluebutton_attempt_$attempt"
  curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 https://raw.githubusercontent.com/bigbluebutton/bbb-install/v3.0.x-release/bbb-install.sh -o /tmp/bbb-install.sh
  chmod 0700 /tmp/bbb-install.sh
  set +e
  timeout --signal=TERM --kill-after=120s 7200s stdbuf -oL -eL bash /tmp/bbb-install.sh -v jammy-300 -s "$BBB_HOSTNAME" -e "$LETSENCRYPT_EMAIL" -w -g 2>&1 | tee -a "$STATE_DIR/upstream-attempt-$attempt.log"
  result=${PIPESTATUS[0]}
  set -e
  return "$result"
}

install_or_resume_bbb(){
  wait_existing_installer
  repair_packages
  if bbb_core_healthy && greenlight_healthy; then
    phase existing_bigbluebutton_detected
    log "healthy BigBlueButton and Greenlight installation detected, upstream reinstall skipped"
    return
  fi
  if bbb_core_healthy; then log "BigBlueButton core is healthy but Greenlight is missing or stopped, resuming upstream configuration"; fi
  if bbb_packages_present; then log "partial BigBlueButton packages detected, resuming installation"; else log "no BigBlueButton packages detected, starting installation"; fi
  if run_upstream_installer 1 && bbb_core_healthy && greenlight_healthy; then return; fi
  log "first installation attempt did not produce a healthy BBB and Greenlight stack, repairing packages"
  repair_packages
  if run_upstream_installer 2 && bbb_core_healthy && greenlight_healthy; then return; fi
  log "second installation attempt failed, starting protected cleanup and final retry"
  purge_partial_bbb_packages
  repair_packages
  run_upstream_installer 3
  bbb_core_healthy && greenlight_healthy || die "BigBlueButton or Greenlight is still unhealthy after protected recovery"
}

phase installing_base_dependencies
wait_for_apt
apt-get update
apt-get install -y ca-certificates curl jq ufw fail2ban python3 python3-venv ffmpeg rsync unattended-upgrades gettext-base
install_or_resume_bbb

phase configuring_greenlight
greenlight_healthy || die "Greenlight container is not healthy after upstream installation"
greenlight_database_ready || prepare_greenlight_database
create_or_promote_greenlight_admin

phase configuring_composite_recordings
apt-get install -y bbb-playback-video
install -d -m 0755 /etc/bigbluebutton/recording
install -m 0644 /opt/bbb-control-plane/source/provision/recording.yml /etc/bigbluebutton/recording/recording.yml
if [ -f /usr/local/bigbluebutton/core/scripts/video.yml ]; then
  python3 /opt/bbb-control-plane/source/provision/configure_video.py /usr/local/bigbluebutton/core/scripts/video.yml "${VIDEO_CRF:-23}" "${VIDEO_HEIGHT:-720}"
fi

phase installing_telegram_transport
install -d -m 0750 /opt/telegram-bot-api /var/lib/telegram-bot-api
docker pull aiogram/telegram-bot-api:latest
envsubst '${TELEGRAM_API_ID} ${TELEGRAM_API_HASH}' < /opt/bbb-control-plane/source/provision/telegram-bot-api.service.in > /etc/systemd/system/telegram-bot-api.service
install -m 0755 /opt/bbb-control-plane/source/provision/telegram-migrate.py /usr/local/lib/bcp-telegram-migrate.py
install -d -m 0755 /etc/bigbluebutton/nginx
envsubst '${BRIDGE_SHARED_SECRET}' < /opt/bbb-control-plane/source/provision/telegram-gateway.nginx.in > /etc/bigbluebutton/nginx/telegram-gateway.nginx

phase installing_recording_worker
install -d -o bigbluebutton -g bigbluebutton -m 0750 /var/lib/bcp/jobs /var/lib/bcp/done /var/lib/bcp/failed
install -m 0755 /opt/bbb-control-plane/source/worker/worker.py /usr/local/lib/bcp-worker.py
install -m 0755 /opt/bbb-control-plane/source/worker/post_publish.py /usr/local/lib/bcp-post-publish.py
install -m 0755 /opt/bbb-control-plane/source/provision/bcpctl /usr/local/sbin/bcpctl
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-worker.service /etc/systemd/system/bcp-worker.service
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-retention.service /etc/systemd/system/bcp-retention.service
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-retention.timer /etc/systemd/system/bcp-retention.timer
install -d -m 0755 /usr/local/bigbluebutton/core/scripts/post_publish
ln -sfn /usr/local/lib/bcp-post-publish.py /usr/local/bigbluebutton/core/scripts/post_publish/bcp-post-publish.py

phase configuring_firewall_and_services
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 16384:32768/udp
ufw --force enable
systemctl daemon-reload
start_required_service fail2ban
start_required_service telegram-bot-api
start_required_service bcp-retention.timer
systemctl enable bcp-worker
nginx -t
systemctl reload nginx

phase validating_telegram_migration
if /usr/bin/env python3 /usr/local/lib/bcp-telegram-migrate.py; then
  systemctl enable --now bcp-worker
else
  systemctl disable --now bcp-worker 2>/dev/null || true
  log "Telegram migration is pending. Existing bot traffic remains on the cloud API."
  log "Configure the WordPress bridge, then select ACTIVATE TELEGRAM in the controller."
fi

phase final_validation
systemctl restart bbb-rap-resque-worker.service
bbb-conf --setip "$BBB_HOSTNAME"
bbb-conf --restart
/usr/local/sbin/bcpctl repair
nginx -t
bbb-conf --check
bbb-record --check
curl -fkIsS --max-time 20 "https://$BBB_HOSTNAME/" >/dev/null
dpkg --audit | tee "$STATE_DIR/dpkg-audit.txt"
[ ! -s "$STATE_DIR/dpkg-audit.txt" ] || die "package audit reported incomplete packages"
[ ! -f /var/run/reboot-required ] || log "NOTICE: Ubuntu requests a reboot after provisioning"
df -h / /var/bigbluebutton
printf '%s\n' complete > "$STATE_FILE"
log "PROVISIONING COMPLETE"
