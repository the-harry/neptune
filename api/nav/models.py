"""Pydantic models for the navigation subsystem (spec §4, §5, §8, §9)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SensorSample(BaseModel):
    """One tick of raw sensor input (from the sim or real hardware)."""
    t: float                          # seconds since dive start
    heading_deg: float                # BNO085 fused yaw (0=N, 90=E)
    depth_m: float                    # MS5837 — MEASURED, never integrated (§2.4)
    throttle: float                   # -1..1 commanded
    encoder_m: float = 0.0            # tether payout (cumulative) — an UPPER bound (§5.5)
    mag_cal: int = 3                  # IMU mag calibration status 0..3 (§5.6); <2 = suspect
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


class Origin(BaseModel):
    """Captured atomically (§4.4): lat/lon/accuracy/heading0 in one 'set origin'."""
    lat: float
    lon: float
    accuracy: float = Field(ge=0)     # metres — floor on the whole track's accuracy (§4.2)
    heading_deg: float = 0.0          # heading0, from the IMU on the surface (§4.4)
    source: Literal["phone", "map_tap", "device", "manual"] = "phone"
    t: Optional[float] = None          # capture timestamp (epoch ms), from the client (§2)


class Adjustment(BaseModel):
    """Post-hoc translate+rotate of a track (§4.5). Applied to output, raw log untouched."""
    dx_m: float = 0.0
    dy_m: float = 0.0
    rotation_deg: float = 0.0


class FlowVector(BaseModel):
    """Constant current, entered at launch (§5.4)."""
    bearing_deg: float = 0.0
    speed_ms: float = 0.0


class NavState(BaseModel):
    """Broadcast to the SPA map at broadcast_hz."""
    t: float
    lat: float
    lon: float
    depth_m: float
    heading_deg: float
    x_m: float                        # metres east of origin
    y_m: float                        # metres north of origin
    raw_lat: float                    # un-snapped estimate (rendered faint, §5.7)
    raw_lon: float
    snapped: bool
    snap_offset_m: float = 0.0        # raw↔snapped divergence = the drift indicator (§5.7)
    range_m: float                    # straight-line distance from origin
    payout_m: float                   # tether payout bound
    confidence: float = 1.0           # drops when clamped by tether / mag bad / snap far
    mag_cal: int = 3
    speed_ms: float = 0.0
    has_origin: bool = True


class ReadinessItem(BaseModel):
    step: str
    ok: bool
    detail: str = ""


class ReadinessResult(BaseModel):
    passed: bool
    items: list[ReadinessItem]
