"""Canal & River Trust layers → GeoJSON on disk. BOOTSTRAP-TIME ONLY.

WHAT THIS IS FOR
    A canal is full of things that will stop this vehicle and are invisible from the
    surface: sluice intakes that pull, stop-plank grooves that eat a tether, outfalls,
    weirs, culvert mouths, safety gates. The Trust publishes all of them as open data.
    None of it is reachable from the canal bank, because the canal bank has no internet
    — so it is fetched HERE, while there still is some, and the serving side (another
    module) reads nothing but files.

THE WHOLE NETWORK, ONCE, NATIONALLY (download_national)
    The default is not a clipped copy per area. It is every layer, whole, for the
    country, in ONE shared directory — data/crt/national/ — fetched once and never
    again until it goes stale. A marker that is not held cannot be got at the
    waterside, and until this existed the Trust's vectors only arrived for water an
    operator had already decided to visit: the maps were a consequence of the plan
    rather than the thing you plan WITH. They matter in every mode — real sub,
    simulator, or a bench at home working out where to go — so the honest default is
    that they are simply present.

    ~150 MB, dominated by two polygon layers. That is fine: the handheld has the
    space and it is one download. NOTHING is left out for size and nothing is left out
    for tier. Satellite IMAGERY is the one thing that stays per-area — it is tiles, it
    is far bigger, and it is genuinely bounded by where the sub is going.

    The per-area path (download_hazards) is untouched and still works. What it is FOR
    has changed: an area is now an optimisation for what to DRAW, never a precondition
    for having the data.

    Same two-phase model as areas.py and satellite.py: this module talks to the network
    and is never imported by the runtime path. NO HOSTNAME IN THIS FILE — every one lives
    in config, is used exactly once at bootstrap, and is never resolved canal-side, where
    a DNS lookup does not fail so much as hang.

THE HONESTY DOCTRINE, APPLIED TO A DOWNLOAD
    An empty layer file and a missing layer file are opposite claims. "No sluices in this
    area" is a survey result; "the sluices never downloaded" is an absence of one, and a
    pilot who reads the second as the first drives into a sluice. So:
      * a layer that fetched cleanly and matched nothing in the area STILL gets a file,
        with zero features — that is the positive claim, and it is worth writing;
      * a layer whose fetch failed part-way gets NO layer file. A truncated page is the
        exact shape of "no hazards here" and there is no way to tell them apart later,
        so no .geojson is written and the failure is recorded instead. The serving side
        then reports it ABSENT, which is the true answer. (On the national path the
        pages that DID arrive are still kept — under a .resume name nothing globbing
        *.geojson can pick up as a layer — so the next run continues instead of
        starting 140 MB again. Nothing is served out of them until the layer is whole);
      * a licence that could not be read is recorded as null, never as "OGL v3". An
        assumed licence is an estimate wearing a measurement's clothes, same as any
        other number on this vehicle;
      * a fetch is a REFRESH of a card, never a rebuild of one. What is already on
        disk is hazard data somebody drove somewhere to get. A run that reached
        nothing has replaced nothing and so deletes nothing; it says plainly, and
        with a non-zero exit, that the card it left behind is older than the run
        that just finished. See _SWEEP_FLOOR.

WHAT A FETCH IS SANITY-CHECKED AGAINST
    Two independent counts, because a silently truncated page is this API's signature
    failure. Before paging, the server is asked returnCountOnly for the SAME bbox; after
    paging, what landed on disk is compared with it. And the layer's national count is
    compared with _EXPECTED_FEATURES below — a number measured against the live service,
    not copied from anywhere — so a service quietly swapped for one of its own legacy
    near-duplicates shows up as drift rather than as a shorter list nobody counted.

ON DISK
    <crt_dir>/national/<layer>.geojson     WHOLE FeatureCollection, national, unclipped
    <crt_dir>/national/<layer>.prov.json   that file's provenance, beside that file
    <crt_dir>/national/<layer>.resume.jsonl  a fetch in progress: one Feature per line
    <crt_dir>/national/<layer>.resume.json   how far that fetch got, so it continues
    <crt_dir>/national/provenance.json     index: every layer on the card, every skip,
                                           every warning — rewritten after EVERY layer
                                           so a run killed at the bank still describes
                                           what it left behind
    <crt_dir>/<area>/<layer>.geojson       the same layers CLIPPED to one area, an
    <crt_dir>/<area>/<layer>.prov.json     optimisation for drawing and nothing more
    <crt_dir>/<area>/provenance.json
    Every finished file is written through _write_atomic (or _write_collection_stream,
    which has the same guarantee for files too big to hold in memory), so a reader only
    ever sees a whole file. The resume pair is deliberately named so that NOTHING
    globbing *.geojson can pick up a partial layer as a real one.

RESUMABLE, BECAUSE 150 MB ON A CANAL-SIDE HOTSPOT IS NOT A THING TO START TWICE
    A layer already on disk and CURRENT is not re-fetched. A layer half-fetched
    continues from the feature it got to, because each page is appended to the .resume
    file the moment it arrives and the offset beside it is rewritten atomically. See
    _load_resume for what invalidates a partial (the answer is: the service moving, or
    the national count changing, because both make an offset address a different
    feature). "Current" is defined in config.crt_national_max_age_days: the file holds
    exactly as many features as the service says the layer has TODAY, and it was
    fetched inside the age window. Both are recorded per layer.

USAGE (bootstrap, online)
    python -m nav.crt --list
    python -m nav.crt --national                       # the whole network, resumable
    python -m nav.crt --status                         # what is on the card, no network
    python -m nav.crt gas-street                       # bbox from areas/<name>.json
    python -m nav.crt gas-street -1.92 52.47 -1.89 52.49

Pure stdlib (urllib + json). Every network call goes through the module-level _http_get,
which tests monkeypatch to run offline.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import html
import json
import logging
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import settings

log = logging.getLogger("neptune.nav.crt")

# National feature counts measured against the live services on 2026-08-07. These are here
# to be DISAGREED with: the Trust's org carries older near-duplicates of half these layers
# (five Sluices services answering 886, 892, 893 and 937 — the 937 one is Sluices_View_Public,
# not the Hub-curated view we ask for), and a page that silently stops short looks exactly
# like a service that simply has fewer features. Drift is recorded, never acted on — the
# Trust adds real culverts too, and this module has no business deciding which happened.
_EXPECTED_FEATURES: dict[str, int] = {
    "Canal_And_River_Trust_Winding_Holes_View/0": 534,
    "Canal_And_River_Trust_Wharves_View/0": 75,
    "Canal_And_River_Trust_Tunnel_Portals_View/0": 103,
    "Canal_And_River_Trust_Planning_Buffer_Polygon_View/0": 1296,
    "Canal_And_River_Trust_Canals_By_Navigation_View/1": 193,
    "Canal_And_River_Trust_Lakes_Ponds_Fisheries_View/0": 10,
    "Canal_And_River_Trust_Weirs_View/0": 1108,
    "Canal_And_River_Trust_Tunnels_View/0": 58,
    "Canal_And_River_Trust_Slipways_View/0": 57,
    "Canal_And_River_Trust_Reservoirs_View/0": 80,
    "Canal_And_River_Trust_Embankments_View/0": 1647,
    "Canal_And_River_Trust_Dry_Docks_view/0": 68,
    "Canal_And_River_Trust_Docks_View/0": 30,
    "Canal_And_River_Trust_Culverts_View/0": 2962,
    "Canal_And_River_Trust_Bridges_View/0": 6916,
    "Canal_And_River_Trust_Aqueducts_view/0": 329,
    "Canal_And_River_Trust_Locks_View/0": 1722,
    "Canal_And_River_Trust_Canals_By_KM_Length_View/1": 3173,
    "Canal_And_River_Trust_Boat_Lifts_View/0": 1,
    "Pumping_Station_(Points)/3": 88,
    "Canal_And_River_Trust_Sluices_View/0": 893,
    "Safety_Gates_View_Public/0": 180,
    "Stop_Plank_Grooves_View_Public/0": 2251,
    "Outfall_Discharge_Points_View_Public/0": 2903,
    "Towpath_Access_Points_2022/0": 7691,
    "Canal_And_River_Trust_Moorings_All_View/0": 3628,
    "Canal_And_River_Trust_Feeders_View/0": 429,
}

# The one obligation OGL v3 puts on us, and it has to travel WITH the data — a bare
# FeatureCollection copied out of this directory has lost its attribution, so it goes in
# the file as well as in the provenance.
OGL_ATTRIBUTION = (
    "Contains Canal & River Trust data (c) Canal & River Trust, " "licensed under the Open Government Licence v3.0"
)
_BASE_ATTRIBUTION = "Contains Canal & River Trust data (c) Canal & River Trust"

# Hard ceiling on resultRecordCount. The servers advertise maxRecordCount 2000 (1000 on
# Pumping_Station and Moorings) and standardMaxRecordCount 16000; asking for the larger
# number is how a request times out on a Pi over a phone hotspot.
_MAX_PAGE = 2000
# Paging guard. Nothing here is near it — the largest layer is 7691 features — so hitting
# it means the server stopped honouring resultOffset and is serving page 1 forever.
_MAX_PAGES = 400

# WHAT ENTITLES A RUN TO DELETE ANYTHING. Read this with the sweep at the bottom of
# download_hazards().
#
# The sweep used to build `keep` purely from what the current run fetched, so a run in
# which NOTHING downloaded deleted every layer file on the card and then returned
# ok:true, exit 0. That is not a corner: it is a bootstrap started after the hotspot
# has already dropped — the exact condition this whole module exists to get ahead of —
# and it destroyed a complete 26-layer offline hazard card, for the water a sub was
# about to go into, while telling the operator it had worked.
#
# So a sweep is a REFRESH and it may only take away what it has put back. Two things
# gate it. The run must have written at least one layer, because a fetch that retrieved
# nothing has replaced nothing. And it must have ACCOUNTED FOR — written, or left out
# by decision — at least this fraction of the layers that were on the card when it
# started, because that fraction is the only thing that tells "the Trust no longer
# publishes this layer" apart from "we could not ask", and a half-connected run
# produces the second while looking exactly like the first. Three quarters: one dead
# service in twenty-six is a bad afternoon at the Trust, twenty dead is a bad network,
# and only the second must freeze the card.
_SWEEP_FLOOR = 0.75

# The skips that are DECISIONS rather than failures: the layer was left out on purpose
# and nothing was lost. These are the words service.py's readiness gate already keys on
# (_DELIBERATE_SKIPS there), and the two lists have to stay the same words.
#
# Only a decision entitles the sweep to remove a file it did not write. A layer whose
# fetch merely FAILED still has real hazard data on the card from an earlier run, and
# last month's sluices are worth incomparably more than no sluices — they are recorded
# with the date they were actually fetched and left where they are.
_DELIBERATE_SKIPS = frozenset({"licence", "near-empty", "no-geometry"})


# ---- HTTP (stdlib; monkeypatched in tests) ---------------------------------
def _http_get(url: str, timeout: float = 30.0) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": settings.crt_user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted config URL)
        return r.read()


async def _get_json(url: str, tries: int = 3) -> dict | None:
    """Fetch + parse + rate-limit, with exponential backoff. Returns None on any failure,
    including the one that looks like success: ArcGIS answers HTTP 200 with a JSON body of
    {"error": {...}} for a bad layer id or a throttled key, and json.loads is perfectly
    happy with it. A caller that only checked the status code would page that error body
    forever, finding zero features in it each time and writing an empty layer that reads
    as "surveyed, nothing here"."""
    delay = 1.0 / max(0.5, settings.crt_rate_per_s)
    for attempt in range(tries):
        try:
            raw = await asyncio.to_thread(_http_get, url)
            data = json.loads(raw)
            if isinstance(data, dict) and "error" in data:
                raise ValueError(f"service error: {str(data['error'])[:200]}")
            await asyncio.sleep(delay)  # polite, and only on the way out
            return data
        except Exception as exc:  # noqa: BLE001
            if attempt == tries - 1:
                log.warning("crt fetch failed (%s): %s", url.split("?")[0], exc)
                return None
            await asyncio.sleep(delay + 0.5 * (2**attempt))
    return None


def _q(base: str, **params) -> str:
    return base + "?" + urllib.parse.urlencode(params)


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- licence ----------------------------------------------------------------
def _strip_html(s: str | None) -> str:
    """ArcGIS licence text is a blob of styled HTML. The terms are in the prose."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _classify_licence(text: str | None) -> str:
    """Name the licence family. NOT a legal opinion — a way of not writing "OGL v3" over
    something that says otherwise. Order matters: "Canal & River Trust Internal use only"
    contains the Trust's name, so the refusal is tested first."""
    t = (text or "").lower()
    if not t:
        return "unknown"
    if "internal use only" in t:
        return "restricted"
    if "open government licence" in t or "open government license" in t:
        return "ogl"
    if "inspire" in t:
        return "inspire"
    if "canal" in t and "trust" in t:
        return "crt-data-licence"
    return "unknown"


def _redistributable(klass: str) -> bool | None:
    """True / False / CANNOT-TELL. Only OGL is a licence this file can read as permissive,
    and only "internal use only" is a plain refusal. The CRT and INSPIRE licences are real
    documents with real terms that are not quoted in the item metadata — so the answer is
    None, which is a third thing, and not the reassuring one."""
    return {"ogl": True, "restricted": False}.get(klass)


def _attribution_for(klass: str, text: str | None) -> str:
    if klass == "ogl":
        return OGL_ATTRIBUTION
    if text:
        return f"{_BASE_ATTRIBUTION} — licence as published: {text}"
    return f"{_BASE_ATTRIBUTION} — licence NOT VERIFIED, do not redistribute"


# ---- discovery ---------------------------------------------------------------
def _service_name(url: str) -> str:
    """Trailing service name out of a FeatureServer URL, unquoted (one of them is
    Pumping_Station_(Points), whose brackets arrive percent-encoded)."""
    parts = [p for p in url.split("/") if p]
    for i, p in enumerate(parts):
        if p.lower() == "featureserver" and i:
            return urllib.parse.unquote(parts[i - 1])
    return urllib.parse.unquote(parts[-1] if parts else url)


async def _hub_services() -> list[dict]:
    """The ~20 datasets the Hub publishes. Each item carries its own licenceInfo, so no
    second request is needed for these.

    The item's `extent` is IGNORED on purpose: fourteen of the twenty report a
    world-spanning [-160,-80,160,80] box, which would pass any bbox-overlap test ever
    written and tell you a Birmingham layer covers Peru."""
    data = await _get_json(settings.crt_hub_search_url)
    if not data:
        log.warning("CRT Hub search unavailable — continuing with the hardcoded services only")
        return []
    out = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        url = props.get("url")
        if not url or "/FeatureServer" not in url:
            continue
        lic = _strip_html(props.get("licenseInfo"))
        out.append(
            {
                "title": props.get("title") or _service_name(url),
                "name": _service_name(url),
                "url": url.rstrip("/"),
                "source": "hub",
                "licence": lic or None,
                "licence_source": f"{settings.crt_hub_search_url} (properties.licenseInfo)",
            }
        )
    return out


def _extra_services() -> list[dict]:
    """The hardcoded ones. These are the reason this module exists: the Hub is a window
    onto 204 services, and sluices, safety gates, stop-plank grooves and outfalls — the
    four that most directly stop a small ROV — are all outside it."""
    out = []
    for name in settings.crt_extra_services:
        url = f"{settings.crt_org_service_root}/{urllib.parse.quote(name)}/FeatureServer"
        out.append({"title": name, "name": name, "url": url, "source": "org", "licence": None, "licence_source": None})
    return out


async def _resolve_licence(svc: dict, root: dict) -> None:
    """Fill in a non-Hub service's licence from its ArcGIS item. Mutates svc.

    The FeatureServer root has copyrightText ("Canal & River Trust") and that is an
    ATTRIBUTION, not terms — reading it as a licence is how every one of these ends up
    labelled OGL by wishful thinking. On failure the licence stays None, which is
    cannot-tell and is recorded as such."""
    if svc.get("licence"):
        return
    item_id = root.get("serviceItemId")
    if not item_id:
        svc["licence_note"] = "service has no serviceItemId — licence could not be looked up"
        return
    url = f"{settings.crt_item_lookup_url}/{item_id}?f=json"
    item = await _get_json(url)
    if not item:
        svc["licence_note"] = "item licence lookup failed (offline or throttled)"
        return
    svc["licence"] = _strip_html(item.get("licenseInfo")) or None
    svc["licence_source"] = url
    if not svc["licence"]:
        svc["licence_note"] = "item exists but publishes no licence text"


async def inventory() -> list[dict]:
    """Every (service, layer) we would fetch, with its national count and licence — and no
    feature data. This is the cheap call: it is what `--list` runs, and what a bootstrap
    should run first to see whether the Trust has moved anything."""
    seen: set[str] = set()
    services: list[dict] = []
    for svc in (await _hub_services()) + _extra_services():
        key = svc["url"].lower()  # Dry_Docks_view vs Dry_Docks_View are one service
        if key in seen:
            continue
        seen.add(key)
        services.append(svc)

    layers: list[dict] = []
    for svc in services:
        root = await _get_json(f"{svc['url']}?f=json")
        if not root:
            layers.append({**svc, "layer_id": None, "error": "service root unreadable"})
            continue
        await _resolve_licence(svc, root)
        found = root.get("layers") or []
        if not found:
            layers.append({**svc, "layer_id": None, "error": "service publishes no layers"})
            continue
        for lyr in found:
            # THE LAYER ID IS NOT ALWAYS 0. Canals services are layer 1 and Pumping_Station
            # is layer 3; a hardcoded /0 gets a service error, which _get_json turns into a
            # None and which a less careful reader would turn into an empty hazard layer.
            lid = lyr.get("id")
            meta = await _get_json(f"{svc['url']}/{lid}?f=json") or {}
            cnt = await _get_json(_q(f"{svc['url']}/{lid}/query", where="1=1", returnCountOnly="true", f="json"))
            ekey = f"{svc['name']}/{lid}"
            extent_sr = (meta.get("extent") or {}).get("spatialReference") or {}
            layers.append(
                {
                    **svc,
                    "layer_id": lid,
                    "layer_name": meta.get("name") or lyr.get("name"),
                    "geometry_type": meta.get("geometryType") or lyr.get("geometryType"),
                    "object_id_field": meta.get("objectIdField") or "OBJECTID",
                    "max_record_count": meta.get("maxRecordCount") or _MAX_PAGE,
                    # Storage CRS is a mix of 3857 and 27700 across these services, which is
                    # exactly why every query below passes outSR=4326 rather than trusting the
                    # default: an unnoticed 27700 easting reads as a longitude of 435000.
                    "storage_srs": extent_sr.get("latestWkid") or extent_sr.get("wkid"),
                    "national_features": (cnt or {}).get("count"),
                    "national_expected": _EXPECTED_FEATURES.get(ekey),
                    "layer_key": _layer_key(svc["name"], lid, meta.get("name")),
                }
            )
    return layers


# ---- naming & geometry -------------------------------------------------------
def _layer_key(service: str, layer_id, layer_name: str | None) -> str:
    """Filesystem key for one layer. Built from the SERVICE name, not the layer's display
    name — 'Sluices' is the display name of five different services on this org."""
    stem = re.sub(r"^canal_and_river_trust_", "", service.strip().lower())
    while True:  # Safety_Gates_View_Public sheds both
        shorter = re.sub(r"_(view|public)$", "", stem)
        if shorter == stem:
            break
        stem = shorter
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "layer"
    return f"{stem}-{layer_id}"


def safe_area_name(name: str) -> str | None:
    """Area names come from the operator (areas.py lets them type one). A name that walks
    out of the data directory is not a name — and neither is the reserved one.

    THE NATIONAL DIRECTORY IS NOT AN AREA. It sits beside the areas under crt_dir, and
    an operator who named a launch point "national" would otherwise have their 2 km
    clip write straight over the country's worth of layers — quietly, since every file
    in there has exactly the name it would have in an area. So the name is refused
    here, at the single point every path in this subsystem goes through, rather than
    guarded at each caller.
    """
    n = (name or "").strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n or ":" in n:
        return None
    if n.casefold() == settings.crt_national_name.casefold():
        return None
    return n if re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", n) else None


def area_dir(name: str) -> Path:
    """Where one area's hazard layers live. Pure path arithmetic — no hostname, no
    network. The serving side may import this; it must not import anything else here."""
    return settings.crt_dir / name


def provenance_path(name: str) -> Path:
    return area_dir(name) / "provenance.json"


def national_dir() -> Path:
    """Where the WHOLE Trust network lives, once, for every area and for none.

    Pure path arithmetic, exactly like area_dir, and offered to the serving side on the
    same terms: nothing in here resolves a hostname. Fetched once nationally on launch;
    an area never gates it.
    """
    return settings.crt_dir / settings.crt_national_name


def national_provenance_path() -> Path:
    return national_dir() / "provenance.json"


def _valid_bbox(b) -> bool:
    try:
        w, s, e, n = (float(v) for v in b)
    except (TypeError, ValueError):
        return False
    return -180 <= w < e <= 180 and -90 <= s < n <= 90


def area_bbox(name: str) -> list[float] | None:
    """The area's bbox as satellite.py/areas.py wrote it: areas/<name>.json → "bbox".
    None if the area was never downloaded, which is a real answer — hazards belong to an
    area, and there is no area to clip to yet."""
    meta = settings.areas_dir / f"{name}.json"
    if not meta.exists():
        return None
    try:
        bbox = json.loads(meta.read_text()).get("bbox")
    except Exception:  # noqa: BLE001
        return None
    return [float(v) for v in bbox] if _valid_bbox(bbox) else None


def _coords(geom) -> list:
    """Every [lon, lat] in a geometry, at any nesting depth."""
    out: list[tuple[float, float]] = []

    def walk(c):
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)) and len(c) >= 2:
                out.append((float(c[0]), float(c[1])))
            else:
                for sub in c:
                    walk(sub)

    walk((geom or {}).get("coordinates"))
    return out


def _collection_bbox(features: list[dict]) -> list[float] | None:
    """[W,S,E,N] of everything actually in the file, or None when it is empty. An empty
    collection gets NO bbox member rather than a zero-size one at [0,0]: RFC 7946 allows
    the member to be absent, and a box off the coast of Ghana is not a better answer than
    no box."""
    pts = [p for f in features for p in _coords(f.get("geometry"))]
    if not pts:
        return None
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return [min(lons), min(lats), max(lons), max(lats)]


def _keeps(feature: dict, bbox: list[float]) -> bool:
    """Does this feature belong in this area's file?

    Points are tested exactly. Lines and polygons are kept WHOLE when their own bounding
    box meets the area's, rather than being cut at the boundary, for two reasons that are
    specific to this vehicle. A polygon cut at the box edge grows a boundary nobody
    surveyed — on the planning-buffer layer that new edge would read as the edge of the
    buffer. And the canal centrelines are the snapping target (snap.py): a centreline
    truncated at the area edge stops snapping exactly where a tether-limited ROV spends
    its time, since the area is drawn around the launch point and the far end of the
    cable is near its boundary.

    So the file holds only features that touch this area — which is what clipping is for —
    and both the FeatureCollection and the provenance record clip.rule = "intersects", so
    a feature running off the edge is never mistaken for a mapping error.

    This runs even though the server was already asked for the same envelope, because the
    server's answer is not in our coordinate system. Measured 2026-08-07 on
    Canals_By_KM_Length (stored EPSG:27700) with an envelope of [-2.6,52.0,-1.0,53.2]: the
    server returned 1218 features, 15 of which lie wholly EAST of longitude -1.0, the
    furthest at -0.977 — about 1.5 km outside. The envelope is transformed to 27700 to run
    the query, and a lat/lon rectangle is not a rectangle there. The area bbox is a WGS84
    box (it is the same one the tile pyramid was cut from), so the file has to mean that
    box, not the server's transformed approximation of it."""
    pts = _coords(feature.get("geometry"))
    if not pts:
        return False
    w, s, e, n = bbox
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    if len(pts) == 1:
        return w <= lons[0] <= e and s <= lats[0] <= n
    return not (max(lons) < w or min(lons) > e or max(lats) < s or min(lats) > n)


# ---- writing to the card ------------------------------------------------------
def _write_atomic(path: Path, text: str) -> None:
    """Write a file so a reader only ever sees the whole of it.

    path.write_text() truncates the target first and then writes into it, so a fetch
    that died in the middle — Ctrl-C at the bank, the link dropping, a card with no
    room left — left a truncated GeoJSON where a whole one had been. That fragment has
    a name and a size, which is all the index and the readiness gate look at, so the
    console counted a hazard layer it did not have. Write beside it and rename, and the
    layer on the card is either the previous good file or the new good one and never
    half of either.

    os.replace is atomic on POSIX and on Windows alike and this repo runs on both,
    which is why it is used rather than unlink-then-rename. The temporary is in the
    SAME directory — a rename across filesystems is a copy, and a copy is not atomic —
    and its name is derived from the target's, so a hard kill leaves one piece of
    litter that the next run overwrites instead of a growing pile. The suffix goes on
    the END of the full name so the fragment matches neither *.geojson nor *.prov.json:
    nothing globbing this directory, here or in nominal.py, can pick one up as a layer.
    """
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # BaseException on purpose: KeyboardInterrupt is the case this exists for.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # nothing to add to whatever is already raising
            pass
        raise


def _carried_record(out_dir: Path, key: str, why: str) -> dict | None:
    """The record of an EARLIER fetch of a layer whose file this run did not replace.

    The index has to describe the CARD, not the run. A layer file still on disk is
    still hazard data, and an index that listed only what this run downloaded would
    tell the console the operator has nothing while two dozen good layers sit beside
    it — the same lie as the deleting sweep, told in the other file.

    What is copied forward is the layer's own provenance from beside the file, fetch
    time included: that is the honest part, because this is old data and the record
    says how old, and service.py shows that date per layer. Returns None when nothing
    beside the file can vouch for it — an entry invented here would be this module
    doing precisely what it refuses to let a truncated ArcGIS page do.
    """
    try:
        rec = json.loads((out_dir / f"{key}.prov.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable is an answer; the caller reports it
        return None
    if not isinstance(rec, dict) or rec.get("features") is None:
        # features is the field the console reads as "surveyed, and this is what is in
        # it". A carried record without one would arrive downstream as a zero, which is
        # the survey result "nothing of this kind here" — the one claim never to guess.
        return None
    return {**rec, "layer_key": key, "file": f"{key}.geojson", "carried_over": True, "refresh_failed": why}


# ---- the download job ---------------------------------------------------------
async def _fetch_layer(lyr: dict, bbox: list[float]) -> tuple[list[dict] | None, dict]:
    """Page one layer inside bbox. Returns (features, stats); features is None if any page
    failed, because a partial layer on disk is indistinguishable from a safe one."""
    base = f"{lyr['url']}/{lyr['layer_id']}/query"
    envelope = {
        "geometry": ",".join(str(v) for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    stats: dict = {"pages": 0, "server_bbox_count": None, "clipped_out": 0}

    # What the server thinks is in this box, asked BEFORE paging. This is the only
    # independent witness to a truncated page there is.
    cnt = await _get_json(_q(base, where="1=1", returnCountOnly="true", f="json", **envelope))
    stats["server_bbox_count"] = (cnt or {}).get("count")

    page = min(int(lyr.get("max_record_count") or _MAX_PAGE), _MAX_PAGE)
    stats["page_size"] = page
    feats: list[dict] = []
    offset = 0
    while True:
        fc = await _get_json(
            _q(
                base,
                where="1=1",
                outFields="*",
                outSR="4326",
                f="geojson",
                orderByFields=lyr["object_id_field"],
                resultRecordCount=str(page),
                resultOffset=str(offset),
                **envelope,
            )
        )
        if fc is None:
            stats["error"] = f"page at offset {offset} failed after retries"
            return None, stats
        got = fc.get("features") or []
        feats.extend(got)
        stats["pages"] += 1
        if len(got) < page:
            break
        offset += len(got)
        if stats["pages"] >= _MAX_PAGES:
            stats["error"] = f"stopped at {_MAX_PAGES} pages — resultOffset is not advancing"
            return None, stats

    kept = [f for f in feats if _keeps(f, bbox)]
    stats["clipped_out"] = len(feats) - len(kept)
    stats["fetched_before_clip"] = len(feats)
    return kept, stats


async def download_hazards(area: str, bbox: list[float] | None = None, progress=None) -> dict:
    """Fetch every usable CRT layer for one offline area → <crt_dir>/<area>/.

    Returns a result dict and does NOT raise: this runs inside bootstrap, alongside a
    satellite download that may already have succeeded, and a raise here would throw away
    an area's imagery over a Trust server having a bad afternoon."""

    async def emit(msg: dict) -> None:
        log.info("crt %s", msg)
        if progress:
            # `scope` on every message, so one progress channel can carry both jobs
            # without a watcher having to guess which one it is looking at. The
            # national fetch emits the same shape with scope="national"; nav/service.py
            # broadcasts both as {"type":"area_fetch", …} and the console tells them
            # apart by this word rather than by a second socket nobody has written.
            await progress({"scope": "area", **msg})

    name = safe_area_name(area)
    if not name:
        return {"ok": False, "error": f"unusable area name {area!r}"}
    if bbox is None:
        bbox = area_bbox(name)
    if bbox is None:
        return {
            "ok": False,
            "area": name,
            "error": f"no bbox: pass one, or download the area first so "
            f"{settings.areas_dir / (name + '.json')} exists",
        }
    if not _valid_bbox(bbox):
        return {"ok": False, "area": name, "error": f"bbox {bbox!r} is not [W,S,E,N] in degrees"}
    bbox = [float(v) for v in bbox]

    await emit({"area": name, "state": "discovering", "bbox": bbox})
    layers = await inventory()
    if not layers:
        return {
            "ok": False,
            "area": name,
            "error": "no services reachable — this is a BOOTSTRAP task and needs internet",
        }

    out_dir = area_dir(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    # WHAT WAS ON THE CARD BEFORE THIS RUN TOUCHED IT. Both the sweep and the claim of
    # success are judged against this: a run cannot know it improved a card it never
    # looked at, and it certainly cannot know it is entitled to empty one.
    on_card_keys = {p.name[: -len(".geojson")] for p in out_dir.glob("*.geojson")}
    fetched: list[dict] = []
    skipped: list[dict] = []
    warnings: list[str] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    used: set[str] = set()
    for i, lyr in enumerate(layers):
        key = lyr.get("layer_key") or _layer_key(lyr["name"], lyr.get("layer_id"), None)
        if key in used:
            # Two services whose names shorten to the same stem would otherwise overwrite
            # each other's file, and the loser would look like a layer that was never
            # fetched. Nothing collides today; this is here so a new service cannot make
            # a layer disappear silently.
            key = f"{key}-{len(used)}"
        used.add(key)
        klass = _classify_licence(lyr.get("licence"))
        base = {
            "layer_key": key,
            "title": lyr.get("title"),
            "service": lyr["name"],
            "service_url": lyr["url"],
            "layer_id": lyr.get("layer_id"),
            "layer_name": lyr.get("layer_name"),
            "source": lyr.get("source"),
            "licence": lyr.get("licence"),
            "licence_class": klass,
            "licence_source": lyr.get("licence_source"),
            "licence_note": lyr.get("licence_note"),
            "redistributable": _redistributable(klass),
        }
        await emit({"area": name, "state": "layer", "n": i + 1, "of": len(layers), "layer": key})

        if lyr.get("error"):
            skipped.append({**base, "skipped": "unreadable", "why": lyr["error"]})
            continue
        if not lyr.get("geometry_type"):
            skipped.append(
                {**base, "skipped": "no-geometry", "why": "layer publishes no geometry — nothing to draw on a map"}
            )
            continue
        if klass == "restricted" and settings.crt_restricted == "skip":
            skipped.append(
                {
                    **base,
                    "skipped": "licence",
                    "why": f"licence forbids reuse ({lyr.get('licence')!r}) and " f"NAV_CRT_RESTRICTED=skip",
                }
            )
            continue
        national = lyr.get("national_features")
        if national is not None and national < settings.crt_min_features:
            skipped.append(
                {
                    **base,
                    "skipped": "near-empty",
                    "national_features": national,
                    "why": f"{national} features nationwide (< {settings.crt_min_features}) "
                    f"— a toggle that can only ever be empty",
                }
            )
            continue

        feats, stats = await _fetch_layer(lyr, bbox)
        rec = {
            **base,
            "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "area": name,
            "bbox": bbox,
            "clip_rule": "intersects",
            "geometry_type": lyr.get("geometry_type"),
            "object_id_field": lyr.get("object_id_field"),
            "storage_srs": lyr.get("storage_srs"),
            "out_srs": 4326,
            "national_features": national,
            "national_expected": lyr.get("national_expected"),
            "max_record_count": lyr.get("max_record_count"),
            "attribution": _attribution_for(klass, lyr.get("licence")),
            **stats,
        }

        exp = lyr.get("national_expected")
        if exp is not None and national is not None and national != exp:
            rec["national_drift"] = national - exp
            warnings.append(
                f"{key}: {national} features nationwide, expected {exp} "
                f"(service changed, or a different service answered)"
            )
        if national is None:
            rec["count_check"] = "unavailable"
            warnings.append(f"{key}: national count unavailable — truncation could not be checked")

        if feats is None:
            skipped.append(
                {
                    **rec,
                    "skipped": "fetch-failed",
                    "why": stats.get("error", "fetch failed"),
                    "note": "no file written on purpose — a partial layer reads as "
                    "'nothing here', which is the one lie that matters",
                }
            )
            warnings.append(f"{key}: {stats.get('error', 'fetch failed')} — layer NOT written")
            continue

        # The truncation check. server_bbox_count came from a separate query before paging.
        served = stats.get("server_bbox_count")
        got = stats.get("fetched_before_clip")
        if served is None:
            rec["count_check"] = "unavailable"
        elif served == got:
            rec["count_check"] = "agrees"
        else:
            rec["count_check"] = "disagrees"
            warnings.append(
                f"{key}: server counted {served} features in this bbox, paging " f"returned {got} — possible truncation"
            )

        rec["features"] = len(feats)
        # RFC 7946 §5: a FeatureCollection's bbox is the bbox OF ITS FEATURES. Writing the
        # area bbox there would be a small lie with a specific victim — a whole canal
        # navigation kept for touching this area extends far past it, and a consumer
        # trusting the member would think it had the whole route in a 2 km box. So the
        # member says what is actually in the file, and the area it was cut for is a
        # separate, honestly-named `clip`.
        span = _collection_bbox(feats)
        fc = {
            "type": "FeatureCollection",
            "attribution": rec["attribution"],
            "clip": {"area": name, "bbox": bbox, "rule": "intersects"},
            "features": feats,
        }
        if span:
            fc["bbox"] = span
        rec["feature_bbox"] = span
        path = out_dir / f"{key}.geojson"
        _write_atomic(path, json.dumps(fc))
        rec["file"] = path.name
        rec["bytes"] = path.stat().st_size
        # Per-file provenance, beside the file, built from the SAME dict as the index below
        # so the two can never disagree about what was downloaded. It is also what a later
        # run reads to carry this layer forward when it cannot refresh it.
        _write_atomic(out_dir / f"{key}.prov.json", json.dumps(rec, indent=1))
        fetched.append(rec)
        # Three different statements, kept separate on purpose. "The terms say no" and "the
        # terms were never read" are the licence version of the honesty doctrine, and
        # collapsing them into one polite hedge is how an unread licence gets published.
        if rec["redistributable"] is False:
            warnings.append(
                f"{key}: licence REFUSES reuse ({lyr.get('licence')!r}) — "
                f"downloaded as this operator's own safety copy under "
                f"NAV_CRT_RESTRICTED=flag; DO NOT redistribute this file"
            )
        elif rec["redistributable"] is None:
            warnings.append(
                f"{key}: licence is {klass} ({lyr.get('licence')!r}) — real terms "
                f"exist and are NOT quoted in the item metadata, so this is "
                f"cannot-tell, not permission. Read them before redistributing"
            )
        await emit({"area": name, "state": "wrote", "layer": key, "features": len(feats), "of_national": national})

    # ---- the sweep --------------------------------------------------------------
    # Anything this run did NOT write is a candidate, and no more than a candidate.
    # Re-running after NAV_CRT_RESTRICTED flips to skip, or after the Trust withdraws a
    # service, otherwise leaves last month's file on disk with nothing in the new
    # provenance describing it — a hazard layer that is simultaneously "not fetched" and
    # sitting there being served. That is what this exists for, and it is ALL it is for:
    # see _SWEEP_FLOOR for the run that deleted a whole card and reported success. Only
    # the two extensions this module writes are ever touched.
    written_keys = {r["layer_key"] for r in fetched}
    decided_keys = {s["layer_key"] for s in skipped if s.get("skipped") in _DELIBERATE_SKIPS}
    # How much of THE CARD this run accounted for — intersected with what was there,
    # because a layer this run decided against that is not on the card is no evidence
    # that the card was re-surveyed, and permanently-skipped layers would otherwise
    # inflate the number every single run.
    covered = len((written_keys | decided_keys) & on_card_keys)
    floor = math.ceil(_SWEEP_FLOOR * len(on_card_keys))
    is_refresh = bool(fetched) and covered >= floor

    removed: list[str] = []
    carried_keys: set[str] = set()
    for old in sorted(out_dir.glob("*.geojson")) + sorted(out_dir.glob("*.prov.json")):
        key = old.name[: -len(".prov.json")] if old.name.endswith(".prov.json") else old.name[: -len(".geojson")]
        if key in written_keys:
            continue  # this run put it there
        # A file this run did not write is somebody's hazard data until there is a
        # POSITIVE reason it should not be: either this run's configuration left that
        # layer out on purpose, or discovery — which the floor above says actually
        # ran — no longer lists the layer at all. "The fetch failed" is not such a
        # reason. `used` is every layer key this run's discovery offered.
        why_gone = (
            "left out of this run on purpose"
            if key in decided_keys
            else "no longer offered by the Trust's services" if key not in used else ""
        )
        if is_refresh and why_gone:
            old.unlink()
            removed.append(old.name)
            warnings.append(f"{old.name}: {why_gone} — deleted rather than served as " f"current")
            continue
        if old.name.endswith(".geojson"):
            carried_keys.add(key)

    # ---- what survived, said out loud -------------------------------------------
    # Every surviving file is described in the index or named as unaccounted for. A
    # layer on the card and absent from its own index is the deleting sweep's mistake
    # wearing different clothes: the console would report nothing where there is data.
    carried: list[dict] = []
    unaccounted: list[str] = []
    skip_by_key = {s.get("layer_key"): s for s in skipped}
    for key in sorted(carried_keys):
        why = (skip_by_key.get(key) or {}).get("why") or "this run did not reach this layer at all"
        rec = _carried_record(out_dir, key, why)
        if rec is None:
            unaccounted.append(f"{key}.geojson")
            warnings.append(
                f"{key}.geojson: on the card with no readable provenance "
                f"beside it — KEPT (nothing here deletes hazard data) but not "
                f"listed as a layer, because nothing can say what is in it or "
                f"when it was fetched"
            )
            continue
        carried.append(rec)
        warnings.append(
            f"{key}: NOT refreshed by this run ({why}) — the file fetched "
            f"{rec.get('fetched')} is still on the card and is what the "
            f"console will draw. Older data, honestly dated, beats none"
        )
    # Said in ONE place. service.py reads `skipped` as "this layer is not on the card at
    # all"; a layer whose file survived is present, and reporting it in both lists would
    # have the console call the same layer present and missing in one breath.
    carried_names = {r["layer_key"] for r in carried}
    skipped = [s for s in skipped if s.get("layer_key") not in carried_names]

    index = {
        "area": name,
        "bbox": bbox,
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "removed_stale": removed,
        # What this run did to the card, as opposed to what it downloaded. A reader
        # comparing `written` with `on_card_at_start` can see a partial refresh
        # without having to count two lists.
        "refresh": {
            "on_card_at_start": len(on_card_keys),
            "written": len(written_keys),
            "card_accounted": covered,
            "sweep_floor_layers": floor,
            "swept": is_refresh,
            "carried_over": sorted(carried_names),
            "unaccounted_files": unaccounted,
        },
        "generator": "api/nav/crt.py",
        "hub": settings.crt_hub_search_url,
        "org": settings.crt_org_service_root,
        "clip_rule": "intersects (features kept whole; nothing is cut at the bbox edge)",
        "attribution": OGL_ATTRIBUTION,
        # The card, not the run: this run's layers plus the ones it could not
        # refresh and therefore left alone, each with its own fetch date.
        "layers": fetched + carried,
        "skipped": skipped,
        "warnings": warnings,
    }
    _write_atomic(provenance_path(name), json.dumps(index, indent=1))

    # ---- was this a success? ----------------------------------------------------
    # Success is not "the command finished". It is: something was downloaded, and every
    # layer that was on this card when the run started is either refreshed or gone by
    # decision. Anything else leaves the operator with a card older or thinner than they
    # think, and it has to cost an exit code — _main and nav/cli.py both return 1 on
    # ok:false — because the moment to learn it is while there is still internet.
    reasons: list[str] = []
    if not fetched and on_card_keys:
        reasons.append(
            f"NOTHING DOWNLOADED: 0 of {len(layers)} layers were written. The "
            f"{len(on_card_keys)} layer file(s) already on this card were left "
            f"exactly as they were and nothing was deleted — what the console "
            f"draws is the earlier fetch, not this run. Run this again where "
            f"there is internet"
        )
    elif not fetched:
        reasons.append(
            f"NOTHING DOWNLOADED: 0 of {len(layers)} layers were written and "
            f"this card holds no hazard data at all. This is a BOOTSTRAP task "
            f"and it needs the internet"
        )
    if carried:
        reasons.append(
            f"{len(carried)} layer(s) could not be refreshed and are being "
            f"served from an earlier fetch: {', '.join(sorted(carried_names))}"
        )
    if unaccounted:
        reasons.append(
            f"{len(unaccounted)} file(s) on this card have no readable "
            f"provenance and nothing claims to know what is in them: "
            f"{', '.join(unaccounted)}"
        )
    ok = not reasons

    await emit(
        {
            "area": name,
            "state": "done",
            "layers": len(fetched),
            "carried": len(carried),
            "removed": len(removed),
            "skipped": len(skipped),
            "warnings": len(warnings),
            "ok": ok,
        }
    )
    res = {
        "ok": ok,
        "area": name,
        "dir": str(out_dir),
        "layers": len(fetched),
        "features": sum(r["features"] for r in fetched),
        "carried_over": len(carried),
        "on_card": len(fetched) + len(carried),
        "removed_stale": len(removed),
        "skipped": len(skipped),
        "warnings": warnings,
    }
    if reasons:
        res["error"] = "; ".join(reasons)
    return res


# ==========================================================================
# THE NATIONAL SET — every layer, whole, once, in one shared directory
# ==========================================================================
#
# WHY THIS IS THE DEFAULT AND THE CLIP IS THE OPTIMISATION. Everything above fetches
# what fits inside one 2.4 km box, which means the Trust's data only ever arrived for
# water somebody had ALREADY decided to visit. The maps are how this thing is
# navigated — at the bank, in the simulator, and at a kitchen table on a Thursday
# working out where to put it in — so the data cannot be downstream of the plan. It is
# 150 MB and the handheld has hundreds of gigabytes: the honest default is that all of
# it is simply there.
#
# WHAT IS DIFFERENT FROM THE AREA PATH, AND WHAT IS DELIBERATELY IDENTICAL. Different:
# no envelope on any query, no clipping afterwards, one directory for the whole
# country, and a fetch that can be stopped and continued. Identical, because every one
# of them is a trap this module already paid for:
#   * the layer id is READ off the service and is not always 0 (Canals is 1, Pumping
#     Station is 3) — both paths go through inventory();
#   * outSR=4326 on every feature query, because the storage CRS is a mix of 3857 and
#     27700 and an unnoticed 27700 easting reads as a longitude of 435000;
#   * orderByFields=<objectIdField>, because resultOffset paging over an unordered
#     result set is not paging, it is sampling;
#   * paging stops on a SHORT page and only on a short page, with _MAX_PAGES as the
#     guard against a server that has stopped honouring resultOffset;
#   * an ArcGIS error body arrives as HTTP 200 and _get_json turns it into None;
#   * a layer that could not be finished writes NO .geojson, because a truncated
#     hazard layer is the exact shape of "nothing here".
#
# THE ONE THING A NATIONAL FETCH ADDS TO THE DOCTRINE. A partial fetch still writes no
# layer file — but it no longer throws the pages away either. They go into a .resume
# pair that nothing globbing *.geojson can mistake for a layer, and the next run
# continues from the feature the last one reached. Dropping 140 MB because a hotspot
# died at 95% would make the whole national idea unusable in the only place it gets
# used.

_RESUME_DATA = ".resume.jsonl"  # one Feature per line, append-only, in OBJECTID order
_RESUME_META = ".resume.json"  # how far that got, rewritten atomically per page

# Bytes per feature by geometry type, used ONLY to size the first page of a layer.
# Measured on the real fetched card (data/crt/gas-street) and quoted in the brief that
# asked for this: point layers run 270-350 B a feature, bridges 330, the canal
# centreline 2,422, canals-by-navigation 46,262 and the planning buffer polygon 82,880.
# Rounded UP per class on purpose — guessing small costs one extra request, guessing
# large costs a 160 MB response that a Pi cannot parse and a hotspot cannot deliver.
# After the first page the real measured size takes over (see _repage).
_BYTES_PER_FEATURE = {
    "esriGeometryPoint": 400,
    "esriGeometryMultipoint": 800,
    "esriGeometryPolyline": 3000,
    "esriGeometryPolygon": 60000,
}
_BYTES_PER_FEATURE_UNKNOWN = 3000

# A page must never be smaller than this. Otherwise one enormous feature — a whole
# navigation's boundary polygon — could drive the page size to 1 and turn a 200-feature
# layer into 200 rate-limited requests.
_MIN_PAGE = 25


def _page_for(geometry_type: str | None, max_record: int | None, bytes_per_feature: float | None = None) -> int:
    """How many features to ask for in one request, from a BYTE budget.

    resultRecordCount is a count and the thing that actually hurts is a response size:
    2000 planning-buffer polygons is 160 MB of JSON to be parsed whole. resultOffset is
    a FEATURE offset, so shrinking the page changes only the number of requests — never
    what lands, and never a resume point.
    """
    per = bytes_per_feature or _BYTES_PER_FEATURE.get(geometry_type or "", _BYTES_PER_FEATURE_UNKNOWN)
    budget = max(1.0, float(settings.crt_page_bytes))
    want = int(budget / max(1.0, float(per)))
    ceiling = min(int(max_record or _MAX_PAGE), _MAX_PAGE)
    return max(_MIN_PAGE, min(want, ceiling)) if ceiling >= _MIN_PAGE else ceiling


def _resume_paths(out_dir: Path, key: str) -> tuple[Path, Path]:
    return out_dir / f"{key}{_RESUME_DATA}", out_dir / f"{key}{_RESUME_META}"


def _complete_lines(path: Path) -> int:
    """How many WHOLE lines the resume file holds, discarding a half-written tail.

    A page is appended as one write and a process can be killed in the middle of it, so
    the only line that can ever be damaged is the last. Counting newlines is a scan and
    not a parse — the biggest of these files is over 100 MB and re-parsing it on every
    resume would cost more than re-downloading the layer.
    """
    keep, n = 0, 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                n += chunk.count(b"\n")
                cut = chunk.rfind(b"\n")
                if cut >= 0:
                    keep = fh.tell() - len(chunk) + cut + 1
    except FileNotFoundError:
        return 0
    if keep != path.stat().st_size:
        # The tail after the last newline is a page that was arriving when the power
        # went. Truncated rather than kept: a half feature is not a feature, and the
        # offset below is counted from what survives.
        with open(path, "r+b") as fh:
            fh.truncate(keep)
    return n


def _load_resume(out_dir: Path, key: str, ident: str) -> dict:
    """Where the last attempt at this layer got to, or a clean start.

    A PARTIAL IS ONLY WORTH ANYTHING IF IT IS A PARTIAL OF THE SAME THING. `ident`
    carries the service URL, the layer id, the object-id field and the layer's national
    feature count. Every one of those changes what a resultOffset ADDRESSES: if the
    Trust has added 40 culverts since yesterday, feature 2000 is no longer the feature
    2000 we already have, and continuing would interleave two datasets into one file
    that looks perfectly well-formed. So a mismatch deletes the partial and starts
    again, which is the only safe answer and is said out loud in the record.
    """
    data, meta_p = _resume_paths(out_dir, key)
    fresh = {"offset": 0, "pages": 0, "bbox": None, "ident": ident, "started": _iso(), "restarted": None}
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no partial, or an unreadable one; both start over
        meta = None
    if not isinstance(meta, dict) or meta.get("ident") != ident:
        had = data.exists()
        data.unlink(missing_ok=True)
        meta_p.unlink(missing_ok=True)
        if had:
            fresh["restarted"] = (
                "the service, the layer id or the national feature "
                "count changed since the partial was written, so its "
                "paging offsets address different features"
            )
        return fresh
    on_disk = _complete_lines(data)
    if on_disk != meta.get("offset"):
        # THE FILE IS THE TRUTH AND THE NOTE BESIDE IT IS A CLAIM. A kill between the
        # append and the meta write leaves them one page apart; the file wins, and the
        # running bbox is dropped because it may describe features that did not survive
        # — an over-large bbox member is a small lie with a specific victim (a consumer
        # that trusts it thinks it holds more of the network than it does).
        meta["bbox"] = None
    meta["offset"] = on_disk
    meta.setdefault("pages", 0)
    meta.setdefault("started", _iso())
    meta["ident"] = ident
    meta["restarted"] = None
    return meta


def _grow(bbox: list[float] | None, feats: list[dict]) -> list[float] | None:
    """Running [W,S,E,N] over the pages so far. Accumulated as they arrive rather than
    computed at the end: the alternative is a second full parse of a 100 MB file to
    learn something every page already knew."""
    span = _collection_bbox(feats)
    if span is None:
        return bbox
    if bbox is None:
        return span
    return [min(bbox[0], span[0]), min(bbox[1], span[1]), max(bbox[2], span[2]), max(bbox[3], span[3])]


def _write_collection_stream(path: Path, lines: Path, head: dict, index_path: Path | None = None) -> int:
    """A FeatureCollection assembled from the resume file, without holding it in memory.

    Same guarantee as _write_atomic and for the same reason — a reader sees the whole
    file or the previous one, never half of either — but json.dumps of a 100 MB
    collection would need the decoded features, the encoded string and the file all at
    once. The features are already valid JSON on disk, one per line, so they are copied
    through as bytes.

    AND IT BUILDS THE WINDOW INDEX WHILE IT IS ALREADY TOUCHING EVERY FEATURE. See
    _WINDOW_INDEX below for what that is for. Doing it here costs one parse of data
    this machine has just downloaded, at bootstrap, with a connection; doing it later
    would mean parsing 100 MB on a handheld that is flying a sub.

    Byte offsets, so the file is opened in BINARY: a text file's tell() hands back an
    opaque cookie rather than a position, and a Trust place-name with an accent in it
    would put every offset after it out by one.
    """
    tmp = path.with_name(path.name + ".part")
    entries: list[list] = []
    try:
        with open(tmp, "wb") as out:
            head_txt = json.dumps(head)
            out.write((head_txt[:-1] + ',"features":[').encode("utf-8"))
            first = True
            try:
                with open(lines, "rb") as src:
                    for raw in src:
                        raw = raw.strip()
                        if not raw:
                            continue
                        if not first:
                            out.write(b",")
                        start = out.tell()
                        out.write(raw)
                        first = False
                        entries.append([start, len(raw)] + _feature_box(raw))
            except FileNotFoundError:
                pass  # a layer with no features still gets a file
            out.write(b"]}")
        os.replace(tmp, path)
    except BaseException:
        # BaseException on purpose: KeyboardInterrupt is the case this exists for.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if index_path is not None:
        _write_atomic(
            index_path,
            json.dumps(
                {
                    "scope": "national",
                    "file": path.name,
                    "features": len(entries),
                    "schema": "[byte offset, byte length, W, S, E, N] per feature, in file order",
                    "entries": entries,
                }
            ),
        )
    return path.stat().st_size


# THE WINDOW INDEX, AND THE FAILURE IT EXISTS FOR.
#
# The console cannot hold this data. One national layer is 100 MB of planning-buffer
# polygons; JSON.parse on that stalls the browser's main thread for seconds and leaves
# several hundred megabytes of heap behind — on the one machine standing between an
# operator and a sub on a cable. So client/js/crt.js asks for a WINDOW around where the
# map is looking (?bbox=W,S,E,N) and reports HELD-but-not-drawn for anything the backend
# hands back whole and over its ceiling. Without this index that layer would sit on the
# handheld, complete and correct, and never once be drawn — which is exactly the
# "present and invisible" failure this round is against, arriving by the back door.
#
# So each layer gets a sidecar: per feature, where its bytes are in the .geojson and
# what its bounding box is. A windowed read is then a stat, a small JSON load, and a
# seek-and-copy of the features that overlap — no parse of the layer at all, at any
# size. The boxes are rounded OUTWARD, so rounding can only ever include a feature that
# was borderline; a window that quietly dropped one would be the map hiding a hazard.
_INDEX_SUFFIX = ".index.json"


def _feature_box(raw: bytes) -> list[float]:
    """[W,S,E,N] of one feature line, rounded outward. [] when it has no coordinates."""
    try:
        span = _collection_bbox([json.loads(raw)])
    except Exception:  # noqa: BLE001 — a feature nothing can box is simply never windowed out
        return []
    if not span:
        return []
    w, s, e, n = span
    return [math.floor(w * 1e4) / 1e4, math.floor(s * 1e4) / 1e4, math.ceil(e * 1e4) / 1e4, math.ceil(n * 1e4) / 1e4]


def national_index_path(key: str) -> Path:
    return national_dir() / f"{key}{_INDEX_SUFFIX}"


async def _fetch_layer_national(lyr: dict, out_dir: Path, key: str, emit) -> tuple[bool, dict]:
    """Page one layer WHOLE, nationally, continuing from wherever the last run stopped.

    Returns (finished, stats). `finished` False means no .geojson was written and the
    .resume pair is still on disk holding what did arrive — which is the entire point:
    on a canal-side hotspot the second attempt starts where the first one died.
    """
    base = f"{lyr['url']}/{lyr['layer_id']}/query"
    server = lyr.get("national_features")
    oid = lyr.get("object_id_field") or "OBJECTID"
    ident = "|".join(str(v) for v in (lyr["url"], lyr["layer_id"], oid, server))
    data, meta_p = _resume_paths(out_dir, key)
    st = _load_resume(out_dir, key, ident)
    offset = int(st["offset"])
    bbox = st.get("bbox")
    page = _page_for(lyr.get("geometry_type"), lyr.get("max_record_count"))
    stats: dict = {
        "pages": 0,
        "resumed_from": offset,
        "page_size": page,
        "server_count": server,
        "restarted": st.get("restarted"),
    }
    if offset:
        await emit({"state": "resumed", "layer": key, "features": offset, "of": server})

    bytes_seen, feats_seen = 0, 0
    while True:
        url = _q(
            base,
            where="1=1",
            outFields="*",
            outSR="4326",
            f="geojson",
            orderByFields=oid,
            resultRecordCount=str(page),
            resultOffset=str(offset),
        )
        fc = await _get_json(url)
        if fc is None:
            stats["error"] = f"page at offset {offset} failed after retries"
            stats["kept_partial"] = offset
            return False, stats
        got = fc.get("features") or []
        if got:
            # APPENDED THE MOMENT IT ARRIVES, and the offset beside it rewritten after
            # every page. The most a kill can cost is the page that was in flight.
            with open(data, "a", encoding="utf-8") as fh:
                for f in got:
                    fh.write(json.dumps(f, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            bbox = _grow(bbox, got)
            offset += len(got)
            feats_seen += len(got)
            bytes_seen += sum(len(json.dumps(f, separators=(",", ":"))) + 1 for f in got)
        stats["pages"] += 1
        _write_atomic(
            meta_p,
            json.dumps(
                {
                    "offset": offset,
                    "pages": stats["pages"],
                    "bbox": bbox,
                    "ident": ident,
                    "started": st.get("started"),
                    "touched": _iso(),
                }
            ),
        )
        await emit({"state": "paging", "layer": key, "features": offset, "of": server, "pages": stats["pages"]})
        if len(got) < page:
            break  # a short page is the end, and nothing else is
        if stats["pages"] >= _MAX_PAGES:
            stats["error"] = f"stopped at {_MAX_PAGES} pages — resultOffset is not advancing"
            stats["kept_partial"] = offset
            return False, stats
        # THE MEASURED SIZE TAKES OVER FROM THE GUESS. _BYTES_PER_FEATURE only has to
        # get the FIRST page into a sane range; from here the layer's own bytes decide,
        # which is how the planning-buffer polygons end up at ~96 a page and the point
        # layers stay at the service's own ceiling.
        if feats_seen:
            page = _page_for(
                lyr.get("geometry_type"), lyr.get("max_record_count"), bytes_per_feature=bytes_seen / feats_seen
            )
    stats["features"] = offset
    stats["feature_bbox"] = bbox
    stats["bytes_per_feature"] = round(bytes_seen / feats_seen, 1) if feats_seen else None
    return True, stats


def _age_days(fetched: str | None) -> float | None:
    """How old a recorded fetch is, in days, or None when nothing can say.

    calendar.timegm and NOT time.mktime, which reads a struct as LOCAL time. Every
    stamp this module writes is UTC (the trailing Z says so), and the mktime version of
    this — even corrected with time.timezone — is out by the DST offset for half the
    year. Measured on this machine in August: a lock written one second earlier came
    back as an hour and one second old, which is past _LOCK_STALE_S, so the guard that
    stops two processes appending to the same half-downloaded layer would have been
    stale from the instant it was taken, every summer.
    """
    if not fetched:
        return None
    try:
        t = time.strptime(fetched, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return max(0.0, (time.time() - calendar.timegm(t)) / 86400.0)


def _layer_record(out_dir: Path, key: str) -> dict | None:
    """The record BESIDE a layer file, which is the only thing entitled to vouch for it.

    Read from <key>.prov.json and never from the index. "There is a file there" and "is
    this layer current" are different questions, and a run that only asks the first
    serves half a layer forever: a .geojson whose record is gone is a file nothing can
    date, count or claim is whole, so it is fetched again.
    """
    rec = _read_json_quiet(out_dir / f"{key}.prov.json")
    return rec if isinstance(rec, dict) else None


def _currency(rec: dict | None, path: Path, server_count: int | None) -> dict:
    """Is this layer on the card CURRENT — and if not, in which of the four ways.

    "Current" is two claims and they are recorded separately because they fail
    separately and they mean different things:
      * COUNT — the file holds exactly as many features as the service says the layer
        has today. This is what catches the Trust adding twelve culverts, and it is
        what a fetch date cannot see.
      * AGE — and it was fetched inside crt_national_max_age_days. This is what catches
        a re-survey that moved geometry without changing the count, and a card that has
        not been CHECKED in a year.
    A file whose size no longer matches what the fetch recorded writing is not current
    either, whatever it says — it has been edited, truncated or half-copied, and its
    feature count is then a claim about a different file.

    server_count None is CANNOT-TELL, never "fine": with no count to compare against,
    what is on the card is served exactly as it is, dated, and nothing is re-fetched
    (there is no working service to re-fetch it from anyway).
    """
    out = {"current": False, "why": "", "count_check": None, "age_days": None}
    if rec is None:
        out["why"] = (
            "nothing beside this layer file vouches for it — no record means "
            "nothing can date it, count it or say it is whole"
        )
        return out
    try:
        size = path.stat().st_size
    except OSError:
        out["why"] = "the layer file is not on this card"
        return out
    if rec.get("complete") is not True:
        out["why"] = (
            "the record does not state that the whole layer landed, so what "
            "is on the card is a piece of one until proved otherwise"
        )
        return out
    have = rec.get("features")
    if rec.get("bytes") is not None and size != rec.get("bytes"):
        out["why"] = (
            f"the file is {size} bytes and the fetch recorded writing "
            f"{rec.get('bytes')} — it has been edited or truncated since, so "
            f"nothing it says about itself can be trusted"
        )
        return out
    age = _age_days(rec.get("fetched"))
    out["age_days"] = None if age is None else round(age, 1)
    if server_count is None:
        out["count_check"] = "unavailable"
        out["why"] = (
            "the service did not answer a count, so whether this file is still " "complete cannot be told from here"
        )
        return out
    if have != server_count:
        out["count_check"] = "disagrees"
        out["why"] = f"this file holds {have} feature(s) and the service now publishes " f"{server_count} nationally"
        return out
    out["count_check"] = "agrees"
    if age is None:
        out["why"] = "the record carries no fetch date, so its age cannot be judged"
        return out
    if age > float(settings.crt_national_max_age_days):
        out["why"] = (
            f"the count still agrees, and this was fetched {age:.0f} days ago "
            f"(over the {settings.crt_national_max_age_days:.0f}-day window) — "
            f"a count can agree while the geometry under it has been re-surveyed"
        )
        return out
    out["current"] = True
    out["why"] = (
        f"{have} feature(s), which is what the service publishes nationally " f"today, fetched {age:.0f} day(s) ago"
    )
    return out


def _read_json_quiet(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent and unreadable both mean "nothing to use"
        return None


def national_card() -> dict:
    """WHAT IS ON THE NATIONAL CARD, read off the disk. No network, no hostname.

    Offered to the serving side on exactly the same terms as area_dir: it is pure
    filesystem arithmetic plus a JSON read of the small records, and it is what
    nav/service.py and nav/cli.py both ask so there is one answer and not two that
    drift. It deliberately does NOT parse the layer files — the biggest is over 100 MB
    and this is asked on every readiness poll; what it reports is the recorded size
    against the size on disk, which is the check that catches a truncated download.
    """
    d = national_dir()
    idx = _read_json_quiet(d / "provenance.json")
    idx = idx if isinstance(idx, dict) else None
    rows: list[dict] = []
    complete = True
    # THE FILES ARE THE CARD. The index is read for the things a directory listing
    # cannot show — what was skipped and why, what the last run was doing — but never
    # for what is here: an index row with no file behind it is a layer that reports
    # SHOWN and draws nothing, and a file with no row is a layer the console reports
    # nothing about while drawing it.
    for path in sorted(d.glob("*.geojson")) if d.is_dir() else []:
        key = path.name[: -len(".geojson")]
        rec = _layer_record(d, key)
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        # The recorded national count is the last thing a service SAID. Currency
        # against the live service can only be judged with internet; on the card, the
        # question is whether the file still is what the fetch wrote.
        cur = _currency(rec, path, (rec or {}).get("national_features"))
        ok = bool(rec) and size is not None and rec.get("bytes") in (None, size) and rec.get("complete") is True
        complete = complete and ok
        rows.append(
            {
                **(
                    rec
                    or {
                        "layer_key": key,
                        "why": "no record beside this file — nothing can say " "what is in it or when it was fetched",
                    }
                ),
                "layer_key": key,
                "on_card": size is not None,
                "bytes_on_disk": size,
                "intact": ok,
                "current": cur["current"],
                "currency": cur,
            }
        )
    partial = []
    whole = {r["layer_key"] for r in rows if r["intact"]}
    if d.is_dir():
        for meta_p in sorted(d.glob(f"*{_RESUME_META}")):
            key = meta_p.name[: -len(_RESUME_META)]
            if key in whole:
                # PAGES BESIDE A LAYER THAT IS ALREADY WHOLE. A run killed between
                # writing the record and clearing its own scratch leaves this, and
                # calling it a part-download would make a complete card read as
                # unfinished for ever after. The next fetch clears the litter.
                continue
            meta = _read_json_quiet(meta_p) or {}
            partial.append(
                {
                    "layer_key": key,
                    "features": meta.get("offset"),
                    "pages": meta.get("pages"),
                    "started": meta.get("started"),
                    "touched": meta.get("touched"),
                }
            )
    return {
        "scope": "national",
        "dir": str(d),
        "exists": idx is not None,
        "state": (idx or {}).get("state"),
        "started": (idx or {}).get("started"),
        "finished": (idx or {}).get("finished"),
        "heartbeat": (idx or {}).get("heartbeat"),
        "attribution": (idx or {}).get("attribution") or OGL_ATTRIBUTION,
        "current_rule": (idx or {}).get("current_rule"),
        "layers": rows,
        "skipped": (idx or {}).get("skipped") or [],
        "warnings": (idx or {}).get("warnings") or [],
        "partial": partial,
        "expected_layers": (idx or {}).get("expected_layers"),
        "complete": bool(rows) and complete and not partial and bool((idx or {}).get("complete")),
        "features": sum(r.get("features") or 0 for r in rows),
        "bytes": sum(r.get("bytes_on_disk") or 0 for r in rows),
    }


def national_layer_record(key: str) -> dict | None:
    """One national layer's own record, by key. Pure disk, for the serving side."""
    return _layer_record(national_dir(), key)


def national_is_stale() -> tuple[bool, str]:
    """Should a fetch be started? Answered off the DISK, before any probe.

    The canal-side steady state has to be free: a console whose national card is
    complete must not spend a socket, a DNS lookup or four seconds finding that out.
    """
    card = national_card()
    if not card["layers"]:
        return True, ("the Canal & River Trust's national layers have never been " "downloaded on this handheld")
    if card["partial"]:
        keys = ", ".join(p["layer_key"] for p in card["partial"][:4])
        return True, (
            f"{len(card['partial'])} layer(s) are half-downloaded and will "
            f"continue from where they stopped ({keys})"
        )
    broken = [r["layer_key"] for r in card["layers"] if not r["intact"]]
    if broken:
        return True, (
            f"{len(broken)} layer file(s) are missing, unaccounted for, or no "
            f"longer what the fetch recorded writing "
            f"({', '.join(broken[:4])})"
        )
    if not (card.get("state") == "done" and card.get("complete")):
        return True, (
            "the last national fetch did not finish — what landed is on the "
            "card and the rest continues from where it stopped"
        )
    # AGE IS REPORTED, NOT ACTED ON. A complete card is never re-fetched on its own,
    # however old it is. The Trust's network does not move: locks and weirs are where
    # they were, and a canal that changed enough to matter changed slowly enough to
    # notice. Weighed against that, an automatic refresh is 140 MB started without
    # being asked for, on whatever connection happens to be there — which at the water
    # is a phone hotspot, and which replaces a card that WORKS with a download that may
    # not finish. The operator presses REFRESH when they want a fresh set; until then
    # the age rides on the card and in the panel so they can decide with the number in
    # front of them.
    ages = [_age_days(r.get("fetched")) for r in card["layers"]]
    ages = [a for a in ages if a is not None]
    oldest = max(ages) if ages else None
    old_note = ""
    if oldest is not None and oldest > float(settings.crt_national_max_age_days):
        old_note = (
            f". The oldest layer was fetched {oldest:.0f} days ago, past the "
            f"{settings.crt_national_max_age_days:.0f}-day window — press REFRESH "
            f"when you have a connection you are happy to spend, not at the water"
        )
    return False, (
        f"all {len(card['layers'])} national layer(s) are on this handheld, "
        f"{card['features']} feature(s), "
        f"{card['bytes'] / 1e6:.0f} MB — nothing needs the internet" + old_note
    )


# A second driver must not start a second national fetch. A plain flag rather than an
# asyncio.Lock on purpose: a Lock created at import time binds itself to the first loop
# that awaits it, and this module is driven from a CLI (one asyncio.run per command),
# from the API's loop, and from a test suite that makes a fresh loop per case — the
# Lock would raise "bound to a different event loop" in exactly the place that must
# never raise. There is one thread and one loop at a time, so a flag is enough.
_national_running = False

# AND A SECOND PROCESS MUST NOT EITHER, which a flag cannot say anything about. The
# console runs the map backend and an operator can type `python -m nav.cli crt-fetch`
# at the same machine in the same minute; both would then append pages to the same
# .resume file, interleaving two halves of a layer into a file that looks perfectly
# well-formed. The lock is a file with a pid and a heartbeat in it, refreshed at every
# checkpoint, and it is taken over when it goes quiet — a lock that outlives the
# process holding it is a lock that stops the fetch forever after one power cut.
_LOCK = "fetch.lock"
_LOCK_STALE_S = 120.0


def _lock_holder() -> dict | None:
    """Another LIVE fetch's lock, or None. A stale one is not a holder."""
    rec = _read_json_quiet(national_dir() / _LOCK)
    if not isinstance(rec, dict) or rec.get("pid") == os.getpid():
        return None
    age = _age_days(rec.get("heartbeat"))
    if age is not None and age * 86400.0 > _LOCK_STALE_S:
        return None
    return rec


def _take_lock() -> None:
    _write_atomic(national_dir() / _LOCK, json.dumps({"pid": os.getpid(), "started": _iso(), "heartbeat": _iso()}))


def national_running() -> bool:
    """Is a national fetch in flight — in this process or any other on this card."""
    return _national_running or _lock_holder() is not None


async def download_national(progress=None, *, refresh: bool = False) -> dict:
    """Fetch the WHOLE Canal & River Trust network → <crt_dir>/national/.

    Every layer, whole, unclipped, no bbox anywhere. Resumable at the page, incremental
    at the layer: what is already on this card and current is not requested again.

    Returns a result dict and does NOT raise, for the same reason download_hazards does
    not: this runs on a background task beside a control loop that is flying a sub, and
    a raise here must never be able to take anything else with it.
    """
    global _national_running

    async def emit(msg: dict) -> None:
        log.info("crt national %s", msg)
        if progress:
            await progress({"scope": "national", **msg})

    out_dir = national_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    holder = _lock_holder()
    if _national_running or holder is not None:
        return {
            "ok": False,
            "scope": "national",
            "busy": True,
            "error": (
                f"a national fetch is already running"
                + (f" (process {holder.get('pid')}, started " f"{holder.get('started')})" if holder else "")
                + " — these are rate-limited public services, two jobs would "
                "halve the rate each, and both would be appending pages to "
                "the same half-downloaded layer"
            ),
        }
    _national_running = True
    _take_lock()
    started = _iso()
    try:
        await emit({"state": "discovering"})
        layers = await inventory()
        if not layers:
            await emit({"state": "done", "ok": False, "layers": 0})
            return {
                "ok": False,
                "scope": "national",
                "dir": str(out_dir),
                "error": (
                    "no Trust service was reachable — this is a BOOTSTRAP task "
                    "and it needs the internet. Nothing on this card was "
                    "touched, and what is on it is still on it"
                ),
            }
        await emit({"state": "catalogue", "layers": len(layers)})

        # WHAT WAS ON THE CARD BEFORE THIS RUN. The sweep at the bottom is judged
        # against it, exactly as the area path's is: a run cannot know it is entitled
        # to empty a card it never looked at.
        on_card_keys = {p.name[: -len(".geojson")] for p in out_dir.glob("*.geojson")}

        fetched: list[dict] = []
        kept: list[dict] = []
        carried: list[dict] = []
        skipped: list[dict] = []
        warnings: list[str] = []
        used: set[str] = set()

        def index_now(state: str, finished: str | None = None, complete: bool = False) -> dict:
            return {
                "scope": "national",
                "state": state,
                "complete": complete,
                "started": started,
                "finished": finished,
                "heartbeat": _iso(),
                "pid": os.getpid(),
                "generator": "api/nav/crt.py",
                "hub": settings.crt_hub_search_url,
                "org": settings.crt_org_service_root,
                "clip_rule": (
                    "none — every layer is the whole national layer; an area "
                    "clips a COPY for drawing and is never a precondition for "
                    "having the data"
                ),
                "current_rule": (
                    f"a layer is current when the file holds exactly as many "
                    f"features as the service publishes nationally today AND "
                    f"it was fetched within "
                    f"{settings.crt_national_max_age_days:.0f} days. Both are "
                    f"recorded per layer under `currency`"
                ),
                "attribution": OGL_ATTRIBUTION,
                "layers": fetched + kept + carried,
                "skipped": skipped,
                "warnings": warnings,
                "expected_layers": len(layers),
            }

        def checkpoint(state: str = "running", finished: str | None = None, complete: bool = False) -> None:
            # THE INDEX IS REWRITTEN AFTER EVERY LAYER, not once at the end. A fetch
            # killed at the bank — Ctrl-C, a flat battery, a lid closed — otherwise
            # leaves 140 MB of perfectly good layers on the card with nothing on the
            # card saying they are there, and the console reports NOT DOWNLOADED over
            # data it is sitting on. It is a small file and this is cheap.
            try:
                _write_atomic(national_provenance_path(), json.dumps(index_now(state, finished, complete), indent=1))
                _take_lock()  # the heartbeat: this fetch is still alive
            except OSError as exc:
                log.warning("could not write the national index: %s", exc)

        for i, lyr in enumerate(layers):
            key = lyr.get("layer_key") or _layer_key(lyr["name"], lyr.get("layer_id"), None)
            if key in used:
                key = f"{key}-{len(used)}"
            used.add(key)
            klass = _classify_licence(lyr.get("licence"))
            national = lyr.get("national_features")
            base = {
                "layer_key": key,
                "title": lyr.get("title"),
                "service": lyr["name"],
                "service_url": lyr["url"],
                "layer_id": lyr.get("layer_id"),
                "layer_name": lyr.get("layer_name"),
                "source": lyr.get("source"),
                "licence": lyr.get("licence"),
                "licence_class": klass,
                "licence_source": lyr.get("licence_source"),
                "licence_note": lyr.get("licence_note"),
                "redistributable": _redistributable(klass),
                "scope": "national",
            }
            await emit(
                {
                    "state": "layer",
                    "n": i + 1,
                    "of": len(layers),
                    "layer": key,
                    "title": lyr.get("title"),
                    "expect": national,
                }
            )

            if lyr.get("error"):
                skipped.append({**base, "skipped": "unreadable", "why": lyr["error"]})
                warnings.append(f"{key}: {lyr['error']} — layer NOT written")
                checkpoint()
                continue
            if not lyr.get("geometry_type"):
                skipped.append(
                    {**base, "skipped": "no-geometry", "why": "layer publishes no geometry — nothing to draw"}
                )
                checkpoint()
                continue
            if klass == "restricted" and settings.crt_restricted == "skip":
                skipped.append(
                    {
                        **base,
                        "skipped": "licence",
                        "why": f"licence forbids reuse ({lyr.get('licence')!r}) " f"and NAV_CRT_RESTRICTED=skip",
                    }
                )
                checkpoint()
                continue
            # NOTE WHAT IS *NOT* HERE. The area path skips a layer whose national count
            # is below crt_min_features, because a toggle that can only ever be empty in
            # a 2 km box teaches the pilot that empty means broken. Nationally that
            # reasoning inverts: Boat_Lifts has exactly one boat lift in England and
            # Wales and it is a real boat lift. Nothing is excluded here for being
            # small, and nothing is excluded for its tier — a layer that is held and
            # hidden might as well not be held.

            prior = _layer_record(out_dir, key)
            cur = _currency(prior, out_dir / f"{key}.geojson", national)
            if cur["current"] and not refresh:
                # NOT RE-FETCHED AND NOT RE-WRITTEN. The file and the record beside it
                # are left exactly as they are: rewriting a layer nobody downloaded is
                # how a good file becomes a truncated one for free.
                rec = {**prior, **base, "currency": cur, "national_features": national, "refreshed": False}
                kept.append(rec)
                await emit(
                    {
                        "state": "current",
                        "layer": key,
                        "features": rec.get("features"),
                        "fetched": rec.get("fetched"),
                        "why": cur["why"],
                    }
                )
                checkpoint()
                continue

            t0 = time.monotonic()
            ok, stats = await _fetch_layer_national(lyr, out_dir, key, emit)
            rec = {
                **base,
                "fetched": _iso(),
                "geometry_type": lyr.get("geometry_type"),
                "object_id_field": lyr.get("object_id_field"),
                "storage_srs": lyr.get("storage_srs"),
                "out_srs": 4326,
                "national_features": national,
                "national_expected": lyr.get("national_expected"),
                "max_record_count": lyr.get("max_record_count"),
                "attribution": _attribution_for(klass, lyr.get("licence")),
                "refresh_reason": cur["why"],
                **stats,
            }

            exp = lyr.get("national_expected")
            if exp is not None and national is not None and national != exp:
                rec["national_drift"] = national - exp
                warnings.append(
                    f"{key}: {national} features nationwide, expected {exp} "
                    f"(service changed, or a different service answered)"
                )
            if national is None:
                warnings.append(f"{key}: national count unavailable — truncation could " f"not be checked")

            if not ok:
                # NO FILE, AND THE PAGES ARE KEPT. The area path throws a partial away
                # because there is nothing to come back to; here the .resume pair is the
                # thing the next run continues from, and it is named so that nothing
                # globbing *.geojson can serve it as a layer.
                skipped.append(
                    {
                        **rec,
                        "skipped": "fetch-failed",
                        "why": stats.get("error", "fetch failed"),
                        "note": (
                            f"no layer file was written — a partial layer "
                            f"reads as 'nothing here'. The "
                            f"{stats.get('kept_partial', 0)} feature(s) that "
                            f"did arrive are kept in {key}{_RESUME_DATA} and "
                            f"the next run continues from there"
                        ),
                    }
                )
                warnings.append(
                    f"{key}: {stats.get('error', 'fetch failed')} — layer NOT "
                    f"written, {stats.get('kept_partial', 0)} feature(s) held "
                    f"for the next run"
                )
                await emit(
                    {
                        "state": "failed",
                        "layer": key,
                        "why": stats.get("error", "fetch failed"),
                        "kept": stats.get("kept_partial", 0),
                    }
                )
                checkpoint()
                continue

            got = stats.get("features") or 0
            if national is None:
                rec["count_check"] = "unavailable"
            elif national == got:
                rec["count_check"] = "agrees"
            else:
                rec["count_check"] = "disagrees"
                warnings.append(
                    f"{key}: the service counted {national} features "
                    f"nationally and paging returned {got} — possible "
                    f"truncation, or the layer changed under the fetch"
                )

            span = stats.get("feature_bbox")
            # WHAT GOES IN THE FILE, AND WHY MORE THAN THE FEATURES DOES. This file is
            # handed straight out by the serving side — 100 MB of polygons cannot be
            # decoded and re-encoded per request on a machine that is flying a sub — so
            # everything a renderer needs before it draws has to already be inside the
            # braces. `attribution` is OGL's one obligation and travels with the data
            # wherever it is copied; `status` and `layer` are what the console reads to
            # tell real data from an ABSENT document; `clip` is explicitly null, which is
            # this file saying it is not a piece of something bigger.
            head = {
                "type": "FeatureCollection",
                "attribution": rec["attribution"],
                "scope": "national",
                "status": "present",
                "layer": key,
                "clip": None,
            }
            if span:
                head["bbox"] = span
            path = out_dir / f"{key}.geojson"
            rec["features"] = got
            # THE WORD THE NEXT RUN READS. Not a decoration: _currency refuses to call
            # a layer current without it, so a file written by an older build, or one
            # whose paging and the server's own count disagreed, is fetched again
            # rather than served as the whole layer for the rest of its life.
            rec["complete"] = rec["count_check"] != "disagrees"
            rec["bytes"] = _write_collection_stream(
                path, out_dir / f"{key}{_RESUME_DATA}", head, national_index_path(key)
            )
            rec["window_index"] = national_index_path(key).name
            rec["file"] = path.name
            rec["seconds"] = round(time.monotonic() - t0, 1)
            rec["currency"] = _currency(rec, path, national)
            _write_atomic(out_dir / f"{key}.prov.json", json.dumps(rec, indent=1))
            # The pages have become the layer. Only now are they removed — if this
            # process dies between the two, the next run finds a complete .geojson and
            # a stale .resume pair, sees the layer is current and clears them below.
            for p in _resume_paths(out_dir, key):
                p.unlink(missing_ok=True)
            fetched.append(rec)
            if rec["redistributable"] is False:
                warnings.append(
                    f"{key}: licence REFUSES reuse ({lyr.get('licence')!r}) — "
                    f"downloaded as this operator's own safety copy under "
                    f"NAV_CRT_RESTRICTED=flag; DO NOT redistribute this file"
                )
            elif rec["redistributable"] is None:
                warnings.append(
                    f"{key}: licence is {klass} ({lyr.get('licence')!r}) — "
                    f"real terms exist and are NOT quoted in the item "
                    f"metadata, so this is cannot-tell, not permission"
                )
            await emit(
                {
                    "state": "wrote",
                    "layer": key,
                    "features": got,
                    "bytes": rec["bytes"],
                    "seconds": rec["seconds"],
                    "of_national": national,
                }
            )
            checkpoint()

        # ---- the sweep, under the same rule as the area path --------------------
        # A run may only take away what it has PUT BACK, and only when it accounted for
        # enough of the card to know the difference between "the Trust withdrew this
        # layer" and "we could not ask". See _SWEEP_FLOOR: a run that deleted a whole
        # card and reported success is what that constant is written against.
        skip_by_key = {s.get("layer_key"): s for s in skipped}
        decided = {s["layer_key"] for s in skipped if s.get("skipped") in _DELIBERATE_SKIPS}
        accounted = {r["layer_key"] for r in fetched} | {r["layer_key"] for r in kept} | decided
        covered = len(accounted & on_card_keys)
        floor = math.ceil(_SWEEP_FLOOR * len(on_card_keys))
        is_refresh = bool(fetched or kept) and covered >= floor
        removed: list[str] = []
        removed_keys: set[str] = set()
        for old in sorted(out_dir.glob("*.geojson")) + sorted(out_dir.glob("*.prov.json")):
            k = old.name[: -len(".prov.json")] if old.name.endswith(".prov.json") else old.name[: -len(".geojson")]
            if k in accounted:
                continue
            why_gone = (
                "left out of this run on purpose"
                if k in decided
                else "no longer offered by the Trust's services" if k not in used else ""
            )
            if is_refresh and why_gone:
                old.unlink()
                # The window index belongs to the file, so it goes with it. Left behind
                # it would describe byte offsets into a layer that no longer exists.
                national_index_path(k).unlink(missing_ok=True)
                removed.append(old.name)
                removed_keys.add(k)
                warnings.append(f"{old.name}: {why_gone} — deleted rather than served as " f"current")
        # Resume litter for a layer that is now complete, or that no service offers any
        # more. Never touched for a layer whose fetch merely failed — that is the whole
        # point of it.
        #
        # `kept` IS IN HERE FOR A REASON. The layer file is written, then its record,
        # then the resume pair is removed; a process killed between the second and the
        # third leaves 64 MB of pages beside a layer that is complete. The next run
        # calls that layer CURRENT — so it is never in `fetched` — and without this the
        # litter would sit there for good, and national_card() would go on reporting the
        # layer as part-downloaded, which makes the card permanently "incomplete" and
        # re-probed on every single launch.
        done_keys = {r["layer_key"] for r in fetched} | {r["layer_key"] for r in kept}
        for meta_p in sorted(out_dir.glob(f"*{_RESUME_META}")):
            k = meta_p.name[: -len(_RESUME_META)]
            if k in done_keys or k not in used:
                for p in _resume_paths(out_dir, k):
                    p.unlink(missing_ok=True)

        # ---- what survived, said out loud ---------------------------------------
        # THE INDEX DESCRIBES THE CARD, NOT THE RUN. A run that reached nothing —
        # started after the hotspot had already gone — would otherwise rewrite this
        # file with an empty layer list, and the console would report NOT DOWNLOADED
        # over 150 MB of perfectly good vectors it is sitting on. That is the deleting
        # sweep's mistake wearing different clothes: the data survives and the only
        # thing that knows about it does not. So every layer file this run did not
        # write or keep is carried into the index from its OWN record, with the date
        # it was actually fetched, and the reason this run could not vouch for it.
        unaccounted: list[str] = []
        for path in sorted(out_dir.glob("*.geojson")):
            k = path.name[: -len(".geojson")]
            if k in accounted or k in removed_keys:
                continue
            rec = _layer_record(out_dir, k)
            why = (skip_by_key.get(k) or {}).get("why") or "this run did not reach this layer at all"
            if rec is None or rec.get("features") is None:
                unaccounted.append(path.name)
                warnings.append(
                    f"{path.name}: on the card with no readable record "
                    f"beside it — KEPT (nothing here deletes downloaded "
                    f"data) but not listed as a layer, because nothing can "
                    f"say what is in it or when it was fetched"
                )
                continue
            carried.append({**rec, "layer_key": k, "carried_over": True, "refresh_failed": why})
            warnings.append(
                f"{k}: NOT refreshed by this run ({why}) — the file fetched "
                f"{rec.get('fetched')} is still on the card and is what the "
                f"console will draw. Older data, honestly dated, beats none"
            )
        # Said in ONE place: a layer whose file survived is PRESENT, and reporting it as
        # both carried and skipped would have the console call it present and missing in
        # one breath.
        carried_keys = {r["layer_key"] for r in carried}
        skipped = [s for s in skipped if s.get("layer_key") not in carried_keys]

        # ---- was this a success? -------------------------------------------------
        # Success is not "the command finished". It is: everything the Trust offers is
        # on this handheld, whole, and this run either put it there or checked that it
        # is still there. Anything else leaves the operator with a card thinner or
        # older than they think, and it has to cost an exit code — the moment to learn
        # it is while there is still internet.
        failed = [s for s in skipped if s.get("skipped") not in _DELIBERATE_SKIPS]
        finished = _iso()
        reasons: list[str] = []
        if failed:
            reasons.append(
                f"{len(failed)} layer(s) did not complete: "
                + "; ".join(f"{s['layer_key']} ({s.get('why')})" for s in failed[:6])
                + ". What did land is on the card and the part-downloaded "
                "layers continue where they stopped"
            )
        if carried:
            reasons.append(
                f"{len(carried)} layer(s) could not be refreshed and are "
                f"being served from an earlier fetch: "
                f"{', '.join(sorted(carried_keys))}"
            )
        if unaccounted:
            reasons.append(
                f"{len(unaccounted)} file(s) on this card have no readable "
                f"record and nothing claims to know what is in them: "
                f"{', '.join(unaccounted)}"
            )
        ok = not reasons
        # COMPLETE means every layer the Trust offered this run is on the card WHOLE —
        # not "the command finished", and not "some of it is here". An interrupted run
        # never reaches this line at all, so every index one of those leaves behind says
        # complete:false, which is the true claim about a card nobody finished filling.
        complete = ok and len(accounted) >= len(layers)
        checkpoint("done" if ok else "failed", finished, complete)

        card = national_card()
        res = {
            "ok": ok,
            "scope": "national",
            "dir": str(out_dir),
            "layers": len(fetched) + len(kept) + len(carried),
            "written": len(fetched),
            "already_current": len(kept),
            "carried_over": len(carried),
            "features": sum(r.get("features") or 0 for r in fetched + kept + carried),
            "bytes": card["bytes"],
            "removed_stale": len(removed),
            "skipped": len(skipped),
            "warnings": warnings,
            "complete": card["complete"],
        }
        if reasons:
            res["error"] = "; ".join(reasons)
        await emit(
            {
                "state": "done",
                "ok": ok,
                "layers": res["layers"],
                "written": res["written"],
                "current": res["already_current"],
                "features": res["features"],
                "bytes": res["bytes"],
            }
        )
        return res
    except asyncio.CancelledError:
        # STOPPED. Nothing is awaited on the way out — the loop is tearing this task
        # down and an await may never resume — but the index on the card must not be
        # left saying a fetch is running. Every page that landed is already on disk.
        try:
            idx = _read_json_quiet(national_provenance_path())
            if isinstance(idx, dict):
                idx["state"] = "interrupted"
                idx["heartbeat"] = _iso()
                _write_atomic(national_provenance_path(), json.dumps(idx, indent=1))
        except OSError:
            pass
        raise
    finally:
        _national_running = False
        with contextlib.suppress(OSError):
            (out_dir / _LOCK).unlink(missing_ok=True)


# ---- bootstrap entry point ----------------------------------------------------
async def _main(argv: list[str]) -> int:
    # A CONSOLE THAT CANNOT SPELL A CHARACTER MUST NOT KILL THE COMMAND. Measured on
    # the ROG Ally, 2026-08-08: `python -m nav.crt --national` downloaded all 27 layers
    # — 38,425 features, 140 MB — wrote every one of them, and then died with
    # UnicodeEncodeError on the licence warnings it prints LAST, because a Trust licence
    # string carries U+FFFD and cp1252 has no character for it. ok:true on the card,
    # exit 1 to the operator, and a traceback where the summary should have been.
    # nav/cli.py fixed this for the CLI in its own main(); this module is runnable on
    # its own and had no such guard. 'replace' loses a glyph; strict loses the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 — not a TextIOWrapper (a pipe under test, say)
            pass
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if "--list" in argv:
        for lyr in await inventory():
            if lyr.get("error"):
                print(f"  !! {lyr['name']}: {lyr['error']}")
                continue
            exp = lyr.get("national_expected")
            nat = lyr.get("national_features")
            drift = "" if exp is None or nat is None or nat == exp else f"  DRIFT expected {exp}"
            print(
                f"  {lyr['layer_key']:38} L{lyr['layer_id']} n={nat!s:6} "
                f"srs={lyr.get('storage_srs')!s:6} {_classify_licence(lyr.get('licence'))}"
                f"{drift}"
            )
        return 0
    if "--status" in argv:
        # DISK ONLY. This is the question an operator asks with no signal, so it may
        # not touch the network — not even to be helpful.
        card = national_card()
        stale, why = national_is_stale()
        print(f"national : {card['dir']}")
        for r in card["layers"]:
            print(
                f"  {r.get('layer_key'):38} {r.get('features')!s:>7} feat "
                f"{(r.get('bytes_on_disk') or 0):>12,} B  "
                f"{'intact' if r.get('intact') else 'MISSING/CHANGED':16} "
                f"{r.get('fetched')}"
            )
        for p in card["partial"]:
            print(f"  {p['layer_key']:38} {p.get('features')!s:>7} feat  PART-DOWNLOADED, " f"continues from there")
        print(f"total    : {len(card['layers'])} layer(s), {card['features']} feature(s), " f"{card['bytes']:,} bytes")
        print(f"verdict  : {'FETCH NEEDED — ' if stale else 'complete — '}{why}")
        return 0 if not stale else 1
    if "--national" in argv:

        async def say_national(msg: dict) -> None:
            print("  " + " ".join(f"{k}={v}" for k, v in msg.items()), flush=True)

        res = await download_national(progress=say_national, refresh="--refresh" in argv)
        print(json.dumps({k: v for k, v in res.items() if k != "warnings"}, indent=1))
        for w in res.get("warnings") or []:
            print(f"  warn : {w}")
        return 0 if res.get("ok") else 1

    def _is_flag(tok: str) -> bool:
        # "-1.925" is a longitude, not an option. Every bbox west of Greenwich starts with
        # a minus sign, so a plain startswith("-") test silently drops the whole bbox and
        # then reports that the area has none.
        if not tok.startswith("-"):
            return False
        try:
            float(tok)
        except ValueError:
            return True
        return False

    args = [a for a in argv if not _is_flag(a)]
    if not args:
        print(__doc__.split("USAGE")[1].strip())
        return 2
    bbox = [float(v) for v in args[1:5]] if len(args) >= 5 else None

    async def say(msg: dict) -> None:
        print("  " + " ".join(f"{k}={v}" for k, v in msg.items() if k != "bbox"), flush=True)

    res = await download_hazards(args[0], bbox, progress=say)
    print(json.dumps(res, indent=1))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
