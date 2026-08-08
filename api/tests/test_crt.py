"""The CRT hazard download (nav/crt.py), driven against services that do not exist.

Run:  cd api && python -m unittest tests.test_crt -v

WHAT THIS GUARDS. The Trust's asset layers are pulled at BOOTSTRAP, while there is
still internet, and whatever lands on disk is the whole of what the console will
ever have at the canal. Every defect below is therefore permanent in the artifact
rather than merely annoying at the time:

  THE LAYER ID IS NOT 0. Every ArcGIS example ends its URL in /0/query, and it
  works right up until the service you need numbers its layers 3, 7 and 12 — the
  Canals services are layer 1 and Pumping_Station is layer 3. A hardcoded /0 gets
  a service error, ArcGIS returns that error with HTTP 200 and a JSON body, and a
  careless reader turns it into an empty hazard layer. So the service is ASKED,
  and the ids it answers with are the ids that get queried. No service in this
  double has a layer 0 at all.

  A SHORT PAGE IS THE STOP. ArcGIS caps a response at its own maxRecordCount no
  matter what was asked for, so a client that asks for 2000, gets 4, and advances
  its offset by 2000 keeps four features in every two thousand and reports
  success. The double caps at 4 exactly like a real service, and it does NOT send
  `exceededTransferLimit`, so the only signal available is the one the design
  names: a page shorter than the page size ends the loop.

  outSR=4326 GOING OUT, inSR=4326 GOING IN. These services store in a mix of 3857
  and 27700, so both directions have to be stated. Without outSR the coordinates
  come back as eastings and northings and land in a GeoJSON file as though they
  were degrees — 435000 is a perfectly valid number and a longitude in the middle
  of the Pacific. Without inSR the AREA BOX is read in the layer's storage CRS
  instead, and a Birmingham bbox selects nothing at all, which arrives as an empty
  layer: the false reassurance, made by arithmetic.

  EMPTY AND ABSENT ARE OPPOSITE CLAIMS, and this is the pair the whole doctrine
  turns on. A layer that fetched cleanly and matched nothing in the area is a
  SURVEY RESULT — "no tunnels here" — and it gets a file. A layer whose fetch died
  part-way is an ABSENCE of one, and it must get NO file, because a truncated page
  is a well-formed FeatureCollection that reads exactly like "no hazards here" and
  nothing downstream can ever tell them apart again. Both halves are checked here,
  in the same run, against the same directory.

  NEAR-EMPTY LAYERS ARE SKIPPED AND NAMED. A layer with one feature nationwide is
  a toggle that can only ever be empty, and an empty toggle teaches a pilot that
  empty means broken. It is dropped on its NATIONAL count and the reason is
  recorded — never dropped on the clipped count, which would silently discard the
  true and useful claim "no bridges in this pound".

TWO FETCHES, TWO WORLDS, ONE FILE. Everything above is the PER-AREA fetch
(download_hazards), which clips to an offline area's box and is still how the
depth pair's area card is built. Everything from `THE NATIONAL CARD` down is the
NATIONAL fetch (download_national), which is how the Trust's vectors are actually
got now: the whole published network, once, on launch, onto the handheld. The two
have different doubles because they have different right answers — the clearest
example being the near-empty rule, which is correct for a 2 km box and inverts
nationally, and is argued out where it is tested rather than here.

NO NETWORK, EVER. Every request goes through nav.crt._http_get, which the module
docstring nominates for exactly this. Every hostname here is under .invalid, which
RFC 6761 guarantees will never resolve, so a monkeypatch that fails to take ends
in a DNS error naming this suite rather than in a test run that quietly fetches
the real Canal & River Trust over somebody's hotspot.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import math
import re
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

from nav import crt
from nav.config import settings as real_settings

# .invalid: reserved by RFC 6761 and guaranteed not to resolve.
HUB = "https://hub.crt.example.invalid/api/search/v1/collections/dataset/items?limit=100"
ORG = "https://services.crt.example.invalid/arcgis/rest/services"
ITEMS = "https://items.crt.example.invalid/sharing/rest/content/items"

AREA = "test-cut"
BBOX = [-1.92, 52.47, -1.89, 52.49]          # a couple of km of the Birmingham cut
OGL = "<p>Open Government Licence v3.0</p>"

# The double's whole world. NOT ONE LAYER ID IS 0.
#
#   locks       10 in the box, page size 4  -> 4, 4, 2: the ordinary paging case
#   weirs        4 in the box, page size 4  -> 4, then an EMPTY page: the off-by-one
#   tunnels      0 in the box               -> a CLEAN fetch that found nothing, which
#                                              is a survey result and gets a file
#   boat-lifts   1 feature NATIONWIDE       -> below crt_min_features: skipped, named,
#                                              and never fetched at all
SERVICES = [
    {"service": "Canal_And_River_Trust_Locks_View", "source": "hub", "item": "item-locks",
     "lid": 3, "layer_name": "Locks", "national": 1722, "in_bbox": 10, "page": 4},
    {"service": "Canal_And_River_Trust_Weirs_View", "source": "org", "item": "item-weirs",
     "lid": 7, "layer_name": "Weirs", "national": 1108, "in_bbox": 4, "page": 4},
    {"service": "Canal_And_River_Trust_Tunnels_View", "source": "org", "item": "item-tunnels",
     "lid": 5, "layer_name": "Tunnels", "national": 58, "in_bbox": 0, "page": 4},
    {"service": "Canal_And_River_Trust_Boat_Lifts_View", "source": "org", "item": "item-lifts",
     "lid": 12, "layer_name": "Boat Lifts", "national": 1, "in_bbox": 3, "page": 4},
]
EXTRA = tuple(s["service"] for s in SERVICES if s["source"] == "org")
KEY = {s["service"]: crt._layer_key(s["service"], s["lid"], s["layer_name"]) for s in SERVICES}

# What ArcGIS calls the three shapes. The double has to publish a geometryType per
# layer rather than "point" for everything, because the layers this round is about —
# the planning buffer and the canals-by-navigation polygons — are the two whose SIZE
# is the excuse for leaving them out, and a double where every layer is a 60-byte
# point cannot show that they were not.
_ESRI = {"point": "esriGeometryPoint", "line": "esriGeometryPolyline",
         "polygon": "esriGeometryPolygon"}

# ---------------------------------------------------------------------------
# THE NATIONAL WORLD. A second set of services for the national fetch, and it is
# deliberately NOT the four above: the whole point of the decision under test is
# that the heavy tier-3 polygons come down with everything else, so they have to be
# in the double, and they have to be genuinely the biggest files on the card.
#
# The service names are the REAL ones, so crt._layer_key() produces the real keys
# (planning-buffer-polygon-2, canals-by-navigation-1 — the same names as the fetched
# card in data/crt/gas-street/) and so every layer here also carries a national count
# that DISAGREES with crt._EXPECTED_FEATURES. That disagreement is deliberate too:
# drift is a thing this module records and must never act on, and "we dropped the
# layer whose count moved" would be an exclusion wearing a safety hat.
#
# Counts are small because the double pages at 4 and a test that downloads 7,691
# towpath access points is a test nobody runs. NOT ONE LAYER ID IS 0.
NAT_SERVICES = [
    # hazards (tier 1 on the console), the ordinary paging case
    {"service": "Canal_And_River_Trust_Locks_View", "source": "hub", "item": "n-locks",
     "lid": 3, "layer_name": "Locks", "national": 10, "page": 4, "gtype": "point", "in_bbox": 1},
    # exactly one full page then an empty one
    {"service": "Canal_And_River_Trust_Weirs_View", "source": "hub", "item": "n-weirs",
     "lid": 7, "layer_name": "Weirs", "national": 4, "page": 4, "gtype": "point", "in_bbox": 1},
    {"service": "Canal_And_River_Trust_Culverts_View", "source": "hub", "item": "n-culv",
     "lid": 9, "layer_name": "Culverts", "national": 6, "page": 4, "gtype": "point", "in_bbox": 1},
    # operations
    {"service": "Canal_And_River_Trust_Bridges_View", "source": "hub", "item": "n-bridges",
     "lid": 5, "layer_name": "Bridges", "national": 7, "page": 4, "gtype": "point", "in_bbox": 1},
    {"service": "Towpath_Access_Points_2022", "source": "org", "item": "n-towpath",
     "lid": 4, "layer_name": "Towpath Access Points", "national": 8, "page": 4,
     "gtype": "point", "in_bbox": 1},
    {"service": "Stop_Plank_Grooves_View_Public", "source": "org", "item": "n-planks",
     "lid": 6, "layer_name": "Stop Plank Grooves", "national": 5, "page": 4,
     "gtype": "point", "in_bbox": 1},
    # THE TWO HEAVY POLYGON LAYERS. 82,880 B/feature and 46,262 B/feature on the real
    # card; here, rings big enough that these two files are unmistakably the largest
    # on the card, so "we left the big one out" cannot pass as an oversight.
    {"service": "Canal_And_River_Trust_Planning_Buffer_Polygon_View", "source": "hub",
     "item": "n-buffer", "lid": 2, "layer_name": "Planning Buffer Polygon",
     "national": 3, "page": 4, "gtype": "polygon", "ring": 240, "in_bbox": 1},
    {"service": "Canal_And_River_Trust_Canals_By_Navigation_View", "source": "hub",
     "item": "n-navs", "lid": 1, "layer_name": "Canals By Navigation",
     "national": 2, "page": 4, "gtype": "polygon", "ring": 360, "in_bbox": 1},
    # centreline, by km — a line layer, 2,422 B/feature nationally
    {"service": "Canal_And_River_Trust_Canals_By_KM_Length_View", "source": "hub",
     "item": "n-km", "lid": 8, "layer_name": "Canals By KM Length", "national": 6,
     "page": 4, "gtype": "line", "ring": 40, "in_bbox": 1},
    # One feature nationwide. Kept in the world on purpose: it is the ONE layer this
    # module is entitled to leave out, and the tests below check that whatever happens
    # to it happens for the reason it is allowed to happen for and is written down.
    {"service": "Canal_And_River_Trust_Boat_Lifts_View", "source": "org", "item": "n-lifts",
     "lid": 12, "layer_name": "Boat Lifts", "national": 1, "page": 4, "gtype": "point", "in_bbox": 1},
]
NAT_EXTRA = tuple(s["service"] for s in NAT_SERVICES if s["source"] == "org")
NKEY = {s["service"]: crt._layer_key(s["service"], s["lid"], s["layer_name"])
        for s in NAT_SERVICES}
# The two the decision names. Read through _layer_key rather than typed out, so a
# change to the naming rule cannot leave these tests looking for a file nothing writes.
HEAVY = [NKEY["Canal_And_River_Trust_Planning_Buffer_Polygon_View"],
         NKEY["Canal_And_River_Trust_Canals_By_Navigation_View"]]
NEAR_EMPTY = NKEY["Canal_And_River_Trust_Boat_Lifts_View"]
# Every layer the national world offers that is NOT the one-feature outlier: this is
# the set that must be on the card, whole, every time.
NAT_KEYS = [NKEY[s["service"]] for s in NAT_SERVICES
            if s["service"] != "Canal_And_River_Trust_Boat_Lifts_View"]


class RunawayPaging(BaseException):
    """The loop never stopped, or offline turned into an infinite retry.

    Deliberately NOT an Exception: crt._get_json wraps every fetch in
    `except Exception` and retries, so a guard raised as an ordinary exception
    would be swallowed and the suite would hang until the runner's own timeout,
    reporting nothing at all about anything.
    """


@contextlib.contextmanager
def no_waiting():
    """Run the pipeline without its two deliberate waits.

    crt sleeps twice: 1/crt_rate_per_s after every successful fetch (politeness to
    somebody's free ArcGIS quota) and 0.5 s then 1.0 s of backoff before giving up
    on a URL. Both are right against a real server and both are pure waiting here.
    Left in, this suite cost 25 s on a bench that otherwise finishes in three — the
    politeness delay alone because Windows rounds a 1 ms timer up to a 15 ms one,
    thirty times per download, twenty-four times over.

    Nothing below asserts on either duration. What is asserted is what ends up on
    the DISK and how many requests were made, and that the attempts STOP: the
    double's call cap raises RunawayPaging if they do not, so an implementation
    that retried forever still fails here rather than hanging the runner.
    """
    real = asyncio.sleep

    async def instant(_delay, *a, **kw):
        return await real(0, *a, **kw)

    asyncio.sleep = instant
    try:
        yield
    finally:
        asyncio.sleep = real


# ---------------------------------------------------------------------------
class FakeArcGIS:
    """An ArcGIS org, a Hub and an item store, in memory, that record what they were
    asked. A faithful pedant on the one behaviour that matters: it caps every page at
    its own maxRecordCount whatever the client asked for."""

    def __init__(self, fail_from=None, dead=False, call_cap=200, world=None,
                 interrupt_at=None):
        # (layer_id, offset): every FEATURE page for that layer at or past that offset
        # raises, persistently. Persistent rather than one-shot because _get_json
        # retries three times, and a failure that heals on retry is not the defect.
        self.fail_from = fail_from
        self.dead = dead                     # every request raises: the isolated segment
        self.calls: list[str] = []
        self.call_cap = call_cap
        # WHICH SET OF SERVICES THIS ORG PUBLISHES. Defaults to the four the area
        # tests were written against, so nothing above changes; the national tests
        # pass NAT_SERVICES, which is a different org with different layer ids and
        # two very large polygon layers in it.
        self.world = world if world is not None else SERVICES
        # (layer_id, offset): the FEATURE page at or past that offset raises
        # KeyboardInterrupt — Ctrl-C at the bank, or the machine going down mid-run.
        # A BaseException on purpose: _get_json retries anything that is an Exception,
        # so an interruption modelled as one would be quietly retried and healed,
        # which is the opposite of the case under test.
        self.interrupt_at = interrupt_at

    # -- bodies -------------------------------------------------------------
    def _geometry(self, svc, i):
        """A shape of the layer's own kind. The polygon rings are big because the
        national planning-buffer layer really is 82,880 B/feature, and a test that
        proved 'the big one came down' against a 60-byte stand-in would have proved
        nothing about the only thing anybody would be tempted to leave out."""
        g = svc.get("gtype", "point")
        if g == "point":
            # Inside BBOX, so crt._keeps() holds on to every one of them and a short
            # file means paging, not clipping.
            return {"type": "Point", "coordinates": [-1.910 + i * 0.001, 52.480]}
        n = int(svc.get("ring", 64))
        lon0, lat0 = -2.10 + i * 0.05, 52.30 + i * 0.05
        ring = [[round(lon0 + 0.004 * math.cos(2 * math.pi * k / n), 6),
                 round(lat0 + 0.004 * math.sin(2 * math.pi * k / n), 6)]
                for k in range(n)]
        if g == "line":
            return {"type": "LineString", "coordinates": ring}
        return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}

    def _feature(self, svc, i):
        return {"type": "Feature", "id": i + 1,
                "properties": {"OBJECTID": i + 1, "probe": f"{svc['layer_name']}-{i}",
                               "SAP_OBJECT_NO": 100000 + svc["lid"] * 1000 + i},
                "geometry": self._geometry(svc, i)}

    def _hub(self):
        hub = [s for s in self.world if s["source"] == "hub"]
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"title": s["layer_name"],
                            "url": f"{ORG}/{s['service']}/FeatureServer",
                            "licenseInfo": OGL}} for s in hub]}

    def _root(self, svc):
        return {"currentVersion": 11.1, "serviceDescription": svc["layer_name"],
                "serviceItemId": svc["item"], "copyrightText": "Canal & River Trust",
                "maxRecordCount": 2000,
                "layers": [{"id": svc["lid"], "name": svc["layer_name"],
                            "geometryType": _ESRI[svc.get("gtype", "point")]}],
                "tables": []}

    def _layer_meta(self, svc):
        return {"id": svc["lid"], "name": svc["layer_name"],
                "geometryType": _ESRI[svc.get("gtype", "point")],
                "objectIdField": "OBJECTID",
                "maxRecordCount": svc["page"],
                # A storage CRS that is NOT 4326, which is the whole reason outSR and
                # inSR both have to be stated on every query.
                "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1,
                           "spatialReference": {"wkid": 102100, "latestWkid": 3857}}}

    # -- routing ------------------------------------------------------------
    def get(self, url, timeout=None, *_a, **_kw):
        self.calls.append(url)
        if len(self.calls) > self.call_cap:
            raise RunawayPaging(f"{len(self.calls)} requests and still going. "
                                f"Last URL: {url}")
        if self.dead:
            raise urllib.error.URLError("getaddrinfo failed — no hostname resolution "
                                        "in the isolated segment")
        p = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(p.query)
        if p.netloc == urllib.parse.urlparse(HUB).netloc:
            return json.dumps(self._hub()).encode()
        if p.netloc == urllib.parse.urlparse(ITEMS).netloc:
            return json.dumps({"id": p.path.rsplit("/", 1)[-1], "licenseInfo": OGL}).encode()

        m = re.search(r"/([^/]+)/FeatureServer(?:/(\d+))?(/query)?/?$", p.path)
        if not m:
            raise urllib.error.HTTPError(url, 404, "no such endpoint", {}, None)
        service = urllib.parse.unquote(m.group(1))
        svc = next((s for s in self.world if s["service"] == service), None)
        if svc is None:
            raise urllib.error.HTTPError(url, 404, f"no service {service}", {}, None)
        if m.group(2) is None:
            return json.dumps(self._root(svc)).encode()
        lid = int(m.group(2))
        if lid != svc["lid"]:
            # EXACTLY WHAT A REAL SERVICE DOES: HTTP 200, with an error in the body.
            # A caller that only checks the status code pages this forever and finds
            # zero features in it every time.
            return json.dumps({"error": {"code": 400, "message": "Invalid or missing "
                                         "input parameters.", "details": []}}).encode()
        if not m.group(3):
            return json.dumps(self._layer_meta(svc)).encode()
        return self._query(svc, q)

    def _total(self, svc, q):
        """How many features this query is entitled to see. An envelope means the
        area case and the count is what is in that box; NO envelope means the whole
        country, which is the national fetch and is the difference this round is
        about — a national request that came back with the Birmingham count would be
        a clip nobody asked for, wearing a national label."""
        if "geometry" in q:
            return svc.get("in_bbox", svc["national"])
        return svc["national"]

    def _query(self, svc, q):
        if (q.get("returnCountOnly") or [""])[0] == "true":
            return json.dumps({"count": self._total(svc, q)}).encode()
        offset = int((q.get("resultOffset") or ["0"])[0])
        if self.interrupt_at and svc["lid"] == self.interrupt_at[0] \
                and offset >= self.interrupt_at[1]:
            raise KeyboardInterrupt(
                f"Ctrl-C during {svc['layer_name']} at offset {offset}")
        if self.fail_from and svc["lid"] == self.fail_from[0] and offset >= self.fail_from[1]:
            raise urllib.error.URLError(
                f"connection reset on {svc['layer_name']} at offset {offset} "
                "(the canal-side hotspot, doing what it does)")
        asked = int((q.get("resultRecordCount") or ["2000"])[0])
        count = min(asked, svc["page"])          # the cap the client does not control
        feats = [self._feature(svc, i)
                 for i in range(offset, min(offset + count, self._total(svc, q)))]
        return json.dumps({"type": "FeatureCollection", "features": feats}).encode()

    # -- what it was asked --------------------------------------------------
    @property
    def queries(self):
        return [u for u in self.calls if "/query" in u]

    @property
    def feature_queries(self):
        """Queries that return geometry — i.e. not the returnCountOnly witnesses."""
        return [u for u in self.queries
                if (urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                    .get("returnCountOnly") or [""])[0] != "true"]

    def pages_for(self, lid: int):
        pat = re.compile(rf"/{lid}/query/?$")
        return [u for u in self.feature_queries
                if pat.search(urllib.parse.urlparse(u).path)]

    def pages_for_service(self, service: str):
        """Feature pages fetched from one SERVICE, by name rather than by layer id.

        The national world has ten services in it and ArcGIS layer ids are only
        unique within a service, so "was this layer re-fetched?" has to be asked of
        the service — asking it of an id would answer for two layers at once and an
        incremental run that skipped one and re-paged the other would look correct."""
        pat = f"/{service}/FeatureServer/"
        return [u for u in self.feature_queries if pat in urllib.parse.unquote(u)]


# ---------------------------------------------------------------------------
class CrtTestCase(unittest.TestCase):
    """A temp data directory, a fake org, and nav.crt pointed at both."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="neptune-crt-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The module holds `settings` as its own attribute (`from .config import
        # settings`), so swapping that is enough and the frozen dataclass is never
        # mutated — nothing else in this process sees a different config.
        self._real = crt.settings
        self.addCleanup(setattr, crt, "settings", self._real)
        crt.settings = dataclasses.replace(
            real_settings,
            crt_dir=self.tmp / "crt", areas_dir=self.tmp / "areas",
            crt_hub_search_url=HUB, crt_org_service_root=ORG, crt_item_lookup_url=ITEMS,
            crt_extra_services=EXTRA,
            # The politeness delay is 1/rate and this suite is not being polite to a
            # dictionary. The BACKOFF on a failed fetch is not affected, so the
            # offline case still costs what a real one does.
            crt_rate_per_s=1000.0,
            crt_min_features=5, crt_restricted="flag")
        self._real_http = crt._http_get
        self.addCleanup(setattr, crt, "_http_get", self._real_http)

    def serve(self, fake: FakeArcGIS):
        crt._http_get = fake.get
        return fake

    def arun(self, coro):
        """Every await in this suite goes through here — see no_waiting()."""
        with no_waiting():
            return asyncio.run(coro)

    def download(self, fake: FakeArcGIS | None = None):
        fake = self.serve(fake or FakeArcGIS())
        return fake, self.arun(crt.download_hazards(AREA, list(BBOX)))

    # -- what landed on disk ------------------------------------------------
    @property
    def out(self) -> Path:
        return crt.area_dir(AREA)

    def layer_files(self) -> dict:
        return {p.name: json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.out.glob("*.geojson"))}

    def provenance(self) -> dict:
        p = crt.provenance_path(AREA)
        self.assertTrue(p.exists(), f"no provenance index at {p}. Files present: "
                                    f"{[q.name for q in self.out.glob('*')] if self.out.exists() else 'no directory'}")
        return json.loads(p.read_text(encoding="utf-8"))

    def all_text(self) -> str:
        if not self.out.exists():
            return ""
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in sorted(self.out.rglob("*")) if p.is_file())


# ===========================================================================
class LayerDiscovery(CrtTestCase):
    """The service is asked what it has. Nothing assumes layer 0."""

    def test_inventory_reads_the_layer_ids_off_the_service(self):
        fake = self.serve(FakeArcGIS())
        found = self.arun(crt.inventory())
        ids = sorted(l["layer_id"] for l in found if l.get("layer_id") is not None)
        self.assertEqual(ids, sorted(s["lid"] for s in SERVICES),
                         f"discovered layer ids {ids}; the services publish "
                         f"{sorted(s['lid'] for s in SERVICES)}. Anything else was "
                         f"assumed rather than read: {found!r}")
        self.assertNotIn(0, ids, "a layer 0 appeared from a set of services that has "
                                 "none — that is the hardcoded id, not a discovered one")
        self.assertFalse([l for l in found if l.get("error")],
                         f"a service came back unreadable: "
                         f"{[(l['name'], l['error']) for l in found if l.get('error')]}")

    def test_both_the_hub_and_the_hardcoded_services_are_reached(self):
        # The four layers that most directly stop a small ROV — sluices, safety gates,
        # stop-plank grooves, outfalls — are not on the Hub at all, so a discovery
        # that only walks the Hub silently drops exactly the hazards this is for.
        fake = self.serve(FakeArcGIS())
        found = self.arun(crt.inventory())
        sources = {l["source"] for l in found}
        self.assertEqual(sources, {"hub", "org"},
                         f"only {sources} were reached; the hardcoded services are the "
                         f"reason this module exists")

    def test_the_download_queries_the_discovered_ids_and_never_layer_zero(self):
        fake, _res = self.download()
        paths = [urllib.parse.urlparse(u).path for u in fake.queries]
        self.assertTrue(paths, "no layer was ever queried")
        self.assertFalse([p for p in paths if re.search(r"/0/query/?$", p)],
                         f"something queried layer 0, which no service here has: {paths}")
        for s in SERVICES:
            if s["national"] < 5:
                continue                       # skipped before any query — checked below
            self.assertTrue([p for p in paths if re.search(rf"/{s['lid']}/query/?$", p)],
                            f"{s['service']} layer {s['lid']} was discovered and never "
                            f"queried: {paths}")

    def test_a_service_error_returned_as_http_200_is_not_read_as_an_empty_layer(self):
        # The double answers a wrong layer id with {"error": ...} and HTTP 200, which
        # is what ArcGIS really does. json.loads is perfectly happy with it, and zero
        # features get counted out of it — an empty hazard file, from an error.
        fake = self.serve(FakeArcGIS())
        bad = f"{ORG}/{SERVICES[0]['service']}/FeatureServer/0/query?f=geojson"
        got = self.arun(crt._get_json(bad, tries=1))
        self.assertIsNone(got, f"an ArcGIS error body was returned as data: {got!r}")


# ===========================================================================
class Paging(CrtTestCase):
    """A page shorter than the page size is the end. Nothing else is."""

    def test_a_short_final_page_ends_the_loop_and_every_feature_arrives_once(self):
        fake, _res = self.download()
        doc = self.layer_files().get(f"{KEY['Canal_And_River_Trust_Locks_View']}.geojson")
        self.assertIsNotNone(doc, f"no locks file was written: "
                                  f"{sorted(self.layer_files())}")
        probes = [f["properties"]["probe"] for f in doc["features"]]
        self.assertEqual(
            len(probes), 10,
            f"10 locks are in this box and {len(probes)} were written. 4 means the "
            f"paging loop never ran; more than 10 means a page was fetched twice; a "
            f"multiple-of-4 shortfall means the offset advanced by the REQUESTED page "
            f"size rather than the served one. Pages fetched: {len(fake.pages_for(3))}")
        self.assertEqual(sorted(probes), sorted(f"Locks-{i}" for i in range(10)),
                         f"the ten features are not the ten the service served: {probes}")
        self.assertLessEqual(
            len(fake.pages_for(3)), 4,
            f"layer 3 was paged {len(fake.pages_for(3))} times for 10 features at 4 per "
            f"page. Three is the answer; more means the short final page did not stop "
            f"it: {fake.pages_for(3)}")

    def test_a_full_final_page_followed_by_an_empty_one_also_stops(self):
        # Weirs has exactly 4 in the box and the page size is exactly 4, so page one is
        # FULL and page two is empty. Zero features is still a short page.
        fake, _res = self.download()
        doc = self.layer_files().get(f"{KEY['Canal_And_River_Trust_Weirs_View']}.geojson")
        self.assertIsNotNone(doc, f"no weirs file: {sorted(self.layer_files())}")
        self.assertEqual(sorted(f["properties"]["probe"] for f in doc["features"]),
                         [f"Weirs-{i}" for i in range(4)],
                         "the four weirs did not come out whole")
        self.assertLessEqual(len(fake.pages_for(7)), 3,
                             f"weirs was paged {len(fake.pages_for(7))} times for one "
                             f"full page and one empty one: {fake.pages_for(7)}")

    def test_the_page_size_comes_from_the_layer_and_not_from_a_guess(self):
        fake, _res = self.download()
        asked = set()
        for u in fake.pages_for(3):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            asked.add((q.get("resultRecordCount") or [None])[0])
        self.assertEqual(asked, {"4"},
                         f"the locks layer advertises maxRecordCount 4 and was asked for "
                         f"{asked}. Asking for more than a service will serve is how a "
                         f"request times out on a Pi over a phone hotspot — and how the "
                         f"offset arithmetic then goes wrong.")


# ===========================================================================
class Projection(CrtTestCase):
    """Both directions stated, or the coordinates are not coordinates."""

    def test_every_feature_query_asks_for_wgs84_out(self):
        fake, _res = self.download()
        self.assertTrue(fake.feature_queries,
                        "no feature query was made, so this verified nothing")
        bad = []
        for u in fake.feature_queries:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            if (q.get("outSR") or [None])[0] != "4326":
                bad.append(f"{u}  (outSR={(q.get('outSR') or [None])[0]!r})")
        self.assertFalse(
            bad,
            f"{len(bad)} of {len(fake.feature_queries)} feature queries did not ask for "
            f"outSR=4326. These layers store in 3857 and 27700, and an easting of 435000 "
            f"is a perfectly valid number to find in the longitude slot of a GeoJSON "
            f"file:\n  " + "\n  ".join(bad))

    def test_every_query_that_carries_a_bbox_states_the_bbox_crs_too(self):
        fake, _res = self.download()
        boxed = [u for u in fake.queries
                 if "geometry" in urllib.parse.parse_qs(urllib.parse.urlparse(u).query)]
        self.assertTrue(boxed, "no query was clipped to the area at all — the whole "
                               "national layer would have been downloaded")
        bad = [u for u in boxed
               if (urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("inSR")
                   or [None])[0] != "4326"]
        self.assertFalse(
            bad,
            f"{len(bad)} of {len(boxed)} clipped queries sent a degrees bbox without "
            f"saying it was degrees. Read in the layer's own 3857, -1.92 52.47 is a "
            f"box two metres across off the Gulf of Guinea and selects nothing — which "
            f"arrives as an empty hazard layer:\n  " + "\n  ".join(bad))


# ===========================================================================
class EmptyIsNotAbsent(CrtTestCase):
    """The pair the whole doctrine turns on, checked in one directory."""

    def test_a_clean_fetch_that_found_nothing_still_gets_a_file(self):
        # "No tunnels in this pound" is a survey result and it is worth writing. It is
        # the POSITIVE claim, and the only thing that lets the console distinguish it
        # from the layer that never arrived.
        fake, _res = self.download()
        name = f"{KEY['Canal_And_River_Trust_Tunnels_View']}.geojson"
        doc = self.layer_files().get(name)
        self.assertIsNotNone(
            doc, f"{name} was not written. The tunnels layer fetched cleanly and "
                 f"matched nothing in this box, which is a fact about the canal and not "
                 f"a failure: {sorted(self.layer_files())}")
        self.assertEqual(doc["features"], [], f"expected an empty collection: {doc}")
        self.assertNotIn(
            "bbox", doc,
            "an empty FeatureCollection was given a bbox member. RFC 7946 lets it be "
            "absent, and a zero-size box at [0,0] is a point off the coast of Ghana.")

    def test_a_layer_that_is_near_empty_nationwide_is_skipped_and_named(self):
        fake, _res = self.download()
        prov = self.provenance()
        skipped = {s["layer_key"]: s for s in prov["skipped"]}
        key = KEY["Canal_And_River_Trust_Boat_Lifts_View"]
        self.assertIn(key, skipped,
                      f"the 1-feature-nationwide layer was not skipped: "
                      f"{sorted(skipped)}")
        rec = skipped[key]
        self.assertEqual(rec.get("skipped"), "near-empty",
                         f"it was skipped for the wrong reason: {rec}")
        self.assertRegex(str(rec.get("why", "")), r"\b1\b.*nationwide|nationwide.*\b1\b",
                         f"the reason does not say what was counted: {rec.get('why')!r}")
        self.assertNotIn(f"{key}.geojson", self.layer_files(),
                         "a layer that can only ever be empty was written out anyway")

    def test_the_near_empty_layer_is_not_fetched_at_all(self):
        # Judged on the national count, which inventory() already has, so the skip
        # costs nothing. A skip decided after paging would be a Pi on a hotspot
        # downloading a layer in order to throw it away.
        fake, _res = self.download()
        self.assertEqual(fake.pages_for(12), [],
                         f"the skipped layer was paged anyway: {fake.pages_for(12)}")


# ===========================================================================
class Provenance(CrtTestCase):
    """Where it came from, when, and how much of it — or it is not evidence."""

    def setUp(self):
        super().setUp()
        self.fake, self.result = self.download()
        self.prov = self.provenance()
        self.by_key = {l["layer_key"]: l for l in self.prov["layers"]}

    def test_every_written_layer_records_the_service_it_came_from(self):
        self.assertTrue(self.by_key, f"no layers recorded: {self.prov}")
        for key, rec in self.by_key.items():
            self.assertTrue(rec.get("service_url", "").startswith(ORG),
                            f"{key} records service_url={rec.get('service_url')!r}")
            self.assertIsNotNone(rec.get("layer_id"),
                                 f"{key} does not record which layer id it was — the "
                                 f"only durable handle there is: {rec}")

    def test_every_written_layer_records_when_it_was_fetched(self):
        today = date.today()
        for key, rec in self.by_key.items():
            stamp = str(rec.get("fetched", ""))
            self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T",
                             f"{key} has no ISO fetch time: {stamp!r}. CRT asset data "
                             f"moves; a layer with no date can never be judged stale.")
            self.assertLessEqual(abs((date.fromisoformat(stamp[:10]) - today).days), 1,
                                 f"{key} is stamped {stamp}, which is not today "
                                 f"({today}) — that is a constant, not a fetch time")

    def test_every_written_layer_records_how_many_features_are_in_it(self):
        expect = {KEY[s["service"]]: s["in_bbox"] for s in SERVICES
                  if s["national"] >= 5}
        got = {k: r.get("features") for k, r in self.by_key.items()}
        self.assertEqual(got, expect,
                         f"recorded feature counts {got} against what the service "
                         f"served {expect}")
        for key, rec in self.by_key.items():
            on_disk = len(self.layer_files()[f"{key}.geojson"]["features"])
            self.assertEqual(rec["features"], on_disk,
                             f"{key} claims {rec['features']} features and the file "
                             f"holds {on_disk} — the provenance is describing a "
                             f"different download from the one on disk")

    def test_the_independent_count_is_recorded_as_agreeing(self):
        # The server was asked returnCountOnly for the same box BEFORE paging. That
        # separate witness is the only way a silently truncated page is ever caught.
        for key, rec in self.by_key.items():
            self.assertEqual(rec.get("count_check"), "agrees",
                             f"{key}: count_check={rec.get('count_check')!r}, server "
                             f"said {rec.get('server_bbox_count')}, paging returned "
                             f"{rec.get('fetched_before_clip')}")

    def test_the_per_layer_provenance_beside_the_file_says_the_same_thing(self):
        for key, rec in self.by_key.items():
            side = self.out / f"{key}.prov.json"
            self.assertTrue(side.exists(),
                            f"{key}.geojson has no provenance beside it — a file copied "
                            f"out of this directory on its own has lost everything that "
                            f"makes it evidence")
            beside = json.loads(side.read_text(encoding="utf-8"))
            for field in ("service_url", "layer_id", "features", "fetched", "attribution"):
                self.assertEqual(beside.get(field), rec.get(field),
                                 f"{key}: the index and the file's own provenance "
                                 f"disagree about {field}")

    def test_the_attribution_travels_inside_the_geojson_itself(self):
        # The one obligation OGL v3 puts on us, and a bare FeatureCollection copied out
        # of here has shed everything outside its own braces.
        for key in self.by_key:
            doc = self.layer_files()[f"{key}.geojson"]
            self.assertIn("Canal & River Trust", str(doc.get("attribution", "")),
                          f"{key}.geojson carries no attribution: "
                          f"{doc.get('attribution')!r}")

    def test_the_licence_is_read_rather_than_assumed(self):
        for key, rec in self.by_key.items():
            self.assertEqual(rec.get("licence_class"), "ogl",
                             f"{key}: licence_class={rec.get('licence_class')!r} from "
                             f"licence text {rec.get('licence')!r}")
            self.assertTrue(rec.get("licence_source"),
                            f"{key} does not record WHERE its licence text was read "
                            f"from, so nothing can re-check it: {rec}")


# ===========================================================================
class PartialFetch(CrtTestCase):
    """A fetch that dies partway must leave nothing that looks finished."""

    def setUp(self):
        super().setUp()
        # Locks dies from its SECOND page on. Page one — four of ten locks — came back
        # perfectly well-formed, which is exactly the trap.
        self.fake, self.result = self.download(FakeArcGIS(fail_from=(3, 4)))

    def test_no_partial_layer_is_written(self):
        self.assertNotIn(
            "Locks-", self.all_text(),
            f"a partial locks layer reached the disk. Four of ten, in a valid "
            f"FeatureCollection, and from tomorrow nothing can tell it from a complete "
            f"one — it reads as 'these are the locks here'. Files: "
            f"{sorted(self.layer_files())}")
        self.assertNotIn(f"{KEY['Canal_And_River_Trust_Locks_View']}.geojson",
                         self.layer_files(), "the locks file exists")

    def test_the_failure_is_recorded_rather_than_silently_dropped(self):
        prov = self.provenance()
        skipped = {s["layer_key"]: s for s in prov["skipped"]}
        key = KEY["Canal_And_River_Trust_Locks_View"]
        self.assertIn(key, skipped,
                      f"the layer simply vanished from the record: {sorted(skipped)} / "
                      f"{sorted(l['layer_key'] for l in prov['layers'])}")
        self.assertEqual(skipped[key].get("skipped"), "fetch-failed",
                         f"recorded as {skipped[key].get('skipped')!r}: {skipped[key]}")
        self.assertRegex(str(skipped[key].get("why", "")), r"offset|page|fail",
                         f"the reason says nothing about what went wrong: {skipped[key]}")
        self.assertTrue([w for w in prov["warnings"] if key in w],
                        f"nothing in the warnings names the layer that did not arrive: "
                        f"{prov['warnings']}")

    def test_one_dead_layer_does_not_cost_the_others(self):
        files = self.layer_files()
        weirs = files.get(f"{KEY['Canal_And_River_Trust_Weirs_View']}.geojson")
        self.assertIsNotNone(weirs, f"weirs went down with locks: {sorted(files)}")
        self.assertEqual(len(weirs["features"]), 4, "weirs came out truncated")
        self.assertIn(f"{KEY['Canal_And_River_Trust_Tunnels_View']}.geojson", files,
                      "the empty-but-clean layer went missing too")


# ===========================================================================
class Offline(CrtTestCase):
    """The isolated segment. Unavailable is an answer; hanging is not."""

    def test_nothing_is_written_and_every_layer_is_accounted_for(self):
        fake, result = self.download(FakeArcGIS(dead=True))
        self.assertEqual(self.layer_files(), {},
                         f"an offline run wrote layer files: "
                         f"{sorted(self.layer_files())}")
        prov = self.provenance()
        self.assertEqual(prov["layers"], [], f"layers recorded as fetched: {prov['layers']}")
        self.assertTrue(prov["skipped"], "no layer was recorded at all — the run left "
                                         "nothing an operator could read as 'nothing "
                                         "came down and here is why'")
        for rec in prov["skipped"]:
            self.assertTrue(rec.get("why"), f"a layer is missing with no reason: {rec}")

    def test_the_result_does_not_report_a_download_that_did_not_happen(self):
        _fake, result = self.download(FakeArcGIS(dead=True))
        self.assertEqual(result.get("layers"), 0,
                         f"an offline run reported {result.get('layers')} layers: {result}")
        self.assertEqual(result.get("features", 0), 0,
                         f"an offline run reported features: {result}")


# ===========================================================================
# THE NATIONAL CARD
#
# WHAT CHANGED, AND WHY THE TESTS ABOVE ARE NO LONGER THE WHOLE STORY. The Trust's
# vectors are no longer fetched per area. The whole national network is pulled ONCE,
# on launch, onto the handheld, because a marker that is not held cannot be got at
# the waterside — there is no internet on a towpath, and the map is how this thing is
# navigated in every mode, on the water, in the simulator and on a bench at home. All
# of it is roughly 150 MB and the Ally has hundreds of gigabytes; that is not a
# reason to leave anything out, and NOTHING IS EXCLUDED FOR SIZE OR FOR TIER.
#
# The four things these classes exist to hold down, each of them a shortcut that
# would leave the console quietly worse than it claims to be:
#
#   THE WHOLE LAYER, WITH NO BOX. A national fetch that still sends an envelope is
#   an area fetch with a different directory name, and the file it writes reads as
#   "these are the locks" while holding the locks of one city.
#
#   THE BIG ONES TOO. The planning buffer is 82,880 B/feature and canals-by-
#   navigation 46,262 B/feature: between them they are most of the download, and
#   they are the two anybody under time pressure would drop. A card missing them
#   is a card whose operator was never told.
#
#   IT MUST BE RESUMABLE, BECAUSE 150 MB OVER A HOTSPOT DOES NOT FINISH FIRST TIME.
#   A layer that is already whole is not fetched again; a run that died leaves the
#   set unfinished and the NEXT run finishes it. Resumption is at LAYER granularity
#   on purpose — the doctrine at the top of nav/crt.py forbids a partial layer from
#   ever reaching the disk, because a truncated page is a well-formed
#   FeatureCollection that reads exactly like "no hazards here".
#
#   AND AN INTERRUPTED RUN MUST NOT LOOK FINISHED. Ctrl-C at the bank, a lid closing,
#   a hotspot dying: whatever is on the card afterwards must be either whole or
#   absent, and nothing may claim the set is complete when it is not.
# ===========================================================================

# The national entry point. The first spelling is the one the rest of this system
# should use — see the report that landed with this file — and the others are
# accepted so that a naming disagreement between two halves of one change costs a
# renamed function rather than a suite of red tests about something else.
_NATIONAL_ENTRY_NAMES = ("download_national", "download_national_hazards",
                         "download_all", "fetch_national")

# Reasons a layer is allowed not to be on the national card. Deliberately short, and
# deliberately not extensible from the outside: every one of these is a DECISION
# somebody wrote down, and none of them is about how many bytes the layer is.
_ALLOWED_SKIPS = frozenset(crt._DELIBERATE_SKIPS) | {"unreadable", "fetch-failed"}
# Words that give the game away. A skip that talks about size, disk or tier is the
# exclusion this round exists to forbid, whatever else the sentence says.
_EXCUSES = re.compile(
    r"(?i)\b(too\s+(big|large|heavy)|size|sizes|bytes?|kb|mb|gb|megabytes?|gigabytes?|"
    r"disk\s+space|tier\s*[123]?|extras?|heavy|bandwidth|quota\s+of\s+space)\b")


class NationalTestCase(CrtTestCase):
    """A temp data directory and the national world, with nav.crt pointed at both."""

    def setUp(self):
        super().setUp()
        # The org half of the national world. The Hub half arrives through the fake
        # Hub search, exactly as it does for the area tests.
        crt.settings = dataclasses.replace(crt.settings, crt_extra_services=NAT_EXTRA)

    # -- the entry point ----------------------------------------------------
    def entry(self):
        for name in _NATIONAL_ENTRY_NAMES:
            fn = getattr(crt, name, None)
            if callable(fn):
                return name, fn
        self.fail(
            "nav/crt.py publishes no national fetch. This suite looked for "
            + ", ".join(f"crt.{n}()" for n in _NATIONAL_ENTRY_NAMES)
            + " and found none of them. The whole Canal & River Trust network is "
              "supposed to be fetched ONCE, NATIONALLY, on launch — "
              "download_hazards(area, bbox) is the per-area fetch and cannot stand "
              "in for it: it clips to a box, and a box is the thing being removed. "
              f"Module has: {sorted(n for n in dir(crt) if 'down' in n or 'nat' in n)}")

    def national(self, fake: FakeArcGIS | None = None):
        """Run the national fetch against the national world. Returns (fake, result)."""
        fake = self.serve(fake or FakeArcGIS(world=NAT_SERVICES, call_cap=900))
        _name, fn = self.entry()
        return fake, self.arun(fn())

    def national_raising(self, fake: FakeArcGIS):
        """The same, for the run that is meant to be interrupted. Returns whatever
        came back OUT of it — a result dict if the module chose to catch, or the
        BaseException if it propagated. Both are legitimate; what is not legitimate
        is the state left on the disk, which is what the tests then read."""
        fake = self.serve(fake)
        _name, fn = self.entry()
        try:
            return fake, self.arun(fn())
        except BaseException as exc:  # noqa: BLE001 — KeyboardInterrupt is the case
            return fake, exc

    # -- where it landed ----------------------------------------------------
    def card_dir(self) -> Path:
        """The national card's directory.

        Read through crt.national_dir() when that exists, because that is the
        function the SERVING side has to call — the console reads these files and
        cannot go looking for them by guesswork. Falling back to a search keeps a
        naming disagreement from failing thirty tests about other things; the one
        test that fails is the one that asks for the accessor by name."""
        nd = getattr(crt, "national_dir", None)
        if callable(nd):
            return Path(nd())
        root = Path(crt.settings.crt_dir)
        homes = sorted({p.parent for p in root.rglob("*.geojson")})
        return homes[0] if len(homes) == 1 else root

    def card_files(self) -> dict:
        d = self.card_dir()
        if not d.exists():
            return {}
        return {p.name[: -len(".geojson")]: p for p in sorted(d.glob("*.geojson"))}

    def card_layer(self, key: str) -> dict:
        return json.loads(self.card_files()[key].read_text(encoding="utf-8"))

    def card_prov(self, key: str) -> dict | None:
        p = self.card_dir() / f"{key}.prov.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def card_index(self) -> dict | None:
        p = self.card_dir() / "provenance.json"
        if not p.exists():
            hits = sorted(Path(crt.settings.crt_dir).rglob("provenance.json"))
            if not hits:
                return None
            p = hits[0]
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — an unreadable index is an answer too
            return None

    def card_state(self) -> str:
        """Everything on the card, as one line, for a failure message that names what
        actually happened rather than what did not."""
        d = self.card_dir()
        if not d.exists():
            return f"no card directory at {d}"
        return f"{d}: " + ", ".join(
            f"{p.name}({p.stat().st_size}B)" for p in sorted(d.iterdir()))

    def skips(self) -> dict:
        idx = self.card_index() or {}
        return {s.get("layer_key"): s for s in (idx.get("skipped") or [])}


# ===========================================================================
class NationalIsWholeAndUnclipped(NationalTestCase):
    """One national run, and what it is entitled to have left behind."""

    def setUp(self):
        super().setUp()
        self.fake, self.result = self.national()
        self.files = self.card_files()

    def test_the_card_has_a_home_the_serving_side_can_find_by_name(self):
        nd = getattr(crt, "national_dir", None)
        self.assertTrue(
            callable(nd),
            "nav/crt.py does not publish national_dir(). The national card is read by "
            "api/nav/service.py and written here, and the two halves have already been "
            "broken twice by each spelling the same place differently. One function, "
            f"one answer. Files went to: {self.card_state()}")
        self.assertNotEqual(
            Path(nd()), Path(crt.area_dir(AREA)),
            "the national card is being written into an AREA's directory. The area "
            "cards are per-launch-point and are swept by their own runs; a national "
            "layer sitting in one is a layer that disappears when that area does.")

    def test_every_layer_the_trust_publishes_is_on_the_card(self):
        missing = [k for k in NAT_KEYS if k not in self.files]
        self.assertFalse(
            missing,
            f"{len(missing)} of {len(NAT_KEYS)} national layers were not written: "
            f"{missing}. Every layer the Trust publishes is supposed to be held, "
            f"always — a layer that is not on the handheld cannot be got at the "
            f"waterside. On the card: {sorted(self.files)}. Skipped: "
            f"{ {k: v.get('why') for k, v in self.skips().items()} }")

    def test_the_two_heavy_polygon_layers_are_there_and_are_the_biggest_files(self):
        # THE ONES ANYBODY WOULD DROP. 82,880 and 46,262 bytes per feature on the real
        # card, most of the national download between them, and neither of them a
        # hazard — so they are the cheapest thing in the world to leave out and the
        # easiest omission to never notice.
        for key in HEAVY:
            self.assertIn(key, self.files,
                          f"{key} is not on the card. This is one of the two heavy "
                          f"polygon layers, and 'we left the big one out' is exactly "
                          f"the shortcut this decision forbids. {self.card_state()}")
        sizes = {k: p.stat().st_size for k, p in self.files.items()}
        biggest = sorted(sizes, key=lambda k: -sizes[k])[:len(HEAVY)]
        self.assertEqual(
            sorted(biggest), sorted(HEAVY),
            f"the biggest files on the card are {biggest}, not the two heavy polygon "
            f"layers {HEAVY}. Sizes: {sizes}. Either the polygons came down truncated "
            f"or something is being simplified on the way in — the operator was "
            f"promised the published geometry, not this console's opinion of it.")

    def test_not_one_query_carries_a_bounding_box(self):
        boxed = [u for u in self.fake.queries
                 if "geometry" in urllib.parse.parse_qs(urllib.parse.urlparse(u).query)]
        self.assertFalse(
            boxed,
            f"{len(boxed)} of {len(self.fake.queries)} queries sent an envelope. A "
            f"national fetch with a box in it is an area fetch with a different "
            f"directory name, and the file it writes says 'these are the locks' while "
            f"holding one city's:\n  " + "\n  ".join(boxed[:4]))

    def test_the_files_hold_the_whole_national_layer(self):
        for svc in NAT_SERVICES:
            key = NKEY[svc["service"]]
            if key not in self.files:
                continue                      # accounted for by the tests above/below
            doc = self.card_layer(key)
            got = len(doc.get("features") or [])
            self.assertEqual(
                got, svc["national"],
                f"{key} holds {got} features and the service has {svc['national']} "
                f"nationwide. Short by a multiple of the page size is paging; short by "
                f"anything else is a clip that nobody asked for.")

    def test_nothing_on_the_card_claims_to_have_been_cut_to_an_area(self):
        # The area files carry `clip: {area, bbox, rule}` — that member is the file
        # saying "I am a piece of something bigger". On a national file it would be
        # false, and a consumer that trusted it would think the whole country was
        # 2 km of the Birmingham cut.
        for key in self.files:
            doc = self.card_layer(key)
            clip = doc.get("clip")
            self.assertFalse(
                clip,
                f"{key}.geojson carries clip={clip!r}. Nothing here was clipped to "
                f"anything; a file that says it was is describing a download that did "
                f"not happen.")

    def test_every_page_still_asks_for_wgs84(self):
        # Unchanged by any of this, and worth re-checking on the national path: these
        # services store in a mix of 3857 and 27700, and an unnoticed easting of
        # 435000 is a perfectly valid longitude in the middle of the Pacific.
        self.assertTrue(self.fake.feature_queries, "no feature query was made at all")
        bad = [u for u in self.fake.feature_queries
               if (urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("outSR")
                   or [None])[0] != "4326"]
        self.assertFalse(bad, f"{len(bad)} national pages did not ask for outSR=4326:\n  "
                              + "\n  ".join(bad[:4]))

    def test_a_layer_whose_national_count_has_drifted_is_still_fetched(self):
        # Every count in this world disagrees with crt._EXPECTED_FEATURES. Drift is
        # recorded and never acted on: the Trust adds real culverts, and a module that
        # dropped a layer because its count moved would drop the layer on exactly the
        # day it gained the features you needed.
        idx = self.card_index() or {}
        drifted = [r for r in (idx.get("layers") or [])
                   if r.get("national_expected") is not None
                   and r.get("national_features") != r.get("national_expected")]
        self.assertTrue(
            drifted,
            "no layer in this run recorded a national count differing from the "
            "measured expectation, so this test verified nothing. Every service in "
            "NAT_SERVICES is deliberately off the number in crt._EXPECTED_FEATURES.")
        for rec in drifted:
            self.assertIn(rec["layer_key"], self.files,
                          f"{rec['layer_key']} drifted ({rec.get('national_features')} "
                          f"vs {rec.get('national_expected')}) and was not written. "
                          f"Drift is a thing to record, never a reason to exclude.")

    def test_nothing_is_left_out_for_being_big_or_for_the_tier_it_sits_in(self):
        excuses = []
        for key, rec in self.skips().items():
            reason = rec.get("skipped")
            why = str(rec.get("why", ""))
            if reason not in _ALLOWED_SKIPS:
                excuses.append(f"{key}: skipped={reason!r} — not one of {sorted(_ALLOWED_SKIPS)}")
            hit = _EXCUSES.search(why)
            if hit:
                excuses.append(f"{key}: {why!r} (the word {hit.group(0)!r} gives it away)")
        self.assertFalse(
            excuses,
            "a layer was left off the national card for a reason this decision "
            "forbids. Nothing is excluded for size or for tier — the whole set is "
            "roughly 150 MB, the handheld has hundreds of gigabytes, and it is a "
            "one-time download:\n  " + "\n  ".join(excuses))

    def test_the_one_feature_layer_is_either_fetched_or_named_as_a_decision(self):
        # A layer with one feature nationwide is the one thing this module has ever
        # been entitled to leave out, and the reason was written for an AREA clip: a
        # toggle that can only ever be empty teaches a pilot that empty means broken.
        # Nationally the same layer is one real structure at one real place. Either
        # answer is defensible; vanishing without one is not.
        key = NEAR_EMPTY
        if key in self.card_files():
            doc = self.card_layer(key)
            self.assertEqual(len(doc.get("features") or []), 1,
                             f"{key} was fetched and holds {len(doc.get('features') or [])} "
                             f"features against a national count of 1")
            return
        rec = self.skips().get(key)
        self.assertIsNotNone(
            rec, f"{key} is neither on the card nor in the index's skip list. A layer "
                 f"that simply disappears is the one outcome nothing downstream can "
                 f"report: the console will show no row, no absence and no reason. "
                 f"{self.card_state()}")
        self.assertIn(rec.get("skipped"), crt._DELIBERATE_SKIPS,
                      f"{key} was left out as {rec.get('skipped')!r}, which is a "
                      f"failure and not a decision: {rec}")
        self.assertRegex(str(rec.get("why", "")), r"\b1\b",
                         f"the reason does not say what was counted: {rec.get('why')!r}")

    def test_the_run_says_it_worked(self):
        self.assertTrue(
            self.result.get("ok"),
            f"a national run against a healthy org reported failure: "
            f"{self.result.get('error')!r}. Full result: {self.result}")


# ===========================================================================
class NationalProvenance(NationalTestCase):
    """What makes a layer CURRENT has to be written down beside it.

    This is not bookkeeping. The next run decides what to re-fetch by reading these
    records, and 150 MB over a canal-side hotspot is several runs — so a record that
    cannot answer "is this layer whole, and of what" is a record that either re-
    downloads the country or serves half a layer as though it were all of it."""

    def setUp(self):
        super().setUp()
        self.fake, self.result = self.national()
        self.idx = self.card_index() or {}
        self.by_key = {r["layer_key"]: r for r in (self.idx.get("layers") or [])}

    def test_every_layer_has_a_record_beside_it(self):
        for key in self.card_files():
            self.assertIsNotNone(
                self.card_prov(key),
                f"{key}.geojson has no {key}.prov.json beside it. That file is what "
                f"the NEXT run reads to decide whether this layer is still current, "
                f"and a layer that cannot vouch for itself has to be downloaded again "
                f"— the whole country, every launch.")

    def test_the_record_says_what_makes_the_layer_current(self):
        # Five questions, and a run that cannot answer all five has to fetch the
        # country again:
        #   is this the national layer, or a clipped piece of one?  scope
        #   how much of it is here, and how much should there be?   features /
        #                                                           national_features
        #   is what is on the disk still the file that was written? bytes
        #   when?                                                   fetched
        #   and, in words, why that adds up to CURRENT              currency
        want = ("scope", "complete", "features", "national_features", "bytes",
                "fetched", "currency")
        for key in sorted(self.card_files()):
            rec = self.card_prov(key) or {}
            missing = [f for f in want if rec.get(f) is None]
            self.assertFalse(
                missing,
                f"{key}.prov.json cannot say what makes this layer current: {missing} "
                f"is/are absent. It holds {sorted(rec)}.")
            self.assertEqual(
                rec.get("scope"), "national",
                f"{key} records scope={rec.get('scope')!r}. The area cards and the "
                f"national card are two different claims about the same layer name, "
                f"and a record that does not say which it is turns one into the other.")
            self.assertIs(
                rec.get("complete"), True,
                f"{key} is on the card without complete=True: {rec.get('complete')!r}. "
                f"That flag is the one thing separating a whole layer from the pages of "
                f"one that happened to reach the disk, and nav/crt.py's own _currency() "
                f"refuses to call a layer current without it.")
            cur = rec.get("currency")
            self.assertIsInstance(
                cur, dict, f"{key} records currency={cur!r}, which nothing can read as "
                           f"a judgement with a reason attached")
            self.assertIs(
                cur.get("current"), True,
                f"{key} is on the card and its own record does not call it current: "
                f"{cur}. A layer nothing states is whole is a layer the next run must "
                f"fetch again, and one this console cannot honestly draw.")
            self.assertTrue(
                str(cur.get("why", "")).strip(),
                f"{key} is called current with no reason given: {cur}. 'Current' is a "
                f"judgement about a 150 MB download made on somebody's hotspot; a "
                f"verdict with no evidence behind it cannot be argued with when it is "
                f"wrong.")
            self.assertEqual(
                rec.get("features"), len(self.card_layer(key).get("features") or []),
                f"{key} claims {rec.get('features')} features and the file holds "
                f"{len(self.card_layer(key).get('features') or [])} — the record is "
                f"describing a different download from the one on the disk")
            self.assertEqual(
                rec.get("bytes"), self.card_files()[key].stat().st_size,
                f"{key} records {rec.get('bytes')} bytes written and the file on the "
                f"disk is {self.card_files()[key].stat().st_size} — that number is the "
                f"only thing that catches a truncated or half-copied file later")
            self.assertRegex(str(rec.get("fetched", "")), r"^\d{4}-\d{2}-\d{2}T",
                             f"{key} has no ISO fetch time: {rec.get('fetched')!r}")
            self.assertLessEqual(
                abs((date.fromisoformat(str(rec["fetched"])[:10]) - date.today()).days), 1,
                f"{key} is stamped {rec.get('fetched')}, which is not today — that is "
                f"a constant, not a fetch time")

    def test_the_index_states_the_rule_the_records_are_judged_by(self):
        # The per-layer verdicts are only as readable as the rule behind them. This is
        # what an operator staring at a card that says "current" a month after the
        # Trust re-surveyed something has to be able to go and read.
        rule = str(self.idx.get("current_rule") or self.idx.get("currency_rule") or "")
        self.assertTrue(
            rule.strip(),
            f"the national index does not say what makes a layer current. Keys: "
            f"{sorted(self.idx)}")
        self.assertRegex(
            rule, r"(?i)(count|feature)",
            f"the stated rule never mentions counting anything: {rule!r}")

    def test_the_independent_count_still_witnesses_the_paging(self):
        # The server was asked returnCountOnly before paging. On a national fetch that
        # witness matters more, not less: 7,691 towpath access points at 2,000 a page
        # is four chances for a page to come back short and be believed.
        for key in sorted(self.card_files()):
            rec = self.card_prov(key) or {}
            self.assertEqual(
                rec.get("count_check"), "agrees",
                f"{key}: count_check={rec.get('count_check')!r}; the server counted "
                f"{rec.get('server_count', rec.get('server_bbox_count'))} and paging "
                f"returned {rec.get('fetched_before_clip', rec.get('features'))}")

    def test_the_index_describes_the_whole_card(self):
        on_disk = set(self.card_files())
        listed = set(self.by_key)
        self.assertEqual(
            on_disk - listed, set(),
            f"{sorted(on_disk - listed)} are on the card and not in its index. A layer "
            f"present on the disk and absent from the file that describes the disk is "
            f"a layer the console reports nothing about while drawing it.")
        self.assertEqual(
            listed - on_disk, set(),
            f"{sorted(listed - on_disk)} are listed as layers and are not on the card. "
            f"The index is what the serving side reads; a row with no file behind it "
            f"is a layer that reports SHOWN and draws nothing.")

    def test_the_index_and_the_record_beside_the_file_do_not_disagree(self):
        for key, rec in self.by_key.items():
            beside = self.card_prov(key) or {}
            for field in ("features", "fetched", "service_url", "layer_id", "attribution"):
                self.assertEqual(
                    beside.get(field), rec.get(field),
                    f"{key}: the index and the file's own record disagree about "
                    f"{field} ({rec.get(field)!r} vs {beside.get(field)!r})")

    def test_the_attribution_still_travels_inside_every_file(self):
        for key in self.card_files():
            doc = self.card_layer(key)
            self.assertIn("Canal & River Trust", str(doc.get("attribution", "")),
                          f"{key}.geojson carries no attribution: "
                          f"{doc.get('attribution')!r}. A FeatureCollection copied out "
                          f"of this directory has shed everything outside its braces, "
                          f"and OGL v3 asks for exactly one thing.")


# ===========================================================================
class NationalIsIncremental(NationalTestCase):
    """150 MB over a hotspot is not one afternoon, so a second run finishes the job
    rather than starting it again."""

    def test_a_layer_that_is_already_current_is_not_fetched_again(self):
        first, res1 = self.national()
        self.assertTrue(res1.get("ok"), f"the first run did not finish: {res1}")
        second, _res2 = self.national()          # a fresh fake: its counters are run 2's
        repaged = {s["service"]: len(second.pages_for_service(s["service"]))
                   for s in NAT_SERVICES
                   if NKEY[s["service"]] in self.card_files()
                   and second.pages_for_service(s["service"])}
        self.assertFalse(
            repaged,
            f"the second run re-downloaded {len(repaged)} layer(s) that were already "
            f"whole on the card: {repaged}. The national set is ~150 MB and the two "
            f"polygon layers are most of it; a run that starts again from nothing "
            f"every launch is a launch that never finishes on a canal-side hotspot.")

    def test_and_it_leaves_the_files_it_did_not_fetch_exactly_as_they_were(self):
        self.national()
        before = {k: (p.read_bytes(), p.stat().st_size) for k, p in self.card_files().items()}
        self.national()
        after = {k: (p.read_bytes(), p.stat().st_size) for k, p in self.card_files().items()}
        self.assertEqual(
            sorted(before), sorted(after),
            f"the card changed shape across an incremental run: "
            f"{sorted(before)} -> {sorted(after)}")
        changed = [k for k in before if before[k][0] != after[k][0]]
        self.assertFalse(
            changed, f"{changed} were rewritten by a run that did not re-fetch them. "
                     f"Rewriting a file nobody downloaded is how a good layer becomes "
                     f"a truncated one for free.")

    def _only_one_was_refetched(self, fake, service):
        """The layer named was fetched again and nothing else was."""
        pages = fake.pages_for_service(service)
        others = {s["service"]: len(fake.pages_for_service(s["service"]))
                  for s in NAT_SERVICES
                  if s["service"] != service and fake.pages_for_service(s["service"])}
        return pages, others

    def test_a_file_that_has_been_truncated_since_the_fetch_is_fetched_again(self):
        # THE OTHER HALF OF INCREMENTAL, and the one that decides whether "current"
        # is a judgement or just "there is a file there". A layer file that is not
        # the file the fetch wrote — half-copied onto the card, truncated by a full
        # disk, edited by somebody being helpful — is a well-formed GeoJSON holding
        # some of the locks in England, and it will be served as all of them forever
        # by any run that only checks whether the path exists.
        self.national()
        victim = NKEY["Canal_And_River_Trust_Locks_View"]
        path = self.card_dir() / f"{victim}.geojson"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["features"] = doc["features"][:4]           # four of the ten locks
        path.write_text(json.dumps(doc), encoding="utf-8")
        second, _res = self.national()
        pages, others = self._only_one_was_refetched(
            second, "Canal_And_River_Trust_Locks_View")
        self.assertTrue(
            pages,
            f"{victim}.geojson was cut down to 4 of 10 features and the next run left "
            f"it alone. Whatever is deciding currency cannot see that the file is no "
            f"longer the file the fetch recorded writing, so a truncated hazard layer "
            f"is current forever and reads as 'these are the locks'.")
        self.assertEqual(
            len(self.card_layer(victim).get("features") or []), 10,
            "the re-fetched layer did not come out whole")
        self.assertFalse(
            others,
            f"re-fetching one layer pulled {others} down with it — the whole point of "
            f"incremental is that it costs one layer, not the country")

    def test_a_layer_nothing_vouches_for_is_fetched_again(self):
        # No record at all, which is what an older build, a hand-copied file or a lost
        # index leaves behind. Nothing can say what is in it or when it arrived, and
        # "it is probably fine" is the one answer this project never accepts about a
        # hazard layer.
        self.national()
        victim = NKEY["Canal_And_River_Trust_Weirs_View"]
        idx_path = self.card_dir() / "provenance.json"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        idx["layers"] = [r for r in idx["layers"] if r.get("layer_key") != victim]
        idx_path.write_text(json.dumps(idx), encoding="utf-8")
        (self.card_dir() / f"{victim}.prov.json").unlink()
        second, _res = self.national()
        pages, others = self._only_one_was_refetched(
            second, "Canal_And_River_Trust_Weirs_View")
        self.assertTrue(
            pages,
            f"{victim} is on the card with nothing anywhere describing it, and the "
            f"next run decided it was current. Currency is being read off the presence "
            f"of a file rather than off a record of what that file is.")
        rec = self.card_prov(victim) or {}
        self.assertIs((rec.get("currency") or {}).get("current"), True,
                      f"{victim} was re-fetched and its new record still does not call "
                      f"it current: {rec.get('currency')}")
        self.assertFalse(others, f"re-fetching one layer pulled {others} down with it")

    def test_a_layer_that_failed_is_finished_by_the_next_run(self):
        # Locks dies from its second page on. Page one — four of ten — came back
        # perfectly well-formed, which is the trap, and nav/crt.py's doctrine is that
        # nothing is written at all. So the SET is unfinished, and the next run's job
        # is to finish it without re-downloading the nine layers that did land.
        first, res1 = self.national(FakeArcGIS(world=NAT_SERVICES, call_cap=900,
                                               fail_from=(3, 4)))
        locks = NKEY["Canal_And_River_Trust_Locks_View"]
        self.assertNotIn(locks, self.card_files(),
                         f"a partial locks layer reached the disk: {self.card_state()}")
        self.assertFalse(res1.get("ok"),
                         f"a run that failed to fetch a layer reported success: {res1}")
        landed = set(self.card_files())
        self.assertTrue(len(landed) >= 5,
                        f"only {sorted(landed)} landed in the first run; this test needs "
                        f"a mostly-complete card to say anything about the second")
        second, res2 = self.national()
        self.assertIn(locks, self.card_files(),
                      f"the layer that failed was not picked up by the next run. The "
                      f"card is permanently missing a hazard layer and every future "
                      f"run will decide there is nothing to do: {self.card_state()}")
        self.assertEqual(
            len(self.card_layer(locks).get("features") or []), 10,
            "the resumed layer did not come out whole")
        again = {s["service"]: len(second.pages_for_service(s["service"]))
                 for s in NAT_SERVICES
                 if NKEY[s["service"]] in landed and second.pages_for_service(s["service"])}
        self.assertFalse(
            again, f"the resuming run re-downloaded {again}, which had already landed")
        self.assertTrue(res2.get("ok"),
                        f"the run that completed the card did not report success: {res2}")


# ===========================================================================
class NationalInterrupted(NationalTestCase):
    """Ctrl-C at the bank, a lid closing, a hotspot dropping mid-download.

    Whatever is on the card afterwards is either whole or absent, nothing claims the
    set is finished, and the next run picks up where this one stopped."""

    # Interrupted late and in the middle of paging: six layers have landed, this one
    # is half-fetched, and three more have not been reached at all. That spread is the
    # point — each third has a different right answer.
    HURT = "Canal_And_River_Trust_Canals_By_KM_Length_View"

    def setUp(self):
        super().setUp()
        self.fake, self.outcome = self.national_raising(
            FakeArcGIS(world=NAT_SERVICES, call_cap=900, interrupt_at=(8, 4)))
        self.hurt_key = NKEY[self.HURT]

    def test_the_interruption_was_real(self):
        # The premise of everything below. If the module quietly retried its way past
        # a KeyboardInterrupt, the rest of this class is asserting about a run that
        # was never interrupted.
        self.assertTrue(
            self.fake.pages_for_service(self.HURT),
            "the interrupted layer was never paged, so nothing was interrupted")
        landed = set(self.card_files())
        self.assertGreaterEqual(
            len(landed), 3,
            f"only {sorted(landed)} landed before the interruption; this class needs "
            f"layers on both sides of it. {self.card_state()}")

    def test_no_half_written_file_and_no_litter_is_left_behind(self):
        root = Path(crt.settings.crt_dir)
        litter = [str(p) for p in root.rglob("*.part")]
        self.assertFalse(
            litter, f"a partial write was left on the card: {litter}. _write_atomic "
                    f"exists precisely so a killed run leaves either the old whole file "
                    f"or the new whole one.")
        for key, path in self.card_files().items():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{key}.geojson is not whole JSON after an interruption: {exc}")

    def test_the_interrupted_layer_is_not_on_the_card_at_all(self):
        self.assertNotIn(
            self.hurt_key, self.card_files(),
            f"{self.hurt_key} was written after being interrupted four features in. A "
            f"truncated FeatureCollection reads exactly like a complete one and there "
            f"is no way to tell them apart tomorrow: {self.card_state()}")

    def test_nothing_left_behind_reads_as_a_finished_card(self):
        idx = self.card_index()
        if idx is None:
            return                     # no index at all is the honest outcome too
        listed = {r.get("layer_key") for r in (idx.get("layers") or [])}
        self.assertNotIn(
            self.hurt_key, listed,
            f"the index lists {self.hurt_key} as a layer of this card after its fetch "
            f"was interrupted. The console reads this file to decide what it holds.")
        self.assertFalse(
            idx.get("complete"),
            f"an index written by an interrupted run says the card is complete: "
            f"complete={idx.get('complete')!r}")
        for r in (idx.get("layers") or []):
            self.assertIn(r.get("layer_key"), self.card_files(),
                          f"the index claims {r.get('layer_key')} and there is no file "
                          f"for it: {self.card_state()}")

    def test_the_layers_that_did_land_are_whole(self):
        for key, path in self.card_files().items():
            doc = json.loads(path.read_text(encoding="utf-8"))
            svc = next(s for s in NAT_SERVICES if NKEY[s["service"]] == key)
            self.assertEqual(
                len(doc.get("features") or []), svc["national"],
                f"{key} survived the interruption holding "
                f"{len(doc.get('features') or [])} of {svc['national']} features")

    def test_the_next_run_finishes_the_card_without_refetching_what_landed(self):
        landed = set(self.card_files())
        second, res = self.national()
        missing = [k for k in NAT_KEYS if k not in self.card_files()]
        self.assertFalse(
            missing, f"after an interruption and a clean re-run the card is still "
                     f"missing {missing}. {self.card_state()}")
        again = {s["service"]: len(second.pages_for_service(s["service"]))
                 for s in NAT_SERVICES
                 if NKEY[s["service"]] in landed and second.pages_for_service(s["service"])}
        self.assertFalse(again, f"the resuming run re-downloaded {again}")
        self.assertTrue(res.get("ok"), f"the resuming run did not report success: {res}")


# ===========================================================================
class NationalSweepTakesNothingItDidNotReplace(NationalTestCase):
    """The rule from _SWEEP_FLOOR, on the national card.

    A run that reached nothing has replaced nothing, so it deletes nothing. This is
    the defect that once emptied a complete 26-layer hazard card — for the water a sub
    was about to go into — and returned ok:true. On the national card the stakes are
    the same and the download is 150 MB longer."""

    def test_a_run_that_reached_nothing_deletes_nothing(self):
        self.national()
        before = {k: p.read_bytes() for k, p in self.card_files().items()}
        self.assertTrue(before, "nothing was on the card to protect")
        _dead, res = self.national(FakeArcGIS(world=NAT_SERVICES, dead=True, call_cap=900))
        after = {k: p.read_bytes() for k, p in self.card_files().items()}
        self.assertEqual(
            sorted(after), sorted(before),
            f"an offline run changed the card: {sorted(before)} -> {sorted(after)}. "
            f"A run that downloaded nothing has replaced nothing.")
        self.assertEqual([k for k in before if before[k] != after.get(k)], [],
                         "an offline run rewrote a layer file")
        self.assertFalse(
            res.get("ok"),
            f"a run that reached nothing reported success: {res}. The moment to learn "
            f"the card is older than the run is while there is still internet.")

    def test_a_run_that_reached_half_deletes_the_other_half_of_nothing(self):
        self.national()
        locks = NKEY["Canal_And_River_Trust_Locks_View"]
        # Take locks off the card so the run has something real to do, then break it.
        (self.card_dir() / f"{locks}.geojson").unlink()
        (self.card_dir() / f"{locks}.prov.json").unlink()
        before = {k: p.read_bytes() for k, p in self.card_files().items()}
        _fake, res = self.national(FakeArcGIS(world=NAT_SERVICES, call_cap=900,
                                              fail_from=(3, 0)))
        after = {k: p.read_bytes() for k, p in self.card_files().items()}
        self.assertEqual(sorted(after), sorted(before),
                         f"a half-failed run changed the card: {sorted(before)} -> "
                         f"{sorted(after)}")
        self.assertNotIn(locks, after,
                         "the layer whose every page failed was written anyway")
        self.assertFalse(res.get("ok"),
                         f"a run that could not fetch a layer reported success: {res}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
