#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

log(){ printf '[bcp] %s\n' "$*"; }
die(){ printf '[bcp] ERROR: %s\n' "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

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
[ -f /etc/bbb-control-plane.env ] || die "/etc/bbb-control-plane.env is missing"
set -a; . /etc/bbb-control-plane.env; set +a
for key in BBB_HOSTNAME LETSENCRYPT_EMAIL WORDPRESS_URL BRIDGE_SHARED_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_ARCHIVE_CHAT_ID GREENLIGHT_ADMIN_NAME GREENLIGHT_ADMIN_EMAIL GREENLIGHT_ADMIN_PASSWORD; do
  [ -n "${!key:-}" ] || die "$key is required"
done
resolved=$(getent ahostsv4 "$BBB_HOSTNAME" | awk 'NR==1{print $1}')
public=$(curl -4fsS --max-time 10 https://api.ipify.org)
[ "$resolved" = "$public" ] || die "DNS $resolved does not match public IPv4 $public"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl jq ufw fail2ban python3 python3-venv ffmpeg rsync unattended-upgrades gettext-base

log "installing BigBlueButton 3.0 with the upstream installer"
curl -fsSL https://raw.githubusercontent.com/bigbluebutton/bbb-install/v3.0.x-release/bbb-install.sh -o /tmp/bbb-install.sh
bash /tmp/bbb-install.sh -v jammy-300 -s "$BBB_HOSTNAME" -e "$LETSENCRYPT_EMAIL" -w -g

log "creating the Greenlight administrator when absent"
if docker ps --format '{{.Names}}' | grep -qx greenlight-v3; then
  docker exec greenlight-v3 bundle exec rake "admin:create[$GREENLIGHT_ADMIN_NAME,$GREENLIGHT_ADMIN_EMAIL,$GREENLIGHT_ADMIN_PASSWORD]" || log "Greenlight administrator already exists or requires review"
fi

log "enabling composite recording workflow"
apt-get install -y bbb-playback-video
install -d -m 0755 /etc/bigbluebutton/recording
install -m 0644 /opt/bbb-control-plane/source/provision/recording.yml /etc/bigbluebutton/recording/recording.yml
if [ -f /usr/local/bigbluebutton/core/scripts/video.yml ]; then
  python3 /opt/bbb-control-plane/source/provision/configure_video.py /usr/local/bigbluebutton/core/scripts/video.yml "${VIDEO_CRF:-23}" "${VIDEO_HEIGHT:-720}"
fi

log "installing local Telegram Bot API"
install -d -m 0750 /opt/telegram-bot-api /var/lib/telegram-bot-api
docker pull aiogram/telegram-bot-api:latest
envsubst < /opt/bbb-control-plane/source/provision/telegram-bot-api.service.in > /etc/systemd/system/telegram-bot-api.service
install -m 0755 /opt/bbb-control-plane/source/provision/telegram-migrate.py /usr/local/lib/bcp-telegram-migrate.py
envsubst < /opt/bbb-control-plane/source/provision/telegram-gateway.nginx.in > /etc/bigbluebutton/nginx/telegram-gateway.nginx

log "installing recording queue worker"
install -d -o bigbluebutton -g bigbluebutton -m 0750 /var/lib/bcp/jobs /var/lib/bcp/done /var/lib/bcp/failed
install -m 0755 /opt/bbb-control-plane/source/worker/worker.py /usr/local/lib/bcp-worker.py
install -m 0755 /opt/bbb-control-plane/source/worker/post_publish.py /usr/local/lib/bcp-post-publish.py
install -m 0755 /opt/bbb-control-plane/source/provision/bcpctl /usr/local/sbin/bcpctl
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-worker.service /etc/systemd/system/bcp-worker.service
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-retention.service /etc/systemd/system/bcp-retention.service
install -m 0644 /opt/bbb-control-plane/source/provision/bcp-retention.timer /etc/systemd/system/bcp-retention.timer
install -d -m 0755 /usr/local/bigbluebutton/core/scripts/post_publish
ln -sfn /usr/local/lib/bcp-post-publish.py /usr/local/bigbluebutton/core/scripts/post_publish/bcp-post-publish.py

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 16384:32768/udp
ufw --force enable
systemctl daemon-reload
systemctl enable --now fail2ban telegram-bot-api bcp-retention.timer
systemctl enable bcp-worker
nginx -t
systemctl reload nginx
if /usr/bin/env python3 /usr/local/lib/bcp-telegram-migrate.py; then
  systemctl enable --now bcp-worker
else
  log "Telegram migration is pending. BigBlueButton is installed, existing bot traffic remains on the cloud API, and the recording worker is stopped."
  log "Configure and activate the WordPress transport bridge, then run: sudo bcpctl telegram-migrate"
fi
systemctl restart bbb-rap-resque-worker.service
bbb-conf --restart
bbb-conf --check
log "provisioning complete"
