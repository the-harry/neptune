"""WebSocket message contract (Pydantic) — the single source of truth for what
the client and server send each other over /ws/control.

Inbound (client -> server) is a discriminated union on `type`; parse with
`parse_inbound()`, which returns None (and logs) on anything malformed so a bad
frame can never crash the socket. Outbound (server -> client) models are dumped
to dicts by the app.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

log = logging.getLogger("neptune.proto")

# ---- inbound: client -> server -------------------------------------------

class ControlMsg(BaseModel):
    type: Literal["control"]
    throttle: float = 0.0   # -1..1 (clamped on apply)
    steer: float = 0.0      # -1..1


class CameraMsg(BaseModel):
    type: Literal["camera"]
    pan: float = 0.0        # -1..1
    tilt: float = 0.0       # -1..1


class BallastMsg(BaseModel):
    type: Literal["ballast"]
    cmd: Literal["fill", "empty", "hold"]


class CommandMsg(BaseModel):
    type: Literal["command"]
    name: str
    # arm|disarm|stop|surface|ballast_home carry no value; magnet/light_* carry
    # bool; *_level carry float; dropweight carries "release".
    value: Optional[Union[bool, float, str]] = None
    c_id: Optional[str] = None      # correlation id (§3) — carried through every stage, echoed in the ack


class PingMsg(BaseModel):
    type: Literal["ping"]
    t1: Optional[float] = None      # client monotonic ms at send (§2 SNTP) — echoed back in the pong


Inbound = Annotated[
    Union[ControlMsg, CameraMsg, BallastMsg, CommandMsg, PingMsg],
    Field(discriminator="type"),
]
_inbound_adapter: TypeAdapter = TypeAdapter(Inbound)

# Command names the server understands (anything else is logged + ignored).
COMMAND_NAMES = frozenset({
    "arm", "disarm", "stop", "surface", "magnet", "ballast_home",
    "light_green", "light_white", "light_green_level", "light_white_level",
    "dropweight",
})


def parse_inbound(raw: str | bytes):
    """Validate a raw WS frame → a message model, or None if malformed."""
    try:
        return _inbound_adapter.validate_json(raw)
    except ValidationError as exc:
        log.debug("dropped malformed frame: %s", exc.errors()[:1])
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("dropped unparseable frame: %s", exc)
        return None


# ---- outbound: server -> client ------------------------------------------

class Telemetry(BaseModel):
    type: Literal["telemetry"] = "telemetry"
    seq: Optional[int] = None       # monotonically increasing frame number (§4 — client gap detection)
    t: Optional[float] = None       # Pi monotonic ms at send (§2/§4 — staleness/max_age)
    armed: bool
    left: float
    right: float
    ballast_level: float
    ballast_target: float
    depth: float
    pressure: float
    heading: float
    heading_card: str
    magnet: bool
    light_green: bool
    light_white: bool
    light_green_level: float
    light_white_level: float
    leak: bool
    leak_state: str
    battery_v: float
    signal: int
    link_ms: Optional[int] = None
    # --- Pi system health (REAL readings; see api/sysinfo.py) ------------------
    # All Optional on purpose: None means "could not read this probe" and renders
    # as "--" topside. A real 0 (e.g. an idle CPU) stays a 0 and is never faked.
    cpu_c: Optional[float] = None
    cpu_pct: Optional[float] = None
    ram_pct: Optional[float] = None
    disk_gb: Optional[float] = None
    uptime_s: Optional[float] = None
    net_tether_up: Optional[bool] = None
    net_tether_mbps: Optional[int] = None
    net_cam_up: Optional[bool] = None
    net_cam_signal: Optional[float] = None
    # True only when the VEHICLE hardware is simulated. Pi metrics above are
    # always real regardless of this flag.
    mock: bool


class Alarm(BaseModel):
    type: Literal["alarm"] = "alarm"
    name: str


class Pong(BaseModel):
    type: Literal["pong"] = "pong"
    t1: Optional[float] = None      # echoed client send time
    t2: Optional[float] = None      # Pi monotonic ms at receive  (§2 SNTP)
    t3: Optional[float] = None      # Pi monotonic ms at send


class Ack(BaseModel):
    """Command acknowledgement (§3) — closes the correlation loop back to the client."""
    type: Literal["ack"] = "ack"
    c_id: Optional[str] = None
    name: str
    ok: bool
    reason: Optional[str] = None
