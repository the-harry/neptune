"""Automatic offline areas: creation from a launch point, and the fetch it starts.

Run:  cd api && python -m unittest tests.test_areas -v
      python api/tests/run.py areas

WHAT THIS GUARDS. Until this round `data/areas/` was empty on every card that had
ever been built, and it was nobody's bug: `areas.py` listed areas and read them,
`satellite.py` filled in one that already had a name and a bbox, and `crt-fetch`
refused to run without an area to clip to. Nothing anywhere MADE one. So the
console's "no chart data is downloaded" was true, permanent, and could only be
fixed by an operator who knew to draw a box by hand — at home, because at the
canal there is no internet to fix it with. Setting the launch point is the first
moment the system knows WHERE it is going to be, so that is the moment an area
can exist, and this suite is about the chain that hangs off it.

Every defect below is permanent in the artifact rather than merely annoying at the
time, because the artifact is a card carried to a place with no network:

  A HALF-DOWNLOADED AREA MUST NOT READ AS A FINISHED ONE. An MBTiles archive
  appears on disk with the FIRST tile, so "the file exists" was true 3% of the way
  through a download — and `list_areas()` published `present: true` off exactly
  that stat(). A map that looks complete and is not is worse than one that says it
  is empty: the empty one sends the operator to the internet while there still is
  some. So an interrupted fetch has to leave a state that says INTERRUPTED, a
  readiness gate that refuses, and nothing anywhere that reads as done.

  DOWNLOADING IS ITS OWN STATE. Absent, downloading, present and failed are four
  different facts and the operator does something different about each. Collapsing
  "still coming" into either of its neighbours is what produced the two bugs above
  and below at the same time.

  THE SAME LAUNCH POINT TWICE IS ONE AREA. The console re-POSTs the stored origin
  on every page load and again from the location watch, so an idempotence bug here
  does not make two areas — it makes an unbounded number of them, each re-fetching
  a thousand tiles from somebody's free public service, from a handheld sitting on
  a bank doing nothing.

  A CAP THAT ONLY EXISTS IN A COMMENT IS NOT A CAP. An operator who taps a map
  must never silently start a download measured in gigabytes over a phone hotspot.
  The refusal has to happen BEFORE the first request and has to quote the number
  it is enforcing, or the sentence sends nobody anywhere.

  NO INTERNET IS NOT A FAILURE. It is the ordinary condition of this vehicle's
  whole working life. A canal-side console that reports it as an error trains the
  operator to ignore errors, and one that answers it by retrying spends the dive
  doing that instead. It is a fact, reported once, quietly, and the job does not
  start.

NO NETWORK, EVER. Every request in this file goes through `nav.satellite._http_get`
and `nav.crt`'s own entry points, which both modules' docstrings nominate for
exactly this. Belt and braces on top: `urllib.request.urlopen` and
`socket.create_connection` are replaced for the duration with things that raise
`RealNetwork` — a BaseException, so no `except Exception:` retry loop anywhere can
swallow it and turn a suite that quietly downloaded the real Esri World Imagery
into a suite that passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import math
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from nav import areas as areamod
from nav import crt as crtmod
from nav import satellite as satmod
from nav import service as svcmod
from nav.config import settings as nav_settings
from nav.models import Origin
from nav.service import NavService, build_router

# A launch point on the Birmingham cut, the same water the one hand-made area in
# this repo (data/crt/gas-street) covers. Everything below is measured from here.
LAT, LON = 52.4780, -1.9120

# Far enough to be another canal in another town — outside any bbox this launch
# point could plausibly produce, whatever radius the implementation settles on.
FAR_LAT, FAR_LON = 53.4808, -2.2426  # Manchester, ~130 km


class RealNetwork(BaseException):
    """Something reached for the actual internet.

    Deliberately NOT an Exception. `satellite._fetch_retry` and `crt._get_json`
    both wrap every fetch in `except Exception:` and retry, so a guard raised as an
    ordinary exception would be swallowed, retried three times, and reported as a
    tile that failed to download — i.e. as a normal bad afternoon on a Trust
    server, in a suite whose entire premise is that no request leaves this process.
    """


# ---------------------------------------------------------------------------
# The world, as this suite pretends to be it
# ---------------------------------------------------------------------------
# A one-pixel JPEG. Real bytes on purpose: `satellite.download_area` puts whatever
# comes back into an MBTiles blob, and a str would only fail later and elsewhere.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffc00011080001000103011100021101031101ffc4"
    "001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002"
    "010303020403050504040000017d01020300041105122131410613516107227114328191a108"
    "2342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a4344454647"
    "48494a535455565758595a636465666768696a737475767778797a838485868788898a929394"
    "95969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6"
    "d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fefe28a2"
    "8a2803ffd9"
)


class FakeInternet:
    """What is behind every hostname, and whether there is anything behind them.

    One object rather than a handful of module globals because every check below
    asks it something afterwards: how many requests were made, whether any two
    overlapped, whether the second run re-fetched what the first one already had.
    """

    def __init__(self) -> None:
        self.online = True
        self.tiles = 0  # imagery tiles served
        self.urls: list[str] = []  # every URL asked for, in order
        self.probes: list[tuple] = []  # every reachability probe (host, port)
        self.fail_tiles_after: int | None = None  # die mid-download after N tiles
        self.gate: threading.Event | None = None  # held here until a test lets go
        self.live = 0  # requests in flight right now
        self.max_live = 0  # ...and the worst it ever got
        self._lock = threading.Lock()

    # -- the seam satellite.py nominates for exactly this --------------------
    def http_get(self, url: str, timeout: float = 20.0) -> bytes | None:
        if not self.online:
            # The shape the isolated segment actually produces: no resolver to ask,
            # so the name does not resolve at all. Not a timeout, not a 500.
            raise OSError("getaddrinfo failed - no hostname resolution in the isolated segment")
        with self._lock:
            self.urls.append(url)
            self.live += 1
            self.max_live = max(self.max_live, self.live)
        try:
            if self.gate is not None:
                # Blocks a WORKER THREAD (satellite calls this through
                # asyncio.to_thread), never the event loop — which is the whole
                # point of the never-blocks check that uses it.
                self.gate.wait(timeout=30)
            if "nominatim" in url:
                return json.dumps(
                    {"address": {"waterway": "Birmingham Canal Navigations"}, "name": "Birmingham Canal Navigations"}
                ).encode()
            if "overpass" in url:
                return json.dumps(
                    {
                        "elements": [
                            {
                                "type": "way",
                                "tags": {"waterway": "canal"},
                                "geometry": [{"lat": LAT, "lon": LON}, {"lat": LAT + 0.001, "lon": LON + 0.001}],
                            }
                        ]
                    }
                ).encode()
            with self._lock:
                self.tiles += 1
                n = self.tiles
            if self.fail_tiles_after is not None and n > self.fail_tiles_after:
                raise OSError(
                    f"connection reset after {self.fail_tiles_after} tiles "
                    f"(the canal-side hotspot, doing what it does)"
                )
            return JPEG
        finally:
            with self._lock:
                self.live -= 1

    # -- the probe api/nav already owns (nav/cli.py _reachable) --------------
    def create_connection(self, address, timeout=None, *a, **kw):
        self.probes.append(tuple(address))
        if not self.online:
            raise socket.gaierror("getaddrinfo failed - there is no name service here")
        return _DeadSocket()

    def reachable(self, url: str, timeout: float = 4.0) -> tuple[bool, str]:
        host = re.sub(r"^https?://", "", url).split("/")[0]
        self.probes.append((host, 443))
        return (
            (True, f"{host}:443 answered")
            if self.online
            else (False, f"{host} does not resolve - there is no name service on this segment")
        )


class _DeadSocket:
    def close(self) -> None:
        pass


class FakeHazards:
    """`crt.download_hazards`, without the ArcGIS estate behind it.

    Stubbed at the module's own public entry point rather than at its socket,
    because what is under test here is the ORCHESTRATION — who is asked, in what
    order, and what is recorded when one of them cannot be finished. tests/
    test_crt.py already drives the real paging, licence and truncation logic
    against a service double, and duplicating that here would only give the two
    suites a chance to disagree about it.

    It writes what the real thing writes: one `<layer>.geojson` per layer plus a
    `provenance.json`, into `crt.area_dir(area)` — so `service._crt_layers()` and
    the readiness gate read this suite's output through exactly the code the Pi
    runs. A layer that fails gets NO FILE, which is crt.py's own rule and the
    reason an absent hazard layer can never read as an empty canal.
    """

    LAYERS = ("locks", "weirs", "culverts", "sluices")

    def __init__(self, net: FakeInternet) -> None:
        self.net = net
        self.calls: list[tuple[str, tuple | None]] = []
        self.fail_from: int | None = None  # layer index at which the hotspot dies

    async def download_hazards(self, area: str, bbox=None, progress=None) -> dict:
        self.calls.append((area, tuple(bbox) if bbox else None))
        if not self.net.online:
            return {
                "ok": False,
                "area": area,
                "error": "no services reachable - this is a BOOTSTRAP task and needs internet",
            }
        name = crtmod.safe_area_name(area) or area
        out = crtmod.area_dir(name)
        out.mkdir(parents=True, exist_ok=True)
        fetched, skipped = [], []
        for i, key in enumerate(self.LAYERS):
            path = out / f"{key}.geojson"
            if path.exists():
                # ALREADY ON THE CARD. The real fetch is resumable for the same
                # reason: these are somebody's free public services and a re-run
                # that re-downloads everything is the rudest thing this project
                # could do with them.
                fetched.append({"layer_key": key, "file": path.name, "features": 1, "reused": True})
                continue
            if self.fail_from is not None and i >= self.fail_from:
                skipped.append(
                    {
                        "layer_key": key,
                        "skipped": "fetch-failed",
                        "why": "connection reset mid-page",
                        "note": "no file written on purpose - a partial layer " "reads as 'nothing here'",
                    }
                )
                continue
            await asyncio.sleep(0)
            path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"OBJECTID": i, "layer": key},
                                "geometry": {"type": "Point", "coordinates": [LON, LAT]},
                            }
                        ],
                    }
                )
            )
            fetched.append({"layer_key": key, "file": path.name, "features": 1})
            if progress:
                await progress({"area": name, "state": "layer", "layer": key, "n": i + 1, "of": len(self.LAYERS)})
        crtmod.provenance_path(name).write_text(
            json.dumps(
                {
                    "area": name,
                    "bbox": list(bbox) if bbox else None,
                    "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "layers": fetched,
                    "skipped": skipped,
                    "warnings": [],
                }
            )
        )
        return {"ok": not skipped, "area": name, "layers": fetched, "skipped": skipped, "dir": str(out)}


# ---------------------------------------------------------------------------
# Finding the implementation, and saying exactly what was looked for
# ---------------------------------------------------------------------------
# These names are the ones api/nav/config.py's own comments nominate
# ("nav/areas.py create_area", the four-state `state` field). The alternatives are
# listed so a rename costs a clear sentence rather than a mystery, and so this
# suite fails with a finding somebody can act on instead of an AttributeError.

_CREATE_NAMES = (
    "create_area",
    "create_area_for_point",
    "area_for_point",
    "auto_create_area",
    "ensure_area",
    "ensure_area_for_origin",
)
_COMPLETE_NAMES = ("area_completeness", "completeness", "area_complete")


def _resolve(names, *modules):
    for mod in modules:
        for n in names:
            fn = getattr(mod, n, None)
            if callable(fn):
                return fn
    return None


def _searched(names, *modules) -> str:
    where = ", ".join(m.__name__ for m in modules)
    return f"none of {list(names)} exists in {where}"


def _create():
    return _resolve(_CREATE_NAMES, areamod, svcmod)


def _completeness():
    return _resolve(_COMPLETE_NAMES, svcmod, areamod)


def _call(fn, lat, lon, **extra):
    """Invoke the area-creation entry point, whatever shape it settled on.

    `create_area(lat, lon)` is the expected one; a version taking an `Origin`, or
    keyword latitude/longitude, is the same act under a different spelling and is
    accepted rather than reported as a missing feature. Anything genuinely
    different fails on the argument names, which is the honest outcome.
    """
    origin = Origin(lat=lat, lon=lon, accuracy=8.0, source="map_tap", t=time.time())
    pool = {
        "lat": lat,
        "latitude": lat,
        "lon": lon,
        "lng": lon,
        "longitude": lon,
        "origin": origin,
        "o": origin,
        "point": (lat, lon),
        **extra,
    }
    sig = inspect.signature(fn)
    positional, kwargs = [], {}
    for pname, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if pname in pool:
            if p.kind is p.POSITIONAL_ONLY:
                positional.append(pool[pname])
            else:
                kwargs[pname] = pool[pname]
        elif p.default is p.empty:
            raise AssertionError(
                f"{fn.__module__}.{fn.__name__} wants a required argument {pname!r} that "
                f"this suite has no value for. It is meant to be callable with nothing "
                f"but a launch point."
            )
    unknown = [k for k in extra if k not in sig.parameters]
    if unknown:
        raise AssertionError(f"{fn.__name__} takes no {unknown[0]!r}")
    out = fn(*positional, **kwargs)
    return asyncio.run(out) if inspect.isawaitable(out) else out


def _strings(obj, out=None) -> list[str]:
    """Every string anywhere inside a report, so a sentence can be looked for."""
    out = [] if out is None else out
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _strings(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _strings(v, out)
    return out


def _origin_endpoint(svc: NavService):
    for route in build_router(svc).routes:
        if getattr(route, "path", None) == "/api/origin" and "POST" in getattr(route, "methods", ()):
            return route.endpoint
    raise AssertionError(
        "nav/service.py no longer exposes POST /api/origin - the launch point is the "
        "trigger for the whole automatic download, so this suite must follow it "
        "wherever it went rather than quietly stop checking it."
    )


def _area_meta(name: str | None = None) -> dict | None:
    areas = areamod.list_areas()
    if name is None:
        return areas[0] if len(areas) == 1 else None
    return next((a for a in areas if a["name"] == name), None)


def _state_of(meta: dict | None) -> str:
    """The area's own word for what it is. 'unstated' when it does not have one.

    Not defaulted to 'absent' or to 'present': a metadata document with no state
    field is the exact defect this field was added for, and inventing a plausible
    answer for it here would hide it.
    """
    if not meta:
        return "no-such-area"
    v = meta.get("state")
    return v if isinstance(v, str) else "unstated"


def _bbox_of(meta: dict) -> list[float]:
    bb = meta.get("bbox")
    assert isinstance(bb, (list, tuple)) and len(bb) == 4, f"bbox is {bb!r}"
    return [float(v) for v in bb]


def _metres(lat1, lon1, lat2, lon2) -> float:
    """Flat-earth metres. geo.py's approximation; exact enough at canal scale."""
    m_lat = math.radians(1.0) * 6378137.0
    return math.hypot((lat2 - lat1) * m_lat, (lon2 - lon1) * m_lat * math.cos(math.radians((lat1 + lat2) / 2)))


# ---------------------------------------------------------------------------
# The fixture: a temporary card, a fake internet, and no way out to the real one
# ---------------------------------------------------------------------------
class AreaTestCase(unittest.TestCase):
    """Every test gets an empty card and an internet that only this file owns."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="neptune-areas-"))
        self.saved: dict[str, object] = {}
        self._set(
            data_dir=self.tmp,
            areas_dir=self.tmp / "areas",
            dives_dir=self.tmp / "dives",
            speed_lut_dir=self.tmp / "speed_luts",
            crt_dir=self.tmp / "crt",
        )
        for d in (self.tmp / "areas", self.tmp / "dives", self.tmp / "crt"):
            d.mkdir(parents=True, exist_ok=True)
        # No waiting. satellite.py sleeps 1/sat_rate_per_s between tiles out of
        # politeness to Esri, which is right against a real server and is pure
        # dead time against this one. The politeness itself is checked separately,
        # by asking whether requests ever overlapped rather than how long they took.
        self._set(sat_rate_per_s=10_000.0, crt_rate_per_s=10_000.0)

        self.net = FakeInternet()
        self.haz = FakeHazards(self.net)
        self._patched: list[tuple[object, str, object]] = []
        self._patch(satmod, "_http_get", self.net.http_get)
        self._patch(crtmod, "_http_get", self._crt_seam)
        self._patch(crtmod, "download_hazards", self.haz.download_hazards)
        self._patch(socket, "create_connection", self.net.create_connection)
        self._patch(urllib.request, "urlopen", self._no_real_network)
        try:  # the probe api/nav already owns
            from nav import cli as climod

            self._patch(climod, "_reachable", self.net.reachable)
        except Exception:  # noqa: BLE001 - a card without the CLI still tests
            pass
        self.svc = NavService()  # no start(): the DR loop is not what is on trial

    def tearDown(self) -> None:
        for obj, name, old in reversed(self._patched):
            setattr(obj, name, old)
        for name, old in self.saved.items():
            object.__setattr__(nav_settings, name, old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture plumbing ---------------------------------------------------
    def _set(self, **kw) -> None:
        """Point a frozen settings object at this test's own card.

        A frozen dataclass is frozen against accident, not against a test that
        says why: without this the suite would download into the operator's real
        data/areas and the first check to pass would have written a live area.
        """
        for k, v in kw.items():
            self.saved.setdefault(k, getattr(nav_settings, k))
            object.__setattr__(nav_settings, k, v)

    def _patch(self, obj, name, new) -> None:
        self._patched.append((obj, name, getattr(obj, name)))
        setattr(obj, name, new)

    def _no_real_network(self, *a, **kw):
        raise RealNetwork(f"a real HTTP request was attempted: {a[:1]}")

    def _crt_seam(self, *a, **kw):
        raise RealNetwork(
            "nav.crt._http_get was reached. The hazard fetch is stubbed at "
            "download_hazards; something imported it by value, so the stub did not take."
        )

    # -- driving ------------------------------------------------------------
    def set_origin(self, lat=LAT, lon=LON, accuracy=8.0, settle=True, timeout=25.0):
        """POST /api/origin, the way the console does, and let the job run.

        The endpoint is the specified trigger, so it is what this suite pulls.
        `settle=False` returns the moment the endpoint does, which is what the
        never-blocks check needs.
        """
        endpoint = _origin_endpoint(self.svc)
        o = Origin(lat=lat, lon=lon, accuracy=accuracy, t=time.time())

        async def go():
            t0 = time.monotonic()
            res = await endpoint(o, override=True) if _takes_override(endpoint) else await endpoint(o)
            took = time.monotonic() - t0
            state_at_return = _state_of(_area_meta())
            if settle:
                await self._settle(timeout)
            return {"response": res, "took_s": took, "state_at_return": state_at_return}

        return asyncio.run(go())

    async def _settle(self, timeout=25.0) -> None:
        """Wait out whatever background work the trigger started.

        Written against asyncio rather than against a named attribute so it holds
        whichever way the job is held (a task on the service, a task group, a
        plain create_task). The DR loop is deliberately not running, so anything
        pending here IS the fetch.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
            if not pending:
                return
            await asyncio.wait(pending, timeout=0.2)
        raise AssertionError(f"the background fetch was still running after {timeout:.0f}s")

    # -- reading the result -------------------------------------------------
    def report(self, res: dict) -> dict:
        """Everything the system says about the automatic download, in one dict.

        Merged from the origin response, the service's own record of the job, and
        the completeness roll-up, because which of those carries a given sentence
        is an implementation decision and the CLAIM is what these checks are about.
        """
        out = {"origin_response": res.get("response")}
        for attr in ("last_fetch", "fetch"):
            v = getattr(self.svc, attr, None)
            if v is None:
                continue
            snap = getattr(v, "snapshot", None) or getattr(v, "status", None)
            out[attr] = snap() if callable(snap) else (v if isinstance(v, dict) else getattr(v, "__dict__", str(v)))
        comp = _completeness()
        if comp:
            with contextlib.suppress(Exception):
                out["completeness"] = comp(self.svc.active_area or (_area_meta() or {}).get("name") or "")
        out["areas"] = areamod.list_areas()
        return out

    def readiness_item(self, needle: str):
        for item in self.svc.readiness().items:
            if needle.lower() in item.step.lower():
                return item
        return None


def _takes_override(endpoint) -> bool:
    return "override" in inspect.signature(endpoint).parameters


# ===========================================================================
# 1. A launch point becomes an area
# ===========================================================================
class CreationTest(AreaTestCase):
    """`create_area` is the function whose absence was the whole bug.

    `grep -rn "create_area"` used to return nothing at all, which is why an
    operator's only route to an offline area was to know that one had to be drawn
    by hand, and to do it at home.
    """

    def test_a_launch_point_becomes_an_area_whose_box_contains_it(self):
        fn = _create()
        self.assertIsNotNone(
            fn,
            _searched(_CREATE_NAMES, areamod, svcmod) + " - nothing in the repo can make an offline area, which is "
            "the defect this whole round exists to fix",
        )
        _call(fn, LAT, LON)
        areas = areamod.list_areas()
        self.assertEqual(len(areas), 1, f"one launch point, {len(areas)} areas: {areas}")
        bb = _bbox_of(areas[0])
        w, s, e, n = bb
        self.assertLess(w, e, f"bbox is not [W,S,E,N]: {bb}")
        self.assertLess(s, n, f"bbox is not [W,S,E,N]: {bb}")
        # STRICTLY inside, not merely within. A launch point on the edge of its own
        # area is a sub that leaves the map on the first leg.
        self.assertTrue(
            w < LON < e and s < LAT < n, f"the launch point {LAT},{LON} is not inside its own area's bbox {bb}"
        )
        # ...and centred on it, or the reach is a different distance in each
        # direction and the operator cannot know which way is short.
        cx, cy = (w + e) / 2, (s + n) / 2
        off = _metres(LAT, LON, cy, cx)
        self.assertLess(
            off, 50.0, f"the area's centre is {off:.0f} m from the launch point it was " f"made for (bbox {bb})"
        )

    def test_the_box_is_the_size_the_configuration_says_it_is(self):
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        want = float(getattr(nav_settings, "area_radius_m", 1200.0))
        _call(fn, LAT, LON)
        w, s, e, n = _bbox_of(areamod.list_areas()[0])
        north = _metres(LAT, LON, n, LON)
        east = _metres(LAT, LON, LAT, e)
        for label, got in (("north", north), ("east", east)):
            self.assertAlmostEqual(
                got,
                want,
                delta=max(60.0, want * 0.15),
                msg=f"the area reaches {got:.0f} m {label} of the launch point; "
                f"NAV_AREA_RADIUS_M says {want:.0f} m. A radius that does not "
                f"match its own setting cannot be reasoned about before a dive.",
            )

    def test_the_new_area_carries_the_metadata_the_rest_of_the_system_reads(self):
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        _call(fn, LAT, LON)
        meta = areamod.list_areas()[0]
        self.assertIn("bbox", meta)
        for key in ("minzoom", "maxzoom"):
            self.assertIn(
                key,
                meta,
                f"the area records no {key}, so nothing downstream can say what "
                f"detail it is meant to hold or what it would cost to fetch",
            )
        self.assertFalse(
            meta.get("present"),
            "a just-created area claims its archive is PRESENT before a "
            "single tile has been fetched - this is the stat()-is-not-a-"
            "download bug, and everything downstream believes it",
        )
        self.assertEqual(
            _state_of(meta),
            "absent",
            f"a created-but-unfetched area reads as {_state_of(meta)!r}. "
            f"The four states are {list(getattr(areamod, 'STATES', ()))} and "
            f"this one is ABSENT: the plan exists, nothing has been "
            f"downloaded for it.",
        )

    def test_the_same_launch_point_twice_leaves_one_area_not_two(self):
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        first = _call(fn, LAT, LON)
        second = _call(fn, LAT, LON)
        areas = areamod.list_areas()
        self.assertEqual(
            len(areas),
            1,
            f"the same launch point set twice made {len(areas)} areas "
            f"({[a['name'] for a in areas]}). The console re-POSTs the "
            f"stored origin on every page load, so this does not make two "
            f"- it makes one per reload, each re-fetching a thousand tiles "
            f"off somebody's free service.",
        )
        self.assertEqual(
            _name_of(first, areas), _name_of(second, areas), "the second call reported a different area from the first"
        )

    def test_a_move_along_the_towpath_reuses_the_area_and_another_town_does_not(self):
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        _call(fn, LAT, LON)
        one = areamod.list_areas()[0]["name"]
        # ~80 m along the cut: the operator has walked to a better bit of bank.
        near_lat = LAT + 80.0 / (math.radians(1.0) * 6378137.0)
        _call(fn, near_lat, LON)
        areas = areamod.list_areas()
        self.assertEqual(
            [a["name"] for a in areas],
            [one],
            f"walking 80 m along the towpath made a second area ({[a['name'] for a in areas]}). "
            f"The first one already covers this water; a second one is a second download "
            f"of the same imagery.",
        )
        # ...and another city is inside nobody's box.
        _call(fn, FAR_LAT, FAR_LON)
        names = sorted(a["name"] for a in areamod.list_areas())
        self.assertEqual(
            len(names),
            2,
            f"a launch point 130 km away was folded into the existing area "
            f"({names}). Reuse has to stop somewhere, and it has to stop long "
            f"before the box would have to grow to a county.",
        )

    def test_the_size_cap_refuses_before_it_starts_and_says_what_the_cap_is(self):
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        cap = float(getattr(nav_settings, "area_max_radius_m", 3000.0))
        huge = cap * 50  # a county, not a pound
        # An area's size is either an argument or a setting depending on how the
        # entry point was written, and the cap has to hold either way. Whichever it
        # is, the ask below is fifty times what the configuration permits.
        params = inspect.signature(fn).parameters
        radius_arg = next((p for p in params if "radius" in p.lower()), None)
        extra = {radius_arg: huge} if radius_arg else {}
        if not radius_arg:
            self._set(area_radius_m=huge)
        with self.assertRaises(
            (ValueError, RuntimeError),
            msg=f"a launch point asking for a {huge:.0f} m radius was accepted. "
            f"NAV_AREA_MAX_RADIUS_M is {cap:.0f} m, and an operator who taps a "
            f"map must not be able to silently start a download measured in "
            f"gigabytes over a phone hotspot at the water's edge.",
        ) as caught:
            _call(fn, LAT, LON, **extra)
        self.assertRegex(
            str(caught.exception),
            r"\d",
            "the refusal quotes no number, so it cannot tell the operator what they are "
            "up against or what to ask for instead",
        )
        self.assertEqual(areamod.list_areas(), [], "the refusal still wrote an area")
        self.assertEqual(
            self.net.tiles,
            0,
            f"{self.net.tiles} tiles were fetched before the cap refused - the whole "
            f"point of a cap is that it bites BEFORE the first request, not after the "
            f"download is under way",
        )

    def test_creating_an_area_resolves_no_hostname(self):
        """THE TWO-PHASE MODEL SURVIVES. A plan is arithmetic; filling it in is not.

        nav/areas.py says so in its own docstring, and it has to be true or the
        console cannot make an area at the bank at all - which is exactly where an
        operator discovers they never made one. A lookup there does not fail so
        much as hang, so this is checked by taking the network away entirely rather
        than by reading the source.
        """
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        self.net.online = False
        _call(fn, LAT, LON)
        self.assertEqual(
            len(areamod.list_areas()),
            1,
            "an area could not be created with no network, so an operator "
            "who arrives at the canal without one can never make one",
        )
        self.assertEqual(self.net.urls, [], f"creating an area fetched something: {self.net.urls[:3]}")
        self.assertEqual(
            self.net.probes,
            [],
            f"creating an area probed the network: {self.net.probes[:3]} - "
            f"writing a bbox and a JSON file needs no opinion about DNS",
        )

    def test_the_default_area_is_inside_the_tile_and_size_budgets(self):
        """BOUNDED means bounded by the numbers the downloader itself enforces.

        `satellite.download_area` refuses anything over `sat_tile_cap`, so an
        automatic area whose default size trips that would create an area that can
        never be filled in - a plan on the card that always fails.
        """
        fn = _create()
        self.assertIsNotNone(fn, _searched(_CREATE_NAMES, areamod, svcmod))
        _call(fn, LAT, LON)
        meta = areamod.list_areas()[0]
        bb = _bbox_of(meta)
        zmin = int(meta.get("minzoom", nav_settings.sat_min_zoom))
        zmax = int(meta.get("maxzoom", nav_settings.sat_max_zoom))
        est = satmod.estimate(bb, zmin, zmax)
        self.assertLessEqual(
            est["tiles"],
            nav_settings.sat_tile_cap,
            f"the default automatic area is {est['tiles']} tiles and the downloader "
            f"refuses above {nav_settings.sat_tile_cap} - every automatic area would be "
            f"created and then refused",
        )
        self.assertLessEqual(
            est["mb"],
            nav_settings.area_size_cap_mb,
            f"the default automatic area estimates {est['mb']} MB against a " f"{nav_settings.area_size_cap_mb} MB cap",
        )


def _name_of(result, areas) -> str:
    """The area a create call is talking about, however it reported it."""
    if isinstance(result, dict):
        for k in ("name", "area"):
            if isinstance(result.get(k), str):
                return result[k]
    if isinstance(result, str):
        return result
    return areas[0]["name"] if len(areas) == 1 else "?"


# ===========================================================================
# 2. The origin is the trigger, and the fetch runs behind it
# ===========================================================================
class AutoFetchTest(AreaTestCase):
    """Setting a launch point is enough. That is the whole requirement."""

    def test_setting_an_origin_makes_an_area_and_fetches_it(self):
        self.set_origin()
        areas = areamod.list_areas()
        self.assertEqual(
            len(areas),
            1,
            "POST /api/origin created no offline area. Setting the launch "
            "point is the moment the system first knows where it will be, "
            "and it is meant to be the only thing an operator has to do.",
        )
        meta = areas[0]
        self.assertGreater(self.net.tiles, 0, "an area was created and not one imagery tile was fetched")
        self.assertEqual(
            _state_of(meta),
            "present",
            f"after a clean run the area reads {_state_of(meta)!r}; the "
            f"imagery, the hazard layers and the centreline all landed",
        )
        self.assertTrue(meta.get("present"), f"the area's archive is not reported present: {meta}")
        self.assertTrue((nav_settings.areas_dir / f"{meta['name']}.mbtiles").exists(), "no MBTiles archive on the card")
        self.assertTrue(
            meta.get("has_centreline"),
            "no waterway centreline was fetched, so snapping has nothing to "
            "snap to and the readiness gate cannot pass",
        )
        self.assertTrue(
            self.haz.calls,
            "the CRT hazard layers were never asked for. Imagery with no "
            "hazards is a map of clear water over a canal full of sluices.",
        )

    def test_the_endpoint_returns_long_before_the_download_does(self):
        """NEVER BLOCK. This is a vehicle control surface, not a download manager.

        ONE EVENT LOOP FOR THE WHOLE TEST, and it has to be. The first version of this
        called set_origin(settle=False) — which is asyncio.run(...) — and then
        asyncio.run(self._settle()) afterwards. asyncio.run CLOSES its loop on the way
        out, so the background fetch was destroyed the instant the endpoint returned,
        and the settle then opened a fresh loop with nothing in it and returned at once.
        The area was still reading "downloading" because the job had been killed, not
        because it had failed to finish: a harness artefact wearing a product defect's
        clothes.
        """
        self.net.gate = threading.Event()
        endpoint = _origin_endpoint(self.svc)
        o = Origin(lat=LAT, lon=LON, accuracy=8.0, t=time.time())

        async def go():
            t0 = time.monotonic()
            res = await endpoint(o, override=True) if _takes_override(endpoint) else await endpoint(o)
            took = time.monotonic() - t0
            at_return = _state_of(_area_meta())
            self.net.gate.set()  # let the held tile through, same loop
            await self._settle()
            return took, at_return, res

        took, at_return, _res = asyncio.run(go())
        self.assertLess(
            took,
            3.0,
            f"POST /api/origin took {took:.1f}s with the first tile "
            f"still in flight - the console was unflyable for that whole time",
        )
        self.assertEqual(
            at_return,
            "downloading",
            f"at the moment the endpoint returned the area read "
            f"{at_return!r}. DOWNLOADING is its own state, distinct from "
            f"absent and from present, and it is what the operator must be "
            f"able to see happening.",
        )
        self.assertEqual(
            _state_of(_area_meta()),
            "present",
            "the fetch ran to completion but the card was never updated to "
            "say so, so a finished download still reads as a live one",
        )

    def test_a_second_origin_over_a_finished_area_re_downloads_nothing(self):
        """POLITE. Esri and the Trust are free public services, and the console
        re-POSTs its stored origin on every single page load."""
        self.set_origin()
        first = self.net.tiles
        self.assertGreater(first, 0)
        self.set_origin()
        self.assertEqual(
            self.net.tiles,
            first,
            f"the second origin re-fetched {self.net.tiles - first} tiles that were "
            f"already on the card. The console posts the stored origin on every load, "
            f"so this is not one extra download - it is one per reload.",
        )
        self.assertEqual(len(areamod.list_areas()), 1)

    def test_a_re_run_skips_the_hazard_layers_already_on_the_card(self):
        self.set_origin()
        before = sorted(
            p.name for p in crtmod.area_dir(crtmod.safe_area_name(areamod.list_areas()[0]["name"])).glob("*.geojson")
        )
        self.assertTrue(before, "no hazard layers landed on the first run")
        self.set_origin()
        index = svcmod._crt_layers(areamod.list_areas()[0]["name"])
        reused = [r for r in (index.get("layers") or []) if r.get("status") == "present"]
        self.assertEqual(
            len(reused),
            len(before),
            f"after a re-run {len(reused)} of {len(before)} hazard layers " f"are still readable on the card",
        )

    def test_the_requests_are_made_one_at_a_time(self):
        """POLITE, measured rather than asserted in a comment.

        A parallel fetch of a thousand tiles is how this gets blocked mid-bootstrap,
        which leaves half an area on the card at the exact moment there is still
        internet to fix it with. Concurrency is the thing that is actually
        observable here; the rate limit itself is turned off in setUp so the suite
        does not spend its life sleeping.
        """
        self.set_origin()
        self.assertGreater(self.net.max_live, 0, "no requests were made at all")
        self.assertEqual(
            self.net.max_live,
            1,
            f"{self.net.max_live} requests were in flight at once against " f"somebody's free public service",
        )


# ===========================================================================
# 3. What happens when it does not finish
# ===========================================================================
class InterruptedTest(AreaTestCase):

    def test_an_interrupted_fetch_leaves_nothing_that_reads_as_complete(self):
        self.net.fail_tiles_after = 3
        self.haz.fail_from = 1
        self.set_origin()
        meta = _area_meta()
        self.assertIsNotNone(meta, "the interrupted area vanished from the card entirely")
        state = _state_of(meta)
        self.assertNotEqual(
            state,
            "present",
            f"a download that died after 3 tiles left the area reading PRESENT. An "
            f"MBTiles file appears on disk with the FIRST tile, so a stat() says yes "
            f"3% of the way through - and the map then draws what landed and clear "
            f"water everywhere else.",
        )
        self.assertIn(
            state,
            ("failed", "downloading", "absent", "interrupted"),
            f"the area's state is {state!r}, which is none of " f"{list(getattr(areamod, 'STATES', ()))}",
        )

    def test_an_interrupted_fetch_fails_the_pre_dive_gate(self):
        self.net.fail_tiles_after = 3
        self.haz.fail_from = 1
        self.set_origin()
        name = (_area_meta() or {}).get("name")
        self.svc.activate_area(name)
        item = self.readiness_item("offline area COMPLETE") or self.readiness_item("basemap present")
        self.assertIsNotNone(
            item,
            "the readiness check has no item covering whether the offline area is "
            "finished, so an operator can be handed a half-downloaded card by a "
            "panel of greens",
        )
        self.assertFalse(
            item.ok,
            f"the pre-dive gate passed on a half-downloaded area: {item.step!r} -> "
            f"{item.detail!r}. This is the last moment anybody can find out, because "
            f"afterwards there is no internet to fix it with.",
        )

    def test_an_interrupted_hazard_layer_is_named_rather_than_drawn_as_empty(self):
        self.haz.fail_from = 2
        self.set_origin()
        name = (_area_meta() or {}).get("name")
        block = svcmod._crt_layers(name)
        self.assertTrue(
            block.get("failed"),
            f"two hazard layers never downloaded and the index reports none failed: "
            f"{block.get('status')!r}. An absent hazard layer drawn as an empty one is "
            f"the single most dangerous picture this console can produce.",
        )

    def test_an_interrupted_fetch_leaves_enough_to_carry_on_from(self):
        """RESUMABLE. The tiles that did land are not thrown away and are not re-fetched."""
        self.net.fail_tiles_after = 5
        self.set_origin()
        got = self.net.tiles
        meta = _area_meta()
        self.assertIsNotNone(meta)
        archive = nav_settings.areas_dir / f"{meta['name']}.mbtiles"
        self.assertTrue(
            archive.exists(),
            "the partial download was discarded, so resuming means starting "
            "again from tile one on a hotspot that has already failed once",
        )
        con = sqlite3.connect(f"file:{archive}?mode=ro", uri=True)
        try:
            kept = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        finally:
            con.close()
        self.assertGreater(kept, 0, "nothing was kept from the tiles that did arrive")
        # ...and the retry picks up where it stopped rather than at the beginning.
        #
        # THE THRESHOLD HERE USED TO BE `got + kept`, AND NOTHING COULD HAVE PASSED IT.
        # The fixture uses the real default radius, so the area is ~980 tiles; a run
        # that dies after five leaves ~975 genuinely outstanding, and demanding the
        # retry make fewer than ~35 requests was demanding it skip tiles it has never
        # fetched. The property actually wanted is that it does not re-ask for what is
        # already on the card, so that is what is asserted: every request the retry
        # makes must be for a tile the first run did not land, and the tiles that DID
        # land must be visibly skipped rather than silently re-fetched.
        before = self.net.tiles
        self.net.fail_tiles_after = None
        self.set_origin()
        retried = self.net.tiles - before
        con = sqlite3.connect(f"file:{archive}?mode=ro", uri=True)
        try:
            total_now = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        finally:
            con.close()
        self.assertLessEqual(
            retried,
            total_now - kept,
            f"the retry made {retried} requests to add {total_now - kept} tiles to an area "
            f"that already had {kept} — it re-asked for tiles that were already on the card",
        )
        self.assertEqual(_state_of(_area_meta()), "present", "the resumed download did not finish the area")


# ===========================================================================
# 4. No internet is the ordinary condition, not an error
# ===========================================================================
class NoInternetTest(AreaTestCase):

    def test_with_no_internet_the_job_never_starts(self):
        self.net.online = False
        self.set_origin()
        self.assertEqual(
            self.net.tiles,
            0,
            f"{self.net.tiles} tile requests were attempted with no internet. 'Try and "
            f"see' on the hot path is exactly what a canal-side console must not spend "
            f"its afternoon doing.",
        )
        self.assertEqual([u for u in self.net.urls], [], f"requests were attempted anyway: {self.net.urls[:3]}")

    def test_the_absence_of_internet_is_reported_as_a_plain_fact(self):
        self.net.online = False
        res = self.set_origin()
        said = " ".join(_strings(self.report(res))).lower()
        self.assertRegex(
            said,
            r"internet|offline|no network|not reachable|no route",
            "nothing anywhere says there was no internet. The operator is owed the "
            "reason the map is empty, because it is fixable at home and not at the "
            "canal.",
        )
        self.assertNotRegex(
            said, r"\b(traceback|exception|crash)\b", "the report of a perfectly ordinary condition reads as a crash"
        )

    def test_no_internet_is_not_recorded_as_a_download_that_failed(self):
        """A fetch that could not START and a fetch that DIED are different facts.

        One is fixed by going somewhere with signal, the other by trying again — and
        a console that shows the same red for both teaches the operator that red
        means nothing. Nothing was attempted here, so nothing failed: the area is
        ABSENT, which is the true and useful claim that there is a plan on the card
        and no data behind it yet.
        """
        self.net.online = False
        self.set_origin()
        state = _state_of(_area_meta())
        self.assertNotIn(
            state,
            ("failed", "downloading"),
            f"with no internet to start with, the area reads {state!r}. Nothing was "
            f"attempted, so nothing failed and nothing is in progress - reporting "
            f"either turns the ordinary condition of this vehicle's whole working "
            f"life into an alarm.",
        )
        self.assertEqual(
            state,
            "absent",
            f"the area reads {state!r}; ABSENT is what it is - the plan exists and "
            f"nothing has been downloaded for it",
        )

    def test_the_launch_point_itself_still_works_with_no_internet(self):
        """The origin is client-owned and the runtime needs no network at all.

        Auto-download must never make setting a datum depend on a hotspot: a dive
        with no area is a worse dive, and a dive with no origin is no dive at all.
        """
        self.net.online = False
        self.set_origin()
        self.assertIsNotNone(self.svc.origin, "with no internet the launch point was not even recorded")
        self.assertAlmostEqual(self.svc.origin.lat, LAT, places=5)


# ===========================================================================
# 5. The rule the whole subsystem is built on
# ===========================================================================
class NoRealNetworkTest(AreaTestCase):

    def test_a_full_run_makes_no_request_this_suite_did_not_serve(self):
        """If the monkeypatch ever fails to take, this is where it is caught.

        `urllib.request.urlopen` and `socket.create_connection` are replaced with
        things that raise `RealNetwork`, which is a BaseException precisely so the
        retry loops in satellite.py and crt.py cannot swallow it. A suite that
        quietly downloaded the real Esri World Imagery would otherwise pass.
        """
        self.set_origin()
        for url in self.net.urls:
            self.assertRegex(url, r"^https?://", f"a request went somewhere unexpected: {url!r}")
        self.assertTrue(self.net.urls, "the run made no requests at all")

    def test_the_serving_path_still_answers_with_the_network_gone(self):
        """TWO-PHASE SURVIVES. Downloading is bootstrap; serving is not.

        The whole subsystem rests on the runtime being a stat() and a file read.
        An auto-download that made the serving side ask the network anything would
        break the console at the canal specifically — where a lookup does not fail
        so much as hang, and a hung request is a map that never draws.
        """
        self.set_origin()
        name = (_area_meta() or {}).get("name")
        self.net.online = False
        self.net.urls.clear()
        self.net.probes.clear()
        block = svcmod._crt_layers(name)
        self.assertEqual(block.get("status"), "present", f"the hazard index could not be served off the card: {block}")
        self.assertEqual(areamod.list_areas()[0]["name"], name)
        self.assertEqual(
            self.net.urls + [str(p) for p in self.net.probes],
            [],
            f"serving what is already on the card reached for the network: "
            f"{self.net.urls[:2]}{self.net.probes[:2]}",
        )


if __name__ == "__main__":
    unittest.main()
