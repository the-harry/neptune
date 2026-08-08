"""NOMINAL depth per waterway section — guidance published by the navigation
authority, never a survey and never a measurement.

WHAT THIS IS FOR. The sub is flying blind along the bed of a canal nobody has
sounded. Before the first sounding exists there is still something worth drawing
over the water: the Canal & River Trust publishes waterway dimensions for every
navigation it manages — length, beam, headroom and **maximum draught** — and that
draught is a statement about how much water is there. It is not much, but it is
the difference between "expect roughly a metre under the boat" and "no idea at
all", and an operator who knows which of those two they are looking at can decide
whether to send the vehicle down.

WHAT THIS IS NOT, AND WHY EVERY FIELD SAYS SO OUT LOUD. It is not bathymetry. It
was not measured by this vehicle or by anybody, it has no position accuracy, and
it is flat across a channel that is not. The single failure this whole module is
written around is a client rendering these numbers in the same ink as a sounding
and an operator reading "1.07 m" off the map as though something had gone and
looked. So the word NOMINAL is stamped on the collection, on every feature, in
the human-readable title and in the aria-label — five places, because the layer
travels through a renderer this file cannot see and a label that only exists in
one of them is a label one refactor away from being dropped.

A GUIDELINE DRAUGHT IS A FLOOR ON THE DEPTH, NOT THE DEPTH. This is the part that
is easy to get quietly wrong. "Maximum draught 3 ft 6 in" means the authority
believes a boat drawing 1.07 m can pass; the bed is therefore *deeper* than 1.07 m
by some margin nobody publishes. Reporting the draught as the depth understates
the water — and that is the direction this file deliberately errs in, because the
vehicle's hazard is hitting the bottom, so a nominal that is too shallow makes an
operator cautious and a nominal that is too deep makes them confident. The
understatement is stated in `basis` on every feature rather than silently baked
in, so nobody later "corrects" it by adding an invented clearance.

AND IT SHOALS. Every figure here is a MID-CHANNEL figure. A narrow canal is a
shallow trapezoid: the mid-channel depth holds for a few metres and then the bed
climbs to the bank, where there is often less than half of it, plus whatever has
been thrown in. `shoals_to_banks` is true on every section for that reason and the
sentence goes into the title, because a depth drawn as a flat wash over a water
polygon looks exactly like a claim that the edge is as deep as the middle.

TWO PHASES (see areas.py, satellite.py). Nothing here touches the network. It
reads whatever waterway geometry is already on disk for an area and computes; on
a canal bank with no internet and no DNS it behaves identically to the bench. If
there is no waterway geometry at all it returns None — ABSENT — and never an
empty FeatureCollection, because "no water mapped here" and "no depth guidance
here" are different claims and neither of them is "the canal is not there".
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import settings

log = logging.getLogger("neptune.nav.nominal")

# The layer's own name, used in the URL, in the file it may be cached to, and in
# every properties block. One constant so a rename cannot desynchronise them.
LAYER = "nominal-depth"

_M_PER_FT = 0.3048
_M_PER_IN = 0.0254

# No canal on the CRT network is 40 m deep and none is 4 cm deep. A value outside
# this is a unit error (feet read as metres, centimetres read as metres) or a
# corrupt attribute, and a unit error rendered as a depth is worse than a blank:
# 3.5 read as metres instead of feet triples the water. Out-of-range values are
# REFUSED and reported, never clamped — a clamp turns a wrong number into a
# plausible one, which is the failure this project is named after.
_PLAUSIBLE_M = (0.1, 40.0)


# ---------------------------------------------------------------------------
# Where the numbers come from
# ---------------------------------------------------------------------------
#
# HAND-TYPED, AND LABELLED AS HAND-TYPED. Nothing below was downloaded by this
# vehicle. It is CRT's published guidance for a class of waterway, transcribed
# into this file, and each row carries the sentence that says so — that sentence
# is copied into every feature the row produces, so a section drawn on the map can
# be traced back to a human typing a figure out of a handbook rather than to an
# instrument. The moment the CRT download carries a per-navigation draught
# attribute, `draught_from_props` uses THAT and this table is not consulted; the
# table is the fallback for water we have geometry for and dimensions for.

@dataclass(frozen=True)
class Guideline:
    """One class of waterway and the published guidance for it.

    band_m is the mid-channel depth range the published guidance describes, kept
    beside the single figure because a range is what the guidance actually says
    and a single number is what a renderer needs. Showing only the number would
    let a 7 cm difference between two sections read as a survey result.
    """
    waterway: str
    label: str                       # what an operator calls this water
    draught_m: float | None          # the figure used as the nominal, or None
    band_m: tuple[float, float] | None
    source: str                      # where the figure came from, in a full sentence


_CRT_NOTE = ("Canal & River Trust publish maximum-draught guidance per navigation; "
             "this figure was typed into api/nav/nominal.py by hand from that published "
             "guidance and has never been downloaded, measured or checked against this "
             "stretch of water")

GUIDELINES: dict[str, Guideline] = {
    # THE TRUST'S OWN GAUGE, when a downloaded layer carries it. Verified on a live
    # fetch (2026-08-07, Gas Street bbox): canals-by-km-length-1.geojson tags every
    # section `sapwidth` = "Narrow" or "Broad". That is not a draught — nothing in
    # the CRT open data publishes one — but it is the Trust telling us WHICH
    # published guideline applies to this length of canal, which is a great deal
    # better than assuming.
    "narrow": Guideline(
        waterway="narrow", label="narrow canal", draught_m=1.07, band_m=(1.0, 1.2),
        source=(_CRT_NOTE + ". Narrow-canal guidance clusters at 3 ft 6 in (1.07 m) of "
                "draught, inside a mid-channel depth band of roughly 1.0–1.2 m")),
    "broad": Guideline(
        waterway="broad", label="broad canal", draught_m=1.22, band_m=(1.2, 1.4),
        source=(_CRT_NOTE + ". Broad-canal guidance runs about a foot deeper than "
                "narrow — 4 ft (1.22 m) of draught, in a mid-channel band of roughly "
                "1.2–1.4 m")),
    # A canal whose gauge nobody stated. OSM's `waterway=canal` says nothing about
    # narrow versus broad, so this row takes the NARROW figure and says out loud
    # that it is an assumption. Under-stating the water is the safe direction to be
    # wrong in for a vehicle whose hazard is the bottom: a broad canal treated as
    # narrow makes an operator cautious, and the reverse makes them confident.
    "canal": Guideline(
        waterway="canal", label="canal of unstated gauge", draught_m=1.07,
        band_m=(1.0, 1.2),
        source=(_CRT_NOTE + ". Nothing in the source says whether this is a narrow or "
                "a broad canal, so the NARROW figure (3 ft 6 in / 1.07 m) is used — "
                "the shallower of the two guidelines, which is the direction this "
                "layer is deliberately wrong in")),
    # A river IS navigable water and CRT do publish figures for the ones they
    # manage — but per navigation, and per reach within a navigation, because a
    # river's depth is set by its flow and its weirs and not by the fact that it
    # is a river. There is no class-level number to type here, so this says
    # nothing rather than inventing an average. A section that lands here comes
    # out with a null nominal and the reason attached.
    "river": Guideline(
        waterway="river", label="river navigation", draught_m=None, band_m=None,
        source=("Canal & River Trust publish river draughts per navigation and per "
                "reach, not per class — there is no class-level figure to quote, so "
                "this section has no nominal depth rather than an averaged one")),
    # Not navigations at all. Nobody publishes a draught for a field drain because
    # nothing is meant to float on it. They appear in an Overpass waterway query
    # (satellite.fetch_centreline asks for them) so they have to be answered for.
    "stream": Guideline(
        waterway="stream", label="stream", draught_m=None, band_m=None,
        source="not a navigation — no authority publishes a draught for it"),
    "ditch": Guideline(
        waterway="ditch", label="ditch", draught_m=None, band_m=None,
        source="not a navigation — no authority publishes a draught for it"),
    "drain": Guideline(
        waterway="drain", label="drain", draught_m=None, band_m=None,
        source="not a navigation — no authority publishes a draught for it"),
}

# Attribute names a downloaded waterway feature might carry its own draught in,
# most specific first. WHICH ONE WAS READ IS REPORTED on the feature
# (`depth_source_field`), because "1.07 from the CRT layer's `max_draught`" and
# "1.07 from a table in a Python file" are worth very different amounts to the
# person deciding whether to dive, and a bare 1.07 cannot tell them apart.
#
#   max_draught / maxdraught  — CRT's own naming, and OSM's key for the same idea
#   draught / draft           — the plain spellings, British and American
#   depth / depth_m           — a real depth if a layer ever carries one, and the
#                               only entries here that are NOT a draught, hence
#                               their own basis sentence in draught_from_props
DRAUGHT_FIELDS: tuple[str, ...] = (
    "max_draught", "maxdraught", "MAX_DRAUGHT", "MaxDraught",
    "draught", "draft", "DRAUGHT",
    "depth", "depth_m", "DEPTH",
)

# The subset of the above that are depths rather than draughts — a depth needs no
# "this is a floor, the bed is deeper" caveat, and attaching one to a real depth
# would be its own small lie.
_DEPTH_FIELDS = frozenset({"depth", "depth_m", "DEPTH"})

# Candidate property names for the waterway GAUGE — narrow or broad. `sapwidth` is
# the Trust's own field name, seen on a live fetch; the rest are what an equivalent
# layer from anywhere else would plausibly call it.
GAUGE_FIELDS: tuple[str, ...] = ("sapwidth", "SAPWIDTH", "gauge", "width_class")
_GAUGE_VALUES = {"narrow": "narrow", "broad": "broad", "wide": "broad"}

# Candidate property names for the waterway class, in the order a downloaded layer
# is likely to carry them.
_CLASS_FIELDS: tuple[str, ...] = ("waterway", "WATERWAY", "class", "type", "navigation")

# WHETHER THE TRUST STILL CALLS THIS LENGTH NAVIGABLE, and what happens when it
# does not. `sapnavstatus` on the CRT canal-sections layer reads "Fully Navigable"
# on a working pound; a length that is drained, closed or only partly navigable is
# not described by a guideline draught for navigable water, and quoting one over it
# would be the layer's worst possible failure — a confident metre of water drawn
# over a pound with none in it. Anything that is not unambiguously navigable comes
# out with NO nominal depth and the status attached.
NAV_STATUS_FIELDS: tuple[str, ...] = ("sapnavstatus", "SAPNAVSTATUS", "navstatus",
                                      "navigation_status")
_NAVIGABLE = "fully navigable"


# ---------------------------------------------------------------------------
# Reading a draught out of whatever a layer happens to carry
# ---------------------------------------------------------------------------
_FT_IN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|in|inch(?:es)?)?)?\s*$",
                    re.IGNORECASE)
_METRES = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*$", re.IGNORECASE)


def parse_draught(raw) -> tuple[float | None, str]:
    """One attribute value → metres, plus a sentence saying how it was read.

    Returns (None, why) rather than a number for anything it cannot read with
    certainty. THE UNITS ARE THE WHOLE JOB HERE. British waterway dimensions are
    published in feet and inches and stored in OSM in metres, and the two are
    written in the same field: `3'6"`, `3 ft 6 in`, `1.07`, `1.07 m`. A bare `3.5`
    is 3.5 metres in OSM's schema and 3 ft 6 in on a lock-side plate, and guessing
    which would be a 2.4 m error dressed as a depth — so a bare number is taken as
    metres (the schema's rule) and anything that fails the plausibility band is
    refused outright with the reason kept.
    """
    if raw is None:
        return None, "no value"
    if isinstance(raw, bool):        # JSON true/false in a numeric field is corruption
        return None, f"value {raw!r} is a boolean, not a length"
    if isinstance(raw, (int, float)):
        v = float(raw)
        if not math.isfinite(v):
            return None, f"value {raw!r} is not a finite number"
        unit_note = "taken as metres (the tagging schema's unit for a bare number)"
    else:
        s = str(raw).strip()
        if not s:
            return None, "empty value"
        m = _FT_IN.match(s)
        if m:
            feet = float(m.group(1))
            inches = float(m.group(2)) if m.group(2) else 0.0
            v = feet * _M_PER_FT + inches * _M_PER_IN
            unit_note = f"read as {s!r} = {feet:g} ft {inches:g} in"
        else:
            m = _METRES.match(s)
            if not m:
                return None, f"value {s!r} is not a length this reader understands"
            v = float(m.group(1))
            unit_note = (f"read as {s!r} in metres" if not s.rstrip().lower().endswith(("m", "s"))
                         else f"read as {s!r}")
    lo, hi = _PLAUSIBLE_M
    if not (lo <= v <= hi):
        return None, (f"{v:.2f} m is outside the {lo}–{hi} m band any CRT navigation "
                      f"occupies, so it is a unit error or a corrupt attribute and was "
                      f"REFUSED rather than clamped ({unit_note})")
    return round(v, 2), unit_note


def draught_from_props(props: dict) -> tuple[float | None, str | None, str, list[str]]:
    """The feature's own draught, if the layer that produced it carries one.

    Returns (metres, field_name, basis sentence, refusals). `refusals` is every
    field that held something and could not be read — kept and reported rather
    than dropped, because a CRT download whose `max_draught` column is arriving as
    `"3'6"` unparsed is a downloader bug that would otherwise present as this
    whole area quietly falling back to the hand-typed table.
    """
    refusals: list[str] = []
    for field in DRAUGHT_FIELDS:
        if field not in props:
            continue
        metres, how = parse_draught(props[field])
        if metres is None:
            if props[field] not in (None, ""):
                refusals.append(f"{field}={props[field]!r}: {how}")
            continue
        if field in _DEPTH_FIELDS:
            basis = (f"a depth carried on the source layer's {field!r} attribute "
                     f"({how}). It is not this vehicle's measurement — whoever "
                     f"published the layer measured or modelled it, and nothing here "
                     f"knows which")
        else:
            basis = (f"the maximum draught carried on the source layer's {field!r} "
                     f"attribute ({how}). A published draught is a FLOOR on the "
                     f"mid-channel depth and not the depth: the bed is deeper than "
                     f"this by a margin nobody publishes")
        return metres, field, basis, refusals
    return None, None, "", refusals


def guideline_for(props: dict) -> tuple[Guideline | None, str | None]:
    """The hand-typed row for this section, and the attribute that chose it.

    THE GAUGE OUTRANKS THE CLASS. `waterway=canal` says a canal; `sapwidth=Broad`
    says WHICH canal guideline applies, and it is the Trust saying it. Returning
    the field name alongside is the same rule as `depth_source_field` above: an
    operator is entitled to know whether "broad canal" came off the downloaded
    layer or out of an assumption in this file.
    """
    for field in GAUGE_FIELDS:
        v = props.get(field)
        if isinstance(v, str):
            key = _GAUGE_VALUES.get(v.strip().lower())
            if key:
                return GUIDELINES[key], field
    for field in _CLASS_FIELDS:
        v = props.get(field)
        if isinstance(v, str) and v.strip().lower() in GUIDELINES:
            return GUIDELINES[v.strip().lower()], field
    return None, None


def navigable(props: dict) -> tuple[bool | None, str | None, str | None]:
    """Does the source say this length is navigable? (verdict, field, raw value).

    None is cannot-tell and is the answer for every layer that does not carry the
    field at all — which is most of them, including the OSM centreline. Only an
    explicit statement that this length is something other than fully navigable
    withdraws the guideline, because that is the only case where we have been TOLD
    the guidance does not describe the water.
    """
    for field in NAV_STATUS_FIELDS:
        v = props.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip().lower() == _NAVIGABLE, field, v.strip()
    return None, None, None


# ---------------------------------------------------------------------------
# One section
# ---------------------------------------------------------------------------
def _sentences(depth: float | None, label: str, basis: str, source: str,
               why_none: str = "") -> tuple[str, str]:
    """The title and the aria-label for one section, as full sentences.

    Both are written HERE and shipped on the feature rather than assembled in the
    renderer. The house rule is that every number on screen carries a written
    explanation of what it MEANS, and this number's meaning is almost entirely
    caveat — which of two guidance sources it came from, that it is a floor, that
    it is mid-channel only. A renderer that has to reconstruct that from four
    separate properties will one day ship a tooltip that says "1.07 m" and stop.
    """
    if depth is None:
        t = (f"NOMINAL depth: CANNOT TELL for this {label}. {why_none} No depth is "
             f"claimed here at all — that is not shallow water and not deep water, it "
             f"is no claim about the water, and the vehicle should be flown here as "
             f"though the bed could be anywhere.")
        a = (f"Nominal depth for this {label}: cannot tell. {why_none} No depth is "
             f"claimed here. An absent figure is not a safe figure.")
        return " ".join(t.split()), " ".join(a.split())
    t = (f"NOMINAL depth about {depth:.2f} metres mid-channel for this {label}. "
         f"This is GUIDANCE, not a survey: {basis}. Source: {source}. The channel "
         f"shoals toward both banks, so the water at the edge is shallower than "
         f"this — often much shallower — and nothing has measured this stretch.")
    a = (f"Nominal depth for this {label}, about {depth:.2f} metres in mid-channel. "
         f"It is published guidance and not a measurement: {basis}. The canal is "
         f"shallower toward each bank than this figure, and no survey of this "
         f"stretch exists.")
    return t, a


def section_properties(props: dict, idx: int) -> dict:
    """The nominal block for one waterway section, ready to render and to argue with."""
    depth, field, basis, refusals = draught_from_props(props)
    guide, guide_field = guideline_for(props)
    label = guide.label if guide else "unclassified waterway"
    nav_ok, nav_field, nav_value = navigable(props)
    band, why_none = None, ""

    if nav_ok is False:
        # THE SOURCE HAS TOLD US THE GUIDANCE DOES NOT APPLY. A guideline draught
        # describes navigable water; drawing 1.07 m of it over a length the Trust
        # has recorded as anything else would be the worst failure this layer has
        # available — a confident metre of water over a pound that may have none.
        # Any draught the feature carried is dropped with it, for the same reason.
        depth, field = None, None
        depth_source = "withheld:not-navigable"
        source = (f"the source layer's {nav_field!r} attribute reads {nav_value!r}, "
                  f"which is not 'Fully Navigable'")
        why_none = (f"The source records this length as {nav_value!r} rather than fully "
                    f"navigable, so published guidance for navigable water does not "
                    f"describe it and none is quoted.")
        basis = ""
    elif depth is not None:
        source = f"the source layer's own {field!r} attribute"
        depth_source = f"attribute:{field}"
    elif guide is not None and guide.draught_m is not None:
        depth = guide.draught_m
        band = guide.band_m
        source = guide.source
        depth_source = "guideline-table"
        chose = (f"chosen by the source layer's {guide_field!r} attribute" if guide_field
                 else "and nothing in the source chose it")
        basis = (f"the published guideline maximum draught for a {guide.label}, {chose}. "
                 f"It is a FLOOR on the mid-channel depth and not the depth itself — "
                 f"the bed is deeper than this by a margin nobody publishes. "
                 f"Under-stating the water is the deliberate direction of error for a "
                 f"vehicle whose hazard is the bottom")
    else:
        source = (guide.source if guide is not None
                  else "this section carries no waterway class and no gauge, so no "
                       "guidance could be looked up for it at all")
        depth_source = "none"
        basis = ""
        why_none = f"Nothing published a draught for it and nothing has surveyed it: {source}."

    title, aria = _sentences(depth, label, basis, source, why_none)
    out = {
        "layer": LAYER,
        "section_id": f"s{idx:04d}",
        "waterway": label,
        # THE THREE FLAGS A RENDERER MUST BRANCH ON, and they are deliberately
        # redundant with each other. `nominal` says what this is, `measured` says
        # what it is not, and `is_survey` says the same thing in the word a chart
        # renderer would look for. Any one of them could be dropped in a refactor;
        # all three being wrong at once takes intent.
        "nominal": True,
        "measured": False,
        "is_survey": False,
        "nominal_depth_m": depth,
        # THE SAME NUMBER UNDER THE NAME A DEPTH RENDERER LOOKS FOR FIRST.
        # client/js/crt.js reads `depth_m` before anything else, and a nominal
        # layer whose figure it cannot find draws nothing at all — which on a
        # depth map is indistinguishable from water nobody has guidance for. It is
        # safe to publish it under the plain name here ONLY because the same
        # properties block carries `nominal: true`, `measured: false` and
        # `is_survey: false` beside it, and because the console decides hatched
        # versus solid from which LAYER it fetched rather than from this key. Null
        # stays null: a section with no guidance publishes no depth under either
        # name.
        "depth_m": depth,
        "depth_source": depth_source,
        "depth_source_field": field,
        # Which attribute picked the guideline row, so "broad canal" can be traced
        # to the Trust's own gauge rather than to a guess in this file.
        "class_source_field": guide_field,
        "band_m": list(band) if band else None,
        "shoals_to_banks": True,
        "navigable": nav_ok,
        "navigation_status": nav_value,
        "basis": basis or None,
        "provenance": source,
        "title": title,
        "aria_label": aria,
    }
    if refusals:
        # A draught that was THERE and could not be read is a finding, not a gap.
        out["refused_attributes"] = refusals
    return out


# ---------------------------------------------------------------------------
# Building the layer for an area
# ---------------------------------------------------------------------------
def _iter_features(gj: dict):
    """Every Feature in a GeoJSON document, whatever it is wrapped in."""
    t = (gj or {}).get("type")
    if t == "FeatureCollection":
        for f in gj.get("features") or []:
            if isinstance(f, dict):
                yield f
    elif t == "Feature":
        yield gj
    elif t in ("LineString", "MultiLineString", "Polygon", "MultiPolygon"):
        # A bare geometry with no properties. It still describes water, and a
        # section with no class comes out with a null nominal and says why — which
        # is more useful than dropping the geometry and leaving a hole the client
        # cannot distinguish from land.
        yield {"type": "Feature", "properties": {}, "geometry": gj}


def build(source: dict, *, area: str, built_from: str) -> dict:
    """A waterway GeoJSON → the NOMINAL depth layer over the same geometry.

    The geometry is passed through untouched. The client draws this over the water
    polygon, so the sections have to sit exactly where the water it is colouring
    sits; re-deriving or simplifying the geometry here would put a depth claim a
    few metres off the water it is about, which on a 7 m wide canal is the whole
    channel.
    """
    feats, unknown = [], 0
    for i, f in enumerate(_iter_features(source)):
        props = f.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        np_ = section_properties(props, i)
        if np_["nominal_depth_m"] is None:
            unknown += 1
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": np_})

    known = len(feats) - unknown
    title = (f"NOMINAL depth guidance for {area}: {known} of {len(feats)} waterway "
             f"sections carry a published guideline depth and {unknown} carry none. "
             f"Every figure is guidance for a class of waterway, mid-channel, and "
             f"nothing on this layer has been surveyed or measured by this vehicle.")
    aria = (f"Nominal depth layer for area {area}. {known} of {len(feats)} sections "
            f"have published guidance; {unknown} have none and claim no depth. This "
            f"layer is guidance and not a survey.")
    return {
        "type": "FeatureCollection",
        "features": feats,
        # Foreign members on a FeatureCollection: legal GeoJSON (RFC 7946 §6.1),
        # ignored by MapLibre, and the only place a whole-layer caveat can live
        # where a renderer that draws the collection cannot miss it.
        "layer": LAYER,
        # The same key every other layer endpoint answers with, so one branch in a
        # client covers all of them. A layer that is computed can still be absent
        # (nothing to compute from), and that answer is built in service.py; this
        # is the other side of it, and a null here would make the two look
        # different for no reason.
        "status": "present",
        "nominal": True,
        "measured": False,
        "is_survey": False,
        "area": area,
        "built_from": built_from,
        "sections": len(feats),
        "sections_with_guidance": known,
        "sections_without_guidance": unknown,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "title": title,
        "aria_label": aria,
    }


# ===========================================================================
# Where the waterway geometry comes from
# ===========================================================================
#
# Two candidates, and they are worth very different amounts.
#
#   1. A LAYER THE CRT DOWNLOAD ACTUALLY PUT ON THIS CARD. nav/crt.py discovers
#      the Trust's published layers at bootstrap rather than working from a
#      hardcoded list, so nothing here may name one: the layer keys are whatever
#      the org's service catalogue said that day. What CAN be asked of a file is
#      whether it carries a draught attribute, so the candidates are found by
#      LOOKING rather than by being named, and the one that answers is used.
#
#   2. THE OSM WATERWAY CENTRELINE that satellite.fetch_centreline already writes
#      to areas/<area>.geojson. It carries a waterway CLASS and nothing else —
#      that function keeps `{"waterway": …}` and drops every other tag, so an OSM
#      `maxdraught` cannot survive the trip even where one exists. That limit is
#      in satellite.py, not here, and it is the reason the hand-typed table above
#      exists at all.
#
# WHAT IS NOT A CANDIDATE: data/soundings/<area>.json. Those are the depths this
# vehicle has actually stood on, and mixing a measured lower bound into a layer
# stamped NOMINAL would destroy the one distinction this file is for. The two are
# served side by side and drawn separately; the measured one wins on screen, but
# it wins as a different layer, with its own name on it.


def _crt_layer_dir(area: str) -> Path | None:
    """data/crt/<area>/, if the CRT download has ever run for this area.

    Reached through nav/crt.py's own `area_dir` so there is one definition of
    where those files live — that function's docstring says in as many words that
    the serving side may import it and nothing else from that module, because
    everything else in it talks to the network. This import is path arithmetic
    only and resolves no hostname; it is safe canal-side.
    """
    try:
        from . import crt
    except ImportError:      # the downloader is not in this build; nothing to scan
        return None
    name = crt.safe_area_name(area)
    if not name:
        return None
    try:
        d = crt.area_dir(name)
        return d if d.is_dir() else None
    except Exception:  # noqa: BLE001 — a missing config attribute must not blank the layer
        return None


# The attribute names worth opening a file for. A layer that mentions none of them
# anywhere in its text cannot produce a single nominal section, so it is not parsed.
_USEFUL_FIELDS: tuple[str, ...] = DRAUGHT_FIELDS + GAUGE_FIELDS + _CLASS_FIELDS


def crt_depth_layer(area: str) -> tuple[Path | None, str]:
    """Scan the downloaded CRT layers for the best one to hang depth guidance on.

    Returns (path or None, what the scan found — in a sentence). THE SENTENCE IS
    HALF THE POINT. "Fell back to the hand-typed table" and "the CRT layers ARE on
    this card and not one of them publishes a draught" are the same outcome and
    different findings, and only the second tells whoever is holding this that
    there is nothing better to be had. Measured on a live fetch, 2026-08-07: 26
    layers for a Birmingham bbox, zero draught attributes anywhere in them, and
    one layer — canals-by-km-length — carrying `sapwidth` Narrow/Broad, which is
    the Trust naming which of its published guidelines applies.

    The substring pre-filter is not an optimisation for its own sake: a bootstrap
    leaves a couple of dozen layer files in that directory, one of them 2 MB, and
    this runs on a Pi. Every file is read once as text and only the ones that
    mention a useful attribute by name are parsed as JSON.
    """
    d = _crt_layer_dir(area)
    if d is None:
        return None, ("no CRT layers have been downloaded for this area — "
                      "`python -m nav.cli crt-fetch <area>` is a BOOTSTRAP-time job "
                      "and it has not been run here")
    files = sorted(p for p in d.glob("*.geojson"))
    if not files:
        return None, (f"{d} exists but holds no layer files — a CRT fetch was started "
                      f"for this area and wrote nothing")
    best, best_score, best_why, scanned, with_draught = None, 0, "", 0, 0
    for p in files:
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not any(f'"{f}"' in text for f in _USEFUL_FIELDS):
            continue
        try:
            gj = json.loads(text)
        except Exception:  # noqa: BLE001 — a corrupt layer is the downloader's to report
            continue
        n_draught = n_class = 0
        for f in _iter_features(gj):
            props = f.get("properties") or {}
            if draught_from_props(props)[0] is not None:
                n_draught += 1
            elif guideline_for(props)[0] is not None:
                n_class += 1
        with_draught += 1 if n_draught else 0
        # A PUBLISHED DRAUGHT BEATS ANY AMOUNT OF CLASSIFICATION. One section with a
        # real figure on it is worth more than a hundred sections classified as
        # "narrow canal", because only the first is the Trust talking about THIS
        # length of water; the second is this file's table talking about all of them.
        score = n_draught * 1000 + n_class
        if score > best_score:
            best, best_score = p, score
            best_why = (f"{n_draught} of its features publish a draught"
                        if n_draught else
                        f"{n_class} of its features carry a gauge or waterway class")
    if best is None:
        return None, (f"{scanned} CRT layer(s) are on this card for this area and not "
                      f"one carries a draught, a gauge or a waterway class this reader "
                      f"can use (looked for {', '.join(DRAUGHT_FIELDS[:3])}, "
                      f"{', '.join(GAUGE_FIELDS[:2])})")
    tail = ("" if with_draught else
            " — no CRT layer here publishes a draught at all, so the figures come from "
            "the hand-typed guideline table in api/nav/nominal.py")
    return best, (f"{best.name}, out of {scanned} CRT layer(s) scanned: {best_why}{tail}")


def source_path(area: str) -> tuple[Path, str] | None:
    """The best waterway geometry on disk for this area, or None if there is none.

    None is ABSENT and it is returned rather than an empty layer on purpose: an
    area with no waterway geometry is an area nobody has downloaded the water for,
    which is a different statement from "the water here has no published depth"
    and sends the operator somewhere different (run the download, versus accept
    that this stretch is unguided).
    """
    crt_layer, scan = crt_depth_layer(area)
    if crt_layer is not None:
        return crt_layer, scan
    centre = settings.areas_dir / f"{area}.geojson"
    if centre.exists():
        return centre, (f"{centre.name}, the OSM waterway centreline cached for this "
                        f"area, which carries a waterway class and no draught. {scan}")
    return None


def load(area: str) -> dict | None:
    """The NOMINAL layer for an area, computed from disk. None = ABSENT.

    COMPUTED RATHER THAN CACHED TO A FILE, deliberately. The inputs are two files
    on the same disk and the arithmetic is a table lookup per section, so the only
    thing a cached copy could add is the chance of being stale — a nominal layer
    still describing the centreline that was on the card three area-downloads ago,
    with nothing on it to say so. Everything here is offline either way.
    """
    found = source_path(area)
    if found is None:
        return None
    path, why = found
    try:
        gj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a corrupt source is not an absent one
        # Re-raised as a value the caller can report as UNREADABLE. Swallowing it
        # into None would tell an operator "no water mapped here" about an area
        # whose water file is sitting right there, half-written.
        raise ValueError(f"{path.name} could not be read as GeoJSON: {exc}") from exc
    # `why` already names the file it chose and says what else it looked at, so it
    # is passed through rather than prefixed with the filename a second time.
    return build(gj, area=area, built_from=why)
