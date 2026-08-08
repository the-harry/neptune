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
import json
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

    def __init__(self, fail_from=None, dead=False, call_cap=200):
        # (layer_id, offset): every FEATURE page for that layer at or past that offset
        # raises, persistently. Persistent rather than one-shot because _get_json
        # retries three times, and a failure that heals on retry is not the defect.
        self.fail_from = fail_from
        self.dead = dead                     # every request raises: the isolated segment
        self.calls: list[str] = []
        self.call_cap = call_cap

    # -- bodies -------------------------------------------------------------
    def _feature(self, svc, i):
        return {"type": "Feature", "id": i + 1,
                "properties": {"OBJECTID": i + 1, "probe": f"{svc['layer_name']}-{i}",
                               "SAP_OBJECT_NO": 100000 + svc["lid"] * 1000 + i},
                # Inside BBOX, so crt._keeps() holds on to every one of them and a
                # short file means paging, not clipping.
                "geometry": {"type": "Point",
                             "coordinates": [-1.910 + i * 0.001, 52.480]}}

    def _hub(self):
        hub = [s for s in SERVICES if s["source"] == "hub"]
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
                            "geometryType": "esriGeometryPoint"}],
                "tables": []}

    def _layer_meta(self, svc):
        return {"id": svc["lid"], "name": svc["layer_name"],
                "geometryType": "esriGeometryPoint", "objectIdField": "OBJECTID",
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
        svc = next((s for s in SERVICES if s["service"] == service), None)
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

    def _query(self, svc, q):
        if (q.get("returnCountOnly") or [""])[0] == "true":
            # National (no envelope) versus in-this-box (envelope present).
            n = svc["in_bbox"] if "geometry" in q else svc["national"]
            return json.dumps({"count": n}).encode()
        offset = int((q.get("resultOffset") or ["0"])[0])
        if self.fail_from and svc["lid"] == self.fail_from[0] and offset >= self.fail_from[1]:
            raise urllib.error.URLError(
                f"connection reset on {svc['layer_name']} at offset {offset} "
                "(the canal-side hotspot, doing what it does)")
        asked = int((q.get("resultRecordCount") or ["2000"])[0])
        count = min(asked, svc["page"])          # the cap the client does not control
        feats = [self._feature(svc, i)
                 for i in range(offset, min(offset + count, svc["in_bbox"]))]
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
