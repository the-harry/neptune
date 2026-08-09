"""Camera → MJPEG.

Primary backend is Picamera2 (the Pi CSI camera). Off-Pi, it falls back to a
synthetic animated test pattern (needs Pillow) so /stream.mjpg — and the whole
client video path — works on a laptop. If neither is available the stream simply
produces nothing and the client shows its own NO-FEED overlay. Nothing here ever
raises into the request path.

Frames are produced on a background thread (Picamera2's encoder, or the
synthetic loop) into a thread-safe StreamingOutput; the async MJPEG generator
hands them to Starlette without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time

from config import settings

log = logging.getLogger("neptune.cam")


class StreamingOutput:
    """Thread-safe latest-frame holder with versioning so readers get fresh frames."""

    def __init__(self) -> None:
        self._frame: bytes | None = None
        self._ver = 0
        self._cond = threading.Condition()

    def write(self, buf) -> int:  # Picamera2 FileOutput / synthetic loop call this
        data = bytes(buf)
        with self._cond:
            self._frame = data
            self._ver += 1
            self._cond.notify_all()
        return len(data)

    def get_frame(self, last_ver: int, timeout: float):
        """Block until a frame newer than last_ver arrives (or timeout)."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._ver != last_ver and self._frame is not None, timeout):
                return None, last_ver
            return self._frame, self._ver


class CameraBase:
    kind = "none"

    def __init__(self) -> None:
        self.output = StreamingOutput()

    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def available(self) -> bool:
        return self.kind != "none"


class PiCamera2Camera(CameraBase):
    kind = "picamera2"

    def start(self) -> None:
        from picamera2 import Picamera2
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput

        self.picam2 = Picamera2()
        cfg = self.picam2.create_video_configuration(main={"size": (settings.cam_width, settings.cam_height)})
        self.picam2.configure(cfg)
        self.picam2.start_recording(JpegEncoder(q=settings.cam_jpeg_quality), FileOutput(self.output))
        log.info("Picamera2 streaming %dx%d", settings.cam_width, settings.cam_height)

    def stop(self) -> None:
        try:
            self.picam2.stop_recording()
        except Exception:  # noqa: BLE001
            pass


class SyntheticCamera(CameraBase):
    """Animated bench pattern (requires Pillow)."""

    kind = "synthetic"

    def start(self) -> None:
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name="synthcam", daemon=True)
        self._t.start()
        log.info("Synthetic camera streaming (bench pattern)")

    def stop(self) -> None:
        if hasattr(self, "_stop"):
            self._stop.set()

    def _loop(self) -> None:
        from PIL import Image, ImageDraw

        w, h = settings.cam_width, settings.cam_height
        period = 1.0 / max(1, settings.cam_fps)
        i = 0
        while not self._stop.is_set():
            img = Image.new("RGB", (w, h), (18, 4, 38))
            d = ImageDraw.Draw(img)
            for gx in range(0, w, 80):
                d.line([(gx, 0), (gx, h)], fill=(40, 20, 70), width=1)
            for gy in range(0, h, 80):
                d.line([(0, gy), (w, gy)], fill=(40, 20, 70), width=1)
            x = int((i * 6) % w)
            d.rectangle([x, 0, x + 4, h], fill=(180, 107, 255))
            d.text((20, 20), "NEPTUNE — BENCH CAMERA", fill=(180, 107, 255))
            d.text((20, 40), time.strftime("%H:%M:%S"), fill=(77, 255, 166))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=settings.cam_jpeg_quality)
            self.output.write(buf.getvalue())
            i += 1
            self._stop.wait(period)


def get_camera() -> CameraBase:
    import os

    choice = os.environ.get("NEPTUNE_CAM", "auto").lower()

    def try_pi():
        try:
            cam = PiCamera2Camera()
            cam.start()
            return cam
        except Exception as exc:  # noqa: BLE001
            log.warning("Picamera2 unavailable (%s)", exc)
            return None

    def try_synth():
        try:
            import PIL  # noqa: F401
        except Exception:  # noqa: BLE001
            log.warning("Pillow not installed — no bench camera; client will show NO FEED")
            return None
        cam = SyntheticCamera()
        cam.start()
        return cam

    if choice == "picamera2":
        return try_pi() or CameraBase()
    if choice == "synthetic":
        return try_synth() or CameraBase()
    if choice == "none":
        return CameraBase()
    # auto: real camera first, then bench pattern, then nothing.
    return try_pi() or try_synth() or CameraBase()


async def mjpeg_stream(cam: CameraBase):
    """Async multipart-MJPEG generator for a StreamingResponse."""
    boundary = b"--frame"
    loop = asyncio.get_event_loop()
    last = 0
    while True:
        frame, last = await loop.run_in_executor(None, cam.output.get_frame, last, 5.0)
        if frame is None:
            # keepalive gap; loop again (client also has its own retry)
            await asyncio.sleep(0.05)
            continue
        yield (
            boundary + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
        )
