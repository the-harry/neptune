#!/usr/bin/env bash
# =============================================================================
# NEPTUNE — Raspberry Pi one-shot installer / updater
#
#   curl -fsSL https://raw.githubusercontent.com/the-harry/neptune/master/install.sh | sudo bash
#
# Fresh install OR update (auto-detected). Clones the repo, keeps ONLY the backend
# (strips the client), installs the control API + go2rtc video plane behind a plain
# nginx reverse proxy (no TLS — sealed tether), pins the camera route to wlan0, and
# enables everything on boot. Idempotent: re-run to update.
#
# Override anything via env, e.g.:
#   curl -fsSL .../install.sh | sudo NEPTUNE_BRANCH=dev bash
#   curl -fsSL .../install.sh | sudo NEPTUNE_REPO=https://github.com/you/fork.git bash
# =============================================================================
set -euo pipefail

# ---- configuration (env-overridable) ----------------------------------------
REPO_URL="${NEPTUNE_REPO:-https://github.com/the-harry/neptune.git}"
BRANCH="${NEPTUNE_BRANCH:-master}"
INSTALL_DIR="${NEPTUNE_DIR:-/opt/neptune}"
SERVICE_USER="${NEPTUNE_USER:-neptune}"
TETHER_IFACE="${NEPTUNE_TETHER_IFACE:-eth0}"      # tether / default route
TETHER_IP="${NEPTUNE_TETHER_IP:-192.168.42.1}"    # FIXED tether address (no DHCP on a direct link)
TETHER_CIDR="${TETHER_IP}/24"
CAM_IFACE="${NEPTUNE_CAM_IFACE:-wlan0}"           # camera AP
CAMERA_IP="${NEPTUNE_CAMERA_IP:-192.72.1.1}"
PI_HOSTNAME="${NEPTUNE_HOSTNAME:-neptune}"        # so neptune.local resolves
GO2RTC_VERSION="${GO2RTC_VERSION:-v1.9.4}"

log()  { printf '\033[1;36m[neptune]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[neptune] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[neptune] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (pipe to 'sudo bash')."
command -v apt-get >/dev/null 2>&1 || die "expects Debian/Raspberry Pi OS (apt-get not found)."
case "$REPO_URL" in
  ""|*REPLACE_ME*) die "set your repo: re-run with  ... | sudo NEPTUNE_REPO=https://github.com/you/fork.git bash" ;;
esac

# ---- 1. system packages -----------------------------------------------------
log "installing system packages…"
export DEBIAN_FRONTEND=noninteractive
# avahi-daemon publishes <hostname>.local so topside has a name to fall back on when
# the fixed tether address has not come up yet. It is NOT the primary path (mDNS from
# Windows over a link-local adapter is unreliable) - the static IP below is.
PKGS="git python3 python3-venv python3-pip nginx curl ca-certificates iproute2 avahi-daemon iw wireless-tools"

# The Pi has NO INTERNET on the tether (wlan0 is never-default, eth0 has no gateway),
# and "re-run any time to update" has to keep working there. So apt is best-effort:
# if everything is already installed we carry on, and we only hard-fail when a package
# is genuinely missing and unobtainable.
missing=""
for p in $PKGS; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing="$missing $p"
done
if [ -n "$missing" ]; then
  log "need:$missing"
  apt-get update -qq || warn "apt-get update failed (no internet on the tether?) — trying the install anyway"
  if ! apt-get install -y -qq $missing; then
    die "could not install:$missing — give the Pi internet (plug eth0 into your router) and re-run"
  fi
else
  log "all system packages already present — skipping apt (works offline on the tether)"
fi

# ---- 2. service user --------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "creating service user '$SERVICE_USER'…"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# ---- 2a. tether addressing (THE way topside) --------------------------------
# A direct Ally<->Pi Ethernet run has NO DHCP SERVER. Left on DHCP the Pi ends up
# with no IPv4 at all (or a random 169.254.x.x), so topside has nothing to aim at
# and the dashboard shows "no connection" with the cable plugged in.
#
# Fix: give eth0 a FIXED address that is additive to DHCP. Plugged into a router
# it still takes a lease (so this installer can reach the internet); on the tether
# it always holds ${TETHER_IP}. Topside holds ${TETHER_IP%.*}.2.
log "pinning the tether address ${TETHER_CIDR} on ${TETHER_IFACE}…"
if command -v nmcli >/dev/null 2>&1; then
  # Reuse the existing profile for this device if there is one, so an install run
  # over Ethernet does not drop its own connection.
  CON="$(nmcli -t -f NAME,DEVICE connection show 2>/dev/null | awk -F: -v d="$TETHER_IFACE" '$2==d{print $1; exit}')"
  [ -n "$CON" ] || CON="$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-3-ethernet"{print $1; exit}')"
  if [ -n "$CON" ]; then
    log "updating NetworkManager profile '$CON'"
  else
    CON="neptune-tether"
    nmcli connection add type ethernet ifname "$TETHER_IFACE" con-name "$CON" >/dev/null 2>&1 || true
  fi
  # method=auto keeps DHCP working on a router; ipv4.addresses adds the fixed one
  # on top; may-fail=yes means the link still comes up when no DHCP answers.
  #
  # dhcp-timeout matters more than it looks: on a direct tether there is no DHCP
  # server at all, and with the default (infinite) timeout NetworkManager parks the
  # device in "connecting (getting IP configuration)" forever. Cap it so the profile
  # settles onto the fixed address instead of retrying a lease that will never come.
  nmcli connection modify "$CON" \
      ipv4.method auto \
      ipv4.addresses "$TETHER_CIDR" \
      ipv4.may-fail yes \
      ipv4.dhcp-timeout 15 \
      connection.autoconnect yes >/dev/null 2>&1 \
    || warn "could not update '$CON' — the fallback unit below still pins the address"
  nmcli connection up "$CON" >/dev/null 2>&1 || true
else
  warn "no nmcli — relying on neptune-tether.service to hold ${TETHER_IP}"
fi
# Apply immediately too, so this run can report the right address.
ip link set "$TETHER_IFACE" up 2>/dev/null || true
ip addr replace "$TETHER_CIDR" dev "$TETHER_IFACE" 2>/dev/null || true

# ---- 2b. hostname + mDNS (fallback discovery path) --------------------------
if [ "$(hostname)" != "$PI_HOSTNAME" ]; then
  log "setting hostname to '${PI_HOSTNAME}' (publishes ${PI_HOSTNAME}.local)…"
  hostnamectl set-hostname "$PI_HOSTNAME" 2>/dev/null || echo "$PI_HOSTNAME" > /etc/hostname
  grep -q "127.0.1.1[[:space:]]\+${PI_HOSTNAME}" /etc/hosts 2>/dev/null \
    || printf '127.0.1.1\t%s\n' "$PI_HOSTNAME" >> /etc/hosts
fi
systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon not available — ${PI_HOSTNAME}.local will not resolve"

# ---- 2c. camera WiFi (wlan0 joins the camera AP, never the default route) ---
# Solves the §1 routing constraint at the source: ipv4.never-default keeps the
# camera hop off the default route so the tether (eth0) stays the way topside.
# The camera's AP. Also needed TOPSIDE, in client/launch/neptune-camera-ssid.txt, so the
# handheld can scan for it and tell a dead Pi antenna from a dead camera. Change both.
CAM_SSID="${NEPTUNE_CAM_SSID:-ActionCam_b981}"
CAM_PSK="${NEPTUNE_CAM_PSK:-12345678}"
if command -v nmcli >/dev/null 2>&1; then
  log "saving camera Wi-Fi profile for '$CAM_SSID' on $CAM_IFACE (never-default, autoconnect)…"
  nmcli connection delete neptune-cam >/dev/null 2>&1 || true
  # Creating the PROFILE is what must persist — it autoconnects whenever the camera AP appears.
  # `autoconnect-retries 0` = retry forever, so a camera powered on later still gets joined.
  # `wifi.powersave 2` = DISABLE power save. The radio parking between beacons stalls the
  # RTSP pull, and topside that looks exactly like the camera going to sleep. The driver
  # re-enables it on re-association, so neptune-wifi.service re-asserts it as well.
  if nmcli connection add type wifi ifname "$CAM_IFACE" con-name neptune-cam \
       ssid "$CAM_SSID" \
       wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$CAM_PSK" \
       ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled \
       wifi.powersave 2 \
       connection.autoconnect yes connection.autoconnect-retries 0 >/dev/null; then
    log "camera Wi-Fi profile saved — auto-joins when '$CAM_SSID' is powered on"
    # Activating NOW is best-effort: it fails harmlessly if the camera AP isn't broadcasting yet.
    if nmcli connection up neptune-cam >/dev/null 2>&1; then
      log "camera AP joined now"
    else
      log "camera AP not in range yet — it will connect automatically when the camera is on"
    fi
  else
    warn "could not save the camera Wi-Fi profile on $CAM_IFACE — add '$CAM_SSID' manually"
  fi
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
  # Best-effort: on the tether there is no route to GitHub. Re-applying the config to
  # the code already on disk is still useful, so a failed fetch warns instead of
  # aborting the run. New CODE obviously needs internet; config does not.
  if git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH" 2>/dev/null; then
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
  else
    warn "could not reach $REPO_URL — keeping the code already installed"
    warn "  (no internet on the tether; plug eth0 into your router to pull new code)"
  fi
else
  log "cloning $REPO_URL ($BRANCH) → $INSTALL_DIR…"
  mkdir -p "$INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ---- 4. strip non-backend files (the Pi is backend-only, keep it lightweight) --
# The dashboard + any frontend run TOPSIDE on the ROG Ally, never on the Pi. The
# `reset --hard` above restores these on every update, so remove them each run.
for junk in client README.md; do
  if [ -e "$INSTALL_DIR/$junk" ]; then
    log "removing $junk (not needed on the backend)…"
    rm -rf "${INSTALL_DIR:?}/$junk"
  fi
done

# ---- 5. python venv + deps --------------------------------------------------
log "setting up python venv + dependencies…"
VENV="$INSTALL_DIR/api/.venv"
# --system-site-packages so the apt-installed python3-picamera2 (which is NOT
# pip-installable) is importable from inside the venv, as the README instructs.
python3 -m venv --system-site-packages "$VENV"
# Pillow is bench-only (synthetic camera) and unused on the Pi; skip it here.
grep -viE '^\s*Pillow' "$INSTALL_DIR/api/requirements.txt" > /tmp/neptune-reqs.txt
# Skip pip entirely when the venv already satisfies the requirements, so a re-run on
# the (internet-less) tether still works. sysinfo.py deliberately has no dependencies.
if "$VENV/bin/python" -c "import fastapi, uvicorn, pydantic, httpx" >/dev/null 2>&1; then
  log "python dependencies already satisfied — skipping pip (works offline on the tether)"
else
  "$VENV/bin/pip" install --quiet --upgrade pip || warn "pip self-upgrade failed (continuing)"
  "$VENV/bin/pip" install --quiet -r /tmp/neptune-reqs.txt \
    || die "could not install python dependencies — give the Pi internet and re-run"
fi
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

# ---- 7. stamp addresses into go2rtc.yaml ------------------------------------
# The WebRTC candidate must be an address the Ally can actually reach. That is the
# FIXED tether IP now, not whatever eth0 happened to hold at install time - the old
# behaviour stamped the home-LAN address (or left the literal placeholder when eth0
# had no IPv4), and ICE then failed forever with no way to re-stamp in the field.
sed -i "s#192.72.1.1#${CAMERA_IP}#g" "$INSTALL_DIR/deploy/go2rtc.yaml"    # camera RTSP host
sed -i "s#<PI_TETHER_IP>#${TETHER_IP}#g" "$INSTALL_DIR/deploy/go2rtc.yaml" # legacy placeholder
sed -i "s#^\( *- \)[0-9.]\+:8555#\1${TETHER_IP}:8555#" "$INSTALL_DIR/deploy/go2rtc.yaml"
log "go2rtc WebRTC candidate = ${TETHER_IP}:8555"
if grep -q '<PI_TETHER_IP>' "$INSTALL_DIR/deploy/go2rtc.yaml"; then
  die "go2rtc.yaml still contains <PI_TETHER_IP> — refusing to start a video plane that cannot work"
fi

# ---- 8. nginx (backend-only, PLAIN HTTP: reverse proxy only, no SPA, no TLS) -
# Two trusted devices on a sealed tether → no cert, no TLS overhead. WebRTC media
# stays DTLS-encrypted regardless; only the signaling is plain.
log "configuring nginx (plain-HTTP reverse proxy — backend only)…"
rm -rf /etc/neptune/tls 2>/dev/null || true          # drop any cert left by an older install
sed "s#/opt/neptune#${INSTALL_DIR}#g" "$INSTALL_DIR/deploy/nginx.conf" \
    > /etc/nginx/sites-available/neptune
ln -sf /etc/nginx/sites-available/neptune /etc/nginx/sites-enabled/neptune
rm -f /etc/nginx/sites-enabled/default
nginx -t

# ---- 9. systemd units -------------------------------------------------------
log "installing systemd units…"
# Copy the repo's units, but rewrite paths/ifaces to the real values here so the
# units always match this host.
for unit in neptune-api go2rtc wolfang-route neptune-tether neptune-wifi; do
  install -m 0644 "$INSTALL_DIR/deploy/systemd/${unit}.service" "/etc/systemd/system/${unit}.service"
done
# patch the route unit for the configured camera iface/IP
sed -i "s#192.72.1.1#${CAMERA_IP}#g; s#wlan0#${CAM_IFACE}#g" /etc/systemd/system/wolfang-route.service
# patch the wifi power-save unit for the configured camera iface
sed -i "s#NEPTUNE_CAM_IFACE=wlan0#NEPTUNE_CAM_IFACE=${CAM_IFACE}#" /etc/systemd/system/neptune-wifi.service
# patch the tether unit for the configured iface/address
sed -i "s#NEPTUNE_TETHER_IFACE=eth0#NEPTUNE_TETHER_IFACE=${TETHER_IFACE}#; \
        s#NEPTUNE_TETHER_CIDR=192.168.42.1/24#NEPTUNE_TETHER_CIDR=${TETHER_CIDR}#" \
    /etc/systemd/system/neptune-tether.service
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
systemctl enable neptune-tether.service neptune-wifi.service wolfang-route.service go2rtc.service neptune-api.service nginx >/dev/null 2>&1 || true
# Each subsystem is started independently and a failure in one never aborts the
# others - the dashboard is built to show them individually up or down.
systemctl restart neptune-tether.service || warn "neptune-tether failed (tether address may be unstable)"
systemctl restart neptune-wifi.service   || warn "neptune-wifi failed ($CAM_IFACE power save may stall the video)"
systemctl restart wolfang-route.service  || warn "wolfang-route failed (is $CAM_IFACE up? camera powered on?)"
systemctl restart go2rtc.service         || warn "go2rtc failed to start (video only)"
systemctl restart neptune-api.service    || warn "neptune-api failed to start"
systemctl restart nginx                  || warn "nginx failed to (re)start"

# ---- 12. report -------------------------------------------------------------
echo
log "done. service status:"
for s in neptune-tether wolfang-route go2rtc neptune-api nginx; do
  printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || echo inactive)"
done
echo
log "addresses on ${TETHER_IFACE}:"
ip -4 -o addr show "$TETHER_IFACE" 2>/dev/null | awk '{printf "  %s\n", $4}' || true
# Fail loudly if the one thing topside depends on did not stick. A silent miss here
# is exactly what produced "no connection with the cable plugged in".
if ip -4 -o addr show "$TETHER_IFACE" 2>/dev/null | grep -q "${TETHER_IP}/"; then
  log "tether address ${TETHER_IP} is UP on ${TETHER_IFACE}"
else
  warn "tether address ${TETHER_IP} is NOT on ${TETHER_IFACE} — topside will not find this Pi."
  warn "  check: nmcli connection show '${CON:-Wired connection 1}' | grep ipv4"
  warn "  and:   systemctl status neptune-tether"
fi
echo
log "this Pi is BACKEND-ONLY (plain HTTP) — the dashboard runs topside on the ROG Ally."
log "control API : http://${TETHER_IP}/api/status    system health: /api/system"
log "video       : go2rtc stream 'sub' — WebRTC candidate ${TETHER_IP}:8555"
log "topside     : run client/launch/Neptune.bat on the Ally (it finds ${TETHER_IP} by itself)"
echo
log "ON THE ALLY, once: give its Ethernet adapter the matching fixed address —"
log "  netsh interface ip set address name=\"Ethernet\" static ${TETHER_IP%.*}.2 255.255.255.0"
log "  (client/launch/tether-setup.ps1 does this for you, as Administrator)"
log "re-run this installer any time to update."
