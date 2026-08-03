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


class PingMsg(BaseModel):
    type: Literal["ping"]


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
    cpu_c: float
    ram_pct: int
    disk_gb: float
    mock: bool


class Alarm(BaseModel):
    type: Literal["alarm"] = "alarm"
    name: str


class Pong(BaseModel):
    type: Literal["pong"] = "pong"
