"""Real Pi system health — hardware and network, straight from the kernel.

Deliberately dependency-free: everything here comes from /proc, /sys, os.statvfs
or a small, cached subprocess call. psutil is not required (and is not used), so
this works on any Raspberry Pi OS image without a wheel build.

Two tiers, because they cost very different amounts:

  fast()   pure file reads (microseconds). Safe to call every second straight
           from the asyncio loop. CPU/mem/disk/uptime/net counters + rates.
  deep()   shells out (vcgencmd, systemctl, iw) and does a TCP reachability
           probe. Runs in a worker thread on a slow cadence and is cached.

EVERY probe degrades on its own: a failure yields None for that field and never
raises, so one missing sensor can never blank the whole reading. `None` means
"could not read", which the client renders as "--" — it is never conflated with
a real zero. That distinction matters: "CPU 0 deg C" is a lie, "CPU --" is honest.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger("neptune.sysinfo")

# Interfaces we care about. eth0 is the tether (the way topside), wlan0 joins the
# camera's AP. Overridable so the same code runs on odd hardware.
TETHER_IFACE = os.environ.get("NEPTUNE_TETHER_IFACE", "eth0")
CAM_IFACE = os.environ.get("NEPTUNE_CAM_IFACE", "wlan0")
CAMERA_IP = os.environ.get("NEPTUNE_CAMERA_IP", "192.72.1.1")
SERVICES = ("neptune-api", "go2rtc", "nginx", "wolfang-route")


def _read(path: str) -> str | None:
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except Exception:  # noqa: BLE001 — a missing probe is normal, not an error
        return None


def _read_int(path: str) -> int | None:
    v = _read(path)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
def cpu_temp_c() -> float | None:
    """CPU temperature in degrees C.

    Pi OS exposes the SoC sensor as thermal_zone0 (type `cpu-thermal`), in
    millidegrees. Scan the zones rather than trusting zone0's position.
    """
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        kind = _read(str(zone / "type")) or ""
        milli = _read_int(str(zone / "temp"))
        if milli is None:
            continue
        if "cpu" in kind.lower() or "soc" in kind.lower() or zone.name == "thermal_zone0":
            return round(milli / 1000.0, 1)
    return None


_last_cpu: tuple[float, float] | None = None  # (busy_jiffies, total_jiffies)


def cpu_pct() -> float | None:
    """Busy percentage since the previous call, from /proc/stat.

    The first call has no baseline and returns None rather than a fabricated 0.
    """
    global _last_cpu
    line = _read("/proc/stat")
    if not line:
        return None
    first = line.split("\n", 1)[0].split()
    if len(first) < 5 or first[0] != "cpu":
        return None
    try:
        vals = [float(x) for x in first[1:]]
    except ValueError:
        return None
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)   # idle + iowait
    total = sum(vals)
    busy = total - idle
    prev, _last_cpu = _last_cpu, (busy, total)
    if prev is None:
        return None
    d_busy, d_total = busy - prev[0], total - prev[1]
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * d_busy / d_total)), 1)


def load_avg() -> list[float] | None:
    v = _read("/proc/loadavg")
    if not v:
        return None
    try:
        return [float(x) for x in v.split()[:3]]
    except ValueError:
        return None


def cpu_mhz() -> int | None:
    khz = _read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    return round(khz / 1000) if khz else None


def cpu_count() -> int | None:
    try:
        return os.cpu_count()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Memory / disk / uptime
# ---------------------------------------------------------------------------
def mem() -> dict[str, float | int | None]:
    raw = _read("/proc/meminfo")
    if not raw:
        return {"total_mb": None, "used_mb": None, "pct": None}
    kv: dict[str, int] = {}
    for line in raw.split("\n"):
        parts = line.split(":")
        if len(parts) == 2:
            try:
                kv[parts[0]] = int(parts[1].strip().split()[0])   # kB
            except (ValueError, IndexError):
                pass
    total = kv.get("MemTotal")
    avail = kv.get("MemAvailable")
    if not total:
        return {"total_mb": None, "used_mb": None, "pct": None}
    if avail is None:                                            # very old kernels
        avail = kv.get("MemFree", 0) + kv.get("Cached", 0) + kv.get("Buffers", 0)
    used = total - avail
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "pct": round(100.0 * used / total, 1),
    }


def swap() -> dict[str, float | int | None]:
    raw = _read("/proc/meminfo") or ""
    kv: dict[str, int] = {}
    for line in raw.split("\n"):
        parts = line.split(":")
        if len(parts) == 2 and parts[0] in ("SwapTotal", "SwapFree"):
            try:
                kv[parts[0]] = int(parts[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    total = kv.get("SwapTotal")
    if not total:
        return {"total_mb": 0, "used_mb": 0, "pct": 0.0}
    used = total - kv.get("SwapFree", 0)
    return {"total_mb": round(total / 1024), "used_mb": round(used / 1024),
            "pct": round(100.0 * used / total, 1)}


def disk(path: str = "/") -> dict[str, float | None]:
    try:
        st = os.statvfs(path)
    except Exception:  # noqa: BLE001
        return {"free_gb": None, "total_gb": None, "pct": None}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return {"free_gb": None, "total_gb": None, "pct": None}
    return {
        "free_gb": round(free / 1e9, 1),
        "total_gb": round(total / 1e9, 1),
        "pct": round(100.0 * (total - free) / total, 1),
    }


def uptime_s() -> float | None:
    v = _read("/proc/uptime")
    if not v:
        return None
    try:
        return round(float(v.split()[0]), 1)
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def _iface_addrs(iface: str) -> dict[str, list[str]]:
    """IPv4/IPv6 addresses without shelling out to `ip`.

    IPv4 comes from a SIOCGIFADDR ioctl; IPv6 from /proc/net/if_inet6.
    """
    v4: list[str] = []
    v6: list[str] = []
    try:
        import fcntl
        import struct

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = struct.pack("256s", iface.encode()[:15])
            addr = fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24]   # SIOCGIFADDR
            v4.append(socket.inet_ntoa(addr))
        finally:
            s.close()
    except Exception:  # noqa: BLE001 — no v4 address assigned is a normal state
        pass

    raw = _read("/proc/net/if_inet6") or ""
    for line in raw.split("\n"):
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == iface:
            hexa = parts[0]
            try:
                grouped = ":".join(hexa[i:i + 4] for i in range(0, 32, 4))
                v6.append(socket.inet_ntop(socket.AF_INET6, socket.inet_pton(socket.AF_INET6, grouped)))
            except Exception:  # noqa: BLE001
                pass
    return {"v4": v4, "v6": v6}


_last_net: dict[str, tuple[float, int, int]] = {}   # iface -> (t, rx, tx)


def iface(name: str) -> dict:
    """Everything the kernel knows about one interface, plus throughput."""
    base = f"/sys/class/net/{name}"
    if not os.path.isdir(base):
        return {"name": name, "present": False, "up": False}

    operstate = _read(f"{base}/operstate")          # up | down | unknown
    carrier = _read_int(f"{base}/carrier")          # 1 = cable/assoc present
    speed = _read_int(f"{base}/speed")              # Mb/s, -1 when unknown
    mac = _read(f"{base}/address")
    rx = _read_int(f"{base}/statistics/rx_bytes")
    tx = _read_int(f"{base}/statistics/tx_bytes")

    rx_bps = tx_bps = None
    now = time.monotonic()
    if rx is not None and tx is not None:
        prev = _last_net.get(name)
        _last_net[name] = (now, rx, tx)
        if prev:
            dt = now - prev[0]
            if dt > 0.2:                            # ignore jittery sub-tick deltas
                rx_bps = max(0, round((rx - prev[1]) / dt))
                tx_bps = max(0, round((tx - prev[2]) / dt))

    addrs = _iface_addrs(name)
    return {
        "name": name,
        "present": True,
        "up": operstate == "up",
        "operstate": operstate,
        "carrier": (bool(carrier) if carrier is not None else None),
        "speed_mbps": (speed if (speed is not None and speed > 0) else None),
        "mac": mac,
        "ipv4": addrs["v4"],
        "ipv6": addrs["v6"],
        "rx_bytes": rx,
        "tx_bytes": tx,
        "rx_bps": rx_bps,
        "tx_bps": tx_bps,
    }


def wifi(name: str = CAM_IFACE) -> dict:
    """Association state + signal for a wireless interface, from /proc/net/wireless."""
    out: dict = {"associated": False, "signal_dbm": None, "quality": None, "ssid": None}
    raw = _read("/proc/net/wireless") or ""
    for line in raw.split("\n"):
        if line.strip().startswith(name + ":"):
            parts = line.split()
            try:
                out["quality"] = float(parts[2].rstrip("."))
                out["signal_dbm"] = float(parts[3].rstrip("."))
                out["associated"] = True
            except (ValueError, IndexError):
                pass
    return out


# ---------------------------------------------------------------------------
# Slow probes (subprocess / network round-trip) — cached
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 3.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — tool missing or hung
        return None


def throttled() -> dict | None:
    """Undervoltage / thermal throttling flags from vcgencmd.

    Under-voltage on a Pi 3 shows up as random freezes and dropped Ethernet, so
    this is worth surfacing rather than hiding.
    """
    out = _run(["vcgencmd", "get_throttled"])
    if not out or "=" not in out:
        return None
    try:
        bits = int(out.split("=")[1], 0)
    except (ValueError, IndexError):
        return None
    return {
        "raw": hex(bits),
        "undervoltage_now": bool(bits & 0x1),
        "throttled_now": bool(bits & 0x4),
        "undervoltage_since_boot": bool(bits & 0x10000),
        "throttled_since_boot": bool(bits & 0x40000),
    }


def services(units: tuple[str, ...] = SERVICES) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in units:
        r = _run(["systemctl", "is-active", u], timeout=2.0)
        out[u] = r if r else "inactive"
    return out


def wifi_ssid(name: str = CAM_IFACE) -> str | None:
    out = _run(["iwgetid", "-r", name], timeout=2.0)
    if out:
        return out
    out = _run(["iw", "dev", name, "link"], timeout=2.0)
    if out:
        for line in out.split("\n"):
            if "SSID:" in line:
                return line.split("SSID:", 1)[1].strip()
    return None


def tcp_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def model() -> str | None:
    v = _read("/proc/device-tree/model")
    return v.replace("\x00", "").strip() if v else None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def fast() -> dict:
    """Cheap, file-only snapshot. Safe on the hot path."""
    return {
        "t": round(time.time(), 3),
        "cpu": {
            "temp_c": cpu_temp_c(),
            "pct": cpu_pct(),
            "load": load_avg(),
            "mhz": cpu_mhz(),
            "cores": cpu_count(),
        },
        "mem": mem(),
        "swap": swap(),
        "disk": disk(),
        "uptime_s": uptime_s(),
        "net": {
            "tether": iface(TETHER_IFACE),
            "camera": {**iface(CAM_IFACE), "wifi": wifi(CAM_IFACE)},
        },
    }


class DeepProbe:
    """Background refresher for the probes that are too slow for the hot path."""

    def __init__(self, period_s: float = 10.0) -> None:
        self.period_s = period_s
        self.data: dict = {"services": {}, "throttled": None, "ssid": None,
                           "camera_reachable": None, "model": None}
        self._task: asyncio.Task | None = None

    def _collect(self) -> dict:
        return {
            "services": services(),
            "throttled": throttled(),
            "ssid": wifi_ssid(),
            "camera_reachable": tcp_reachable(CAMERA_IP, 554),   # RTSP
            "model": model(),
        }

    async def _loop(self) -> None:
        while True:
            try:
                self.data = await asyncio.get_running_loop().run_in_executor(None, self._collect)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never kill the refresher
                log.warning("deep probe failed: %s", exc)
            await asyncio.sleep(self.period_s)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None


def snapshot(deep: DeepProbe | None = None) -> dict:
    """Full system health: fast probes plus the latest cached deep results."""
    out = fast()
    out["deep"] = deep.data if deep else {}
    return out


def telemetry_fields(snap: dict | None = None) -> dict:
    """The compact subset carried on every telemetry frame.

    None is preserved end-to-end so the dashboard can show '--' rather than a
    plausible-looking zero.
    """
    s = snap or fast()
    net = s.get("net", {})
    tether = net.get("tether", {})
    cam = net.get("camera", {})
    return {
        "cpu_c": s["cpu"]["temp_c"],
        "cpu_pct": s["cpu"]["pct"],
        "ram_pct": s["mem"]["pct"],
        "disk_gb": s["disk"]["free_gb"],
        "uptime_s": s.get("uptime_s"),
        "net_tether_up": tether.get("up"),
        "net_tether_mbps": tether.get("speed_mbps"),
        "net_cam_up": cam.get("up"),
        "net_cam_signal": (cam.get("wifi") or {}).get("signal_dbm"),
    }
