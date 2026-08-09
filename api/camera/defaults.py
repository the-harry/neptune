"""Camera defaults for a sealed-hull ROV — probed, never assumed.

The CGI protocol is reverse-engineered, and for almost everything worth setting the
**valid value set is unknown** (spec §7). A wrong value returns `722`, or — worse —
is accepted and silently ignored. The camera also uses **asymmetric names**: you
write `Videores` and read `Camera.Menu.VideoRes`, and for most of the properties
below the write name is an educated guess, not an observation.

That combination is exactly how the old `preflight()` check managed to pass forever
while doing nothing: it wrote `PowerSaving`, read back a property of that name,
got `None` because the real name is `Camera.Menu.PowerSaving`, and treated `None`
as success.

So nothing here is written blind:

  * each setting carries **candidate write names** and **candidate values**, in
    preference order;
  * every attempt is verified by re-reading the property;
  * whatever actually worked is cached per **firmware version**, so the probe costs
    a handful of CGI calls once rather than on every boot.

This module is pure data and pure functions. The applier lives in `service.py`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("neptune.cam.defaults")

# Tiers — what is lost if the setting is left at its factory value.
CRITICAL = "critical"  # a dive fails, or footage is unrecoverable
QUALITY = "quality"  # image quality in turbid, low-light water
HULL = "hull"  # consequences of being sealed in a dry box


@dataclass(frozen=True)
class Setting:
    key: str  # stable id, used as the capability-cache key
    read: str  # how it reads back (`Camera.Menu.*`)
    writes: tuple[str, ...]  # candidate WRITE names, in order
    values: tuple[str, ...]  # candidate VALUES, in preference order
    tier: str
    why: str
    hot: bool = True  # safe to assert while the vehicle may be under way?


# `hot=False` means the write is slow and/or blanks the video. Every `UIMode`-class
# operation stalls the camera's single-threaded server for ~1.1 s, and the RTSP
# stream shares it — that is a second of blind piloting. Cold settings are applied
# at connect and never re-asserted by the guard loop while a dive is live.

SETTINGS: tuple[Setting, ...] = (
    # ---- critical: leaving these at factory loses the dive or the footage -----
    Setting(
        "power_saving",
        "Camera.Menu.PowerSaving",
        ("PowerSaving", "Camera.Menu.PowerSaving"),
        ("OFF",),
        CRITICAL,
        "Factory 5MIN powers the camera off mid-dive. The symptom is indistinguishable "
        "from a tether fault, so it gets misdiagnosed as one.",
    ),
    Setting(
        "power_off_delay",
        "Camera.Menu.PowerOffDelay",
        ("PowerOffDelay", "Camera.Menu.PowerOffDelay"),
        ("OFF",),
        CRITICAL,
        "Same class of failure as PowerSaving, on a separate timer.",
    ),
    Setting(
        "video_clip_time",
        "Camera.Menu.VideoClipTime",
        ("VideoClipTime", "Camera.Menu.VideoClipTime"),
        ("3MIN", "5MIN", "2MIN", "1MIN", "10MIN"),
        CRITICAL,
        "OFF means one continuous file, and a .MOV still being written when power is cut "
        "is UNRECOVERABLE. Segmenting caps the loss at one segment. On a battery sub with "
        "a hard-kill risk this is the highest-value setting on the camera.",
    ),
    Setting(
        "mtd",
        "Camera.Menu.MTD",
        ("MTD", "Camera.Menu.MTD"),
        ("OFF",),
        CRITICAL,
        "Motion-triggered recording. Drifting particulate underwater would trigger it " "continuously.",
    ),
    Setting(
        "gsensor",
        "Camera.Menu.GSensor",
        ("GSensor", "Camera.Menu.GSensor"),
        ("OFF",),
        CRITICAL,
        "Impact-triggered file locking. The sub bumps things routinely; locked files " "accumulate and fill the card.",
    ),
    Setting(
        "def_mode",
        "Camera.Menu.DefMode",
        ("DefMode", "Camera.Menu.DefMode"),
        ("VIDEO",),
        CRITICAL,
        "Boot straight into VIDEO so RTSP is live after a camera reboot without anyone "
        "topside having to drive a mode change first.",
    ),
    # ---- quality: turbid, low-light water ------------------------------------
    Setting(
        "videores",
        "Camera.Menu.VideoRes",
        ("Videores",),
        ("1080P30",),
        QUALITY,
        "Not 4K. Underwater detail is limited by turbidity, not sensor resolution, so 4K "
        "buys little real information while costing card space, bitrate and — in a sealed "
        "hull with no convection — heat. 30fps over 60fps roughly doubles the light.",
        hot=False,
    ),
    Setting(
        "imageres",
        "Camera.Menu.ImageRes",
        ("Imageres",),
        ("12MP",),
        QUALITY,
        "20MP on this sensor is almost certainly interpolated rather than native, and a "
        "smaller image should shorten the very slow (~2 s) capture operation.",
        hot=False,
    ),
    Setting(
        "hdr",
        "Camera.Menu.HDR",
        ("HDR", "Camera.Menu.HDR"),
        ("OFF",),
        QUALITY,
        "Underwater scenes are low-contrast, not high-contrast — HDR adds processing for "
        "little benefit and can look mushy.",
    ),
    Setting(
        "spot_meter",
        "Camera.Menu.SpotMeter",
        ("SpotMeter", "Camera.Menu.SpotMeter"),
        ("OFF",),
        QUALITY,
        "Spot metering samples the centre, which underwater is usually the dark open water "
        "column. Average metering handles the scene better.",
    ),
    Setting(
        "ev",
        "Camera.Menu.EV",
        ("EV", "Camera.Menu.EV"),
        ("EV0",),
        QUALITY,
        "Neutral. Raising EV lengthens exposure and adds noise, so prefer adding light to "
        "adding EV. Only EV0 has ever been observed, so the value set is a guess.",
    ),
    # ---- hull: it is sealed, and nobody can see or hear the camera -----------
    Setting(
        "status_lights",
        "Camera.Menu.StatusLights",
        ("StatusLights", "Camera.Menu.StatusLights"),
        ("OFF",),
        HULL,
        "Indicator LEDs are invisible inside a sealed hull and reflect off the inside of "
        "the port, flaring the recorded image.",
    ),
    Setting(
        "sound_indicator",
        "Camera.Menu.SoundIndicator",
        ("SoundIndicator", "Camera.Menu.SoundIndicator"),
        ("OFF",),
        HULL,
        "Beeps are inaudible inside a sealed hull, and keep any future hydrophone clean.",
    ),
    Setting(
        "looping_video",
        "Camera.Menu.LoopingVideo",
        ("LoopingVideo", "Camera.Menu.LoopingVideo"),
        ("OFF",),
        HULL,
        "Retain the whole dive rather than overwriting the earliest footage.",
    ),
    Setting(
        "flicker",
        "Camera.Menu.Flicker",
        ("Flicker", "Camera.Menu.Flicker"),
        ("50Hz",),
        HULL,
        "UK mains. Only matters under artificial light, but harmless and already correct.",
    ),
    Setting(
        "tv_system",
        "Camera.Menu.TVSystem",
        ("TVSystem", "Camera.Menu.TVSystem"),
        ("PAL",),
        HULL,
        "Matches 50Hz.",
    ),
    Setting(
        "timestamp",
        "Camera.Preview.MJPEG.TimeStamp",
        ("Camera.Preview.MJPEG.TimeStamp",),
        ("ACTIVE",),
        HULL,
        "Burns a timestamp into the image, which is what lets recorded footage be "
        "correlated against the blackbox log after a dive.",
    ),
    # ---- the pilot's actual picture ------------------------------------------
    # The operator flies on the RTSP substream, which is 640x360 @ 1.2 Mbit by
    # default — the 4K recording goes to the card and is NOT what you see live.
    # Firmware 0255 is expected to ignore this; `ignored` is a truthful outcome,
    # not a failure, and the report says so.
    Setting(
        "preview_w",
        "Camera.Preview.H264.w",
        ("Camera.Preview.H264.w",),
        ("1280",),
        QUALITY,
        "The pilot's picture is the 640x360 substream. Best effort — firmware 0255 may "
        "ignore the bump. Never re-encode topside to compensate: that costs latency and "
        "CPU to produce a worse image.",
        hot=False,
    ),
    Setting(
        "preview_h",
        "Camera.Preview.H264.h",
        ("Camera.Preview.H264.h",),
        ("720",),
        QUALITY,
        "Height half of the substream bump; see preview_w.",
        hot=False,
    ),
)

SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}


# --------------------------------------------------------------------------
# Deliberately NOT set. This list is load-bearing: without it, the next person
# to read the table assumes these were forgotten and "fixes" them.
# --------------------------------------------------------------------------
NOT_SET: tuple[tuple[str, str], ...] = (
    (
        "Camera.Menu.LCDPower",
        "OFF may mean 'the screen never blanks' — wasting power and adding heat in a sealed "
        "hull — rather than 'screen off'. The semantics are unverified on the physical camera, "
        "so it is left alone. Probe it deliberately with POST /api/camera/probe while watching "
        "the camera, then record which it is.",
    ),
    (
        "Camera.Menu.UpsideDown",
        "Depends on how the camera is physically mounted. Set WOLFANG_UPSIDE_DOWN to assert a "
        "value; rotating in post is worse than getting it right in the sensor.",
    ),
    (
        "Camera.Menu.IsStreaming",
        "Read-only status — whether an RTSP client is attached — not a setting. It reads NO "
        "until go2rtc pulls the stream.",
    ),
    (
        "Camera.Menu.Timelapse",
        "5SEC may be the interval used WHEN timelapse runs rather than evidence that it is "
        "engaged. Ambiguous, so it is reported and not written.",
    ),
    ("Camera.Menu.PhotoBurst", "Reads UNKNOW on firmware 0255. Reported, not written."),
    ("Camera.Menu.Q-SHOT", "Already OFF and the value set is unknown. Reported, not written."),
    ("Camera.Menu.SD0", "READY is status. The only write is `format`, which is destructive."),
    ("Camera.Menu.AWB", "Conditional on the lights — see awb_setting()."),
)

# Properties worth showing in the report even though we never write them, so a
# surprising value (Timelapse engaged, PhotoBurst UNKNOW) is visible rather than
# buried in a raw menu dump.
REPORT_ONLY: tuple[str, ...] = tuple(name for name, _ in NOT_SET)


# --------------------------------------------------------------------------
# AWB — the most consequential image setting, and the only conditional one.
# --------------------------------------------------------------------------
def awb_setting(white_lights_on: bool) -> Setting:
    """Water absorbs red first, so everything goes blue-green.

    With no lamps, the warmest available preset counteracts that cast most strongly —
    the standard trick for a camera with no dedicated underwater mode. With the white
    LEDs on, the lamps restore red at short range and forcing a warm preset on top of
    that produces an orange cast instead.
    """
    if white_lights_on:
        values = ("AUTO", "DAYLIGHT")
        why = "White LEDs restore red at short range; a warm preset on top of them casts " "everything orange."
    else:
        # AUTO is deliberately NOT in this chain. Every value listed here is also
        # treated as "already acceptable", and AUTO is the factory value — including
        # it would mean the warm preset was never applied at all. Both of these come
        # from the camera's own cammenu.xml, so the chain cannot come up empty.
        values = ("INCANDESCENT", "CLOUDY")
        why = (
            "No lamps: the warmest preset available counteracts the blue-green cast that "
            "water's absorption of red produces."
        )
    return Setting("awb", "Camera.Menu.AWB", ("AWB",), values, QUALITY, why)


# --------------------------------------------------------------------------
# Value comparison
# --------------------------------------------------------------------------
# Firmware 0255 reports `StatusLights=OF`, not `OFF`. Writing OFF and reading OF
# back would otherwise be scored as a silent no-op and re-probed on every boot.
_READBACK_ALIASES = {"OF": "OFF"}


def norm(v: str | None) -> str:
    if v is None:
        return ""
    s = v.strip().upper()
    return _READBACK_ALIASES.get(s, s)


def same(a: str | None, b: str | None) -> bool:
    return norm(a) == norm(b)


# --------------------------------------------------------------------------
# Capability cache — what actually worked, per firmware version.
# --------------------------------------------------------------------------
# Probing costs a set+read per candidate pair. Once we know that `PowerSaving`
# (not `Camera.Menu.PowerSaving`) is the write name that takes, there is no reason
# to rediscover it every boot. Keyed on FWversion because a firmware change
# invalidates the whole table.


def _caps_path() -> str:
    return os.environ.get("WOLFANG_CAPS_PATH", "/var/lib/neptune/camera-caps.json")


@dataclass
class Capabilities:
    fw: str = ""
    # key -> the write name that actually took
    write_names: dict[str, str] = field(default_factory=dict)
    # key -> the value that actually took
    values: dict[str, str] = field(default_factory=dict)
    # keys the firmware accepts (code 0) but silently ignores — do not re-probe
    # every boot, but DO retry occasionally in case a later firmware honours them
    ignored: list[str] = field(default_factory=list)
    # keys where every candidate value was rejected (722)
    rejected: list[str] = field(default_factory=list)

    def preferred(self, s: Setting) -> tuple[list[str], list[str]]:
        """Candidate (write names, values) with anything already proven first."""
        writes = list(s.writes)
        values = list(s.values)
        if (w := self.write_names.get(s.key)) and w in writes:
            writes.remove(w)
            writes.insert(0, w)
        if (v := self.values.get(s.key)) and v in values:
            values.remove(v)
            values.insert(0, v)
        return writes, values

    def to_json(self) -> dict:
        return {
            "fw": self.fw,
            "write_names": self.write_names,
            "values": self.values,
            "ignored": sorted(set(self.ignored)),
            "rejected": sorted(set(self.rejected)),
        }


def load_caps(fw: str) -> Capabilities:
    """Load the cache, but only if it was built against THIS firmware."""
    path = _caps_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return Capabilities(fw=fw)
    if fw and raw.get("fw") and raw.get("fw") != fw:
        log.info("camera firmware changed (%s -> %s) — discarding the capability cache", raw.get("fw"), fw)
        return Capabilities(fw=fw)
    return Capabilities(
        fw=raw.get("fw") or fw,
        write_names=dict(raw.get("write_names") or {}),
        values=dict(raw.get("values") or {}),
        ignored=list(raw.get("ignored") or []),
        rejected=list(raw.get("rejected") or []),
    )


def save_caps(caps: Capabilities) -> bool:
    """Best-effort. Failing to remember what worked must never fail a dive — it
    only costs a re-probe next boot."""
    path = _caps_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(caps.to_json(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.info("capability cache not saved (%s) — will re-probe next boot", exc)
        return False
