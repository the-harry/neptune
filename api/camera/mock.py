"""Mock WOLFANG 4K camera — reproduces the CGI protocol, timing table, single-
threaded blocking, and the 722 error, so the whole stack runs without hardware.

Run:  python -m camera.mock   (listens on :8072 by default)
Point the CGI client at it with  WOLFANG_BASE=http://127.0.0.1:8072

Faithful to spec §3: text/plain "code\nOK\nk=v..." bodies, `Server` +
`Cache-Control: max-age=2` + `Connection: close` headers, single global lock so
concurrent requests serialize, per-operation blocking delays from §3.3c, and
`722 Invalid state` when the shutter fires in the wrong UIMode.
"""
from __future__ import annotations

import asyncio
import os
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

SERVER_HEADER = "AIT Multimedia Network Solution, UPnP/1.0 devices/1.6.19"

# Per-operation blocking times (seconds) from the measured table in §3.3c.
DELAYS = {
    "Video:capture": 2.1,
    "Video:record:start": 0.63,
    "Video:record:stop": 0.29,
    "Camera.Menu.UIMode": 1.1,
    "Playback:enter": 0.37,
    "Playback:exit:change": 0.94,
    "Playback:exit:noop": 0.18,
    "SD0:format": 0.15,
    "get": 0.03,
    "set": 0.05,
}

CAMMENU_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<menu>
  <item property="Videores"><option>4K30</option><option>2.7K30</option><option>1080P60</option><option>1080P30</option><option>720P120</option></item>
  <item property="Imageres"><option>20MP</option><option>16MP</option><option>12MP</option><option>8MP</option></item>
  <item property="AWB"><option>AUTO</option><option>DAYLIGHT</option><option>CLOUDY</option><option>FLUORESCENT1</option><option>FLUORESCENT2</option><option>FLUORESCENT3</option><option>INCANDESCENT</option></item>
</menu>
"""


class MockCamera:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()  # single-threaded server: everything serializes
        self.playback = False
        self.battery = 87
        # Exactly the FACTORY state observed in the HAR capture (spec §4) — including
        # the parts that are hostile: PowerSaving=5MIN is what powers the camera off
        # mid-dive, VideoClipTime=OFF is what makes a power-cut lose the whole file,
        # and StatusLights reads back "OF", not "OFF".
        self.menu = {
            "Camera.Menu.AWB": "AUTO", "Camera.Menu.DefMode": "VIDEO",
            "Camera.Menu.EV": "EV0", "Camera.Menu.FWversion": "0255",
            "Camera.Menu.Flicker": "50Hz", "Camera.Menu.GSensor": "OFF",
            "Camera.Menu.HDR": "OFF", "Camera.Menu.ImageRes": "20MP",
            "Camera.Menu.IsStreaming": "NO", "Camera.Menu.LCDPower": "30SEC",
            "Camera.Menu.LoopingVideo": "OFF", "Camera.Menu.MTD": "OFF",
            "Camera.Menu.PhotoBurst": "UNKNOW",
            "Camera.Menu.PowerOffDelay": "OFF", "Camera.Menu.PowerSaving": "5MIN",
            "Camera.Menu.Q-SHOT": "OFF",
            "Camera.Menu.SD0": "READY", "Camera.Menu.SoundIndicator": "ON",
            "Camera.Menu.SpotMeter": "OFF", "Camera.Menu.StatusLights": "OF",
            "Camera.Menu.TV": "NONE",
            "Camera.Menu.TVSystem": "PAL", "Camera.Menu.Timelapse": "5SEC",
            "Camera.Menu.UIMode": "VIDEO", "Camera.Menu.UpsideDown": "Normal",
            "Camera.Menu.VideoClipTime": "OFF", "Camera.Menu.VideoRes": "1080P30",
        }
        self.preview = {
            "Camera.Preview.H264.bitrate": "1200000", "Camera.Preview.H264.h": "360",
            "Camera.Preview.H264.w": "640", "Camera.Preview.MJPEG.bitrate": "4000000",
            "Camera.Preview.MJPEG.fps": "30", "Camera.Preview.MJPEG.w": "320",
            "Camera.Preview.MJPEG.h": "240", "Camera.Preview.MJPEG.status": "ACTIVE",
            "Camera.Preview.MJPEG.status.mode": "Videomode",
            "Camera.Preview.MJPEG.status.record": "Standby",
            "Camera.Preview.MJPEG.TimeStamp": "ACTIVE",
            "Camera.Preview.MJPEG.WarningMSG": "",  # non-empty => fault (e.g. "NO CARD!")
            "Camera.Preview.RTSP.av": "4", "Camera.Preview.RTSP.keepalive": "60",
            "Camera.Preview.RTSP.rtcp": "10", "Camera.Preview.RTSP.tran": "100",
            "Camera.Preview.Source.1.Camid": "front", "Camera.Preview.Source.Totals": "2",
        }
        self.recording = False
        self.remaining = 41230
        # one seeded video file
        self.files = {
            "Video": [{
                "name": "/SD/Video/FILE260803-164124-000001F.MOV",
                "size": 14487552, "fmt": "MOV", "res": "1920x1080",
                "fps": "30", "time_s": "7.1", "mtime": "2026-08-03 16:41:24",
            }],
            "Photo": [{
                "name": "/SD/Photo/FILE260803-164008-000001.JPG",
                "size": 6553600, "fmt": "jpeg", "res": "5120x3840",
                "fps": None, "time_s": None, "mtime": "2026-08-03 16:40:08",
            }],
        }

    async def sleep(self, key: str) -> None:
        await asyncio.sleep(DELAYS.get(key, 0.03))


CAM = MockCamera()


# --------------------------------------------------------------------------
# What THIS mock believes about the firmware's write names and value sets.
#
# Deliberately NOT imported from config.py. The client's name map is a set of
# GUESSES; if the mock derived its behaviour from those same guesses, every probe
# would succeed by construction and the test would prove nothing. This is an
# independent model of the device.
# --------------------------------------------------------------------------

# Ground truth — these three come from the camera's own cammenu.xml (spec §3.1),
# so they work under every policy.
_OBSERVED_WRITE_NAMES = {
    "Videores": "Camera.Menu.VideoRes",
    "Imageres": "Camera.Menu.ImageRes",
    "AWB": "Camera.Menu.AWB",
}

# Which naming convention the emulated firmware honours for everything else. The
# real answer is unknown, which is the whole point of probing:
#   short  (default) — `PowerSaving`, matching the three observed names
#   dotted           — `Camera.Menu.PowerSaving`
#   none             — neither; every menu write is accepted and silently ignored
MOCK_WRITE_NAMES = os.environ.get("MOCK_WRITE_NAMES", "short")


def _write_to_read(prop: str) -> str | None:
    """The read name a write lands on, or None if the firmware does not know it."""
    if prop in _OBSERVED_WRITE_NAMES:
        return _OBSERVED_WRITE_NAMES[prop]
    if prop in CAM.preview:                      # preview props are written dotted
        return prop
    if MOCK_WRITE_NAMES == "short":
        cand = f"Camera.Menu.{prop}"
        return cand if cand in CAM.menu else None
    if MOCK_WRITE_NAMES == "dotted":
        return prop if prop in CAM.menu else None
    return None


# Valid values. The first three are from cammenu.xml; the rest are UNKNOWN on the
# real device (spec §7), so these are arbitrary choices that exist to exercise the
# client's value-walking — NOT assertions about what the firmware accepts.
# VideoClipTime deliberately refuses 3MIN so the preferred value gets a 722 and the
# client has to fall back to 5MIN.
VALUE_SETS = {
    "Camera.Menu.VideoRes": {"4K30", "2.7K30", "1080P60", "1080P30", "720P120"},
    "Camera.Menu.ImageRes": {"20MP", "16MP", "12MP", "8MP"},
    "Camera.Menu.AWB": {"AUTO", "DAYLIGHT", "CLOUDY", "FLUORESCENT1", "FLUORESCENT2",
                        "FLUORESCENT3", "INCANDESCENT"},
    "Camera.Menu.VideoClipTime": {"OFF", "5MIN", "10MIN"},
    "Camera.Menu.PowerSaving": {"OFF", "3MIN", "5MIN", "10MIN"},
    "Camera.Menu.EV": {"EV0"},
}

# The camera reports StatusLights as "OF", not "OFF" (spec §4). A client that
# compares the read-back literally scores a successful write as a silent no-op.
READBACK_QUIRKS = {
    "Camera.Menu.StatusLights": lambda v: "OF" if v.strip().upper() == "OFF" else v,
}


def _plain(code: int, text: str, pairs: list[str] | None = None) -> Response:
    body = f"{code}\n{text}\n" + ("\n".join(pairs) + "\n" if pairs else "")
    return Response(
        body, media_type="text/plain",
        headers={"Server": SERVER_HEADER, "Cache-Control": "max-age=2", "Connection": "close"},
    )


def _match(prop: str) -> list[str]:
    """Emulate `get` with wildcards / dotted prefixes → matching k=v pairs."""
    src = {**CAM.menu, **CAM.preview,
           "Camera.Battery.Level": str(CAM.battery),
           "Camera.Capture.Remaining": str(CAM.remaining)}
    if prop == "*":
        return [f"{k}={v}" for k, v in src.items()]
    if prop.endswith(".*") or prop.endswith("*"):
        pre = prop.rstrip("*").rstrip(".")
        return [f"{k}={v}" for k, v in src.items() if k == pre or k.startswith(pre + ".")]
    if prop in src:
        return [f"{prop}={src[prop]}"]
    return []


async def config_cgi(request: Request) -> Response:
    q = request.query_params
    action = q.get("action", "")
    async with CAM.lock:  # serialize — reproduce single-threaded server
        if action == "get":
            await CAM.sleep("get")
            pairs = _match(q.get("property", ""))
            return _plain(0, "OK", pairs)

        if action == "set":
            return await _handle_set(q)

        if action == "del":
            await CAM.sleep("set")
            path = q.get("property", "")            # $SD$Video$FILE...
            name = path.replace("$", "/")
            for coll in CAM.files.values():
                before = len(coll)
                coll[:] = [f for f in coll if f["name"] != name]
                if len(coll) != before:
                    return _plain(0, "OK")
            return _plain(0, "OK")  # firmware is forgiving on missing files

        if action == "dir":
            await CAM.sleep("get")
            return _dir(q)

        return _plain(720, "Invalid action")


async def _handle_set(q) -> Response:
    prop = q.get("property", "")
    val = q.get("value", "")

    if prop == "Playback":
        if val == "enter":
            noop = CAM.playback
            CAM.playback = True
            await CAM.sleep("Playback:enter" if not noop else "Playback:exit:noop")
            return _plain(0, "OK")
        if val == "exit":
            changed = CAM.playback
            CAM.playback = False
            await CAM.sleep("Playback:exit:change" if changed else "Playback:exit:noop")
            return _plain(0, "OK")
        return _plain(722, "Invalid state")

    if prop == "Camera.Menu.UIMode":
        await CAM.sleep("Camera.Menu.UIMode")
        CAM.menu["Camera.Menu.UIMode"] = val
        return _plain(0, "OK")

    if prop == "Video":
        mode = CAM.menu["Camera.Menu.UIMode"]
        if val == "record":
            if mode != "VIDEO":                      # spec §4.3 rule 1
                return _plain(722, "Invalid state")
            await CAM.sleep("Video:record:start" if not CAM.recording else "Video:record:stop")
            CAM.recording = not CAM.recording        # TOGGLE (spec §4.3 rule 2)
            CAM.preview["Camera.Preview.MJPEG.status.record"] = "Recording" if CAM.recording else "Standby"
            return _plain(0, "OK")
        if val == "capture":
            if mode != "CAMERA":
                return _plain(722, "Invalid state")
            await CAM.sleep("Video:capture")
            ts = time.strftime("%y%m%d-%H%M%S")
            CAM.files["Photo"].insert(0, {
                "name": f"/SD/Photo/FILE{ts}-000009.JPG", "size": 6553600,
                "fmt": "jpeg", "res": "5120x3840", "fps": None, "time_s": None,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            CAM.remaining -= 1
            return _plain(0, "OK")
        return _plain(722, "Invalid state")

    if prop == "SD0" and val == "format":
        await CAM.sleep("SD0:format")               # returns fast, formats in bg
        for coll in CAM.files.values():
            coll.clear()
        return _plain(0, "OK")

    if prop == "TimeSettings":
        await CAM.sleep("set")
        return _plain(0, "OK")

    # Menu writes (asymmetric names) + preview tweaks + best-effort Camera.Menu.* writes.
    await CAM.sleep("set")
    read_name = _write_to_read(prop)

    if prop in ("Camera.Preview.H264.w", "Camera.Preview.H264.h"):
        # firmware 0255 may ignore the preview bump — emulate: accept but DON'T change
        return _plain(0, "OK")

    if read_name is None:
        # THE FAILURE MODE THAT MATTERS: an unrecognised property name is accepted
        # with code 0 and silently does nothing. A client that trusts the response
        # code believes it configured the camera. Anything that writes must verify
        # by re-reading.
        return _plain(0, "OK")

    allowed = VALUE_SETS.get(read_name)
    if allowed is not None and val not in allowed:
        return _plain(722, "Invalid state")          # parsed the property, refused the value

    store = CAM.menu if read_name in CAM.menu else CAM.preview
    store[read_name] = READBACK_QUIRKS.get(read_name, lambda v: v)(val)
    return _plain(0, "OK")


def _dir(q) -> Response:
    prop = q.get("property", "Normal")
    coll = CAM.files["Video"] if prop == "Normal" else CAM.files["Photo"]
    try:
        frm = int(q.get("from", "0")); cnt = int(q.get("count", "100"))
    except ValueError:
        frm, cnt = 0, 100
    page = coll[frm:frm + cnt]
    rows = []
    for f in page:
        if f["fps"] is not None:
            fmt = f'<format size="{f["res"]}" fps="{f["fps"]}" time="{f["time_s"]}">{f["fmt"]}</format>'
        else:
            fmt = f'<format size="{f["res"]}">{f["fmt"]}</format>'
        rows.append(
            f"<file>\n    <name>{f['name']}</name>\n    {fmt}\n"
            f"    <size>{f['size']}</size>\n    <attr>RW</attr>\n    <time>{f['mtime']}</time>\n</file>"
        )
    root = prop
    xml = (f'<?xml version="1.0" encoding="UTF-8" ?>\n<{root}>\n'
           + "\n".join(rows) + (f"\n<amount>{len(coll)}</amount>\n</{root}>\n"))
    return Response(xml, media_type="text/xml",
                    headers={"Server": SERVER_HEADER, "Connection": "close"})


async def download(request: Request) -> Response:
    # Serve /SD/... with Range support (spec §5.2). Route strips the /SD/ literal,
    # so re-add it to match the stored "/SD/Video/..." names.
    path = "/SD/" + request.path_params["path"]
    entry = None
    for coll in CAM.files.values():
        for f in coll:
            if f["name"] == path:
                entry = f
    if entry is None:
        return Response("not found", status_code=404, headers={"Connection": "close"})
    total = entry["size"]
    ctype = "video/quicktime" if entry["fmt"] == "MOV" else "image/jpeg"
    # deterministic pseudo-content
    blob = (b"NEPTUNE-MOCK-MEDIA;" * ((total // 19) + 1))[:total]
    rng = request.headers.get("range")
    async with CAM.lock:
        await asyncio.sleep(0.02)
    if rng and rng.startswith("bytes="):
        spec = rng.split("=", 1)[1]
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total - 1
        chunk = blob[start:end + 1]
        return Response(chunk, status_code=206, media_type=ctype, headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes", "Content-Length": str(len(chunk)),
            "Server": SERVER_HEADER, "Connection": "close",
        })
    return Response(blob, media_type=ctype, headers={
        "Accept-Ranges": "bytes", "Content-Length": str(total),
        "Server": SERVER_HEADER, "Connection": "close",
    })


async def thumb(request: Request) -> Response:
    # 1x1 JPEG stand-in (spec §5.2 thumbnails return JPEG).
    jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300"
        "080606070605080707070909080a0c140d0c0b0b0c1912130f14"
        "1d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434"
        "1f27393d38323c2e333432ffc0000b080001000101011100ffc4"
        "001f0000010501010101010100000000000000000102030405060708090a0b"
        "ffc400b5100002010303020403050504040000017d01020300041105122131"
        "41061351610722711432819112a1082342b1c11552d1f02433627282090a16"
        "1718191a25262728292a3435363738393a434445464748494a53545556575859"
        "5a636465666768696a737475767778797a838485868788898a929394959697"
        "98999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2"
        "d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00"
        "0c03010002110311003f00fbfa28a2803fffd9"
    )
    return Response(jpeg, media_type="image/jpeg",
                    headers={"Server": SERVER_HEADER, "Connection": "close"})


async def cammenu(request: Request) -> Response:
    return Response(CAMMENU_XML, media_type="text/xml",
                    headers={"Server": SERVER_HEADER, "Connection": "close"})


app = Starlette(routes=[
    Route("/cgi-bin/Config.cgi", config_cgi),
    Route("/cammenu.xml", cammenu),
    Route("/SD/{path:path}", download),
    Route("/thumb/{path:path}", thumb),
])


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WOLFANG_MOCK_PORT", "8072"))
    # workers=1 (default) — the real camera is single-threaded; the global lock
    # inside handlers reproduces its serialization.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
