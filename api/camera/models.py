"""Pydantic models for the camera control API (spec §7.1)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CgiError(Exception):
    """Non-zero CGI status (e.g. 722 Invalid state)."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"CGI {code}: {message}")


class CameraUnavailable(Exception):
    """Circuit breaker open / camera unreachable."""


class Status(BaseModel):
    battery: Optional[int] = None          # 0-100
    recording: bool = False                # from polled Camera.Preview.MJPEG.status.record
    record_raw: str = ""                   # raw value ("Standby" | recording state)
    mode: str = ""                         # Camera.Menu.UIMode (VIDEO | CAMERA)
    sd: str = ""                           # Camera.Menu.SD0 (READY | ...)
    warning: str = ""                      # Camera.Preview.MJPEG.WarningMSG — primary fault channel
    remaining: Optional[int] = None        # Camera.Capture.Remaining
    is_streaming: str = ""                 # Camera.Menu.IsStreaming
    video_res: str = ""                    # Camera.Menu.VideoRes  (quality)
    awb: str = ""                          # Camera.Menu.AWB       (white balance / "light mode")
    image_res: str = ""                    # Camera.Menu.ImageRes  (still quality)
    ev: str = ""                           # Camera.Menu.EV        (exposure)
    degraded: bool = False                 # circuit breaker open


class MenuOption(BaseModel):
    property: str                          # WRITE name (e.g. "Videores")
    read_property: str                     # READ name (e.g. "Camera.Menu.VideoRes")
    options: list[str]
    current: Optional[str] = None


class FileEntry(BaseModel):
    name: str                              # "/SD/Video/FILE....MOV"
    kind: str                              # "video" | "photo"
    size: int                              # bytes
    resolution: str = ""                   # "1920x1080"
    fps: Optional[float] = None            # video only
    duration: Optional[float] = None       # seconds, video only
    time: str = ""                         # "2026-08-03 16:41:24"


class RecordState(BaseModel):
    recording: bool
    record_raw: str
    changed: bool                          # did the polled state actually flip?


class PreflightCheck(BaseModel):
    step: str
    ok: bool
    detail: str = ""


class PreflightResult(BaseModel):
    passed: bool
    checks: list[PreflightCheck]
