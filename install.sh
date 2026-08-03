#!/usr/bin/env bash
# =============================================================================
# NEPTUNE — Raspberry Pi one-shot installer / updater
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/install.sh | sudo bash
#
# Fresh install OR update (auto-detected). Clones the repo, drops the client
# (the Pi is headless — the dashboard runs topside), installs the control API +
# go2rtc video plane, wires nginx + systemd, pins the camera route to wlan0, and
# enables everything on boot. Idempotent: re-run to update.
#
# Override anything via env, e.g.:
#   curl -fsSL .../install.sh | sudo NEPTUNE_REPO=https://github.com/me/sub.git bash
# =============================================================================
set -euo pipefail

# ---- configuration (env-overridable) ----------------------------------------
REPO_URL="${NEPTUNE_REPO:-https://github.com/REPLACE_ME/sub.git}"   # <-- set to your repo
BRANCH="${NEPTUNE_BRANCH:-main}"
INSTALL_DIR="${NEPTUNE_DIR:-/opt/neptune}"
SERVICE_USER="${NEPTUNE_USER:-neptune}"
TETHER_IFACE="${NEPTUNE_TETHER_IFACE:-eth0}"      # tether / default route
CAM_IFACE="${NEPTUNE_CAM_IFACE:-wlan0}"           # camera AP
CAMERA_IP="${NEPTUNE_CAMERA_IP:-192.72.1.1}"
GO2RTC_VERSION="${GO2RTC_VERSION:-v1.9.4}"

log()  { printf '\033[1;36m[neptune]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[neptune] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[neptune] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (pipe to 'sudo bash')."
command -v apt-get >/dev/null 2>&1 || die "expects Debian/Raspberry Pi OS (apt-get not found)."
[ "$REPO_URL" = "https://github.com/REPLACE_ME/sub.git" ] && \
  die "set your repo: re-run with  ... | sudo NEPTUNE_REPO=https://github.com/you/sub.git bash"

# ---- 1. system packages -----------------------------------------------------
log "installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip nginx curl ca-certificates iproute2

# ---- 2. service user --------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "creating service user '$SERVICE_USER'…"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# ---- 2b. camera WiFi (wlan0 joins the camera AP, never the default route) ---
# Solves the §1 routing constraint at the source: ipv4.never-default keeps the
# camera hop off the default route so the tether (eth0) stays the way topside.
CAM_SSID="${NEPTUNE_CAM_SSID:-ActionCam_b981}"
CAM_PSK="${NEPTUNE_CAM_PSK:-12345678}"
if command -v nmcli >/dev/null 2>&1; then
  log "configuring $CAM_IFACE to join camera AP '$CAM_SSID' (never-default)…"
  nmcli connection delete neptune-cam >/dev/null 2>&1 || true
  nmcli connection add type wifi ifname "$CAM_IFACE" con-name neptune-cam \
    ssid "$CAM_SSID" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$CAM_PSK" \
    ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled \
    connection.autoconnect yes >/dev/null \
    && nmcli connection up neptune-cam >/dev/null 2>&1 \
    || warn "camera WiFi setup failed — join '$CAM_SSID' on $CAM_IFACE manually"
elif [ -d /etc/wpa_supplicant ] || command -v wpa_supplicant >/dev/null 2>&1; then
  # older Pi OS (Bullseye / dhcpcd) fallback
  log "configuring $CAM_IFACE via wpa_supplicant to join '$CAM_SSID'…"
  WPA=/etc/wpa_supplicant/wpa_supplicant.conf
  touch "$WPA"
  if ! grep -q "ssid=\"$CAM_SSID\"" "$WPA"; then
    cat >> "$WPA" <<EOF

network={
    ssid="$CAM_SSID"
    psk="$CAM_PSK"
    key_mgmt=WPA-PSK
}
EOF
  fi
  # keep wlan0 off the default route (dhcpcd)
  if [ -f /etc/dhcpcd.conf ] && ! grep -q "^interface $CAM_IFACE" /etc/dhcpcd.conf; then
    printf '\ninterface %s\n    nogateway\n' "$CAM_IFACE" >> /etc/dhcpcd.conf
  fi
  wpa_cli -i "$CAM_IFACE" reconfigure >/dev/null 2>&1 || true
else
  warn "no nmcli/wpa_supplicant — join '$CAM_SSID' on $CAM_IFACE manually (must NOT set a default route)"
fi

# ---- 3. clone or update the repo -------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  log "existing install found — updating from $BRANCH…"
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  log "cloning $REPO_URL ($BRANCH) → $INSTALL_DIR…"
  mkdir -p "$INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ---- 4. drop the client (headless Pi) --------------------------------------
# The dashboard is served topside from disk, not from the Pi. reset --hard above
# would restore it on every update, so we remove it here each run.
if [ -d "$INSTALL_DIR/client" ]; then
  log "removing client/ (Pi is headless)…"
  rm -rf "$INSTALL_DIR/client"
fi

# ---- 5. python venv + deps --------------------------------------------------
log "setting up python venv + dependencies…"
VENV="$INSTALL_DIR/api/.venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# Pillow is bench-only (synthetic camera) and unused on the Pi; skip it here.
grep -viE '^\s*Pillow' "$INSTALL_DIR/api/requirements.txt" > /tmp/neptune-reqs.txt
"$VENV/bin/pip" install --quiet -r /tmp/neptune-reqs.txt
rm -f /tmp/neptune-reqs.txt

# ---- 6. go2rtc binary (video plane) ----------------------------------------
install_go2rtc() {
  local arch bin
  case "$(uname -m)" in
    aarch64|arm64) arch=arm64 ;;
    armv7l|armv6l) arch=arm ;;
    x86_64|amd64)  arch=amd64 ;;
    *) warn "unknown arch $(uname -m); skipping go2rtc"; return 0 ;;
  esac
  bin="/usr/local/bin/go2rtc"
  if [ -x "$bin" ] && "$bin" --version 2>&1 | grep -q "${GO2RTC_VERSION#v}"; then
    log "go2rtc ${GO2RTC_VERSION} already installed"; return 0
  fi
  log "installing go2rtc ${GO2RTC_VERSION} (${arch})…"
  curl -fsSL -o "$bin" \
    "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${arch}" \
    || { warn "go2rtc download failed — install it manually to $bin"; return 0; }
  chmod +x "$bin"
}
install_go2rtc

# ---- 7. fill the tether IP into go2rtc.yaml ---------------------------------
TETHER_IP="$(ip -4 -o addr show "$TETHER_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)"
sed -i "s#192.72.1.1#${CAMERA_IP}#g" "$INSTALL_DIR/deploy/go2rtc.yaml"   # camera RTSP host
if [ -n "$TETHER_IP" ]; then
  log "tether ($TETHER_IFACE) IP = $TETHER_IP"
  sed -i "s/<PI_TETHER_IP>/$TETHER_IP/g" "$INSTALL_DIR/deploy/go2rtc.yaml"
else
  warn "could not detect $TETHER_IFACE IP — edit $INSTALL_DIR/deploy/go2rtc.yaml (<PI_TETHER_IP>) by hand"
fi

# ---- 8. nginx (headless: proxy-only, no SPA) --------------------------------
log "configuring nginx (reverse proxy)…"
cat > /etc/nginx/sites-available/neptune <<'NGINX'
# NEPTUNE headless Pi — single origin, proxy only (no SPA served here).
upstream neptune_api { server 127.0.0.1:8000; }
upstream go2rtc      { server 127.0.0.1:1984; }
server {
    listen 80 default_server;
    server_name _;

    location = / { return 200 "NEPTUNE Pi OK\n"; add_header Content-Type text/plain; }

    location /api/ {
        proxy_pass http://neptune_api;
        proxy_http_version 1.1; proxy_set_header Host $host;
        proxy_read_timeout 120s; proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
    location /ws/ {
        proxy_pass http://neptune_api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
        proxy_set_header Host $host; proxy_read_timeout 3600s;
    }
    location /stream.mjpg { proxy_pass http://neptune_api; proxy_buffering off; }
    location /go2rtc/ {
        proxy_pass http://go2rtc/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
    location /stream/ {
        proxy_pass http://go2rtc/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/neptune /etc/nginx/sites-enabled/neptune
rm -f /etc/nginx/sites-enabled/default
nginx -t

# ---- 9. systemd units -------------------------------------------------------
log "installing systemd units…"
# Copy the repo's units, but rewrite paths/ifaces to the real values here so the
# units always match this host.
for unit in neptune-api go2rtc wolfang-route; do
  install -m 0644 "$INSTALL_DIR/deploy/systemd/${unit}.service" "/etc/systemd/system/${unit}.service"
done
# patch the route unit for the configured camera iface/IP
sed -i "s#192.72.1.1#${CAMERA_IP}#g; s#wlan0#${CAM_IFACE}#g" /etc/systemd/system/wolfang-route.service
# ensure paths + user in the api/go2rtc units match this install
sed -i "s#/opt/neptune#${INSTALL_DIR}#g; s#User=neptune#User=${SERVICE_USER}#g" \
    /etc/systemd/system/neptune-api.service /etc/systemd/system/go2rtc.service

# ---- 10. permissions --------------------------------------------------------
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ---- 11. enable on boot + restart to apply changes -------------------------
# `enable` sets boot start; explicit `restart` applies new code/config on an
# update (enable --now would NOT restart an already-running service).
log "enabling on boot + (re)starting services…"
systemctl daemon-reload
systemctl enable wolfang-route.service go2rtc.service neptune-api.service nginx >/dev/null 2>&1 || true
systemctl restart wolfang-route.service || warn "wolfang-route failed (is $CAM_IFACE up?)"
systemctl restart go2rtc.service        || warn "go2rtc failed to start"
systemctl restart neptune-api.service   || warn "neptune-api failed to start"
systemctl restart nginx                 || warn "nginx failed to (re)start"

# ---- 12. report -------------------------------------------------------------
echo
log "done. service status:"
for s in wolfang-route go2rtc neptune-api nginx; do
  printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || echo inactive)"
done
echo
log "control API : http://${TETHER_IP:-<pi>}/api/status   (preflight: POST /api/preflight)"
log "video (WebRTC): http://${TETHER_IP:-<pi>}/stream/ (go2rtc stream 'sub')"
log "re-run this installer any time to update."
