"use strict";
/* ============================================================================
   CRT — THE CHART LAYERS, AND WHAT EVERY MARK MEANS FOR THIS VEHICLE

   The Canal & River Trust publish their asset data as open layers: locks, weirs,
   sluices, culverts, tunnel portals, outfalls, and a long tail of operational and
   incidental features. This file draws them on the map and — more importantly —
   says what each one means for a 5 kg tethered sub on a reel of cable, which is
   never what the layer is called. "Weir" is a noun. "Anything that gets over the
   sill goes with the water and does not come back, and so does the tether" is the
   fact the operator actually needs, and it is the only version of it that is any
   use standing on a wet towpath.

   THREE TIERS, AND THE TIER IS A SAFETY DECISION, not a display preference. What it
   decides is HOW LOUDLY a mark is drawn and in what order — never whether it is drawn:

     1  HAZARDS       always drawn, NOT toggleable, drawn LAST and loudest.
                      Entrainment, suction and no-retrieval structures. There is no
                      operator preference that makes it right to hide these.
     2  OPERATIONS    drawn beneath the hazards, quieter, toggleable. Where you can
                      get in, get out, turn round, and what is moored in the way.
     3  EXTRAS        drawn quietest of all, under both, toggleable. Worth keeping,
                      and worth keeping out of the way of the two above it.

   NOTHING IS OFF BY DEFAULT ANY MORE, AND THAT IS A CORRECTION RATHER THAN A TASTE.
   Every layer this handheld holds is drawn on a fresh console. The toggles stay and
   are still remembered, because the operator is entitled to prune what they do not
   want after driving with it — but the console does not prune for them in advance.
   A layer that is present and invisible is a layer nobody knows they have, and the
   old tier-3 default meant a handheld could be carrying the aqueducts, the pumping
   stations and the Trust's own centreline to the water and show none of them unless
   somebody happened to open a panel and go looking.

   THE COST OF THAT IS SOUP, AND SOUP IS ITS OWN DISHONESTY — the operator cannot see
   the weir for the moorings. So the answer is in the DRAWING and not in a switch:
   four draw bands (below), weight and opacity graded by how much a mark matters to a
   small tethered ROV, and screen-space DECLUTTERING that merges marks which would
   overlap and says in words how many it merged. See crtDraw.

   THE WHOLE NETWORK IS HELD, NATIONALLY, AND IT IS NOT AN ACCESSORY TO AN AREA.

   This file used to be able to say nothing at all until an offline AREA existed: every
   URL below carried an area name, so a handheld with the Trust's entire published
   network sitting on its disk drew a blank map because nobody had tapped a launch
   point yet. That is the wrong way round. The maps are HOW THIS THING IS NAVIGATED —
   in the water, in the simulator, and on a kitchen table planning a run — so the
   national store is asked first, always, and needs no area, no launch point, no Pi and
   no particular mode to answer. Satellite IMAGERY stays per-area, because it is tiles
   and it is bounded by where you are going; the Trust's VECTORS are not.

   THE PER-AREA CARD IS STILL READ, SECOND. Consoles fetched under the old scheme have
   data/crt/<area>/ on their disk and it is perfectly good data about real water. A
   layer the national store does not have and the area card does is drawn from the card
   and says so in the row. National first, card second, and every row names which of
   the two answered for it — because "where did this mark come from" is a question with
   two possible true answers now, and a console that blurred them would be inventing the
   one thing this file exists not to invent.

   AND SO "NOT DOWNLOADED" BECOMES THE LAST RESORT RATHER THAN THE EVERYDAY STATE. A
   fresh console used to show it as a matter of course, which taught everybody to read
   the absence marks as "still booting". The ordinary states after this change are
   DOWNLOADING (the once-only national fetch, on a new handheld) and HERE. NOT
   DOWNLOADED and CANNOT TELL still say exactly what they always said, in the same
   words, with the same honesty — they are simply no longer what a healthy console
   shows you on launch.

   TWO HONESTY RULES RUN THROUGH THE WHOLE FILE.

   NO FLOW IS EVER SHOWN. CRT publish no flow measurement of any kind, so the
   hazard marks are the honest proxy for "expect current here" — and every one of
   them says exactly that in its own words. An inference is not a measurement, and
   a mark that implied a measured current at a weir would be inventing the single
   number an operator would most like to have been given.

   AN ABSENT LAYER SAYS ABSENT. "No locks here" and "no lock data here" are
   opposite claims and only one of them is safe to act on, so: a layer whose file
   is not on the backend reports ABSENT; a layer that IS there with nothing inside
   this area reports NONE MAPPED; and a backend that cannot be asked at all reports
   CANNOT TELL. Three states, three different words, and never a quietly empty map.

   AND A FOURTH, BECAUSE CANNOT TELL WAS BEING SHOUTED AT PEOPLE WHO HAD NEVER ASKED
   ANYBODY ANYTHING. CANNOT TELL is an ALARM: it means this console had reason to
   expect an answer and did not get one, and on the hazard tier it is the loudest
   thing on this map. A console opened on a bench — a laptop, the ?sim=1 demo, a
   handheld that has never been plugged into a Pi — has nobody to ask and never had,
   and it was raising that alarm on every launch that happened to remember an area
   from a previous session. An alarm that fires on a healthy console is an alarm that
   gets ignored, and the one it teaches you to ignore is the one that means hazard
   data is missing for water somebody is about to put a sub into.

   So the silence is split in two, by evidence and not by wording:

     CANNOT TELL     asked, no answer, AND there was reason to expect one: this
                     console ADDRESSES A MAP BACKEND, and either that backend has
                     been answering or this area's chart index has answered before.
                     Loud, and retried in the background.
     NOT DOWNLOADED  no chart data for this area is held anywhere this console can
                     reach — because it has never been downloaded here, or because
                     the backend said so itself, or because there is no backend
                     behind this console at all. Said quietly in the panel; the map
                     notes it without alarming.

   BOTH HALVES OF THE EVIDENCE ARE GATED ON THERE BEING SOMEBODY TO ASK, and that is
   the correction the demo forced. The seen-record is a fact about a BACKEND — one
   answered for this cut once — and a console that addresses none does not inherit it,
   because it asks nobody and therefore has nothing that could have gone quiet. What it
   does NOT relax: a console that IS pointed at a backend, on a cut that has answered
   before, is loud the moment the answers stop, whether or not anything came up this
   session. That is the console the alarm was written for.

   NOTHING IS RELAXED BY THAT. Absent is still never drawn as present, NOT DOWNLOADED
   is still an absence and still says in its own words that an empty stretch means NO
   DATA rather than clear water, and every layer still says what happened to it. The
   only thing that changed is WHICH true sentence is said and HOW LOUDLY. What decides
   it is crtHadReasonToExpectAnAnswer(), further down, and the evidence it reads.

   THE VEHICLE AND THE MAP ARE DIFFERENT BACKENDS, and this file used to assume they
   were one. Every request below went to `state.httpBase` — the VEHICLE host — so the
   chart layers could only ever exist on a console with a Pi on the other end of the
   tether. The ?sim=1 demo resolves no host at all (core.js resolveHost returns early),
   which meant the map was permanently blank on a handheld that was perfectly capable
   of holding every byte of it: "no chart data downloaded", true and useless.

   SIM MEANS THE VEHICLE IS SIMULATED. It does not mean the MAP is fake. The Trust's
   hazard layers, the satellite imagery, the canal centreline and the downloaded area
   are real data about real water, fetched from real services, and they are exactly as
   true with no sub attached as with one. So the simulated vehicle stays simulated and
   stays flagged everywhere it is drawn — and the map reads from a MAP DATA BACKEND
   which, on the ROG Ally, is a service on the handheld itself. Nothing that comes back
   from it is marked simulated, because none of it is.

   TWO-PHASE, like areas.py and satellite.py: nothing here touches the internet.
   Every request goes to the map data backend (mapDataBase, below), carries its own
   timeout, and a backend that does not answer produces CANNOT TELL rather than a hung
   overlay.
   ============================================================================ */

/* WHERE THE DATA COMES FROM.

   The INDEX is the gate, and that is deliberate. If a per-layer GET 404s we would
   like to say ABSENT — "the backend looked, and that file is not on the disk" — but
   a 404 also happens when the backend holds nothing for this area at all, and those
   two are not the same claim. So the index is asked first: it answering is what earns
   the console the right to report per-layer absence. No index, no per-layer claim —
   and which of CANNOT TELL and NOT DOWNLOADED the whole panel then reads is decided
   by the evidence block further down, never by the shape of the failure.

   Paths are here, in one object, and not in config.js — that file belongs to
   another owner this round. The index may also hand back its own `path` per layer,
   which wins over these, so the server side can move an endpoint without this file
   having to be edited in the same breath. */
const CRT_API = {
  /* THE NATIONAL STORE — the whole Canal & River Trust network, fetched once on
     launch and held on this handheld. NO AREA IN ANY OF THESE PATHS, which is the
     entire point: a console with no launch point, no Pi and no offline area still
     has a map. */
  net:       '/api/crt',                    // the national layer index
  netLayer:  '/api/crt/{layer}',            // one national layer, optionally windowed
  netFetch:  '/api/crt/fetch',              // GET the once-only download's progress; POST starts it
  /* THE PER-AREA CARD — second, and only for what the national store does not have.
     Consoles fetched under the old scheme hold data/crt/<area>/ and it is real data
     about real water; refusing to read it would be throwing away a map to make a
     point. The depth pair is per-area BY NATURE (one is built from this area's dive
     journals) and is only ever read from here. */
  index:     '/api/areas/{area}/crt',
  layer:     '/api/areas/{area}/crt/{layer}',
  timeoutMs: 4000,        // a backend that is thinking, versus one that is gone
  /* A NATIONAL LAYER IS MEGABYTES, NOT KILOBYTES. The 4 s that tells a live backend
     from a dead one over a tether is not long enough to read 46 kB a feature off a
     local disk, and a timeout that fires on a working read produces CANNOT TELL —
     the map's loudest alarm — about data that was arriving. */
  netTimeoutMs: 30000,
  retryMs:   30000,       // how often a CANNOT TELL layer is quietly re-asked
  dlPollMs:  2000,        // how often the once-only national download is asked where it has got to
  /* THE WINDOW. 3,173 centreline features and 1,296 planning-buffer polygons at
     82 kB each is a national dataset, and no browser holds all of it parsed while
     drawing at 10 Hz. So a body is asked for around WHERE THE MAP IS LOOKING, with
     this much slack on every side, and re-asked when the view leaves it. That is
     paging, not pruning: the store holds everything, the row reports everything the
     store holds, and what is in the window is what could be drawn anyway. */
  windowM:   6000,
  /* Don't pull a body this big into memory with nowhere to draw it. Only reachable on
     a console that has never had a position of any kind — the row still says the layer
     is HERE, because it is, and says in its own words that none of it has been read
     yet. Read the moment the map knows where it is. */
  bigBytes:  8 * 1024 * 1024,
  /* THE CEILING ON ONE BODY, MEASURED AGAINST A REAL STORE RATHER THAN GUESSED.
     data/crt/national on this machine holds 27 layers and 140 MB, and ONE file is
     100 MB of it: planning-buffer-polygon-0, 1,296 consultation-zone polygons at
     ~78 kB each. JSON.parse on that stalls the main thread for seconds and the parsed
     result is several hundred megabytes of heap — on a console that is at that moment
     the only thing between an operator and a sub on a cable.

     So there is a ceiling, and crossing it is NOT a quiet skip: the row reports HELD
     with the size and the reason in full, it keeps its switch, and it draws the moment
     the store answers a windowed request with a window. Everything else in the store
     is under it — the next largest is the 9.8 MB centreline — so this is one layer,
     and it is the one whose claim ("this is inside a planning consultation zone") is
     the furthest from anything you steer on. See CRT_API.windowM. */
  readCeilingBytes: 24 * 1024 * 1024,
  maxDraw:   1500,        // marks placed per layer per frame; the row says when it truncates
};
/* THE SEEN-RECORD'S KEY FOR THE NATIONAL STORE. `CRT.seen` is keyed by area name;
   the national store has no area, so it gets a key no area can collide with — a
   plain name would be a real cut somewhere and the two records would silently share
   a slot. */
const CRT_NATIONAL_KEY = '*national*';
/* HOW MANY LAYERS THE TRUST PUBLISHES, for "DOWNLOADING, N OF 27" before the backend
   has told us its own total. Measured, not guessed: api/nav/crt.py fetches 27 layers
   across 27 services. The backend's own `total` always wins when it sends one, so
   this going stale costs a wrong denominator for a few seconds and never a wrong
   claim about what landed. */
const CRT_NATIONAL_LAYERS = 27;

/* ============================================================================
   WHERE THE MAP DATA LIVES — AND IT IS NOT THE VEHICLE.

   Chart layers, the areas list, the depth layers and the canal centreline are all
   read through here, and every one of them used to be addressed at `state.httpBase`,
   which is the VEHICLE. That is the defect: the sub is not a map server, it is a
   thing that gets driven around inside the map, and on this system the map data is
   held by the handheld the operator is carrying. Pointing chart requests at the sub
   made the whole map conditional on a Pi being plugged in, which is why the demo —
   and any bench console — showed nothing at all.

   ONE BASE, ONE NAME, ONE MACHINE. core.js resolves the map service at boot and
   publishes it as `state.dataBase` (with `state.dataFrom` saying how it was found);
   this file only consumes it.

   AN EMPTY STRING IS AN ANSWER, NOT A BLANK. '' means SAME ORIGIN — the page was
   served by something that also answers /api — which is how `state.httpBase` has
   always spelled the same idea. `dataFrom` is what distinguishes "same origin" from
   "nowhere", so the emptiness is never read as an absence.

   THERE IS NO FALLBACK TO THE VEHICLE, and that is the point. This used to try
   `state.mapBase`, then two other spellings nobody publishes, and finally the vehicle's
   own host — so with the map service unreachable the charts were fetched from the Pi
   and every sentence below said "the Pi". The maps are not an accessory to the sub:
   they are how this thing is navigated, they matter in the simulator and on a bench as
   much as in the water, and they live on the handheld, which is the most capable
   machine here and the only one always present. A Pi is a 3B+ on the end of a cable
   with a vehicle to run. If there is no map service, that is a fault to report.
   ============================================================================ */
function mapDataBase(){
  const s = (typeof state!=='undefined' && state) ? state : null;
  return (s && typeof s.dataBase === 'string') ? s.dataBase : '';
}
/* Is the map data held by something OTHER than the vehicle — i.e. is there a map
   service of this console's own? This is the question every sentence in this file
   turns on, because it decides both what is true ("the Pi looked" versus "this
   handheld looked") and what the operator can actually DO about a gap. */
function mapDataLocal(){
  // core.js calls this state.dataBase / state.dataFrom. This function was written
  // against state.mapBase, which has never existed — two halves of one change, named
  // differently, so the test silently answered "no map service" forever and every
  // console fell through to the VEHICLE logic and raised CANNOT TELL about a Pi it was
  // not even trying to reach.
  const s = (typeof state!=='undefined' && state) ? state : null;
  if(!s) return false;
  return typeof s.dataFrom === 'string' && s.dataFrom !== 'none';
}
/* WHOSE DISK the chart data is on, and WHO gets asked for it — in words, because
   these end up inside sentences an operator reads on a towpath. Kept as two
   functions rather than one string so a row can say "this handheld holds none of
   this" and "the map service on this handheld could not be asked" without either
   sentence having to be written twice. */
function mapDataHolder(){ return mapDataLocal() ? 'this handheld' : 'the Pi'; }
function mapDataName(){
  return mapDataLocal() ? 'the map service on this handheld' : 'the Pi';
}

/* Clauses that every hazard mark must carry, kept in one place so a hazard added
   next year cannot be the one that forgets to say them. */
const CRT_NO_FLOW =
  'The Canal & River Trust data carries no flow measurement of any kind, so this mark says a '
+ 'STRUCTURE is here — it never says water is moving through it now. Read it as a place to '
+ 'EXPECT current, never as a reading of one.';
const CRT_RING =
  'The dashed ring is a fixed standoff this console draws around the mark, chosen by us; it is '
+ 'not a surveyed danger area and the real one may be larger.';
/* THE CLAUSE EVERY CHEVRON OWES, kept in one place for the same reason the two above it
   are: it is the half of the mark that says what a rotated glyph does NOT tell you.
   267 features (265 bridges, 2 locks) carry no angle at all, and a chevron pointing at a
   default would be an invented fact at every one of them — so those keep the plain
   symmetric mark, which is a cannot-tell you can see. */
const CRT_CHEVRON =
  'A mark drawn as a CHEVRON (reading as > or <) is one this console has an angle for, turned to '
+ 'that angle on screen and turning with the map. A mark drawn as the plain symmetric glyph is one '
+ 'it does NOT have an angle for, or one standing in for several features that do not share a '
+ 'single direction — in both cases the console cannot tell you which way it lies, and says so by '
+ 'not pointing rather than by pointing at a default. AND THE ANGLE IS A LINE, NOT AN ARROW: what '
+ 'is published is an orientation, so a chevron drawn at it and the same chevron turned right round '
+ 'describe the identical published fact. Which end carries the point is this console\'s drawing and '
+ 'not the Trust\'s claim. Read the chevron as the line it lies on; there is no far end and no near '
+ 'end to it.';

/* ---------------------------------------------------------------------------
   THE TABLE. One entry per layer, and the entry IS the layer: its tier, its mark,
   the sentence that explains it, and the standoff drawn around it. Adding a layer
   is adding a row here; there is no second place to remember.

     id         the console's name for it, and the key its toggle persists under
     aliases    what the same layer may be called on the wire (CRT rename their
                exports; a synonym must not read as a missing layer)
     tier       1 hazard / 2 operations / 3 extras
     mark       the letters inside the glyph, on the map and in the key
     standoffM  radius of the keep-away ring, metres — tier 1 only
     flowProxy  this row's sentence says something about WATER MOVING. Set it and the
                no-flow clause is appended automatically, the same way every tier-1
                row gets it — see crtWhat. Any row that talks about current and is
                not a hazard needs this, or it is the one claim on the console that
                reads as a measurement.
     what       what this means FOR THIS VEHICLE. Not what it is called.
   --------------------------------------------------------------------------- */
const CRT_LAYERS = [
  /* ---- TIER 1 — KEEP AWAY. Always drawn. ---- */
  { id:'locks', tier:1, mark:'L', standoffM:30, name:'LOCKS',
    aliases:['lock','locks','lock_gates','lock_chambers'],
    /* THE ONE FIELD IN THE WHOLE NETWORK THAT CARRIES AN ANGLE — an AXIS, not a
       direction, which took measuring to find out. Every field name across all 27
       national layers was checked and `angle` is the only one anywhere: 1,722 locks
       with 1,720 angles, 6,916 bridges with 6,651. No weir, sluice, culvert, outfall
       or tunnel portal has one, which is why only these two rows carry this.

       THIS ROW USED TO CALL THE FALL-DIRECTION READING "PLAUSIBLE" and leave it there.
       It is not plausible, it is wrong, and this console held the data to say so: the
       angle is the gate line, square across the chamber. Both measurements are in the
       sentence below because a refuted guess is worth more to the next reader than a
       bare assertion — the numbers are what stop it being re-guessed. Note also that
       the two layers do NOT agree with each other: a lock's angle is perpendicular to
       the cut and a bridge's runs along it, so the two rows say different things on
       purpose. See CRT_CHEVRON and _crtAngleOf. */
    angleWhat:
      'THE CHEVRON IS THE LOCK\'S PUBLISHED ANGLE, AND THAT ANGLE IS THE GATE LINE — the chamber '
    + 'squared across the cut, not the way water falls through it. This console measured that rather '
    + 'than guessing at it. Against the Trust\'s own centreline, 1,705 locks sit a median 88.9° off '
    + 'the line of the canal, 98.9% of them between 75° and 90°, and exactly one in 1,705 within 15° '
    + '— a cross-axis everywhere, not a direction of travel. Measured a second way, against this '
    + 'handheld\'s own LIDAR down the Camden flight, where the ground falls 29.01 to 27.64 to 25.10 '
    + 'to 22.61 metres above Ordnance Datum: every published angle there lands within 6.5° of exactly '
    + 'perpendicular to the fall. So a chevron on a lock points ACROSS the channel, at the bank. Read '
    + 'it as the line the gates stand on — how square the chamber is to the water, which is worth '
    + 'knowing before you arrive. Never read it as which way the water goes. On that question this '
    + 'layer is silent, and the two measurements above are why.',
    what:'LOCK — a chamber with gates and paddles at each end. Working one moves tonnes of water '
       + 'through it in a couple of minutes, and the pull at an open paddle is far beyond anything '
       + 'this sub can swim against: it ends up in the chamber, under a gate, or inside the side '
       + 'culverts that feed it, and nothing is ever recovered from those. Keep the sub AND the '
       + 'slack of the tether outside the ring.' },
  /* ONE WEIR ROW, BECAUSE THE TRUST PUBLISHES ONE WEIR SERVICE.
     This was two rows — `weirs` and an `overflow_weirs` beside it — and the second
     was backed by nothing at all: the org carries Canal_And_River_Trust_Weirs_View
     and no relief-weir service of any name (api/nav/crt.py's _EXPECTED_FEATURES is
     the measured list, and data/crt/gas-street/ is a real fetched card holding
     weirs-0.geojson and nothing else weir-shaped). A tier-1 row with no file behind
     it can only ever report ABSENT, so a COMPLETE, correctly-downloaded card lit
     HAZARD LAYERS ABSENT (1) — the loudest alarm on this map — on every single dive.
     An alarm that fires on a healthy vehicle is an alarm that gets ignored, and the
     one it teaches you to ignore is the one that means you are missing hazard data
     for this water. So: one weir layer, and the relief-weir wording folded in here.
     The names a relief weir could arrive under stay as ALIASES — if the Trust ever
     does publish one it binds to this row, and if it turns up as a SECOND file
     crtBind gives it its own row rather than letting either overwrite the other. */
  { id:'weirs', tier:1, mark:'W', standoffM:35, name:'WEIRS',
    aliases:['weir','weirs','weir_points','weir_structures','overflow_weir','overflow_weirs',
             'weirs_overflow','byweir','byweirs','by_weirs','spillway','spillways'],
    what:'WEIR — the channel spills over a fixed sill here and drops. Anything that gets over the '
       + 'sill goes with the water and cannot swim back up, and a tether that goes over it is being '
       + 'pulled by the whole overspill rather than by the sub. One layer covers every kind the '
       + 'Trust publishes, including the relief structures that dump surplus water out of the '
       + 'pound: dry and unremarkable most of the year, and running hard exactly after the rain '
       + 'that washed in the rubbish you came to lift.' },
  { id:'sluices', tier:1, mark:'S', standoffM:30, name:'SLUICES',
    aliases:['sluice','sluices','sluice_gates','penstocks'],
    what:'SLUICE — a gate that lets water out of the channel on demand. Open, it is a drain with a '
       + 'suction field in front of it, and there is no grille anywhere sized to keep a 30 cm '
       + 'vehicle out. Whatever goes through it is on the far side of a structure you cannot reach.' },
  { id:'culverts', tier:1, mark:'C', standoffM:25, name:'CULVERTS',
    aliases:['culvert','culverts','culverted','siphon','siphons'],
    what:'CULVERT — the water goes into a buried pipe here. A sub that follows the flow in cannot '
       + 'turn round, cannot be seen and cannot be swum out — and the TETHER must never follow it, '
       + 'because that cable is the only thing you could ever recover the vehicle with, and a cable '
       + 'jammed inside a pipe you cannot reach is how the sub is lost for good.' },
  { id:'tunnel_portals', tier:1, mark:'T', standoffM:25, name:'TUNNEL PORTALS',
    aliases:['tunnel_portal','tunnel_portals','portals','portal'],
    what:'TUNNEL PORTAL — the mouth of a tunnel: no daylight, no line of sight to the sub, and a '
       + 'rough wall each side for the tether to catch on. Whatever the camera shows in there, the '
       + 'recovery plan is still a cable whose far end you cannot see.' },
  /* The Trust publishes the portals and the tunnel itself as two separate services,
     so they are two rows. This one is a hazard for the same reason the portal is —
     it IS the same tunnel — and splitting them across tiers would have drawn a
     keep-away mark at the mouth and an optional dotted line through the thing the
     mark is warning about. */
  { id:'tunnels', tier:1, mark:'TN', standoffM:0, name:'TUNNELS',
    aliases:['tunnel','tunnels','tunnel_lines'],
    what:'TUNNEL — the length of the tunnel itself, not just its mouth. Inside it there is no '
       + 'daylight, no line of sight and no bank: the sub is somewhere along a line you can only '
       + 'reach from one of two ends, on a cable that is rubbing on brickwork the whole way. No '
       + 'standoff ring is drawn on this one because the hazard is not a point to stay away from, '
       + 'it is the entire line.' },
  { id:'outfalls', tier:1, mark:'OF', standoffM:20, name:'OUTFALLS',
    aliases:['outfall','outfalls','discharge','discharges','inlets',
             'outfall_discharge_points','outfall_discharge_point'],
    what:'OUTFALL — water enters the canal here from a drain, a stream or a spillway. The inflow '
       + 'pushes the sub off course with no throttle applied at all, and it is strongest after heavy '
       + 'rain, which is also when a canal is most worth cleaning.' },

  /* ---- TIER 2 — OPERATIONS. On by default, toggleable. ---- */
  { id:'access_points', tier:2, mark:'A', name:'TOWPATH ACCESS',
    aliases:['access','access_point','access_points','towpath_access','entrances',
             'towpath_access_points','towpath_access_points_2022'],
    what:'TOWPATH ACCESS POINT — a way down to the water from the path. These are the places you '
       + 'can actually put the sub in, and the places you could walk to and reach in from if it had '
       + 'to be recovered by hand rather than driven home.' },
  { id:'slipways', tier:2, mark:'SL', name:'SLIPWAYS',
    aliases:['slipway','slipways','launch','launches'],
    what:'SLIPWAY — a ramp into the water: the one kind of edge where a heavy sub can be walked in '
       + 'and out without being lifted over a coping stone by someone kneeling on wet stone.' },
  { id:'wharves', tier:2, mark:'WH', name:'WHARVES',
    aliases:['wharf','wharves','quay','quays','loading'],
    what:'WHARF — a built vertical edge where boats load. Deep water hard against a wall, mooring '
       + 'ironwork and chains under the surface, and no shelving bank anywhere to land the sub on.' },
  { id:'winding_holes', tier:2, mark:'WD', name:'WINDING HOLES',
    aliases:['winding','winding_hole','winding_holes','turning_points'],
    what:'WINDING HOLE — a bay cut into the bank so a full-length boat can turn round. It is the '
       + 'widest open water on most stretches, which means room to manoeuvre the sub and also the '
       + 'one place a boat will be swinging its propeller across the whole channel.' },
  { id:'bridges', tier:2, mark:'B', name:'BRIDGES',
    aliases:['bridge','bridges','bridge_points'],
    /* THE SAME FIELD, AND EMPHATICALLY NOT THE SAME MEANING — but not the meaning this
       row first guessed at either. It said the angle was "almost certainly how the deck
       lies across the cut", which sounded careful and was measurably false: 6,126
       bridges sit a median 3.3° off the line of the canal, so the angle runs ALONG the
       water and not across it. The deck reading had it at ninety degrees to the truth.
       What survives untouched is the reason this row exists — a bridge has no direction
       of travel and nothing flows through it, so flattening locks and bridges into one
       sentence would hand an operator a current at 6,651 places that never had one
       measured. */
    angleWhat:
      'THE CHEVRON RUNS ALONG THE CUT, not across it. The Trust publish an angle for most bridges, '
    + 'and measured against their own centreline 6,126 of them sit a median 3.3° off the line of the '
    + 'canal, 84.6% within 15° of it and only 7.5% anywhere near square to it — so this is the line '
    + 'of the WATER through the arch, not the line of the deck over it. (This row used to say the '
    + 'deck, which was wrong by ninety degrees; a lock\'s angle is the one that sits square, and the '
    + 'two layers genuinely differ.) It is still an orientation and nothing more: not a direction of '
    + 'travel, not a current, and nothing moves through a bridge, so a chevron here never means water '
    + 'is going that way. What it is good for is picturing how the channel runs under the arch before '
    + 'you drive a tethered sub into it.',
    what:'BRIDGE — the channel narrows and darkens underneath, and the bed collects whatever has '
       + 'been dropped off the parapet, which is very often exactly what you came to lift. Narrow '
       + 'also means the tether has two walls to find instead of none.' },
  { id:'moorings', tier:2, mark:'M', name:'MOORINGS',
    aliases:['mooring','moorings','visitor_moorings','long_term_moorings','moorings_all'],
    what:'MOORING — boats are tied up along here. Under the surface that means chains, ropes, pins '
       + 'and propellers, and a moored boat is one that can start its engine without warning while '
       + 'the sub is beside it.' },
  { id:'safety_gates', tier:2, mark:'SG', name:'SAFETY GATES',
    aliases:['safety_gate','safety_gates','stop_gate','stop_gates','flood_gates'],
    what:'SAFETY GATE — a gate held open that can be dropped to seal the channel if a bank fails. '
       + 'It is a slot in the channel wall with heavy ironwork sitting in it, and it closes when the '
       + 'water needs it to rather than when you are ready.' },
  { id:'stop_plank_grooves', tier:2, mark:'SP', name:'STOP PLANK GROOVES',
    aliases:['stop_plank','stop_planks','stop_plank_groove','stop_plank_grooves','plank_grooves'],
    what:'STOP PLANK GROOVE — slots cut in each bank where planks are dropped in to dam the pound '
       + 'for repairs. Narrow, sharp-edged and exactly the size to trap a tether — and a stretch '
       + 'that has been planked off can be drained with no notice reaching you at all.' },
  /* flowProxy, and it is the whole reason that flag exists. This row used to state a
     water current as a FACT — "It is a current entering the cut" — which made it the
     only water-movement claim on the console not marked as an inference, on a
     dataset that publishes no flow measurement of any kind. A feeder can be shut,
     and this mark is a position out of a structure file either way. */
  { id:'feeders', tier:2, mark:'F', name:'FEEDERS', flowProxy:true,
    aliases:['feeder','feeders','feeder_channel','supply'],
    what:'FEEDER — the channel that brings water into the canal from a reservoir or a river. It is '
       + 'the structural reason to EXPECT water pushing in sideways here, and the sub is the '
       + 'lightest thing in it. Whether any is arriving today is not on this map: a feeder can be '
       + 'shut, and this is a place, not a reading.' },

  /* ---- TIER 3 — EXTRAS. Drawn, quietly, under everything above. They used to be off
         by default and the console had not even asked for them, which meant a handheld
         could carry the lot to the water and show none of it. ---- */
  { id:'aqueducts', tier:3, mark:'AQ', name:'AQUEDUCTS',
    aliases:['aqueduct','aqueducts'],
    what:'AQUEDUCT — the canal crosses a road, a river or a valley in a trough. There is no bank to '
       + 'stand on for its whole length, so a sub that stops in the middle of one is recovered from '
       + 'whichever end you can walk to, on the tether alone.' },
  { id:'water_points', tier:3, mark:'WP', name:'WATER POINTS',
    aliases:['water_point','water_points','taps'],
    what:'WATER POINT — a tap for boats. No hazard to the sub; kept because it is a landmark with '
       + 'hard standing beside the water, which is where you end up rinsing a canal off the hull.' },
  { id:'facilities', tier:3, mark:'FA', name:'BOATER FACILITIES',
    aliases:['facility','facilities','services','service_points','elsan','sanitary'],
    what:'BOATER FACILITIES — rubbish, Elsan, showers. Landmarks and parking rather than hazards, '
       + 'kept because knowing where a car can actually get to matters when you are carrying a sub '
       + 'and a drum of cable.' },
  { id:'pumping_stations', tier:3, mark:'PS', name:'PUMPING STATIONS', hazardish:true,
    aliases:['pumping_station','pumping_stations','pump','pumps','pump_house',
             'pumping_station_points'],
    what:'PUMPING STATION — water is lifted between pounds here. The part that matters to a small '
       + 'sub is the INTAKE, which is an entrainment hazard of the same family as a sluice and is '
       + 'not always published as a feature of its own. It sits in EXTRAS because the hazard tier is '
       + 'a fixed list and this is not on it — so it is drawn in the hazard colour, at hazard weight, '
       + 'and treating a pumping station as a place an intake may be is the safe reading.' },
  { id:'boatyards', tier:3, mark:'BY', name:'BOATYARDS',
    aliases:['boatyard','boatyards','marina','marinas','dry_docks','dry_dock'],
    what:'BOATYARD — boats are craned, moved and worked on here, and the bed underneath collects '
       + 'what falls off them. Expect chains, moorings and propellers turning without warning.' },
  { id:'mileposts', tier:3, mark:'MP', name:'MILEPOSTS',
    aliases:['milepost','mileposts','distance_markers','mile_markers'],
    what:'MILEPOST — a fixed, named point on the bank. Nothing to do with the water: it is how you '
       + 'tell somebody on the telephone exactly where you and the sub are.' },
  { id:'notices', tier:3, mark:'N', name:'NOTICES & STOPPAGES',
    aliases:['notice','notices','stoppage','stoppages','works'],
    what:'NOTICE / STOPPAGE — where the Trust has posted something about this stretch, including '
       + 'planned draining. Worth reading before a dive rather than after one; not a thing in the '
       + 'water.' },
  { id:'towpath', tier:3, mark:'TP', name:'TOWPATH',
    aliases:['towpath','towpaths','path','paths'],
    what:'TOWPATH — the path itself, drawn as a line. It runs the length of everything, so it is '
       + 'drawn as thin, dim line work in the bottom band where it cannot bury the marks that '
       + 'matter: it shows which bank you can walk while the sub works the other one, without '
       + 'becoming the loudest thing on the map by sheer quantity.' },
  { id:'docks', tier:3, mark:'DK', name:'DOCKS',
    aliases:['dock','docks'],
    what:'DOCK — an enclosed basin off the main line. Sheltered water, hard vertical edges, and '
       + 'usually the deepest thing on the stretch; also where boats are moved about under power.' },
  { id:'boat_lifts', tier:3, mark:'BL', name:'BOAT LIFTS', hazardish:true,
    aliases:['boat_lift','boat_lifts','lift'],
    what:'BOAT LIFT — a machine that raises whole boats between levels, with caissons and gates '
       + 'that move a great deal of water when they operate. It sits in EXTRAS because the hazard '
       + 'tier is a fixed list and this is not on it, so it is drawn in the hazard colour and at '
       + 'hazard weight: treat it as a lock that is several times the size.' },
  { id:'embankments', tier:3, mark:'EM', name:'EMBANKMENTS',
    aliases:['embankment','embankments'],
    what:'EMBANKMENT — the canal is carried above the surrounding ground here. It matters to the '
       + 'operator rather than the sub: there is a long drop on one or both sides and often no way '
       + 'down to the water for the length of it.' },
  { id:'reservoirs', tier:3, mark:'RS', name:'RESERVOIRS',
    aliases:['reservoir','reservoirs'],
    what:'RESERVOIR — the water supply that feeds the canal. Open water with its own draw-off '
       + 'works, and not canal at all: if you are diving one, the rules about intakes and '
       + 'permission are different from the towpath.' },
  { id:'canals', tier:3, mark:'CN', name:'CANAL LINES',
    aliases:['canal','canals','canals_by_navigation','canals_by_km_length','navigations'],
    what:'CANAL LINE — the Trust publishes its own centreline for the navigation, by name and by '
       + 'distance. This console already draws a centreline of its own from the downloaded area, so '
       + 'this one is drawn as dim line work in the bottom band rather than as a second bright '
       + 'thread down the same cut: where the two disagree you can see it, which is a real question '
       + 'when snapping is moving the sub onto one of them.' },
  { id:'planning_buffer', tier:3, mark:'PB', name:'PLANNING BUFFER',
    aliases:['planning_buffer','planning_buffer_polygon'],
    what:'PLANNING BUFFER — the consultation zone the Trust draws around its water for planning '
       + 'purposes. Nothing to do with the sub; kept because it is published and because it is the '
       + 'polygon people mistake for a navigation boundary.' },
  { id:'angling', tier:3, mark:'AN', name:'ANGLING',
    aliases:['angling','fishing','pegs','fisheries','lakes_ponds_fisheries'],
    what:'ANGLING — fishing rights and pegs. Lines and weights in the water, and people who will '
       + 'not thank you for driving a submarine through their swim.' },

  /* ---- DEPTH. Same table, because it needs the same three states: a depth layer
         that is not on the Pi has to say ABSENT too, and "no depth here" must never
         be drawn as "shallow" or as nothing at all. ---- */
  { id:'depth_nominal', tier:2, kind:'depth', nominal:true, mark:'DN', name:'DEPTH — NOMINAL',
    path:'/api/areas/{area}/depth/nominal',
    aliases:['depth','depth_nominal','nominal_depth','design_depth'],
    what:'NOMINAL DEPTH — the published design depth of the channel: what it is SUPPOSED to be. '
       + 'Drawn washed out and hatched as a band down the whole length of every waterway section '
       + 'that carries a figure, on purpose, so it can never be mistaken for a measurement. Read '
       + 'the band the way it is drawn: its LENGTH is the section\'s own published geometry and the '
       + 'figure applies along all of it, but its WIDTH is a drawing convention of this console\'s, '
       + 'chosen so the claim is visible over the water it is about — nobody publishes how wide the '
       + 'cut is, and the shallow edges are not in this or any other number here. A canal silts up, '
       + 'and the gap between the nominal figure and the water '
       + 'actually under the sub is the entire reason this layer is drawn as a claim rather than a '
       + 'reading. It uses the same twelve depth colours as the dive track and the ballast, so the '
       + 'colour means the same thing everywhere on this console — only the texture says how much '
       + 'the number behind it is worth.' },
  { id:'depth_surveyed', tier:2, kind:'depth', nominal:false, mark:'DS', name:'DEPTH — SURVEYED',
    path:'/api/areas/{area}/depth/surveyed',
    aliases:['surveyed','depth_surveyed','surveyed_depth','soundings'],
    what:'SURVEYED DEPTH — cells this sub has actually been through, drawn SOLID and outlined '
       + 'because they were measured rather than published. Read the claim precisely: it is the '
       + 'deepest the vehicle got in that cell without grounding, so it is a FLOOR UNDER the water '
       + 'depth and not the depth of the bed — there may well be more water below it, and there is '
       + 'certainly no less. The map starts nominal everywhere and turns solid where the sub has '
       + 'been. Cells measured during THIS session are included the moment they are driven, before '
       + 'any dive log has been saved, and the row says how many of each you are looking at — but '
       + 'ONLY while a real hull is on the link reporting its own sensors. A simulated dive '
       + 'measures nothing and paints no cells at all: this treatment means the sub touched that '
       + 'water, so there is no version of it that a simulator is entitled to draw.' },
];

/* Words that make an unknown layer a hazard until somebody says otherwise.

   The server can publish a layer this table has never heard of, and the safe
   default for an unknown NAME is not "extras, off": if it is called something
   with "weir" in it, it is a weir. Anything caught this way is drawn as a hazard
   AND says in its own tooltip that it was classified by its name rather than by a
   rule anybody wrote — an inference marked as an inference, which is the same
   thing the flow clause does one paragraph up. */
const CRT_HAZARD_WORDS = /(weir|lock|sluice|culvert|tunnel|outfall|siphon|syphon|intake|penstock|spillway|paddle)/i;

const CRT = {
  ready:false,
  area:null,                 // the active area name, or null
  open:false,                // is the layers panel showing
  prefs:null,                // id -> bool, persisted
  state:{},                  // id -> { status, n, drawn, why, data, at }
  extra:[],                  // layers the server published that the table has never heard of
  bind:{},                   // wire id -> the console id it was bound to
  claimed:{},                // console id -> the wire id holding it (one file, one row)
  credits:[],                // extra attribution lines carried by the layers themselves
  indexRaw:null,             // the index's own status/why/remedy for the AREA (see crtFetchIndex)
  seen:{},                   // area name -> when its chart index last ANSWERED (persisted)
  answeredAt:0,              // when the MAP BACKEND last answered anything readable, this session.
                             // Session-scoped on purpose: it is the evidence that this
                             // console's map service is alive NOW, which is what turns a
                             // later silence from it into a fault worth alarming about.
                             // The persisted `seen` record above answers the other, longer
                             // question — "has this cut's index ever come back here".
  live:{cells:null, at:0},   // this session's own soundings, binned
  /* THE TWO STORES, KEPT APART ON PURPOSE. Each holds that index's own answer —
     ok / nothing / why / layers — because "the national store has no locks" and "this
     area's card has no locks" are different facts and the row that reports one of them
     has to be able to say which. */
  net:  {ok:false, nothing:false, why:'', layers:null, raw:null, at:0},
  card: {ok:false, nothing:false, why:'', layers:null, raw:null, at:0},
  /* THE ONCE-ONLY NATIONAL DOWNLOAD, as the backend reports it. `running` with a
     done/total is what makes DOWNLOADING, N OF 27 an ordinary state rather than an
     absence: see crtDownloading. `asked` records that this console has looked at all,
     so a backend that has never answered about it is not read as "not running". */
  dl:   {running:false, done:0, total:0, layer:'', why:'', state:'', at:0, asked:false, ok:false},
  _busy:false, _badge:'', _rowsBuilt:false, _building:false,
  _fetchBuilt:false, _fetchBadge:'',        // the download block above the layer list
  _win:null,                                // the bbox the held bodies were read for
  _winBusy:false, _winAt:0,                 // one window re-read at a time, and not on every frame
};

/* THE SENTENCE, ASSEMBLED IN ONE PLACE.

   The two hazard clauses are appended here rather than typed into each of the seven
   tier-1 rows, because a clause that has to be remembered seven times is a clause
   that will be missing from the eighth. Anything drawn as a hazard — including a
   layer this console adopted off its name alone — therefore says what the ring is
   and says that no flow was ever measured, whoever wrote the row.

   flowProxy is here for the row that is NOT a hazard and still talks about water
   moving. FEEDERS was exactly that and shipped stating a current as fact, which is
   the one thing this data cannot support: an inference must never dress as a
   measurement, and the tier a layer sits in is no reason for it to be allowed to. */
/* THE ORDER HERE IS LOad-BEARING and it is why this is not three lines. The two hazard
   clauses are appended by TESTING THE STRING FOR THEMSELVES, so anything appended before
   them can suppress them: an angle clause that happened to contain the words "no flow
   measurement" would silently delete the no-flow clause from the LOCKS row, which is the
   one row on this console where it matters most. So the hazard clauses are assembled
   first, off e.what alone, and the directional clause goes on the end afterwards. */
function crtWhat(e){
  let s = e.what;
  if(e.tier===1 || e.hazardish || e.flowProxy){
    if(s.indexOf('standoff this console draws') < 0 && e.standoffM) s += ' ' + CRT_RING;
    if(s.indexOf('no flow measurement') < 0) s += ' ' + CRT_NO_FLOW;
  }
  if(e.angleWhat) s += ' ' + e.angleWhat + ' ' + CRT_CHEVRON;
  return s;
}

/* ---- the table, plus whatever the server invented, in draw order ---- */
function crtAll(){ return CRT_LAYERS.concat(CRT.extra); }
function crtEntry(id){ return crtAll().find(e=>e.id===id) || null; }
function crtTierList(t){ return crtAll().filter(e=>e.tier===t); }

/* WIRE NAME -> SOMETHING THIS TABLE CAN MATCH.

   The Pi names a layer after the ArcGIS SERVICE it came from plus that service's
   internal layer number — `locks-0`, `tunnel-portals-0`, `pumping-station-points-3`
   (api/nav/crt.py `_layer_key`). The trailing number is an ArcGIS implementation
   detail: the same layer comes back as `-1` the day the Trust republishes it inside
   a different FeatureServer, and a console that matched on the whole string would
   quietly stop recognising locks and start drawing them as an unknown extra. So the
   number is dropped for MATCHING only — the full wire id is kept on the entry and is
   what the fetch actually asks for. */
function crtNorm(s){
  return String(s||'').trim().toLowerCase()
         .replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'')
         .replace(/_\d+$/,'');
}
/* wire name -> a table entry, via id or any alias */
function crtResolve(wireId){
  const k = crtNorm(wireId);
  return crtAll().find(e => e.id===k || (e.aliases||[]).indexOf(k)>=0) || null;
}
/* ONE FILE, ONE ROW.

   Binding is not just a lookup: if two of the Pi's layers both match one entry —
   and the Trust does publish near-duplicates, five separate Sluices services among
   them — then whichever arrived second would overwrite the first's data and the
   first would vanish from a console that had already told the operator it was
   SHOWN. A row that is already spoken for therefore sends the newcomer to its own
   adopted row instead, where it is visible and says what it is. */
function crtBind(wireId){
  const k = crtNorm(wireId);
  if(CRT.bind[wireId]) return crtEntry(CRT.bind[wireId]);
  const e = crtResolve(k);
  if(e && (!CRT.claimed[e.id] || CRT.claimed[e.id]===wireId)){
    CRT.claimed[e.id] = wireId; CRT.bind[wireId] = e.id; e.wire = wireId;
    return e;
  }
  const a = crtAdopt(wireId, !!e);
  CRT.claimed[a.id] = wireId; CRT.bind[wireId] = a.id; a.wire = wireId;
  return a;
}
/* A layer the table has never heard of. Named by the server, tiered by this
   console, and honest in its own words about which of those two happened. */
function crtAdopt(wireId, duplicate){
  const k = crtNorm(wireId);
  const haz = CRT_HAZARD_WORDS.test(k);
  const id = crtEntry(k) ? crtNorm(wireId)+'_'+(CRT.extra.length+1) : k;
  const e = {
    id:id, wire:wireId, tier: haz?1:3, mark:'?', standoffM: haz?25:0, adopted:true,
    name:k.replace(/_/g,' ').toUpperCase() + (duplicate ? ' (2ND FILE)' : ''),
    aliases:[],
    what:(haz
      ? 'THIS CONSOLE HAS NO ENTRY FOR THIS LAYER, and it is drawn as a hazard anyway because its '
      + 'NAME contains a word that always means one. That is a guess made from a string, not a rule '
      + 'anybody wrote for this layer, so treat the mark as "there is something here worth keeping '
      + 'the sub and the tether away from" and go and read what the layer actually is. ' + CRT_NO_FLOW
      : 'THIS CONSOLE HAS NO ENTRY FOR THIS LAYER. The backend holds it, so it is drawn as a plain '
      + 'mark in the quietest band, but nothing on this handheld knows what it means for the '
      + 'vehicle — which is why it is drawn quietly and why this sentence does not pretend '
      + 'otherwise. It is emphatically not a claim that the layer is unimportant, and it is not a '
      + 'reason to hide it: a mark you can see and go and read about is worth more than one this '
      + 'console decided on your behalf that you did not need.')
      + (duplicate
         ? ' It also arrived SECOND for a row that was already taken: the Pi is carrying more than '
         + 'one file for this kind of feature (the Trust publishes near-duplicate services), and '
         + 'this is the other one, given its own row so that neither can quietly overwrite the other.'
         : ''),
  };
  CRT.extra.push(e);
  return e;
}

/* ---- preferences (kv, like the origin) ----------------------------------

   EVERY LAYER THIS HANDHELD HOLDS IS DRAWN ON A FRESH CONSOLE. There is no
   off-by-default any more, for any tier, including the layers this console adopted
   off their names alone.

   THE DEFECT THIS CLOSES. `e.tier===2` meant tier 3 shipped switched off, so a
   handheld carrying the aqueducts, the boat lifts, the pumping stations — an
   entrainment hazard by any reading — and the Trust's own centreline showed none of
   them until somebody opened a panel they had no reason to open. A layer that is
   present and invisible is a layer nobody knows they have, and "off by default" is
   this console deciding on the operator's behalf, in advance, at a desk, what they
   will not need at the water.

   THE TOGGLE STAYS AND IS STILL REMEMBERED, and that is the other half of the same
   decision: pruning is the operator's to do, with the thing in front of them, after
   driving with it. crtIsOn reads the stored preference first and only falls back to
   this — so switching a layer off is still a choice this handheld keeps. */
function crtDefaultOn(e){ return true; }
function crtIsOn(id){
  const e = crtEntry(id); if(!e) return false;
  if(e.tier===1) return true;                                   // not a preference. A tier-1 mark is not hideable.
  const p = CRT.prefs || {};
  return (typeof p[id]==='boolean') ? p[id] : crtDefaultOn(e);
}
async function crtSavePrefs(){
  try{ if(typeof STORE!=='undefined' && STORE.set) await STORE.set('crt.layers', CRT.prefs||{}); }catch(e){}
}
async function crtLoadPrefs(){
  CRT.prefs = {};
  try{ if(typeof STORE!=='undefined' && STORE.get){ const p = await STORE.get('crt.layers', null); if(p && typeof p==='object') CRT.prefs = p; } }catch(e){}
}
function crtSetOn(id, on){
  const e = crtEntry(id); if(!e) return;
  if(e.tier===1){
    // Deliberately not possible. Logged rather than silently ignored, because a
    // control that does nothing and says nothing is how somebody concludes the
    // hazard layer was switched off when it never was.
    LOG.map('CRT: '+e.name+' is a hazard layer and is always drawn — it cannot be switched off');
    return;
  }
  CRT.prefs = CRT.prefs || {};
  CRT.prefs[id] = !!on;
  crtSavePrefs();
  LOG.map('CRT layer '+e.id+' '+(on?'shown':'hidden'));
  const st = CRT.state[id];
  // Switching a layer on is the first time the console has ever asked for it, so
  // "off" cannot be allowed to look like "absent" — fetch, then let the row say
  // which of the three answers came back. The re-render on completion is the whole
  // point: without it the row keeps saying ASKING… forever, which is a fourth state
  // nobody chose and the one that means "this console has stopped telling you".
  if(on && (!st || st.status==='off' || st.status==='unavailable'))
    crtFetchLayer(e, crtPlanFor(e) || {source:'area', meta:null})
      .then(()=>{ crtRenderRows(); crtRenderBadge(); });
  crtRenderRows(); crtRenderBadge();
}

/* ---- status vocabulary --------------------------------------------------
   off             the layer is switched off BY THE OPERATOR, so nothing has been
                   ASKED. Not a claim about the water. No longer a default state.
   loading         asked, waiting
   downloading     the once-only national fetch is running and has not reached this
                   layer yet. The ordinary state of a new handheld, once, and NOT a
                   fault — see crtDownloading.
   present         the store has the file and it has features in the window
   held            the store has the file and this console has not read it into memory
                   yet, because the map has no position to read a window around. A
                   fact about this console's memory, never about the water.
   empty           the store has the file and there is nothing here — "none mapped"
   absent          the store looked and the file is not on the disk
   unavailable     asked, no answer, and there WAS reason to expect one — CANNOT TELL,
                   and it is an alarm
   not-downloaded  no chart data is held anywhere this console can reach — NOT
                   DOWNLOADED, not a fault, and after this round it is the LAST
                   RESORT rather than what a healthy console shows on launch
   no-area         a per-area layer (the depth pair) with no active area to ask about
   -------------------------------------------------------------------------- */
const CRT_STATE_WORDS = {
  off:              'NOT ASKED',
  loading:          'ASKING…',
  downloading:      'DOWNLOADING',
  present:          'HERE',
  held:             'HELD',
  empty:            'NONE MAPPED',
  absent:           'ABSENT',
  unavailable:      'CANNOT TELL',
  'not-downloaded': 'NOT DOWNLOADED',
  'no-area':        'NO AREA',
};
/* This session's own soundings are counted SEPARATELY from the Pi's saved survey,
   wherever the surveyed row is described. They are drawn identically because they
   are the same measurement by the same sensor, but they have not been through a
   dive log yet, and a row that added them into one number would be claiming the Pi
   holds a survey it has never been sent. It also covers the case that would
   otherwise contradict itself on screen: a saved survey reported ABSENT while
   measured cells are visibly painted on the map. */
function crtLiveClause(e){
  if(e.id!=='depth_surveyed') return '';
  /* REPLAY IS ITS OWN REASON, AND IT USED TO BE GIVEN SOMEBODY ELSE'S. During replay
     this row said the depth "is coming from the simulator rather than from a sensor in
     the water", which is simply not what happened: MAP.track is a saved dive log, and
     the depths in it were whatever the console was showing at the time. The reason no
     cells are painted is narrower and quite different — navui.js saveCurrentDive writes
     each point as {x, y, depth} and nothing else, so the per-point `measured` stamp that
     crtLiveCells gates on (map.js pushTrack) is not in the file. A log carries no record
     of where its depths came from, so this console cannot show any of them as measured,
     and it will not guess: that is the same rule as every absent layer on this panel.
     Tested BEFORE the count, not after, because a log that one day DOES carry the stamp
     must not be described as "measured by the sub during THIS session" — the sub in a
     replay is not diving, and the session that took those soundings was somebody's
     afternoon last month. */
  if(typeof MAP!=='undefined' && MAP && MAP.replay)
    return ' No cells are being added from THIS session because this is a REPLAY of a saved dive '
         + 'log, and the log records each point as a position and a depth with no stamp saying '
         + 'where that depth came from. That is not a claim that the depths in it are false — they '
         + 'are what this console was reading at the time — it is that the file cannot tell this '
         + 'console WHICH of them a sensor in the water produced, and solid outlined cells on this '
         + 'map mean MEASURED. Unstamped points are therefore drawn as the track they are and never '
         + 'as survey, and that goes for every saved dive: the log format holds position and depth '
         + 'only. EXIT REPLAY for the live map, where cells are painted as the sub sounds them.';
  const n = (typeof crtLiveCells==='function') ? crtLiveCells().length : 0;
  if(n)
    return ' Plus ' + n + ' cell' + (n===1?'':'s') + ' measured by the sub during THIS session — '
         + 'each one a HULL DEPTH: the deepest this vehicle got in that cell, which is a floor '
         + 'UNDER the water there and not the depth of the bed. Drawn the same way as the saved '
         + 'ones because they are the same measurement by the same sensor, and counted apart '
         + 'because no dive log has been written for them yet.';
  // NOTHING, AND WHY NOTHING — said in words as well as by drawing nothing. A row that
  // simply reported no cells would read as "the sub has not been anywhere yet", which
  // is a claim about the water; this is a statement about where the numbers come from.
  if(typeof crtLiveMeasured==='function' && !crtLiveMeasured())
    return ' No cells are being added from THIS session: the depth on this console is coming from '
         + 'the simulator rather than from a sensor in the water, and a simulated dive measures '
         + 'nothing, so nothing is painted for it. When they are drawn, each is a HULL DEPTH — the '
         + 'deepest this vehicle got in that cell, a floor under the water there and not the depth '
         + 'of the bed — and solid outlined cells on this map mean MEASURED, which is why there is '
         + 'no simulated version of them.';
  return '';
}
/* HOW MANY OF THE MARKS ON SCREEN ARE ACTUALLY POINTING, AND HOW MANY CANNOT.

   Counted off the last frame the layer was drawn in, so the row reports the picture the
   operator is looking at rather than a property of the file. It exists because a chevron
   layer has two silent failures and neither is visible from the map: a feature with no
   published angle keeps the plain glyph (267 of them nationally — 265 bridges, 2 locks),
   and a merged mark drops its chevron because the features under it do not share one
   direction. Both look identical on the glass to "this console decided not to point",
   and the difference is worth a sentence. */
function crtAngleClause(e){
  if(!e.angleWhat) return '';
  const st = CRT.state[e.id] || {};
  const chev = st.chevrons||0, none = st.noAngle||0, merged = st.merged||0;
  if(!st.drawn) return '';
  let s = ' ' + (chev ? (chev + ' of the ' + st.drawn + ' mark' + (st.drawn===1?'':'s') +
                         ' on screen ' + (chev===1?'is a chevron':'are chevrons') + ', turned to the '
                       + 'angle published for that feature and turning with the map.')
                      : ('None of the ' + st.drawn + ' mark' + (st.drawn===1?'':'s') +
                         ' on screen is drawn as a chevron.'));
  if(none)
    s += ' ' + none + ' feature' + (none===1?'':'s') + ' in view carr' + (none===1?'ies':'y')
       + ' no angle at all, so ' + (none===1?'it keeps':'they keep') + ' the plain symmetric mark — a '
       + 'cannot-tell you can see, rather than a chevron pointing at a default.';
  if(merged)
    s += ' ' + merged + ' mark' + (merged===1?'':'s') + ' stand' + (merged===1?'s':'')
       + ' for more than one feature at this zoom and therefore point' + (merged===1?'s':'')
       + ' nowhere: the features under it do not share a single direction. Zoom in and each gets '
       + 'its own chevron back.';
  return s;
}

/* WHICH STORE THIS ROW IS TALKING ABOUT, in words. There are two now and they are
   different disks with different contents: "the national store has no relief weirs"
   and "this area's card has none" are separate claims, and a row that said only "the
   map service" would be merging them into one sentence that is true of neither. */
function crtStoreName(st){
  if(st && st.source==='net')  return 'the national chart store on this handheld';
  if(st && st.source==='area') return 'the chart card for the area "'+(CRT.area||'')+'"';
  return mapDataName();
}
/* HOW MUCH OF THIS LAYER IS IN MEMORY, and it is never allowed to read as how much
   the store holds. A windowed body is 12 features out of 6,916 nationally, and a row
   that printed the 12 alone would describe a store that is missing nearly all of it.
   The clause exists because the honest number is BOTH of them, with the reason. */
function crtHoldingClause(st){
  if(!st) return '';
  if(st.source!=='net') return '';
  if(st.whole || !st.win)
    return (typeof st.national==='number' && st.national)
      ? (' That is the whole national layer — all ' + st.national.toLocaleString() + ' of them are on '
         + 'this handheld and in memory.')
      : ' That is the whole national layer, on this handheld.';
  return ' The store holds the whole national layer'
       + ((typeof st.national==='number' && st.national)
          ? (' — ' + st.national.toLocaleString() + ' features') : '')
       + '; this console reads the part around where the map is looking, with about '
       + Math.round(CRT_API.windowM/1000) + ' km of slack on every side, and reads more as you pan. '
       + 'Nothing is being withheld: what is outside that window is outside the screen as well.';
}
function crtStateSentence(e){
  const st = CRT.state[e.id] || {status:'off'};
  const live = crtLiveClause(e);
  switch(st.status){
    // OFF SCREEN AND DROPPED ARE DIFFERENT THINGS, and this row used to say the
    // second when it meant the first: "of which only the first N are drawn" fired on
    // any layer whose features were merely outside the current view, which is nearly
    // all of them at any useful zoom. That sentence describes data this console
    // decided not to draw, and on a hazard layer it reads as a warning. Only the
    // per-frame cap is truncation, and it says so in those words; everything else is
    // the map being smaller than the area, which is not a claim about the file. The
    // clause is only reached when the layer is actually being drawn — st.drawn is
    // last frame's count, and on a layer switched off it is stale by definition.
    case 'present': {
      const on = crtIsOn(e.id);
      let s = (st.n===1 ? '1 feature' : st.n.toLocaleString()+' features') + ' loaded from '
            + crtStoreName(st);
      s += crtHoldingClause(st);
      if(!on) s += ' HELD, BUT SWITCHED OFF on this handheld — the data is here and you have chosen '
                 + 'not to draw it. Nothing is being hidden from you; you hid it, and this row is how '
                 + 'you find it again.';
      else if(st.capped)
        s += ' This frame stopped after placing ' + st.drawn + ' marks — the console\'s own per-frame '
           + 'limit, not the end of the file. The rest are loaded and held; zoom in and they draw.';
      else if(st.merged)
        s += ' ' + st.drawn + ' marks are on screen for ' + (st.inview||st.drawn).toLocaleString()
           + ' features in view, because ' + st.merged + ' of those marks would have landed on top of '
           + 'each other at this zoom and are drawn as one with a count on it. Zoom in and they '
           + 'separate — nothing has been dropped.';
      else if(st.drawn && st.drawn < st.n)
        s += ' ' + st.drawn + ' of them are inside the view you are looking at. The others are '
           + 'loaded too and simply off screen — pan or zoom out and they draw.';
      if(on) s += crtAngleClause(e);
      return s + live;
    }
    // HERE, AND NOT YET READ. The store lists it and this console has not pulled the
    // file into memory, because there is nowhere to draw it — no launch point, no view
    // centre, no projection. That is a fact about this console's memory and never one
    // about the water, so it is said as one.
    case 'held':
      if(st.unwindowed)
        return 'HELD — ' + crtStoreName(st) + ' has this layer'
             + ((typeof st.n==='number' && st.n) ? (' ('+st.n.toLocaleString()+' features)') : '')
             + ' and it is NOT being drawn, for a reason that is about this console\'s memory and '
             + 'not about the water. This console asked for the part around the map, about '
             + Math.round(CRT_API.windowM/1000) + ' km each way; the store answered with the whole '
             + 'national layer, ' + Math.round((st.bytes||0)/1048576) + ' MB of it, which is over the '
             + Math.round(CRT_API.readCeilingBytes/1048576) + ' MB this console will read into one '
             + 'layer. Reading it would freeze the console for seconds while it parsed — on the one '
             + 'machine between you and a sub on a cable — so it was not read. It is ON THIS '
             + 'HANDHELD and it will draw the moment the store answers the window it was asked for. '
             + 'Nothing has been switched off and nothing is missing: an unmarked stretch still '
             + 'means NO MARK DRAWN and never "nothing there".' + live;
      return 'HELD — ' + crtStoreName(st) + ' has this layer'
           + ((typeof st.n==='number' && st.n) ? (' ('+st.n.toLocaleString()+' features)') : '')
           + ', and this console has not read it into memory yet because the map has no position to '
           + 'read it around: no launch point, and no view centre. It is read the moment this map '
           + 'knows where it is. Nothing is missing and nothing has failed.' + live;
    // DOWNLOADING. The expected state of a new handheld, once, and the reason NOT
    // DOWNLOADED is no longer what a healthy console shows on launch.
    case 'downloading': {
      const d = crtDownloadCount();
      return 'DOWNLOADING — the Canal & River Trust network is being fetched onto this handheld now, '
           + d.done + ' of ' + d.total + ' layers done'
           + (CRT.dl.layer ? (' (currently: '+CRT.dl.layer+')') : '')
           + '. This happens once, on the first launch, and this layer has not landed yet. Nothing '
           + 'has failed and nothing needs doing — but until it is here this map is not showing these '
           + 'marks, so an unmarked stretch means NOT YET rather than "nothing there".' + live;
    }
    case 'empty':
      return 'NONE MAPPED HERE — ' + crtStoreName(st) + ' HAS this layer and there is nothing of this '
           + 'kind in the water this map is looking at. That is a real answer and it is not the same '
           + 'as the layer being missing.' + crtHoldingClause(st) + live;
    case 'absent':
      return 'ABSENT — ' + crtStoreName(st) + ' looked and the file for this layer is not on the disk, '
           + 'so nothing on this map is telling you where these are. An empty map here means NO '
           + 'DATA, not NONE.'
           + (st.why ? (' It says: ' + st.why) : '') + live;
    case 'unavailable':
      return 'CANNOT TELL — ' + (st.why || (mapDataName()+' could not be asked')) + '. Nothing has '
           + 'been ruled in or out; this is the console admitting it does not know.' + live;
    // THE QUIET ABSENCE, AND IT IS STILL AN ABSENCE. It says the same thing CANNOT
    // TELL says about the map — an unmarked stretch is not a surveyed empty one — and
    // differs only in naming what actually happened: nothing this console can reach
    // holds this data, as opposed to somebody who was answering and stopped. The second
    // is a fault to chase; the first is a console that has not been given the data yet.
    // The REMEDY is split for the same reason the opening is: a handheld with its own
    // map service fixes this by downloading, a console with only a Pi behind it fixes it
    // by connecting one, and a demo with neither cannot fix it from this page at all.
    // An instruction that cannot work is another way of not being believed.
    case 'not-downloaded':
      return 'NOT DOWNLOADED — ' + crtNeverHadClause() + '; ' + crtNobodyToAskClause()
           + (st.why ? (' (' + st.why + ')') : '') + '. Nothing has been ruled in or out, so an '
           + 'unmarked stretch of this map still means NO DATA and never "nothing there" — it is '
           + 'said quietly because nothing has failed, not because it matters less. '
           + crtRemedyClause() + live;
    case 'loading':  return 'asking ' + crtStoreName(st) + ' for it now';
    // ONLY THE DEPTH PAIR CAN REACH THIS NOW, and the sentence says why rather than
    // leaving "no area" to read as a console that has lost its map. The Trust layers
    // are national and need no area at all.
    case 'no-area':
      return 'NO LAUNCH POINT YET — this layer is per-area by nature (one of the two depth layers is '
           + 'built from the dive journals for a particular cut), so there is nothing to ask about '
           + 'until an area exists. The Canal & River Trust layers above are national and do not wait '
           + 'for this.';
    default:
      // THE EXACT WORDS MATTER HERE, and they are guarded: this row has to say WHOSE
      // decision the silence is ("you switched") and has to refuse to make a claim
      // about the water ("not a claim that the layer is missing"). Nothing ships
      // switched off any more, so an off row can only ever be the operator's own doing
      // — and the one thing it must never read as is a finding.
      return 'NOT ASKED — you switched this layer off on this handheld, so the console has not '
           + 'requested it. Nothing ships switched off, so this is your choice and not a default. '
           + 'It is not a claim that the layer is missing and it says nothing whatever about the '
           + 'water: switch it back on and the row will report which of HERE, NONE MAPPED and '
           + 'ABSENT is actually true.';
  }
}

/* ============================================================================
   WHICH SILENCE IS IT? — the evidence behind CANNOT TELL versus NOT DOWNLOADED.

   The index failing is ONE event with two opposite meanings, and getting them the
   same way round as the operator's world is the whole of this block. It is decided by
   a GATE and then two pieces of evidence, none of them a guess, and the gate coming
   first is the fix described below it:

     0  IS THERE ANYBODY TO ASK AT ALL?  A BACKEND FOR THE MAP DATA has to be
        CONFIGURED before any silence from it can mean anything. That is a map service
        of this console's own where there is one; where there is not, the charts come
        off the vehicle and it is the vehicle that has to be configured — ?sim=1
        deliberately resolves no host (see core.js resolveHost) and a page opened off
        the disk with no stored host has none either. Those consoles did not fail to
        get an answer: they never addressed a question to anyone, and no evidence
        below can change that.

     1  HAS THIS BACKEND ANSWERED ANYTHING AT ALL THIS SESSION?  Stamped the moment a
        chart index comes back readable. A map service that was answering and has
        stopped is a fault on this handheld — the service died, or the launcher never
        got it up — and it is exactly as worth alarming about as a Pi that went quiet.

     2  HAS THIS AREA'S CHART INDEX EVER ANSWERED?  Recorded per area name, and
        PERSISTED on purpose. "It answered for this cut last week and does not today"
        is precisely the fault that has to stay loud, and closing the console is not a
        reason to forget it ever worked.

     3  IS THERE A PI ON THIS LINK NOW?  A control socket that has opened, a telemetry
        frame that has arrived, or a /api/healthz probe the Pi answered — any one of
        them is a vehicle that exists on this link. Evidence ONLY on a console whose
        charts genuinely come off the vehicle: with a map service of its own, whether
        the sub is plugged in says nothing whatever about whether the maps are there,
        and treating it as evidence would put the tether back in the middle of a
        picture that no longer depends on it.

   Step 0 AND any of 1, 2 or 3 means the console HAD REASON TO EXPECT AN ANSWER, and a
   silence after that is CANNOT TELL — loud, and re-asked in the background exactly as
   before. Anything else is NOT DOWNLOADED, said in the panel rather than shouted on
   the map.

   WHAT A LOCAL MAP SERVICE DOES *NOT* BUY. It does not make anything quieter: a
   service that has answered and stops is loud, and a layer it says is missing is still
   reported ABSENT in its own words. What it changes is that most consoles now get a
   REAL ANSWER instead of a silence to classify — which is the point. These four states
   exist to describe not knowing, and the fix for not knowing is to have somebody to
   ask, not a gentler word for the gap.

   WHY STEP 0 EXISTS, AND WHAT IT IS NOT ALLOWED TO COST. The seen-record used to stand
   on its own, and it is a fact about a PI rather than about this console: "an index for
   this cut answered here once". Open the ?sim=1 demo on a handheld that has worked this
   cut before and that record was still in IndexedDB, so the demo — a page that looks
   for no vehicle whatsoever — raised the map's loudest alarm about a Pi it had never
   tried to reach. A record of somebody having answered is not an addressee, and an
   explicitly simulated console does not inherit one.

   It buys that quiet from the DEMO only, never from a real console. Step 0 asks whether
   a vehicle is CONFIGURED, not whether it is responding: a handheld that boots on the
   towpath with the Pi switched off has a wsBase, has nothing answering, and — on a cut
   whose index answered before — is loud, exactly as it must be. Buying the demo's
   silence with that console's would be a far worse defect than the one it fixes.

   WHAT IS DELIBERATELY NOT EVIDENCE: how the fetch failed. A 404, a four-second
   timeout and "the request never reached the Pi" all happen to a bench laptop and
   all three happen to a dead Pi on a real towpath, so the failure mode cannot
   separate them. The question is never how the ask failed — it is whether there was
   ever anybody there to ask.
   ============================================================================ */
let _crtSeenP = null;
function crtSeenReady(){
  if(!_crtSeenP) _crtSeenP = (async ()=>{
    try{
      if(typeof STORE!=='undefined' && STORE.get){
        const s = await STORE.get('crt.seen', null);
        if(s && typeof s==='object') CRT.seen = s;
      }
    }catch(e){}
    return CRT.seen;
  })();
  return _crtSeenP;
}
function crtSeenArea(area){ return !!(area && CRT.seen && CRT.seen[area]); }
function crtMarkSeen(area){
  // STEP 1's stamp, and it is written on EVERY readable index, not once per area: it
  // is a fact about the backend being alive right now rather than about this cut.
  CRT.answeredAt = Date.now();
  if(!area || (CRT.seen && CRT.seen[area])) return;      // once per area, not once per refresh
  CRT.seen = CRT.seen || {};
  CRT.seen[area] = Date.now();
  try{ if(typeof STORE!=='undefined' && STORE.set) STORE.set('crt.seen', CRT.seen); }catch(e){}
  LOG.map('CRT: the chart index answered for area "'+area+'" from '+mapDataName()+' — from now on, '
        + 'silence from it is a fault here and says CANNOT TELL rather than "not downloaded"');
}
/* STEP 0 — IS THERE ANYBODY TO ASK? Not "is it answering": is a backend for the map
   data ADDRESSED by this console at all.

   A map service of this console's own settles it immediately, and it is the same
   answer in the demo as on the towpath — the whole point of moving the map off the
   vehicle is that whether a sub is plugged in has nothing to do with whether there
   are charts. Without one the charts come off the vehicle and this is the vehicle
   question it has always been: ?sim=1 configures no vehicle and neither does a page
   opened off the disk, and both are decided once at boot by core.js resolveHost and
   cannot change without a reload, which is what makes them safe to reason from. */
/* WHICH SILENCE IS THIS? The map service is local and always the same machine, so the
   old questions — is there a Pi, has the socket opened, did /api/healthz answer — are
   about a thing that no longer serves charts and cannot answer this.

   What remains is one question, and it is the one the operator can act on: HAS THIS
   AREA'S CHART DATA EVER BEEN READ HERE?

     never       Nothing has been downloaded for this cut yet. That is the ordinary
                 state of a new area and of every console before its first fetch, it is
                 fixed by downloading, and it is NOT a fault. Quiet: NOT DOWNLOADED.
     once, and   Something that was here cannot be read now — the map service is down,
     now silent  the card was deleted, a file went bad. Something is wrong with this
                 console. Loud: CANNOT TELL, and the 30 s retry keeps asking.

   Deliberately NOT keyed on whether the ask just failed. A failed request cannot tell
   those two apart, and guessing from it is what put the loudest mark this map has on a
   bench console that had simply never downloaded anything. */
/* TWO STORES, ONE QUESTION, AND EITHER OF THEM IS ENOUGH TO EARN THE ALARM. The
   national store is the ordinary source now and the per-area card is the second, so
   "has this console ever read chart data here" has two records behind it: one for the
   national store (which has no area and therefore its own key) and one per area name.
   Either having answered before and going silent now is the same fault — something
   that was on this handheld cannot be read — and it stays loud. Neither having ever
   answered is a console that has not downloaded anything yet, which is quiet, and
   which after this round means the launch fetch has not run rather than that somebody
   forgot to type a command. */
function crtHadReasonToExpectAnAnswer(){
  return crtSeenArea(CRT_NATIONAL_KEY) || crtSeenArea(CRT.area);
}
function crtNoAnswerStatus(){ return crtHadReasonToExpectAnAnswer() ? 'unavailable' : 'not-downloaded'; }

/* IS THE ONCE-ONLY NATIONAL FETCH RUNNING RIGHT NOW? The expected state of a new
   handheld, exactly once, and the reason NOT DOWNLOADED stops being the everyday
   word: while this is true a layer that has not landed yet is DOWNLOADING, which is
   a different fact with a different reaction (wait) from a store that is not there
   (go and get it) and from one that has gone silent (something is wrong here). */
function crtDownloading(){ return !!(CRT.dl && CRT.dl.running); }
/* WHERE IT HAS GOT TO, in the words the panel and the map badge both use, so the two
   can never quote different numbers at each other. */
function crtDownloadCount(){
  const d = CRT.dl || {};
  const total = d.total || CRT_NATIONAL_LAYERS;
  return {done: Math.max(0, Math.min(total, d.done||0)), total};
}

/* THE TWO CLAUSES NOT DOWNLOADED IS MADE OF, in one place because the panel row, the
   map badge and the log line all have to say the same true thing and there are now
   several trues to choose between.

   THE TRAP THIS AVOIDS. NOT DOWNLOADED used to open "no chart data has ever been
   downloaded for this area on this handheld", which was safe while the status could
   only be reached by a console that had never seen an index. Since the demo stopped
   inheriting the seen-record it is also reached by a handheld that HAS worked this cut
   — and telling that operator nothing was ever downloaded here would be trading one
   false sentence for another, which is not a fix. So the opening states what this
   console holds, and the second clause states why nobody can supply it. */
function crtNeverHadClause(){
  // THE NATIONAL STORE IS THE ONE THAT SHOULD BE HERE, so it is the one this sentence
  // is about. It is fetched once, on launch, and needs no area and no launch point —
  // so "nothing has been downloaded for this area yet" would be describing a condition
  // that no longer decides anything.
  if(crtSeenArea(CRT_NATIONAL_KEY))
    return 'the national chart store has been read on this handheld before, so the Canal & River '
         + 'Trust network was downloaded here at some point, and the map service is not answering '
         + 'for it now';
  if(!crtSeenArea(CRT.area))
    return 'the Canal & River Trust network has never been downloaded onto this handheld — the '
         + 'once-only fetch that happens on launch has not run here yet';
  // IT ANSWERED HERE BEFORE. Which of the two backends that was decides what is
  // actually true now: a local map service holds its layers on this handheld's own
  // disk, so an index that answered once and does not now is a service that has
  // stopped rather than data that was never kept. Off a Pi, nothing is kept here at
  // all — the layers are read over the tether every single time.
  return mapDataLocal()
    ? 'this area\'s chart index has been read from the map service on this handheld before, so the '
      + 'data was downloaded here at some point, and that service is not answering for it now'
    : 'this console has read this area\'s chart index from a Pi before, but chart layers are never '
      + 'kept on the handheld: they are read from the Pi every time, and it holds none of them now';
}
function crtNobodyToAskClause(){
  return 'the map service on this handheld is not answering for it, so there is nothing here to '
       + 'read it from';
}
/* AND WHAT WOULD ACTUALLY FIX IT, which is not the same instruction on any of them. A
   demo has no tether to push home and no REFRESH that can reach anything, and telling
   somebody to plug in a cable that would change nothing is how the rest of the sentence
   stops being believed. Equally, telling somebody to connect a Pi when the map lives on
   the handheld in their hands sends them off after the wrong box entirely. */
function crtRemedyClause(){
  return 'The Canal & River Trust network is downloaded ONCE, nationally, when Neptune is launched '
       + 'with a connection available - it needs no launch point and no area. DOWNLOAD NOW in this '
       + 'panel starts the same fetch on demand. If it has run and this is still empty, the map '
       + 'service on this handheld is not running: restart Neptune from the launcher.';
}

/* ---- fetching (two-phase safe: timeout, no hostnames, no internet) ------- */
/* THE MAP BASE, NOT THE VEHICLE HOST. This one line is the defect: it used to read
   state.httpBase, so every chart request was addressed to the sub — and a console with
   no sub therefore had no map, however much map data was sitting on its own disk. */
function _crtUrl(tmpl, layer){
  return mapDataBase() + String(tmpl)
    .replace('{area}', encodeURIComponent(CRT.area||''))
    .replace('{layer}', encodeURIComponent(layer||''));
}

/* ============================================================================
   THE WINDOW — WHERE THE MAP IS LOOKING, AND WHY A BODY IS ASKED FOR AROUND IT.

   The national store holds the whole network. This console draws a canal at a time.
   Between those two facts sits a browser that has to parse what it is given and
   stroke it at 10 Hz, and 1,296 planning-buffer polygons at 82 kB each is not a thing
   any browser holds parsed while doing that.

   SO A BODY IS ASKED FOR AROUND THE VIEW, WITH SLACK, AND RE-ASKED WHEN THE VIEW
   LEAVES IT. That is PAGING AND NOT PRUNING, and the difference is the whole point:
   the store still holds every layer, every row still reports what the store holds,
   nothing is switched off, and what is outside the window could not have been drawn
   on this screen anyway. The row says so in its own words rather than leaving the
   operator to infer it.

   THE BACKEND IS ALLOWED TO IGNORE IT. A store that answers the whole national layer
   to a windowed request is answering a superset of what was asked for, which is
   correct and simply larger; this file draws it the same way and stops re-asking,
   because a body that already covers everything cannot be improved by asking again.
   ============================================================================ */
function crtViewBBox(){
  // The projection the imagery was drawn with is the truest answer — it is the actual
  // glass. Half the DIAGONAL, not half the width: the collapsed radar is heading-up
  // and a rotated view reaches into the corners.
  const L = (typeof TILES!=='undefined') ? TILES.last : null;
  if(L && L.worldTP){
    const half = Math.hypot(L.w, L.h)/2 / L.k;                 // tile-px from the centre
    const w = L.worldTP;
    const x0 = (L.cxTP-half)/w, x1 = (L.cxTP+half)/w;
    const y0 = (L.cyTP-half)/w, y1 = (L.cyTP+half)/w;
    if(isFinite(x0) && isFinite(y1))
      return [mercXToLon(Math.max(0,x0)), mercYToLat(Math.min(1,y1)),
              mercXToLon(Math.min(1,x1)), mercYToLat(Math.max(0,y0))];
  }
  const M = (typeof MAP!=='undefined') ? MAP : null;
  const lat = M && (M.viewLat!=null ? M.viewLat : (M.origin ? M.origin.lat : null));
  const lon = M && (M.viewLon!=null ? M.viewLon : (M.origin ? M.origin.lon : null));
  if(typeof lat==='number' && typeof lon==='number'){
    const d = 0.01;                       // ~1 km, before the window slack below
    return [lon-d, lat-d, lon+d, lat+d];
  }
  return null;                            // this console has no position of any kind
}
function crtWindowBBox(){
  const b = crtViewBBox(); if(!b) return null;
  const lat = (b[1]+b[3])/2;
  const dLat = CRT_API.windowM / 111320;
  const dLon = CRT_API.windowM / (111320 * Math.max(0.15, Math.cos(lat*Math.PI/180)));
  return [b[0]-dLon, b[1]-dLat, b[2]+dLon, b[3]+dLat];
}
/* Is the window we last read still good for where the map is now? Compared against
   the VIEW rather than against the old window, so a pan of one screen inside six
   kilometres of slack costs nothing at all. */
function crtWindowCovers(win, view){
  if(!win || !view) return false;
  return view[0]>=win[0] && view[1]>=win[1] && view[2]<=win[2] && view[3]<=win[3];
}
function crtBBoxParam(bbox){
  return bbox ? ('bbox=' + bbox.map(n=>n.toFixed(6)).join(',')) : '';
}

async function _crtGet(url, ms, maxBytes){
  let ctl=null, timer=null;
  try{ ctl = (typeof AbortController!=='undefined') ? new AbortController() : null; }catch(e){}
  if(ctl) timer = setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, ms || CRT_API.timeoutMs);
  try{
    const r = await fetch(url, ctl ? {signal:ctl.signal, cache:'no-store'} : {cache:'no-store'});
    if(timer) clearTimeout(timer);
    // MEASURED BEFORE IT IS PARSED, which is the only place it can be. JSON.parse on a
    // hundred-megabyte body is a multi-second freeze of the one thread that is drawing
    // the map and reading the tether — and by the time r.json() has resolved, the cost
    // has already been paid. Content-Length is the store's own statement of what it is
    // about to hand over, and it is checked while there is still a decision to make.
    if(maxBytes){
      const n = parseInt(r.headers && r.headers.get('content-length'), 10);
      if(isFinite(n) && n > maxBytes){
        try{ if(ctl) ctl.abort(); }catch(e){}
        return { ok:false, status:r.status, tooBig:true, bytes:n };
      }
    }
    let j=null; try{ j = await r.json(); }catch(e){}
    return { ok:r.ok, status:r.status, json:j };
  }catch(e){
    if(timer) clearTimeout(timer);
    const msg = (e && e.name==='AbortError')
      ? (mapDataName()+' did not answer within '+Math.round((ms||CRT_API.timeoutMs)/1000)+' s')
      : ('the request never reached '+mapDataName()+' ('+((e&&e.message)||'no route')+')');
    return { ok:false, status:0, err:msg };
  }
}
function _crtSet(id, status, patch){
  const cur = CRT.state[id] || {};
  const next = Object.assign({n:0, drawn:0, capped:false, why:'', data:null, at:Date.now()},
                             cur, patch||{}, {status, at:Date.now()});
  // THE SHAPE CACHE BELONGS TO THE BODY, AND THE BODY IS BEING REPLACED. `cur` is
  // spread in so a status change keeps everything the row was saying, which is right —
  // but `_kinds` is "does this data hold polygons, lines, points", worked out by
  // walking the features once (see _crtKinds). Carried across a new window it answers
  // for the PREVIOUS one, and a layer whose new window happens to hold no lines would
  // go on being asked to stroke them every frame for the rest of the session.
  if(patch && Object.prototype.hasOwnProperty.call(patch, 'data')) delete next._kinds;
  CRT.state[id] = next;
}

/* Does a body mean "this layer is not here"? The server side spells absence
   explicitly; this accepts the handful of shapes that mean it so a wording
   change on the Pi cannot turn ABSENT into a blank map. */
function _saysAbsent(j){
  if(!j || typeof j!=='object') return false;
  if(j.absent===true || j.present===false || j.missing===true) return true;
  const s = String(j.status||j.state||'').toLowerCase();
  return s==='absent' || s==='missing' || s==='not_present';
}
function _featureCount(j){
  if(!j) return null;
  if(j.type==='FeatureCollection') return (j.features||[]).length;
  if(j.type==='Feature') return 1;
  return null;
}

/* THE INDEX. Its own answer is what licenses every per-layer claim below it.

   ONE FUNCTION, TWO STORES. `which` is 'net' for the national store — no area in the
   path, asked always, on every console — or 'area' for the per-area card. They answer
   in the same shape and are read by the same code on purpose: two readers for two
   nearly-identical JSON documents is two places for a field name to drift, and this
   file has already been bitten once by exactly that (`layer` versus `title`). */
async function crtFetchIndex(which){
  const net = (which==='net');
  const path = net ? CRT_API.net : CRT_API.index;
  const store = net ? 'the national chart store on this handheld'
                    : ('the chart card for the area "'+(CRT.area||'')+'"');
  const r = await _crtGet(_crtUrl(path), net ? CRT_API.netTimeoutMs : CRT_API.timeoutMs);
  // THE INDEX'S OWN ACCOUNT OF WHAT IT HOLDS, kept rather than thrown away after the
  // per-layer rows are built. It carries `status`, `why` and — the load-bearing one —
  // `remedy`, which is the backend telling this console the exact thing that fixes a
  // missing hazard fetch. The download panel quotes it verbatim instead of hard-coding
  // a command that would go stale the day the CLI is renamed.
  const raw = r.ok && r.json && typeof r.json==='object'
    ? {status:r.json.status||'', why:r.json.why||'', means:r.json.means||'', remedy:r.json.remedy||'',
       fetched:r.json.fetched||null, failed:r.json.failed||[], unreadable:r.json.unreadable||[],
       total:(typeof r.json.total==='number') ? r.json.total : null}
    : null;
  if(!r.ok){
    // A 404 IS AN ANSWER, AND IT IS THE ONE THIS CONSOLE KEPT MISREADING AS A SILENCE.
    // The backend replied and what it said was "there is no chart data here" — which is
    // knowing, not not-knowing, and NOT DOWNLOADED is the word for it. It used to be
    // classified by crtNoAnswerStatus() alongside a request that never arrived at all,
    // so a perfectly healthy backend with nothing downloaded yet could raise the map's
    // loudest alarm. `nothing` carries that distinction up to crtLoadAll.
    if(r.status===404 || r.status===501)
      return { ok:false, nothing:true, raw:null,
               why:mapDataName()+' answered '+r.status+' for '+store+', which is it saying it holds '
                 + 'nothing there' };
    if(r.status) return { ok:false, raw:null, why:mapDataName()+' answered '+r.status+' for '+store };
    return { ok:false, raw:null, why:r.err || (mapDataName()+' could not be reached') };
  }
  // Normalise every index shape we are willing to accept into id -> {present,count,path}.
  const out = {};
  const add = (wire, meta)=>{
    if(!wire) return;
    const e = crtBind(wire);
    out[e.id] = {
      present: (meta && meta.present!==undefined) ? !!meta.present : !_saysAbsent(meta),
      count:   (meta && typeof meta.count==='number') ? meta.count
             : (meta && typeof meta.features==='number') ? meta.features : null,
      // HOW BIG THE FILE IS, when the store says. Nationally the two polygon layers
      // are 82 kB and 46 kB A FEATURE, and a console with nowhere to draw them must
      // not pull a hundred megabytes into a browser heap to prove it holds them —
      // see crtFetchLayer's `held` branch, which reports the layer as HERE without
      // reading it and says so in words.
      bytes:   (meta && typeof meta.bytes==='number') ? meta.bytes : null,
      path:    (meta && (meta.path || meta.url)) || null,
      attribution: (meta && meta.attribution) || null,
      // The index already knows WHY a layer is not there — "the fetch skipped it
      // (fetch-failed): the service timed out part-way through paging" — and a layer
      // reported absent from the index is never fetched, so this is the only place
      // that reason can be picked up. Dropping it left the row saying the console's
      // generic sentence about a file not being on the disk, over a Pi that could
      // have said which of four quite different things had happened.
      why:  (meta && meta.why) || null,
      means:(meta && meta.means) || null,
      remedy:(meta && meta.remedy) || null,
    };
  };
  crtNoteCredit(r.json, null);                  // the index carries the store's own attribution
  // DEPTH IS ITS OWN BLOCK on this index, not a row in `layers`: the two depth
  // layers are built by different modules from different evidence (api/nav/nominal.py
  // from published waterway classes, api/nav/soundings.py from dive journals) and the
  // server reports each with its own url and its own sentence about why it is not
  // there. Reading it means the row can quote the Pi's reason instead of this
  // console's generic one.
  const D = r.json && r.json.depth;
  if(D && typeof D==='object'){
    const dmap = {depth_nominal:D.nominal, depth_surveyed:D.surveyed};
    Object.keys(dmap).forEach(id=>{
      const b = dmap[id]; if(!b || typeof b!=='object') return;
      out[id] = {
        present: (b.present!==undefined) ? !!b.present : (b.status==='present'),
        count:   (typeof b.count==='number') ? b.count : null,
        path:    b.url || null,
        attribution: b.attribution || null,
        why:     b.why || null, means: b.means || null, remedy: b.remedy || null,
      };
    });
  }
  const L = r.json && (r.json.layers || r.json.crt || r.json);
  if(Array.isArray(L)){
    // `layer` first, deliberately. The Pi's rows carry the filesystem key under
    // `layer` (api/nav/service.py `_crt_layers`) AND a human `title` beside it —
    // binding to the wrong one of those two gives a row named "Locks" that fetches
    // nothing, which looks exactly like a Pi that has no locks.
    L.forEach(it => typeof it==='string'
      ? add(it, {present:true})
      : add(it && (it.layer || it.layer_key || it.id || it.name), it));
  } else if(L && typeof L==='object'){
    Object.keys(L).forEach(k => add(k, (L[k] && typeof L[k]==='object') ? L[k] : {present:!!L[k]}));
  } else {
    return { ok:false, raw:null, why:'the layer index answered with something this console cannot read' };
  }
  return { ok:true, layers:out, raw:raw };
}

/* ============================================================================
   THE NATIONAL DOWNLOAD, AS THE BACKEND REPORTS IT.

   This is what turns the first launch on a new handheld from "NOT DOWNLOADED" — an
   absence mark, in the vocabulary reserved for something being wrong — into
   "DOWNLOADING, 6 OF 27", which is a thing that is happening and will finish. Same
   honesty, different fact: nothing has failed, the store simply is not complete yet,
   and an operator who is told that waits instead of going looking for a fault.

   IT IS NEVER DRAWN AS A HAZARD ALARM. The map badge for it is the quiet one. A
   download in flight on a brand new handheld is the EXPECTED state exactly once, and
   spending the loudest mark this map has on the expected state is how the loudest
   mark stops meaning anything.
   ============================================================================ */
async function crtFetchDownloadState(){
  const r = await _crtGet(_crtUrl(CRT_API.netFetch));
  const d = CRT.dl;
  d.asked = true; d.at = Date.now();
  if(!r.ok || !r.json || typeof r.json!=='object'){
    // NOT AN ANSWER ABOUT THE DOWNLOAD, so nothing is claimed about it. A 404 here is
    // a backend of an older build with no such endpoint, which is not the same as a
    // download that is not running — but it is treated the same way for DRAWING,
    // because either way there is no progress to report and the layer rows speak for
    // themselves from the index.
    d.ok = false; d.running = false;
    d.why = r.err || (mapDataName()+' does not report a national chart download ('+(r.status||'no answer')+')');
    return d;
  }
  const j = r.json;
  d.ok = true;
  d.state = String(j.state || (j.running ? 'running' : '')||'');
  d.running = (j.running===true) || d.state==='running' || d.state==='downloading';
  d.done  = (typeof j.done==='number') ? j.done
          : (Array.isArray(j.layers) ? j.layers.filter(x=>x && (x.present||x.status==='present')).length : 0);
  d.total = (typeof j.total==='number' && j.total) ? j.total
          : (Array.isArray(j.layers) ? j.layers.length : 0);
  d.layer = String(j.layer || j.current || '');
  d.why   = String(j.why || j.detail || '');
  return d;
}
/* ONE POLL, ONLY WHILE IT IS RUNNING, AND IT STOPS ITSELF. A progress read every two
   seconds for the whole dive is a request per two seconds spent to be told nothing is
   happening; a progress bar that stops moving because nobody re-read it is the stuck
   half-finish this whole surface exists to prevent. So: poll while running, stop when
   it settles, and reload the layers once at the end because the store has changed. */
let _crtDlTimer = null;
function crtWatchDownload(){
  if(_crtDlTimer) return;
  _crtDlTimer = setInterval(async ()=>{
    const before = CRT.dl.done;
    const d = await crtFetchDownloadState();
    crtRenderNet(); crtRenderRows(); crtRenderBadge();
    if(typeof bootNoteCharts==='function') bootNoteCharts();
    if(!d.running){
      clearInterval(_crtDlTimer); _crtDlTimer = null;
      LOG.map('CRT: the national chart download has settled ('+d.done+' of '+(d.total||CRT_NATIONAL_LAYERS)
            + ' layers) — re-reading the store');
      crtLoadAll('the national chart download finished');
    } else if(d.done!==before){
      LOG.map('CRT: downloading the national chart store — '+d.done+' of '
            + (d.total||CRT_NATIONAL_LAYERS)+' layers'+(d.layer?(' (now: '+d.layer+')'):''));
    }
  }, CRT_API.dlPollMs);
}

/* WHICH STORE ANSWERS FOR THIS ROW. National first, card second, and the depth pair
   only ever from the card — one of the two is built from THIS area's dive journals, so
   a national depth layer is not a thing that could exist.

   IN ONE PLACE because two callers need the same answer and used to make it twice:
   crtLoadAll walking every layer, and crtSetOn when the operator switches one back on.
   A row toggled on that resolved to a different store from the one the panel had just
   reported for it is the kind of disagreement nobody would ever look for. */
function crtPlanFor(e){
  const net  = CRT.net  || {}, card = CRT.card || {};
  const nMeta = (e.kind!=='depth' && net.ok && net.layers) ? (net.layers[e.id]||null) : null;
  const cMeta = (card.ok && card.layers) ? (card.layers[e.id]||null) : null;
  if(nMeta && nMeta.present!==false) return {source:'net',  meta:nMeta};
  if(cMeta && cMeta.present!==false) return {source:'area', meta:cMeta};
  if(nMeta) return {source:'net',  meta:nMeta};        // the store looked and has not got it
  if(cMeta) return {source:'area', meta:cMeta};
  return null;
}

/* ONE LAYER, FROM WHICHEVER STORE HAS IT.

   `plan` says which store answered for this row and how to ask it: the national store
   (no area, windowed) or this area's card (the old path, whole file). Nothing else in
   this function cares which, because the two answer in the same shape — what differs
   is only the sentence the row ends up saying about where its marks came from. */
async function crtFetchLayer(e, plan){
  plan = plan || {};
  const idxMeta = plan.meta || null;
  const net = (plan.source==='net');
  if(!net && !CRT.area){ _crtSet(e.id, 'no-area'); return; }
  if(!CRT.indexOk){
    // Reached when a layer is switched ON while both indexes are down. Same decision as
    // crtLoadAll's, through the same function: a row toggled on by hand must not be
    // the one that alarms on a bench console the rest of the panel is quiet about.
    _crtSet(e.id, crtNoAnswerStatus(), {why: CRT.indexWhy || 'the chart index could not be read'});
    return;
  }
  if(idxMeta && idxMeta.present===false){
    _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(idxMeta), source:plan.source});
    return;
  }
  // HELD, BUT NOT READ. The store says it has this layer and there is nowhere to draw
  // it — no projection, no launch point, no view centre of any kind — and the file is
  // big enough that reading it would cost tens of megabytes of browser heap to hold
  // data nothing can put on the glass. The row reports HERE, because it IS here, and
  // says in its own words that none of it has been read yet. It is read the instant
  // the map knows where it is (crtEnsureWindow).
  const win = net ? crtWindowBBox() : null;
  if(net && !win && idxMeta && typeof idxMeta.bytes==='number' && idxMeta.bytes > CRT_API.bigBytes){
    _crtSet(e.id, 'held', {data:null, n:(idxMeta.count||0), source:'net', bytes:idxMeta.bytes, win:null});
    return;
  }
  _crtSet(e.id, 'loading', {source:plan.source});
  const tmpl = (idxMeta && idxMeta.path) || (net ? CRT_API.netLayer : (e.path || CRT_API.layer));
  // The URL asks for the layer by the BACKEND'S name for it, never by this console's:
  // `locks-0` is a filename on the card and `locks` is a row in the table above, and
  // conflating them is a 404 on every hazard layer we can actually draw.
  let url = _crtUrl(tmpl, e.wire || e.id);
  if(net && win) url += (url.indexOf('?')>=0 ? '&' : '?') + crtBBoxParam(win);
  const r = await _crtGet(url, net ? CRT_API.netTimeoutMs : CRT_API.timeoutMs,
                          net ? CRT_API.readCeilingBytes : 0);
  // TOO BIG TO READ, SAID OUT LOUD. The store has it and the console asked for the part
  // around the map; what came back was the whole national file, over the ceiling. That
  // is a fact about this backend not windowing this layer, and it is reported as one:
  // the row says HELD, keeps its switch, keeps its count, and draws the moment the
  // store answers the window it was asked for.
  if(r.tooBig){
    _crtSet(e.id, 'held', {data:null, n:(idxMeta && idxMeta.count)||0, source:'net',
      bytes:r.bytes, national:(idxMeta && idxMeta.count)||null, unwindowed:true,
      why:'the store answered with the whole national layer ('+Math.round(r.bytes/1048576)
        + ' MB) rather than the part around the map'});
    return;
  }
  if(!r.ok){
    // The index answered, so the service exists — a 404 on one layer really is
    // "that file is not on the disk", which is the one case we are entitled to
    // call ABSENT.
    if(r.status===404) _crtSet(e.id, 'absent', {data:null, n:0, source:plan.source});
    else if(r.status)  _crtSet(e.id, 'unavailable', {why:mapDataName()+' answered '+r.status+' for this layer', data:null});
    else               _crtSet(e.id, 'unavailable', {why:r.err||(mapDataName()+' could not be reached'), data:null});
    return;
  }
  // The backend answers an absent layer with 200 and a body that says so, and that body
  // carries WHY: skipped on purpose, fetch never run here, file deleted since. Those
  // are different facts with different remedies and the console has no way to know
  // any of them — so it quotes the backend rather than paraphrasing it into one word.
  if(_saysAbsent(r.json)){ _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(r.json), source:plan.source}); return; }
  const n = _featureCount(r.json);
  if(n===null){ _crtSet(e.id, 'unavailable', {why:mapDataName()+' sent something that is not GeoJSON', data:null}); return; }
  crtNoteCredit(r.json, idxMeta);
  // WHAT ARRIVED, AND WHETHER IT IS ALL OF IT. A body whose feature count reaches the
  // index's national count is the whole layer however it was asked for, and re-asking
  // it as the map moves would be spending a read to be handed the same file. Anything
  // short of that is a WINDOW and is re-read when the view leaves it — which the row
  // says, because "12 features" over a national layer of 6,916 would otherwise read as
  // a store that is missing nearly all of it.
  const nat = (idxMeta && typeof idxMeta.count==='number') ? idxMeta.count : null;
  const whole = !net || !win || (nat!==null && n>=nat);
  const patch = {data:r.json, n:n, source:plan.source, national:nat,
                 win: whole ? null : win.slice(), whole:whole};
  if(n===0){ _crtSet(e.id, 'empty', patch); return; }
  _crtSet(e.id, 'present', patch);
}

/* ============================================================================
   THE WINDOW MOVED — RE-READ WHAT IS NOW ON SCREEN.

   Called from the draw loop, where it costs one bbox comparison a frame. Everything
   expensive is behind three guards: the layer must be windowed at all (a whole
   national body never re-reads), the view must have genuinely left the six kilometres
   of slack, and one re-read runs at a time.

   NOT A POLL. It is driven by the operator moving the map, which is the only thing
   that can change the answer. A console sitting still spends nothing.
   ============================================================================ */
async function crtEnsureWindow(){
  if(CRT._winBusy || CRT._busy || !CRT.net.ok) return;
  const view = crtViewBBox(); if(!view) return;
  if((Date.now() - CRT._winAt) < 1500) return;
  const stale = crtAll().filter(e=>{
    if(e.kind==='depth' || !crtIsOn(e.id)) return false;
    const st = CRT.state[e.id];
    if(!st) return false;
    // NEVER READ, AND NOW THERE IS A VIEW — worth another ask. Except when the reason
    // it was not read is that the store does not window it: panning cannot change that
    // answer, so re-asking on every move would be a request per pan to be told the same
    // thing. That one is re-asked by REFRESH and by the background retry, where a
    // backend that has been fixed gets noticed without costing anything meanwhile.
    if(st.status==='held') return !st.unwindowed;
    if(st.status!=='present' && st.status!=='empty') return false;
    if(st.source!=='net' || st.whole) return false;           // the card, or the whole national file
    return !crtWindowCovers(st.win, view);
  });
  if(!stale.length) return;
  CRT._winBusy = true; CRT._winAt = Date.now();
  try{
    for(const e of stale){
      const meta = (CRT.net.layers && CRT.net.layers[e.id]) || null;
      await crtFetchLayer(e, {source:'net', meta:meta});
    }
    CRT._win = crtWindowBBox();
    crtRenderRows();
  } finally { CRT._winBusy = false; }
}

/* THE LAYER'S OWN CREDIT LINE, IF IT BROUGHT ONE.

   api/nav/crt.py writes the attribution INTO each FeatureCollection precisely so a
   file copied out of that directory does not lose it. The same reasoning applies one
   layer further out: whatever the file says about who to credit is what gets shown,
   rather than this console assuming the standard OGL line covers everything it was
   handed. A layer under different terms therefore credits its own source instead of
   being quietly filed under somebody else's licence. */
/* The Pi's own account of an absence, in its words, when it sent one. */
function _crtWhyOf(o){
  if(!o || typeof o!=='object') return '';
  const bits = [o.why, o.means, o.remedy].filter(x=>typeof x==='string' && x.trim());
  return bits.join(' — ');
}
/* THE SAME SENTENCE TWICE IS NOT TWO CREDITS, and with every layer on that stopped
   being a nicety. The national store answers nine distinct attribution strings across
   its twenty-seven layers, several of them differing only in the case of "Data
   Licence", a trailing full stop, or the whitespace around an em dash — and each layer
   volunteers one from its index row AND another from its own body. Compared verbatim
   that is a dozen near-identical paragraphs, which on a 330 px panel pushed the layer
   list off the bottom of the screen entirely. The words are a licence condition and
   are all still shown; what is dropped is only the repetition. */
function _crtCreditKey(s){
  return String(s).toLowerCase().replace(/[\s ]+/g,' ').replace(/[.,;:]+$/,'').trim();
}
function crtNoteCredit(gj, meta){
  const s = (gj && (gj.attribution || (gj.properties && gj.properties.attribution)))
         || (meta && meta.attribution) || '';
  if(!s || typeof s!=='string') return;
  const t = s.replace(/[\s ]+/g,' ').trim();
  if(!t) return;
  const k = _crtCreditKey(t);
  if(k === _crtCreditKey(CRT_ATTRIBUTION)) return;
  if(CRT.credits.some(c=>_crtCreditKey(c)===k)) return;
  CRT.credits.push(t);
  crtRenderCredits();
}
function crtRenderCredits(){
  const el=$('crt-credit'); if(!el) return;
  const base = el.dataset.base || (el.dataset.base = el.textContent.trim());
  const all = [base].concat(CRT.credits);
  // ONE PER LINE, not run together with separators. "Internal use only" is a different
  // licence from the Open Government Licence and an operator is entitled to see which
  // layers are under what — a wall of text joined by middle dots is words on a screen
  // that nobody can read a condition out of. The box scrolls (see .crt-credit) so all
  // of them stay reachable without any of them displacing the layer list.
  el.textContent = '';
  all.forEach(t=>{ const d=document.createElement('div'); d.className='crt-credit-line';
                   d.textContent = t; el.appendChild(d); });
  const s = 'WHERE THIS DATA COMES FROM, and under what terms. The Canal & River Trust publish '
          + 'their layers under more than one licence — most under the Open Government Licence '
          + 'v3.0, some under their own, and at least one marked internal use only — so every '
          + 'distinct statement the store carried is listed here rather than filed under the '
          + 'commonest one. ' + all.length + ' statement' + (all.length===1?'':'s') + ': ' + all.join('  |  ');
  el.title = s; el.setAttribute('aria-label', s);
}

async function crtLoadAll(why){
  if(CRT._busy) return;
  CRT._busy = true;
  try{
    // AWAITED BEFORE ANYTHING IS CLASSIFIED. The never-had-it / lost-it decision reads
    // a PERSISTED record, and a refresh that raced the store would file a Pi which has
    // been answering for this cut all month as a console that has never seen one — the
    // hazard alarm switched off by a timing accident, which is a far worse failure
    // than the bench alarm this whole change exists to stop.
    await crtSeenReady();
    /* THE NATIONAL STORE IS ASKED FIRST AND IT IS ASKED UNCONDITIONALLY. There is no
       area in its path and no `if` in front of it: this is the line that used to read
       `if(!CRT.area) return`, which is why a handheld holding the Trust's whole
       published network drew nothing until somebody tapped a launch point. */
    LOG.map('CRT: reading the national chart store'
          + (CRT.area ? (' and the card for area "'+CRT.area+'"') : '')+' ('+(why||'refresh')+')');
    const net = await crtFetchIndex('net');
    CRT.net = {ok:!!net.ok, nothing:!!net.nothing, why:net.why||'', layers:net.layers||null,
               raw:net.raw||null, at:Date.now()};
    if(net.ok) crtMarkSeen(CRT_NATIONAL_KEY);
    /* AND THE AREA CARD SECOND, when there is an area. A layer the national store does
       not have and this card does is still real data about real water. */
    const card = CRT.area ? await crtFetchIndex('area')
                          : {ok:false, nothing:true, why:'no map area is active, so there is no '
                             + 'per-area chart card to read — which after this round is a fact about '
                             + 'the depth pair alone, because the Trust layers do not need one'};
    CRT.card = {ok:!!card.ok, nothing:!!card.nothing, why:card.why||'', layers:card.layers||null,
                raw:card.raw||null, at:Date.now()};
    if(card.ok) crtMarkSeen(CRT.area);

    CRT.indexOk  = !!(net.ok || card.ok);
    CRT.indexWhy = net.ok ? '' : (card.ok ? '' : (net.why || card.why || ''));
    CRT.indexRaw = (net.ok && net.raw) || (card.ok && card.raw) || null;

    // WHERE THE DOWNLOAD HAS GOT TO, asked once per load and then watched only while it
    // is running. This is what lets a layer that has not landed yet say DOWNLOADING
    // instead of NOT DOWNLOADED — the difference between "wait" and "something is wrong".
    await crtFetchDownloadState();
    if(CRT.dl.running) crtWatchDownload();

    if(!CRT.indexOk){
      // NOT "absent". Nothing usable came back from either store, so no layer may be
      // reported as missing from a disk nobody read. WHICH silence this is decides the
      // volume and nothing else — see crtNoAnswerStatus.
      //
      // EXCEPT WHEN IT WAS NOT A SILENCE. `nothing` is the backend answering 404: it
      // spoke, and what it said is that it holds nothing. That is NOT DOWNLOADED by
      // definition and it is not a fault — unless a store HAS answered here before, in
      // which case data that was on this console has gone, and that is exactly the
      // fault CANNOT TELL exists to shout about.
      //
      // AND EXCEPT WHILE THE LAUNCH FETCH IS RUNNING, which is the new ordinary state
      // of a new handheld: nothing is missing, it is on its way, and a console that
      // said NOT DOWNLOADED over a download in flight would be teaching the operator
      // to read the absence marks as noise.
      const spoke = net.nothing && (!CRT.area || card.nothing);
      const quiet = crtDownloading() ? 'downloading'
                  : spoke ? (crtHadReasonToExpectAnAnswer() ? 'unavailable' : 'not-downloaded')
                  : crtNoAnswerStatus();
      const why = net.why || card.why || '';
      crtAll().forEach(e=>_crtSet(e.id, quiet, {why:why, data:null}));
      if(quiet==='unavailable') LOG.warn('CRT: chart layers CANNOT TELL — '+why);
      else if(quiet==='downloading')
        LOG.map('CRT: the national chart store is still downloading — '
              + crtDownloadCount().done+' of '+crtDownloadCount().total+' layers');
      else LOG.map('CRT: no chart data is on this handheld yet — ' + crtNobodyToAskClause()
                 + ', so the layer panel says so quietly ('+why+')');
      return;
    }
    for(const e of crtAll()){
      const plan = crtPlanFor(e);
      if(!crtIsOn(e.id) && e.tier!==1){
        // Off means not asked — and after this round it only ever means the OPERATOR
        // switched it off, because nothing ships off. Unless a store has already told
        // us it is absent, in which case that is a fact and worth keeping, with the
        // backend's REASON: one absence must not produce two different sentences
        // depending on whether the layer happened to be switched off.
        const m = plan && plan.meta;
        if(m && m.present===false) _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(m), source:plan.source});
        else _crtSet(e.id, 'off', {data:null, n:0});
        continue;
      }
      // NEITHER STORE MENTIONS IT. An index is the backend's inventory, so a layer it
      // does not list is a layer it does not have — say so from the index rather than
      // firing a GET at every unlisted layer just to be told 404 fifteen times. Two
      // exceptions: a layer with a path of its own (the depth pair) may be served by a
      // part of the backend that never appears in an index; and while the launch fetch
      // is running, a layer that has not landed yet is DOWNLOADING and not missing.
      if(!plan && !e.path){
        _crtSet(e.id, crtDownloading() ? 'downloading' : 'absent', {data:null, n:0});
        continue;
      }
      await crtFetchLayer(e, plan || {source:'area', meta:null});
    }
    CRT._win = crtWindowBBox();
    const st = (s)=>crtAll().filter(e=>(CRT.state[e.id]||{}).status===s).length;
    LOG.map('CRT: '+(st('present')+st('held'))+' layer(s) held, '+st('absent')+' absent, '
          + st('unavailable')+' cannot-tell, '+st('downloading')+' still downloading');
  } finally {
    CRT._busy = false;
    crtRenderNet(); crtRenderRows(); crtRenderBadge();
  }
}

/* The active area changed (or first appeared).

   IT IS NO LONGER WHAT DECIDES WHETHER THERE IS A MAP. The national store answers
   without one; what an area still decides is the depth pair (one of which is built
   from this area's own dive journals) and which card is read second. So a console
   with the area cleared reloads and keeps every national layer it had — where it used
   to blank the whole panel to "no area" and stop. */
function crtSetArea(name){
  const n = name || null;
  if(n === CRT.area) return;
  CRT.area = n;
  // The adopted rows, the wire bindings and the borrowed credits all belonged to the
  // OLD area's files. Carrying them over would leave the last canal's layer list on
  // screen over this one's water, which is the same class of error as leaving its
  // hazards drawn.
  CRT.extra = []; CRT.bind = {}; CRT.claimed = {}; CRT.credits = []; CRT.indexRaw = null;
  crtRenderCredits();
  crtAll().forEach(e=>_crtSet(e.id, 'off', {data:null, n:0}));
  crtBuildPanel(); crtRenderBadge();
  crtLoadAll('area changed');
}

/* ============================================================================
   THE NATIONAL STORE, SAID ONCE, AT THE TOP OF THE PANEL.

   THE STATE THIS EXISTS TO MAKE ORDINARY. Before this, a fresh console's honest
   answer to "where is my map" was twenty-nine rows of NOT DOWNLOADED — an absence
   mark, in the vocabulary this file reserves for something being wrong, shown as a
   matter of course on a console where nothing whatever had gone wrong. Everybody
   learns to read that as "still booting", and the day it means what it says nobody
   is listening. So the two ordinary answers get one line of their own, above the
   per-layer detail:

     DOWNLOADING · 6 OF 27   the once-only launch fetch is running. Expected, exactly
                             once, on a new handheld. Not a fault and never drawn as one.
     HERE · 27 LAYERS        the network is on this handheld. This is what a working
                             console says, and it says it whether or not there is a
                             launch point, a Pi, an area or a sub.

   and the two that are now the LAST RESORT rather than the everyday state:

     NOT DOWNLOADED          the launch fetch has not run here. Quiet: nothing failed.
     CANNOT TELL             it was here and cannot be read now. Loud: something is wrong.
   ============================================================================ */
function crtNetLook(){
  const d = crtDownloadCount();
  if(crtDownloading())
    return {cls:'busy', word:'DOWNLOADING · '+d.done+' OF '+d.total,
      help:'THE CANAL & RIVER TRUST NETWORK IS DOWNLOADING ONTO THIS HANDHELD — '+d.done+' of '
         + d.total+' layers so far'+(CRT.dl.layer?(', currently '+CRT.dl.layer):'')+'. This happens '
         + 'ONCE, on launch: the whole national network is fetched and kept here, so that from then '
         + 'on the map is simply present — with no launch point, no area, no connection and no sub. '
         + 'It is not a fault and it does not need watching; the console is fully flyable while it '
         + 'runs. Layers that have not landed yet say DOWNLOADING in the list below rather than '
         + 'pretending there is nothing there.'};
  if(CRT.net.ok){
    const held = crtAll().filter(e=>{ const s=CRT.state[e.id]||{};
      return s.status==='present'||s.status==='held'||s.status==='empty'; }).length;
    return {cls:'here', word:'HERE · '+held+' LAYERS',
      help:'THE CANAL & RIVER TRUST NETWORK IS ON THIS HANDHELD — '+held+' layers, read from its own '
         + 'disk. The whole national network is held, not a clipping of it: it was fetched once and '
         + 'it does not depend on a launch point, an area, a connection or a sub being plugged in. '
         + 'Every layer it holds is drawn on the map; the rows below say what each mark means for '
         + 'this vehicle and let you switch off the ones you do not want after driving with them.'};
  }
  if(crtHadReasonToExpectAnAnswer())
    return {cls:'lost', word:'CANNOT TELL',
      help:'THE NATIONAL CHART STORE HAS BEEN READ ON THIS HANDHELD BEFORE AND CANNOT BE READ NOW: '
         + (CRT.net.why||'the map service did not answer')+'. Something that was here is not working '
         + '— the map service has stopped, or the store has been deleted or damaged. This map is not '
         + 'showing the Trust\'s marks, so an unmarked stretch means NO DATA and never "nothing '
         + 'there". Restart Neptune from the launcher.'};
  return {cls:'none', word:'NOT DOWNLOADED',
    help:'THE CANAL & RIVER TRUST NETWORK IS NOT ON THIS HANDHELD YET'
       + (CRT.net.why?(' ('+CRT.net.why+')'):'')+'. Nothing has failed: the once-only launch fetch '
       + 'has not run here. '+crtRemedyClause()+' Until it has, this map is not showing locks, '
       + 'weirs, sluices, culverts, tunnels, portals or outfalls at all, and an unmarked stretch '
       + 'means NO DATA rather than "nothing there".'};
}
function crtRenderNet(){
  const el = $('crt-net'); if(!el) return;
  const look = crtNetLook();
  const pill = $('crt-net-state');
  if(pill){
    pill.textContent = look.word; pill.className = 'crt-net-pill n-'+look.cls;
    // The number is on the pill, so the sentence is on the pill too — the house rule
    // is that every glyph and every number carries its own written explanation, and a
    // count with no sentence is exactly the thing that gets misread on a towpath.
    pill.dataset.help = look.help; pill.title = look.help; pill.setAttribute('aria-label', look.help);
  }
  el.className = 'crt-net n-'+look.cls;
  el.dataset.help = look.help; el.title = look.help; el.setAttribute('aria-label', look.help);
  const bar = $('crt-net-bar');
  if(bar){
    const d = crtDownloadCount();
    const pc = crtDownloading() ? Math.round(d.done*100/Math.max(1,d.total)) : (CRT.net.ok ? 100 : 0);
    const fill = bar.querySelector('.crt-net-fill');
    if(fill) fill.style.width = pc + '%';
    bar.classList.toggle('on', pc>0);
  }
}

/* ---- the panel ----------------------------------------------------------
   Rows are built from the table, so a layer added above appears here with its
   sentence attached and cannot ship as a bare glyph. Every row carries the title
   AND the aria-label, and the live state is appended through liveTitle so the
   explanation survives thetext being rewritten every few seconds.
   -------------------------------------------------------------------------- */
function crtGlyphSvg(e){
  const cls = 'crt-g' + (e.tier===1 || e.hazardish ? ' t1' : e.tier===2 ? ' t2' : ' t3');
  /* THE PANEL KEY HAS TO BE THE MARK THAT IS ON THE MAP. A row showing an octagon beside
     a map full of chevrons is a key for a different console, and the key is how anybody
     works out what they are looking at. Drawn pointing EAST, with no claim attached: the
     row's own sentence says the angle is per-feature and that this swatch is a shape and
     not a bearing. Same notched-arrowhead outline as _crtChevronPath, at r = 7.5. */
  const shape = e.angleWhat
    ? '<polygon points="17.9,10 8.9,16.9 4,16.9 7,10 4,3.1 8.9,3.1"/>'
    : (e.tier===1 || e.hazardish)
    ? '<polygon points="7,2 13,2 18,7 18,13 13,18 7,18 2,13 2,7"/>'
    : e.tier===2 ? '<rect x="2.5" y="2.5" width="15" height="15" rx="3.5"/>'
                 : '<circle cx="10" cy="10" r="7.5"/>';
  const fs = (e.mark||'?').length>1 ? 7.5 : (e.angleWhat ? 8.5 : 9.5);
  return '<svg class="'+cls+'" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">'
       + shape + '<text x="10" y="10" text-anchor="middle" dominant-baseline="central" '
       + 'font-size="'+fs+'" font-weight="800">'+(e.mark||'?')+'</text></svg>';
}
function crtBuildPanel(){
  const list = $('crt-list'); if(!list) return;
  CRT._building = true;
  list.innerHTML = '';
  const TIER_HEAD = [
    null,
    ['HAZARDS — ALWAYS ON',
     'TIER 1: the structures that can take the sub or the tether somewhere neither comes back from '
   + '— locks, weirs, sluices, culverts, tunnels and their portals, and outfalls. They have no '
   + 'switch, on purpose: there is no operator preference that makes it right to hide them. They '
   + 'are also the honest proxy for "expect current here", because the Canal & River Trust data '
   + 'publishes no flow at all and this console will not invent one.'],
    ['OPERATIONS — DRAWN UNDER THE HAZARDS',
     'TIER 2: where you can get the sub in and out, turn it round, and what is moored in its way. '
   + 'Drawn in the band beneath the hazard marks, at a lighter weight, so they can never sit over a '
   + 'lock or a weir. Shown unless you switch them off, and the choice is remembered on this '
   + 'handheld.'],
    ['EXTRAS — DRAWN QUIETEST, UNDER BOTH',
     'TIER 3: everything else the Trust publishes. These used to ship SWITCHED OFF, which meant a '
   + 'handheld could carry the aqueducts, the boat lifts and the pumping stations to the water and '
   + 'show none of them unless somebody went looking. They are all drawn now — smallest, dimmest, '
   + 'furthest down the stack, and out of the way of the two tiers above. Switch off what you do not '
   + 'want after driving with it; a row you have switched off says NOT ASKED, which is a fact about '
   + 'your choice and never about the water.'],
  ];
  for(let tier=1; tier<=3; tier++){
    const entries = crtTierList(tier);
    if(!entries.length) continue;
    const head = document.createElement('div');
    head.className = 'crt-head t'+tier;
    head.id = 'crt-tier-'+tier;
    head.textContent = TIER_HEAD[tier][0];
    head.dataset.help = TIER_HEAD[tier][1];
    head.title = TIER_HEAD[tier][1];
    head.setAttribute('aria-label', TIER_HEAD[tier][1]);
    list.appendChild(head);
    entries.forEach(e=>{
      const row = document.createElement('div');
      row.className = 'crt-row t'+tier + (e.hazardish?' hazardish':'');
      row.id = 'crt-row-'+e.id;
      row.dataset.layer = e.id;
      row.dataset.help = crtWhat(e);
      row.innerHTML =
        '<span class="crt-glyph">'+crtGlyphSvg(e)+'</span>'+
        '<span class="crt-name">'+e.name+'</span>'+
        '<span class="crt-state" id="crt-state-'+e.id+'"></span>'+
        (tier===1
          ? '<span class="crt-locked" id="crt-locked-'+e.id+'">ALWAYS</span>'
          : '<button class="crt-toggle" id="crt-toggle-'+e.id+'" type="button" role="switch"></button>');
      list.appendChild(row);
      const btn = row.querySelector('.crt-toggle');
      if(btn) btn.addEventListener('click', (ev)=>{ ev.stopPropagation(); crtSetOn(e.id, !crtIsOn(e.id)); });
      const lock = row.querySelector('.crt-locked');
      if(lock){
        const s = 'ALWAYS DRAWN — this is a hazard layer and it has no off switch. '+crtWhat(e);
        lock.dataset.help = s; lock.title = s; lock.setAttribute('aria-label', s);
      }
    });
  }
  /* THE BANK LAYER GETS ITS OWN BLOCK AT THE BOTTOM, AND NOT A FOURTH TIER.

     The three tiers above are a safety ORDER for Canal & River Trust marks — how loudly
     and in what sequence a point glyph is drawn. This is a raster wash about the LAND,
     from a different authority, with a different survey date and a different set of ways
     to be missing, and filing it as "tier 4" would be claiming it sits somewhere in that
     order when it is not in that order at all: it is painted under every one of them,
     over the imagery. Its own heading says whose data it is, which matters because "the
     Trust does not publish this stretch" and "nobody has processed the terrain here" are
     two different absences with two different fixes. */
  bankBuildRow(list);
  CRT._rowsBuilt = true;
  CRT._building = false;
  crtRenderRows();
}
function crtRenderRows(){
  if(!CRT._rowsBuilt || CRT._building) return;
  let missing = false;
  crtAll().forEach(e=>{
    const row = $('crt-row-'+e.id);
    // A layer the server invented after the build. Rebuild ONCE, after this pass —
    // rebuilding from inside the loop re-enters this function and the guard above is
    // what stops that becoming a recursion instead of a redraw.
    if(!row){ missing = true; return; }
    const st = CRT.state[e.id] || {status:'off'};
    const on = crtIsOn(e.id);
    row.classList.toggle('on', on);
    row.classList.toggle('absent', st.status==='absent');
    row.classList.toggle('unavailable', st.status==='unavailable');
    row.classList.toggle('not-downloaded', st.status==='not-downloaded');
    row.classList.toggle('downloading', st.status==='downloading');
    row.classList.toggle('empty', st.status==='empty');
    row.classList.toggle('shown', on && st.status==='present');
    row.classList.toggle('held', st.status==='held');
    const pill = $('crt-state-'+e.id);
    if(pill){
      const word = CRT_STATE_WORDS[st.status] || st.status;
      // The saved survey and this session's own soundings are two counts, never one:
      // "+6" is the water this sub has been through since the last dive log was
      // written, and the row's sentence says so in full.
      //
      // MATCH, NOT REPLACE. `live.replace(/^ Plus (\d+) cell.*$/, ' +$1')` hands back
      // the WHOLE STRING when it does not match, so the moment the live clause said
      // anything other than a count — it now explains a simulated dive measuring
      // nothing — a paragraph landed inside a 9 px pill. A count is extracted or it
      // is not there.
      const live = on ? crtLiveClause(e) : '';
      const cnt  = live && live.match(/^ Plus (\d+) cell/);
      const n2   = cnt ? (' +' + cnt[1]) : '';
      // THE COUNT ON THE PILL IS THE ONE THE STORE HOLDS, not the one in memory. A
      // windowed body is 12 features out of a national 6,916, and "HERE · 12" beside a
      // layer this handheld holds nearly seven thousand of would read as a store that
      // had lost almost all of it. The national figure is what is claimed; the row's
      // own sentence says how much of it is loaded and why.
      const count = (typeof st.national==='number' && st.national) ? st.national : st.n;
      const shows = (st.status==='present' && on) || (st.status==='held' && count);
      pill.textContent = (shows ? (word+' · '+count.toLocaleString()) : word) + n2;
      pill.className = 'crt-state s-'+st.status + (n2 ? ' live' : '');
    }
    const btn = $('crt-toggle-'+e.id);
    if(btn){
      btn.textContent = on ? 'ON' : 'OFF';
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-checked', on ? 'true' : 'false');
      const s = (on
        ? 'DRAWN. Tap to stop drawing '
        : 'SWITCHED OFF BY YOU — the data is still on this handheld and is simply not being drawn. '
          + 'Tap to draw ')
        + e.name.toLowerCase()
        + ' on the map; the choice is remembered on this handheld. Every layer this console holds is '
        + 'drawn on a fresh install, so switching one off is always your decision and never a '
        + 'default. ' + crtWhat(e);
      btn.dataset.help = s; btn.title = s; btn.setAttribute('aria-label', s);
    }
    liveTitle(row, crtStateSentence(e));
  });
  // The bank row is refreshed with the rest of them: it is in the same panel, answering
  // the same question, and a row that only updated when its own fetch returned would go
  // stale the moment the operator panned off the end of a processed area.
  bankRenderRow();
  if(missing) crtBuildPanel();
}

/* THE BADGE — absence, said on the MAP and not only inside a panel nobody opened.

   The whole point of the doctrine is that a layer which is not there must not be
   readable as a stretch of water with nothing in it, and a panel the operator has
   to open first cannot deliver that. Tier 1 gets its own, louder wording: if the
   hazard layers are not on this Pi then the map is not showing hazards at all, and
   that has to be the first thing anybody notices about it.

   THREE VOLUMES, AND THE VOLUME IS THE CLAIM. The hazard alarm is for data that
   SHOULD be here and is not. The plain badge is for the operational and extra layers.
   The quiet one is for a console with no chart data loaded for this area and nobody on
   the link to get it from — still said, because an unmarked stretch must never read as a
   surveyed empty one, and still said on the map rather than only in a panel nobody
   opened; just not dressed as a fault, because nothing has failed. A bench console
   and the ?sim=1 demo used to raise the LOUDEST badge on this map on every launch,
   which is how an operator learns to look past the badge that means something. */
function crtRenderBadge(){
  // The download badge is a SEPARATE element and is refreshed here because this is the
  // function every layout transition already calls — both badges word themselves
  // differently depending on how much room the current view has.
  crtRenderFetchBadge();
  const el = $('crt-absent'); if(!el) return;
  /* NO MAP ON SCREEN, NOTHING TO MISREAD. This badge exists for exactly one failure —
     an unmarked stretch of water being read as a surveyed empty one — and that failure
     needs water on the screen to happen. A console with no area, no launch point and
     no view centre is drawing no map at all: it is showing its own empty state, and a
     mark saying "no chart data over this" would be a claim about a picture that is not
     there. The panel still says everything, in full, for anybody who opens it.

     It used to be gated on `!CRT.area`, which meant the same thing when an area was
     the only way to have a map. It is not any more — the national store answers with
     no area whatsoever — so the gate is now what it always actually meant: is this
     console drawing water. */
  if(!crtViewBBox() && !(typeof MAP!=='undefined' && MAP.hasArea)){
    el.classList.remove('on'); CRT._badge=''; return;
  }
  const lost = (id)=>{ const s=CRT.state[id]||{}; return s.status==='absent'||s.status==='unavailable'; };
  const t1 = crtTierList(1).filter(e=>lost(e.id));
  const rest = crtAll().filter(e=>e.tier!==1).filter(e=>lost(e.id));
  const never = crtAll().filter(e=>(CRT.state[e.id]||{}).status==='not-downloaded');
  const coming = crtAll().filter(e=>(CRT.state[e.id]||{}).status==='downloading');
  const roomy = MAP.expanded || MAP.blind;
  let text='', cls='', help='';
  /* DOWNLOADING IS FIRST, AND IT IS QUIET. It outranks every absence mark below it for
     one reason: while the launch fetch is running, "these layers are missing" is not
     the useful description of the map — "they are on their way" is, and it is the one
     an operator can act on (by waiting). It is drawn in the quiet style, never as a
     hazard alarm: this is the EXPECTED state of a new handheld, exactly once, and
     spending the loudest mark on this map on the expected state is precisely how the
     loudest mark stops meaning anything. */
  if(crtDownloading()){
    const d = crtDownloadCount();
    text = roomy ? ('DOWNLOADING&nbsp;THE&nbsp;CHARTS&nbsp;·&nbsp;'+d.done+'&nbsp;OF&nbsp;'+d.total)
                 : ('CHARTS&nbsp;'+d.done+'/'+d.total);
    cls  = 'quiet';
    help = 'THE CANAL & RIVER TRUST NETWORK IS DOWNLOADING ONTO THIS HANDHELD — '+d.done+' of '
         + d.total+' layers'+(CRT.dl.layer?(', currently '+CRT.dl.layer):'')+'. This happens once, '
         + 'on launch, and the console is fully flyable while it runs. It is drawn quietly because '
         + 'nothing has gone wrong. What it does NOT mean is that the water is clear: until a layer '
         + 'has landed this map is not showing its marks, so an unmarked stretch right now means NOT '
         + 'YET rather than "nothing there". Open the LAYERS panel to see which layers are here '
         + 'already.';
  } else if(t1.length){
    const allCannot = t1.every(e=>(CRT.state[e.id]||{}).status==='unavailable');
    text = roomy
      ? (allCannot ? 'HAZARD&nbsp;LAYERS&nbsp;·&nbsp;CANNOT&nbsp;TELL' : 'HAZARD&nbsp;LAYERS&nbsp;ABSENT&nbsp;('+t1.length+')')
      : 'NO&nbsp;HAZARD&nbsp;DATA';
    cls = 'tier1';
    help = (allCannot
      ? 'THE HAZARD LAYERS COULD NOT BE ASKED FOR. '
      : ('HAZARD LAYERS ARE MISSING FROM ' + mapDataHolder().toUpperCase() + '. '))
      + t1.map(e=>e.name).join(', ') + ' — '
      + (allCannot
         ? 'nothing has been ruled in or out, so this map is not showing locks, weirs, sluices, '
         + 'culverts, tunnel portals or outfalls and cannot say whether there are any.'
         : mapDataName() + ' looked and those files are not on the disk. An empty-looking stretch on '
         + 'this map therefore means NO DATA, not "nothing there".')
      + ' Do not read the absence of a mark as clear water. Open the LAYERS panel for the row-by-row '
      + 'answer.';
  } else if(rest.length){
    // "NOT SHOWN" was the wrong word the moment nothing was hidden any more: it reads as
    // a display choice the operator made, when what it actually reports is data this
    // handheld does not have. Those send you to different places — one to a toggle, one
    // to a download — so the badge has to say which it is.
    text = roomy ? (rest.length+'&nbsp;CHART&nbsp;LAYER'+(rest.length>1?'S':'')+'&nbsp;MISSING') : 'CHART&nbsp;GAPS';
    help = 'These chart layers are MISSING - not switched off. Every layer this handheld holds '
         + 'is drawn; these are the ones ' + mapDataName() + ' does not have or could not be asked '
         + 'for: ' + rest.map(e=>e.name).join(', ') + '. The hazard layers ARE '
         + 'loaded — this is about the operational and extra layers. Open the LAYERS panel to see '
         + 'which is which.';
  } else if(never.length){
    text = roomy ? 'NO&nbsp;CHART&nbsp;DATA&nbsp;DOWNLOADED' : 'NO&nbsp;CHART&nbsp;DATA';
    cls  = 'quiet';
    // Same two clauses as the panel row, from the same two functions, because a badge
    // and a row disagreeing about why the map is empty is a third thing to work out on
    // a towpath. The badge's own sentence carries on from where they leave off.
    help = 'NO CHART DATA IS ON THIS HANDHELD: ' + crtNeverHadClause() + '; '
         + crtNobodyToAskClause() + ' — so nothing on this map is telling you where the '
         + 'locks, weirs, sluices, culverts, tunnels, portals or outfalls are. An unmarked stretch '
         + 'therefore means NO DATA and not "nothing there": do not read the absence of a mark as '
         + 'clear water. It is drawn quietly rather than as an alarm because nothing has gone '
         + 'wrong — the data has simply not been downloaded here yet, which is a different fact '
         + 'from a backend that was answering and stopped, and THAT one alarms. ' + crtRemedyClause()
         + ' Open the LAYERS panel for the row-by-row answer.';
  }
  const key = text+'|'+cls;
  if(key !== CRT._badge){
    CRT._badge = key;
    el.innerHTML = text;
    el.className = 'crt-absent' + (cls?(' '+cls):'') + (text?' on':'');
    if(help){ el.dataset.help = help; el.title = help; el.setAttribute('aria-label', help); }
  }
}

/* ============================================================================
   THE DOWNLOAD, SHOWN WHERE THE LAYER STATES ALREADY LIVE.

   DOWNLOADING IS ITS OWN STATE. The panel below already draws the difference between
   a layer that is absent, one that is present, and one nobody could be asked about —
   and a fetch in flight is none of those three. Left unsaid it reads as the second
   ("the map is filling in, so it must be fine") right up until it stops half way,
   and a map that looks complete and is not is the single failure this whole surface
   was built against.

   IT SITS ABOVE THE LAYER LIST, IN THE SAME PANEL, on purpose: an operator asking
   "why is there nothing on my map" opens one thing, and the answer — not downloaded,
   downloading now, downloaded, or downloaded except for the part that failed — is at
   the top of it before the row-by-row detail starts.

   EVERY ROW SAYS WHOSE WORK IT IS, and that is `drives` in navui.js's job table — not
   a display flag but the answer to "can this console start this". A row that showed a
   spinner for work this console cannot start would be the worst kind of dishonest: it
   would look like it was happening. Which rows this console drives is no longer fixed,
   because it now depends on whether the map data lives on this handheld or on the Pi,
   so the sentence is built from the flag rather than written out twice — see
   bootOwnership().
   ============================================================================ */
function crtFetchRowText(j){
  const w = (typeof BOOT_WORDS!=='undefined' && BOOT_WORDS[j.state]) || j.state.toUpperCase();
  if(j.state==='running' && j.total)
    return w + ' ' + (typeof bootPct==='function' ? bootPct(j) : 0) + '%';
  if((j.state==='done' || j.state==='held') && j.total)
    return w + ' · ' + j.total.toLocaleString();
  return w;
}
function crtBuildFetch(){
  const box = $('crt-fetch-rows'); if(!box || typeof BOOTFETCH==='undefined') return false;
  // WHOSE CARD EACH ROW FILLS IS DECIDED BEFORE THE ROW IS WRITTEN. `where` is baked
  // into the markup below and only built once, so a row built before the base was
  // resolved would carry the wrong destination for the rest of the session — the
  // CHART LAYERS row saying "the Pi's card" over a download landing on this handheld.
  if(typeof bootOwnership==='function') bootOwnership();
  if(CRT._fetchBuilt) return true;
  box.innerHTML = '';
  BOOTFETCH.order.forEach(id=>{
    const j = BOOTFETCH.jobs[id]; if(!j) return;
    const row = document.createElement('div');
    row.className = 'crt-fetch-row';
    row.id = 'crt-fetch-row-'+id;
    row.dataset.source = id;
    row.innerHTML =
      '<div class="crt-fetch-line">'+
        '<span class="crt-fetch-src" id="crt-fetch-name-'+id+'">'+j.name+'</span>'+
        '<span class="crt-fetch-dest">→ '+j.where+'</span>'+
        '<span class="crt-fetch-pill" id="crt-fetch-pill-'+id+'"></span>'+
      '</div>'+
      '<div class="crt-fetch-bar" id="crt-fetch-bar-'+id+'"><i class="crt-fetch-fill"></i></div>';
    box.appendChild(row);
  });
  CRT._fetchBuilt = true;
  return true;
}
function crtRenderFetch(){
  if(typeof BOOTFETCH==='undefined') return;
  if(!crtBuildFetch()) return;
  const top = BOOTFETCH.state;
  const word = (typeof BOOT_TOP_WORDS!=='undefined' && BOOT_TOP_WORDS[top]) || top.toUpperCase();
  const wrap = $('crt-fetch');
  if(wrap) wrap.className = 'crt-fetch f-'+top;
  const pill = $('crt-fetch-state');
  if(pill){ pill.textContent = word; pill.className = 'crt-fetch-pill f-'+top; }
  const why = $('crt-fetch-why');
  if(why){
    why.textContent = BOOTFETCH.why || '';
    // The sentence is ON the element as well as in it: the panel is 330 px wide and a
    // failure reason runs to two or three lines, so the readable copy has to survive
    // the box being small. Same rule every glyph and number in this console follows.
    why.title = BOOTFETCH.why || ''; why.setAttribute('aria-label', BOOTFETCH.why || '');
  }
  BOOTFETCH.order.forEach(id=>{
    const j = BOOTFETCH.jobs[id]; if(!j) return;
    const row = $('crt-fetch-row-'+id);
    if(row){
      row.className = 'crt-fetch-row f-'+j.state;
      // THE FLAG, NOT A GUESS. `drives` says whether this console can START this
      // download, which is now a question about where the data lives rather than a
      // constant: the imagery has always been this handheld's own, and the chart
      // layers became this handheld's the moment it got a map service of its own.
      // Reading it off the job means the sentence can never drift from the button.
      const s = j.name+' → '+j.where+'. '+(j.drives
        ? 'This console starts this download itself and it lands on '+j.where+'. '
        : 'This row REPORTS what '+j.where+' holds; it is not driven from this console. ')
        + (j.why||'');
      row.dataset.help = s; row.title = s; row.setAttribute('aria-label', s);
    }
    const p = $('crt-fetch-pill-'+id);
    if(p){ p.textContent = crtFetchRowText(j); p.className = 'crt-fetch-pill f-'+j.state; }
    const bar = $('crt-fetch-bar-'+id);
    if(bar){
      const fill = bar.querySelector('.crt-fetch-fill');
      const pc = (j.state==='running') ? (typeof bootPct==='function' ? bootPct(j) : 0)
               : (j.state==='done' || j.state==='held') ? 100
               : (j.state==='stopped' || j.state==='failed') ? (typeof bootPct==='function' ? bootPct(j) : 0)
               : 0;
      if(fill) fill.style.width = pc + '%';
      bar.classList.toggle('on', pc>0);
    }
  });
  const go = $('crt-fetch-go');
  if(go){
    const plan = BOOTFETCH.plan;
    // THE SIZE GOES ON THE BUTTON. "Download" with no number is how somebody on a
    // metered hotspot finds out what it cost afterwards.
    // NOTHING MISSING IS NOT "~0.0 MB". A number that rounds to nothing reads as a
    // download too small to bother reporting, when the fact is that there is nothing
    // left to fetch at all — which is the answer an operator re-tapping a launch point
    // they have already downloaded actually wants. The button stays live: pressing it
    // walks the box and confirms, at the cost of no requests whatsoever.
    const nothing = !!(plan && plan.tiles > 0 && (plan.held||0) >= plan.tiles);
    go.textContent = BOOTFETCH.running ? 'DOWNLOADING…'
                   : nothing ? 'NOTHING TO DOWNLOAD'
                   : (plan ? ('DOWNLOAD ~'+(typeof bootMB==='function'?bootMB(plan.mb):Math.round(plan.mb))+' MB')
                           : 'DOWNLOAD NOW');
    go.disabled = !!BOOTFETCH.running;
    // THE WHOLE SIZE AND THE REMAINING SIZE ARE DIFFERENT NUMBERS, and a resume must
    // quote the second: "about 19 MB" over a box that is 90% downloaded sends somebody
    // on a metered hotspot hunting for a way to avoid bytes that were never going to be
    // spent. plan.held is what is already on this handheld, counted before the run.
    const mbOf = (n)=>(typeof bootMB==='function' ? bootMB(n) : String(Math.round(n)));
    const s = BOOTFETCH.running
      ? 'A download is already running — press STOP to end it.'
      : ('DOWNLOAD EVERYTHING THIS CONSOLE CAN, for the current launch point, now'
         + (plan
            ? (nothing
               ? (': there is nothing to fetch. All '+plan.tiles.toLocaleString()+' imagery tiles of the '
                  +'saved area "'+plan.resuming+'" are already on this handheld, so pressing this walks '
                  +'the box, finds every tile present and requests none of them.')
               : plan.resuming
               ? (': the saved area "'+plan.resuming+'" already holds '+(plan.held||0).toLocaleString()
                  +' of its '+plan.tiles.toLocaleString()+' imagery tiles, so this fetches the '
                  +Math.max(0, plan.tiles-(plan.held||0)).toLocaleString()+' that are missing — about '
                  +mbOf(plan.mb)+' MB.')
               : (': a '+Math.round(plan.radiusM*2)+' m square, '+plan.tiles.toLocaleString()
                  +' imagery tiles, about '+mbOf(plan.mb)+' MB.'))
            : '.')
         + ' It runs in the background and the console stays flyable throughout. Tiles already saved '
         + 'are never fetched again, so pressing this after a failure only re-requests what is missing.');
    go.dataset.help = s; go.title = s; go.setAttribute('aria-label', s);
  }
  const stop = $('crt-fetch-stop');
  if(stop){
    stop.disabled = !BOOTFETCH.running;
    const s = 'STOP the download now. Everything already saved is kept and nothing is undone — the '
            + 'next run resumes from exactly where this one stopped and re-requests no tile it '
            + 'already has. This stops THIS handheld\'s download; a fetch running on the Pi is the '
            + 'Pi\'s own and is not affected.';
    stop.dataset.help = s; stop.title = s; stop.setAttribute('aria-label', s);
  }
  const auto = $('crt-fetch-auto');
  if(auto){
    auto.textContent = BOOTFETCH.auto ? 'AUTO ON' : 'AUTO OFF';
    auto.classList.toggle('on', !!BOOTFETCH.auto);
    auto.setAttribute('aria-checked', BOOTFETCH.auto ? 'true' : 'false');
    const s = (BOOTFETCH.auto
      ? 'AUTOMATIC DOWNLOADING IS ON: setting a launch point starts the download by itself, but only '
      + 'when the launcher reports this handheld actually has internet. Tap to switch it off — '
      + 'which is what somebody on a metered phone hotspot wants, and the choice is remembered on '
      + 'this handheld.'
      : 'AUTOMATIC DOWNLOADING IS OFF: nothing is fetched unless you press DOWNLOAD. Tap to switch it '
      + 'back on, and a launch point will fetch its own map again whenever there is internet.');
    auto.dataset.help = s; auto.title = s; auto.setAttribute('aria-label', s);
  }
  crtRenderFetchBadge();
}
/* ON THE MAP, NOT ONLY IN A PANEL NOBODY OPENED — the same reasoning as the absence
   badge beside it, and deliberately a SEPARATE element from it. A download in flight
   must never be able to replace or soften "HAZARD LAYERS ABSENT": the two facts are
   independent, they can be true at the same time, and the one that says the map is
   not showing hazards is the one that must never be crowded out.

   THIS BADGE IS ABOUT THE PICTURE UNDER THE SUB AND NOTHING ELSE, which is why the
   incomplete case reads the IMAGERY JOB and not the panel's top line. Those two came
   apart in the obvious way: the top line goes PARTLY DOWNLOADED when the Trust chart
   layers are outstanding, or FAILED when the PI's own second copy failed, and neither
   of those is a hole in the imagery this map draws from. Keyed on the top line, the
   badge lit red over a complete map and said "blank squares are imagery that never
   arrived, not open water" — a false statement about the water, made by the one
   element on this console whose entire job is to stop that sentence being needed. The
   chart side has its own badge two lines up and says it in its own words. */
function crtRenderFetchBadge(){
  const el = $('crt-fetch-badge'); if(!el || typeof BOOTFETCH==='undefined') return;
  const j = BOOTFETCH.jobs.imagery;
  let text='', cls='', help='';
  // KEYED ON THE IMAGERY JOB, not the panel's top line, for the reason above — and it
  // matters more now that the top line also goes DOWNLOADING for a chart fetch running
  // on this handheld. That is not a hole in the picture under the sub, and a badge that
  // said "DOWNLOADING THE MAP · 100%" over a complete map while the hazard layers came
  // down would be describing the wrong download.
  if(j.state==='running'){
    const pc = (typeof bootPct==='function') ? bootPct(j) : 0;
    text = (MAP.expanded || MAP.blind) ? ('DOWNLOADING&nbsp;THE&nbsp;MAP&nbsp;·&nbsp;'+pc+'%')
                                       : ('MAP&nbsp;'+pc+'%');
    cls  = 'busy';
    help = 'DOWNLOADING THE OFFLINE MAP FOR THIS LAUNCH POINT — '+pc+'% of '
         + (j.total||0).toLocaleString()+' imagery tiles. It is running in the background and the '
         + 'console is fully flyable while it does. What has landed is drawn; what has not is not '
         + 'there yet, so a blank square right now means "not downloaded yet" and not open water. '
         + 'Open the LAYERS panel to see every source, or to stop it.';
  } else if(BOOTFETCH.state==='holding'){
    text = (MAP.expanded || MAP.blind) ? 'MAP&nbsp;DOWNLOAD&nbsp;NEEDS&nbsp;CONFIRMING' : 'MAP&nbsp;·&nbsp;CONFIRM';
    cls  = 'ask';
    help = BOOTFETCH.why + ' Open the LAYERS panel and press the download button, which carries the size.';
  } else if(j.state==='failed' || j.state==='stopped'){
    // STOPPED IS HERE TOO, and it was the quieter half of the same hole: an operator
    // who presses STOP half way is left with a map that has holes in it, and a badge
    // that said nothing at all about that is the silent half-finish this surface
    // exists to make impossible. It is the operator's own doing, so it is worded as a
    // fact about the map rather than as a fault.
    const part = (j.state==='stopped');
    text = (MAP.expanded || MAP.blind)
      ? (part ? 'MAP&nbsp;DOWNLOAD&nbsp;STOPPED' : 'MAP&nbsp;DOWNLOAD&nbsp;INCOMPLETE')
      : (part ? 'MAP&nbsp;STOPPED' : 'MAP&nbsp;INCOMPLETE');
    // Amber for the operator's own STOP, red for a download that broke. Both say the
    // map has holes; only one of them is something going wrong, and spending the fault
    // colour on a deliberate act is how the fault colour stops meaning anything.
    cls  = part ? 'part' : 'bad';
    help = (part ? 'THE OFFLINE DOWNLOAD WAS STOPPED BEFORE IT FINISHED. '
                 : 'THE OFFLINE DOWNLOAD DID NOT FINISH. ') + (j.why || BOOTFETCH.why)
         + ' Blank squares on this map are imagery that never arrived, not open water. Open the '
         + 'LAYERS panel for the source-by-source account'
         + (part ? ', or press DOWNLOAD NOW to pick up from exactly where it stopped.' : '.');
  }
  const key = text+'|'+cls;
  if(key === CRT._fetchBadge) return;
  CRT._fetchBadge = key;
  el.innerHTML = text;
  el.className = 'crt-fetch-badge' + (cls?(' '+cls):'') + (text?' on':'');
  if(help){ el.dataset.help = help; el.title = help; el.setAttribute('aria-label', help); }
}

function crtTogglePanel(force){
  const p = $('crt-panel'); if(!p) return;
  CRT.open = (force===undefined) ? !CRT.open : !!force;
  p.classList.toggle('on', CRT.open);
  const b = $('map-crt-toggle');
  if(b){ b.classList.toggle('on', CRT.open); liveTitle(b, CRT.open ? 'panel open' : 'panel closed'); }
  if(CRT.open){ crtRenderNet(); crtRenderRows(); crtRenderBadge(); crtRenderFetch(); }
}

function crtInit(){
  crtBuildPanel();
  crtRenderFetch();
  const b = $('map-crt-toggle');
  if(b) b.addEventListener('click', (e)=>{ e.stopPropagation(); crtTogglePanel(); });
  const x = $('crt-close');
  if(x) x.addEventListener('click', (e)=>{ e.stopPropagation(); crtTogglePanel(false); });
  const r = $('crt-refresh');
  // REFRESH RE-READS BOTH STORES. The Trust's layers and the LIDAR bank tiles are
  // separate stores on the same handheld with separate ways of being missing, and a
  // refresh button that read one of them would leave the operator pressing it at a bank
  // row that never changes.
  if(r) r.addEventListener('click', (e)=>{ e.stopPropagation();
                                           crtLoadAll('operator asked'); bankLoad('operator asked'); });
  /* THE CREDIT, ONE TAP AWAY. The licence asks to be shown wherever the data is; it does
     not ask to be pinned across the panel. Left open it took a quarter of the height and
     pushed the hazard list into a scroll strip, so the credit was crowding out the
     safety information it sits beside. aria-expanded carries the state for a screen
     reader, which is also what the CSS colours the button from. */
  const ci = $('crt-credit-toggle'), cb = $('crt-credit');
  if(ci && cb) ci.addEventListener('click', (e)=>{
    e.stopPropagation();
    const open = cb.classList.toggle('is-hidden') === false;
    ci.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  // Both reads are started here rather than inside crtLoadAll's hot path; crtLoadAll
  // awaits the seen record itself, so an area that arrives before this settles is
  // still classified against the real history and not against an empty object.
  Promise.all([crtLoadPrefs(), crtSeenReady()])
    .then(()=>{ CRT.ready=true; crtRenderRows(); crtLoadAll('boot');
                // AFTER THE PREFERENCES, not before: bankIsOn() reads CRT.prefs, and a
                // bank index read on an empty prefs object would render the row ON for
                // an operator who switched it off last session and then flip under them.
                bankLoad('boot'); });
  // A CANNOT TELL is a question, not a verdict: the Pi comes back, the tether is
  // replugged, and the layers should appear without the operator having to know
  // there is a refresh button. Bounded and slow — this is a background retry, not
  // a poll, and it never runs while a fetch is already in flight.
  //
  // NOT DOWNLOADED is not a question, so it is not re-asked on the clock: re-issuing
  // the whole index-and-layers walk every 30 s to be told again that nothing has been
  // downloaded is a poll whose answer cannot change on its own. What changes it is a
  // DOWNLOAD, and the fetch that performs one reloads the layers when it finishes.
  // A Pi appearing is no longer the event: the Pi does not hold the charts.
  setInterval(()=>{
    // NO LONGER GATED ON AN AREA. It used to be `if(!CRT.area) return`, which meant a
    // national store that went quiet on a console with no launch point was never
    // re-asked — the one machine most likely to be sitting on a bench waiting for the
    // launch fetch to finish.
    if(CRT._busy) return;
    // THE BANK LAYER IS RETRIED ON THE SAME RULE AND ONLY ON IT: a store that gave no
    // answer at all is a question worth re-asking, and one that answered "I hold none of
    // this" is not — re-issuing that request every 30 s is a poll whose answer cannot
    // change without a bootstrap run, and the bootstrap reloads the page's data itself.
    if(bankStatus()==='unavailable') bankLoad('retrying the bank layer, which could not be asked for');
    const held = (s)=>crtAll().some(e=>{ const st=CRT.state[e.id]; return st && st.status===s; });
    if(held('unavailable')) crtLoadAll('retrying the layers that could not be asked for');
    // A DOWNLOAD THAT STARTED AFTER THIS CONSOLE LOOKED. The launch fetch is begun by
    // the launcher, so it can perfectly well begin a few seconds after the page loads —
    // and a console that had already decided "nothing is downloaded" would sit there
    // saying so for the rest of the session. One cheap read on the slow timer, only
    // while the store is not here.
    else if(!CRT.net.ok && !crtDownloading())
      crtFetchDownloadState().then(d=>{
        if(d.running){ crtWatchDownload(); crtLoadAll('the national chart download has started'); }
      });
  }, CRT_API.retryMs);
  LOG.state('CRT chart layers initialised ('+CRT_LAYERS.length+' known layers, hazards always on, '
          + 'every layer drawn by default)');
}

/* ============================================================================
   DRAWING
   ============================================================================ */
const CRT_C = {
  // RED IS THE HAZARD, and it is the whole signal. It used to be orange with a dashed
  // standoff ring drawn round every mark, and with the national set on that put eight
  // overlapping rings across one screen — a spirograph that buried the canal it was
  // annotating. Colour survives clutter; a ring that is drawn around everything stops
  // meaning anything, the same way an alarm that fires on a healthy console does.
  hazard:'#ff3b57', hazardDim:'rgba(255,59,87,.55)',
  ops:'#4dffa6',    opsDim:'rgba(77,255,166,.6)',
  extra:'rgba(200,170,255,.75)',
  ink:'#0c0118',
  surveyEdge:'rgba(255,255,255,.55)',
};
function _crtColor(e){ return (e.tier===1 || e.hazardish) ? CRT_C.hazard : e.tier===2 ? CRT_C.ops : CRT_C.extra; }

/* ============================================================================
   DRAWING EVERYTHING AT ONCE WITHOUT MAKING SOUP.

   THE PROBLEM THIS SOLVES, IN ONE SENTENCE: with every layer on, a national dataset
   drawn the old way — one glyph per feature, one black-cased stroke per line, in three
   tiers — puts a mooring, a milepost, a towpath and a planning-buffer outline over the
   weir you came to keep away from. An unreadable map is its own kind of dishonesty: the
   data is all there and none of it can be used, which is a worse state than a map that
   admits it is missing something.

   IT IS SOLVED IN THE DRAWING, NEVER BY SWITCHING A LAYER OFF, and it is solved by
   EXTENDING the vocabulary the tiers already speak rather than inventing a second one.
   The tiers already say shape (octagon / rounded square / dot), colour (hazard orange /
   operations green / extras violet), size and order. Four things are added, all of them
   read off the tier and the geometry, and none of them a new taxonomy to learn:

     1  FOUR BANDS, NOT THREE TIERS. The band comes from the GEOMETRY as well as the
        tier, because a polygon and a point are not the same kind of mark however
        important the layer is. Areas wash the bottom, line work goes above them, then
        the point marks in tier order, hazards last and loudest. So the Trust's own
        centreline and the planning-buffer polygons — which cover the whole screen —
        can never be drawn over a lock.

     2  WEIGHT AND OPACITY GRADED BY HOW MUCH THE MARK MATTERS TO A SMALL TETHERED ROV.
        A hazard is full strength with a dark halo so it reads over anything. Operations
        are lighter. Extras are lighter again and smaller. Area washes are barely there:
        a tint you can see the imagery through, because the claim they make ("this is
        inside a consultation zone") is not one you steer on.

     3  DECLUTTERING, WHICH IS THE ONE THAT MAKES A NATIONAL DATASET READABLE AT AREA
        ZOOM. Marks that would land on top of each other are drawn ONCE with a count on
        them. That is not hiding: nothing is dropped, the row says how many were merged,
        and zooming in separates them. The grid is coarser for the quieter tiers, so a
        crowd of moorings collapses long before a crowd of locks does — and the hazard
        grid is tight enough that hazards only ever merge when they genuinely overlap.

     4  A VERTEX BUDGET AND SCREEN-SPACE DECIMATION for line and polygon work, because
        1,296 planning-buffer polygons at 82 kB each is millions of coordinates and no
        browser projects those at 10 Hz. Sub-pixel detail is skipped (it could not be
        seen) and there is a hard ceiling per layer per frame that the row reports when
        it bites.

   WHAT IS DELIBERATELY NOT DONE: no layer is dimmed to nothing, no layer is skipped at
   low zoom, and no layer is quietly excluded. If a mark cannot be drawn legibly it is
   drawn small and merged with its neighbours and COUNTED — the operator can always see
   that something is there and zoom into it.
   ============================================================================ */
/* Which of the four bands a piece of geometry belongs in. Read off the tier and the
   shape, so adding a layer to the table cannot forget to answer it. */
const CRT_BAND = {AREA:0, LINE:1, MARK:2, HAZARD:3};
/* HOW COARSE THE DECLUTTER GRID IS, in multiples of the mark's own radius. A cell of
   2.2r is barely more than the mark itself — hazards merge only when they would
   physically overlap — where 4.4r on the extras means a dozen mileposts along a mile
   of towpath become one mark with "12" on it at area zoom, and separate again as soon
   as there is room for them. */
function _crtDeclutterCell(e, r){
  const k = (e.tier===1 || e.hazardish) ? 2.2 : e.tier===2 ? 3.2 : 4.4;
  return Math.max(6, k*r);
}
/* THE MARK'S RADIUS AT THIS ZOOM. Hazards barely shrink; the quieter tiers shrink
   more as the view widens, which is most of what stops a wide view turning to gravel.
   `ppm` is device pixels per metre — small when zoomed out. */
function _crtMarkRadius(e, dpr, ppm){
  const t1 = (e.tier===1 || e.hazardish);
  const base = (t1 ? 9 : e.tier===2 ? 7 : 5.5) * dpr;
  // Below about 0.06 px/m the view is kilometres wide. Shrink the quiet tiers toward
  // a dot; keep the hazards nearly full size, because that is the whole point of them.
  const wide = (typeof ppm==='number' && ppm>0 && ppm<0.06);
  if(!wide) return base;
  return t1 ? base*0.85 : e.tier===2 ? base*0.7 : base*0.55;
}
/* How much ink a band is allowed. Tuned by band rather than by layer so a layer added
   next year inherits a budget instead of being the one that stalls the frame. */
const CRT_INK = {
  vertexBudget: [4000, 14000, 0, 0],     // projected vertices per layer per frame, by band
  fillAlpha:    [0.10, 0, 0, 0],         // area wash: visible, and see-through
  lineAlpha:    [0.30, 0.50, 0, 0],
  markAlpha:    [0, 0, 0.85, 1.0],       // extras/ops are graded again below
};

/* Device pixels per metre for the frame being drawn. The imagery is laid down at
   dpr/scale metres per device pixel at the view centre (see tiles.js drawTiles),
   so this is the SAME number the tiles used — a standoff ring measured any other
   way would not sit where its own arithmetic says it does. */
function _crtPpm(dpr){ return dpr / ((typeof curScale==='function') ? curScale() : 1); }

function _crtParts(f){
  if(f._crtParts) return f._crtParts;
  const pts=[], lines=[], polys=[];
  (function walk(g){
    if(!g) return;
    const t=g.type, c=g.coordinates;
    if(t==='Point' && c) pts.push(c);
    else if(t==='MultiPoint' && c) c.forEach(p=>pts.push(p));
    else if(t==='LineString' && c) lines.push(c);
    else if(t==='MultiLineString' && c) c.forEach(l=>lines.push(l));
    else if(t==='Polygon' && c) polys.push(c);
    else if(t==='MultiPolygon' && c) c.forEach(p=>polys.push(p));
    else if(t==='GeometryCollection') (g.geometries||[]).forEach(walk);
  })(f.geometry || f);
  // One representative point per feature: where the glyph and its ring go. A weir
  // drawn as a line still gets exactly one mark, not one per vertex.
  let rep=null;
  if(pts.length) rep=pts[0];
  else if(lines.length && lines[0].length) rep=lines[0][Math.floor(lines[0].length/2)];
  else if(polys.length && polys[0].length && polys[0][0].length){
    const ring=polys[0][0]; let sx=0, sy=0;
    for(const c of ring){ sx+=c[0]; sy+=c[1]; }
    rep=[sx/ring.length, sy/ring.length];
  }
  f._crtParts = {pts, lines, polys, rep};
  return f._crtParts;
}
/* THE PUBLISHED ANGLE OF ONE FEATURE, OR NOTHING.

   `angle` is the ONLY direction-carrying field anywhere in the Trust's 27 national
   layers, and it is on exactly two of them (locks and bridges). Read here rather than in
   the renderer so the two rules that make it safe live in one place:

   ZERO IS A REAL ANGLE. Locks range 0-392 and 0 is in the data, so `!a` would throw away
   a published bearing and draw the cannot-tell mark over it — the reverse of the lie this
   whole feature is guarding against, but a lie all the same.

   AND 392 DEGREES IS A REAL ROW. 17 locks exceed 360 (nothing else does), which is a
   quirk of whatever wrote the file rather than a second turn of the compass, so it is
   taken modulo 360. Anything that is not a finite number at all — absent, null, a blank
   string — comes back null, and null is what the plain mark is drawn from. */
function _crtAngleOf(f){
  const p = f && f.properties; if(!p) return null;
  let a = p.angle;
  if(typeof a === 'string'){ a = a.trim(); if(!a) return null; a = Number(a); }
  if(typeof a !== 'number' || !isFinite(a)) return null;
  a = a % 360;
  return (a < 0) ? a + 360 : a;
}
function _crtEach(gj, fn){
  if(!gj) return;
  const fs = gj.type==='FeatureCollection' ? (gj.features||[]) : (gj.type==='Feature' ? [gj] : []);
  for(let i=0;i<fs.length;i++) fn(fs[i], i);
}
/* One run of [lon,lat] traced into the current path. Returns false when none of it
   is anywhere near the frame, so a canal that leaves the screen costs a walk rather
   than a stroke — and the caller can skip the two or three paints that follow.

   SUB-PIXEL VERTICES ARE SKIPPED, and that is not a shortcut. `budget` is a live
   object shared across the whole layer this frame: a national planning-buffer layer is
   1,296 polygons averaging 82 kB each, which is millions of coordinates, and
   projecting all of them at 10 Hz is not something any browser does. A vertex closer
   than one device pixel to the last one cannot change the picture, so it is not
   projected; when the budget runs out the run stops and the caller reports it, because
   a shape drawn short and not said to be is the map-looks-complete lie in miniature. */
function _crtTraceRun(ctx, coords, W, H, m, budget){
  ctx.beginPath();
  let started=false, near=false, lx=0, ly=0;
  const step = budget ? (budget.step||1) : 0;
  for(const c of coords){
    if(budget){
      if(budget.left<=0){ budget.cut = true; break; }
      budget.left--;
    }
    const s=lonLatToScreen(c[1], c[0]); if(!s) continue;
    if(s[0]>-m && s[0]<W+m && s[1]>-m && s[1]<H+m) near=true;
    if(started && step && Math.abs(s[0]-lx)<step && Math.abs(s[1]-ly)<step) continue;
    started ? ctx.lineTo(s[0],s[1]) : (ctx.moveTo(s[0],s[1]), started=true);
    lx=s[0]; ly=s[1];
  }
  return started && near;
}

/* THE CHEVRON, AS A CLOSED PATH POINTING AT `phi` RADIANS IN SCREEN SPACE.

   An arrowhead with a NOTCHED tail, which is what makes the silhouette read as > or <
   rather than as a triangle: the notch is the concave point at (-0.40, 0). The anchor
   (x, y) — the feature's own position — sits inside the body at local (0, 0), which is
   where the letter goes, so a rotated mark still has its label in the middle of itself
   and the label never rotates with it. A rotated 'L' at 200 degrees is not a letter. */
function _crtChevronPath(ctx, x, y, r, phi){
  const P = [[1.05,0], [-0.15,0.92], [-0.80,0.92], [-0.40,0], [-0.80,-0.92], [-0.15,-0.92]];
  const c=Math.cos(phi), s=Math.sin(phi);
  ctx.beginPath();
  for(let i=0;i<P.length;i++){
    const px = x + r*(P[i][0]*c - P[i][1]*s);
    const py = y + r*(P[i][0]*s + P[i][1]*c);
    i ? ctx.lineTo(px,py) : ctx.moveTo(px,py);
  }
  ctx.closePath();
}
/* A PUBLISHED ANGLE, TURNED INTO THE DIRECTION IT POINTS ON THIS SCREEN.

   Two conversions, and both of them have to be here or the mark points somewhere the
   data never said. `angle` is read as a COMPASS BEARING — degrees clockwise from north,
   which is what a rotation field on a north-up ArcGIS layer means — so a bearing t points
   along (sin t, -cos t) in a north-up canvas, i.e. at canvas angle t - 90 degrees. Then
   the map's own rotation is added, because the collapsed radar is HEADING-UP: north is
   not up there, and a chevron drawn at the raw bearing would swing away from the canal it
   is annotating the moment the sub turned. TILES.last.rot is the same rotation the
   imagery and every other overlay were laid down with. */
function _crtChevronPhi(angle){
  const rot = (typeof TILES!=='undefined' && TILES.last) ? (TILES.last.rot||0) : 0;
  return (angle - 90) * Math.PI/180 + rot;
}

/* THE KEEP-AWAY MARK. Shape first, colour second — the same rule the leak drop and
   the ROV glyph follow, and for the same reason: an operator who cannot pick orange
   out of green still has to be able to tell a lock from a slipway.

   `angle` is the published bearing for this ONE feature, or null. Given one, the
   symmetric glyph is replaced by a chevron turned to it — not decorated with an arrow
   beside it, because a mark and a separate arrow are two things to read and this is one
   fact. Given null the glyph is exactly what it always was, which is the honest drawing
   of "no angle published": a shape with no front. */
function _crtMark(ctx, e, x, y, dpr, r, count, angle){
  const col=_crtColor(e), t1=(e.tier===1 || e.hazardish);
  if(!(r>0)) r = (t1 ? 9 : e.tier===2 ? 7 : 5.5) * dpr;
  const pointed = (typeof angle === 'number' && isFinite(angle));
  ctx.save();
  /* NO DISC BEHIND THE GLYPH. A dark circle was drawn under every hazard mark to lift
     it off whatever it landed on, and with everything on it read as a second ring around
     an already-round mark — chrome carrying no information, on the marks that most need
     to be read at a glance. The SHAPE does that job (octagon, square, dot) and the red
     does the rest; a casing stroke lifts it off the imagery without adding a ring. */
  ctx.globalAlpha = t1 ? 1 : (e.tier===2 ? 0.92 : 0.78);
  ctx.beginPath();
  if(pointed){                                        // chevron: the published angle
    _crtChevronPath(ctx, x, y, r*1.05, _crtChevronPhi(angle));
  } else if(t1){                                      // octagon: the stop-sign shape
    for(let i=0;i<8;i++){
      const a=(Math.PI/4)*i + Math.PI/8;
      const px=x+r*Math.cos(a), py=y+r*Math.sin(a);
      i ? ctx.lineTo(px,py) : ctx.moveTo(px,py);
    }
    ctx.closePath();
  } else if(e.tier===2){                              // rounded square
    ctx.rect(x-r, y-r, r*2, r*2);
  } else {                                            // plain dot
    ctx.arc(x,y,r,0,Math.PI*2);
  }
  ctx.fillStyle = t1 ? col : 'rgba(12,1,24,.75)';
  ctx.fill();
  ctx.lineWidth = (t1 ? 1.6 : e.tier===2 ? 1.3 : 1.1)*dpr;
  ctx.strokeStyle = t1 ? CRT_C.ink : col;
  ctx.stroke();
  // THE LETTERS ONLY WHERE THEY FIT. Below about 6 device pixels the mark is smaller
  // than its own label and the label turns to a smudge that reads as a different
  // colour — worse than no label, because a smudge is not legible AND hides the shape.
  const room = r >= 6*dpr;
  if(e.mark && room && (t1 || e.tier===2)){
    ctx.fillStyle = t1 ? CRT_C.ink : col;
    // A CHEVRON HAS LESS BODY THAN AN OCTAGON, so the letter comes down a point inside
    // one. It stays — "locks keep their L" is the requirement, and a chevron with no
    // letter on it is a direction with nothing saying what is pointing.
    ctx.font = '800 '+((e.mark.length>1 ? 7.5 : (pointed ? 8.5 : 9.5))*dpr)+'px '+
               ((typeof getComputedStyle==='function' && getComputedStyle(document.body).fontFamily) || 'sans-serif');
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(e.mark, x, y+0.5*dpr);
  }
  /* THE COUNT ON A MERGED MARK, and it is the whole reason decluttering is not
     hiding. This mark stands for `count` features that would have landed on top of
     each other at this zoom. Saying so — on the glass, not only in the panel — is what
     lets an operator see that there is more here than one thing and zoom into it. */
  if(count>1){
    const rr = Math.max(4.5*dpr, r*0.62);
    const bx = x + r*0.95, by = y - r*0.95;
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(bx, by, rr, 0, Math.PI*2);
    ctx.fillStyle = CRT_C.ink; ctx.fill();
    ctx.lineWidth = 1*dpr; ctx.strokeStyle = col; ctx.stroke();
    ctx.fillStyle = col;
    ctx.font = '800 '+Math.max(6.5*dpr, rr*1.25)+'px '+
               ((typeof getComputedStyle==='function' && getComputedStyle(document.body).fontFamily) || 'sans-serif');
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(count>99 ? '99+' : String(count), bx, by+0.5*dpr);
  }
  ctx.restore();
}
/* THE STANDOFF RING IS GONE. It drew a dashed circle of `standoffM` metres around
   every tier-1 mark, which was defensible when a handful of hazards were on screen and
   became unreadable the moment the whole network was: eight rings deep enough to hide
   the centreline under them. The keep-away distance is still in the layer's written
   explanation, where it can be read as a number instead of guessed from a radius, and
   the mark itself is now RED, which says "hazard" without covering anything up. */

/* ONE LAYER, ONE BAND.
   band 0  AREAS      polygon fills. A wash you can see the imagery through, no glyphs.
   band 1  LINES      line work and polygon outlines. Thin, graded by tier, no casing
                      except on a hazard — a black-cased line for every towpath in the
                      country is what turned the old drawing into a net over the map.
   band 2  MARKS      the point glyphs for tiers 2 and 3, decluttered.
   band 3  HAZARDS    tier-1 glyphs and their standoff rings, decluttered barely at
                      all, drawn last and loudest with a dark halo.

   The counts written back onto the state are what the panel row reports, and they are
   kept apart on purpose: `drawn` is marks placed, `inview` is features that reached
   the glass, `merged` is how many of those marks stand for more than one feature, and
   `capped` is the only case where this console stopped short of the data it holds. */
/* WHAT SHAPES THIS LAYER ACTUALLY CONTAINS, worked out once per loaded body and cached
   on the state beside it. Without this, a 7,691-feature point layer is walked three
   times a frame to discover twice over that it holds no polygons and no lines — 23,000
   iterations at 10 Hz to draw nothing. Invalidated by being stored on the state object,
   which is replaced wholesale every time a body is fetched. */
function _crtKinds(st){
  if(st._kinds) return st._kinds;
  const k = {poly:false, line:false, point:false};
  _crtEach(st.data, (f)=>{
    if(k.poly && k.line && k.point) return;
    const p=_crtParts(f);
    if(p.polys.length) k.poly = true;
    if(p.lines.length) k.line = true;
    if(p.rep) k.point = true;
  });
  st._kinds = k;
  return k;
}
function _crtDrawLayerBand(ctx, e, band, dpr, ppm, W, H, tally){
  const st = CRT.state[e.id];
  if(!st || st.status!=='present' || !st.data) return;
  const col=_crtColor(e), t1=(e.tier===1 || e.hazardish);
  const m = 120*dpr;
  const kinds = _crtKinds(st);
  if(band===CRT_BAND.AREA && !kinds.poly) return;
  if(band===CRT_BAND.LINE && !kinds.line && !(t1 && kinds.poly)) return;

  if(band===CRT_BAND.AREA || band===CRT_BAND.LINE){
    const budget = {left: CRT_INK.vertexBudget[band], step: dpr, cut:false};
    ctx.save();
    ctx.lineJoin='round'; ctx.lineCap='round';
    _crtEach(st.data, (f)=>{
      if(budget.left<=0) return;
      const p=_crtParts(f);
      if(band===CRT_BAND.AREA){
        // THE WASH. One fill, no outline: the planning buffer and the reservoirs are
        // claims about a REGION, and a bright edge round every one of them at national
        // density is a cage. The tint is enough to notice and weak enough to read the
        // imagery and every mark above it straight through.
        if(!p.polys.length) return;
        for(const poly of p.polys){
          for(const ring of poly){
            if(!_crtTraceRun(ctx, ring, W, H, m, budget)) continue;
            ctx.globalAlpha = CRT_INK.fillAlpha[0]; ctx.fillStyle = col; ctx.fill();
          }
        }
        return;
      }
      // LINE WORK. Hazard lines (a weir across the cut, a tunnel) keep the dark casing
      // that makes them readable over imagery; everything else is a plain thin stroke,
      // because a cased line for all 7,691 towpath features is a net.
      ctx.globalAlpha = t1 ? 0.95 : (e.tier===2 ? CRT_INK.lineAlpha[1] : 0.34);
      const strokeRun=(coords)=>{
        if(!_crtTraceRun(ctx, coords, W, H, m, budget)) return;
        if(t1){
          ctx.strokeStyle='rgba(0,0,0,.55)'; ctx.lineWidth=5*dpr; ctx.stroke();
          ctx.strokeStyle=col;               ctx.lineWidth=2.2*dpr; ctx.stroke();
        } else {
          ctx.strokeStyle=col; ctx.lineWidth=(e.tier===2 ? 1.4 : 1.0)*dpr; ctx.stroke();
        }
      };
      for(const l of p.lines) strokeRun(l);
      // A polygon's OUTLINE only where the layer is not an area wash — a hazard drawn
      // as a polygon still needs its edge, and the wash above already carried the rest.
      if(t1) for(const poly of p.polys) for(const ring of poly) strokeRun(ring);
    });
    ctx.restore();
    if(budget.cut) tally.capped = true;
    return;
  }

  /* MARKS, DECLUTTERED. Two passes: bin every representative point that reaches the
     glass into a screen grid, then draw one mark per occupied cell with the count on
     it. Nothing is dropped and nothing is hidden — a cell holding nine mileposts draws
     one milepost mark wearing a 9, and zooming in gives them each their own cell.

     THE GRID IS IN SCREEN PIXELS, which is what makes this self-tuning: the same code
     merges nothing at all when the sub is working one pound at z18 and merges hard
     when the whole area is on screen, with no zoom thresholds to get wrong. */
  const r = _crtMarkRadius(e, dpr, ppm);
  const cell = _crtDeclutterCell(e, r);
  const bins = new Map();
  const angled = !!e.angleWhat;                 // only locks and bridges publish one
  let inview = 0, hasAngle = 0, noAngle = 0;
  _crtEach(st.data, (f)=>{
    const p=_crtParts(f);
    if(!p.rep) return;
    const s=lonLatToScreen(p.rep[1], p.rep[0]);
    if(!s || !(s[0]>-m && s[0]<W+m && s[1]>-m && s[1]<H+m)) return;
    inview++;
    const a = angled ? _crtAngleOf(f) : null;
    if(angled){ if(a===null) noAngle++; else hasAngle++; }
    const k = Math.floor(s[0]/cell)+'|'+Math.floor(s[1]/cell);
    const b = bins.get(k);
    /* A MERGED MARK LOSES ITS CHEVRON, AND THAT IS THE POINT. This bin now stands for
       several features whose published angles are not the same number, and one chevron
       drawn over nine locks would be pointing on behalf of eight it never asked. The
       plain symmetric glyph with the count on it says exactly what is true: there are
       this many here and this console is not claiming a direction for them. Zoom in and
       they separate into their own cells and get their own chevrons back. */
    if(b){ b.n++; b.a = null; }
    else bins.set(k, {x:s[0], y:s[1], n:1, a:a});
  });
  let drawn=0, merged=0, chevrons=0;
  bins.forEach(b=>{
    if(drawn >= CRT_API.maxDraw){ tally.capped = true; return; }
    // The ring is the hazard's own and is drawn under the mark, once per cell: a
    // standoff drawn per merged feature would be the same circle painted nine times.
    _crtMark(ctx, e, b.x, b.y, dpr, r, b.n, b.a);
    drawn++;
    if(b.n>1) merged++;
    if(b.a!==null && b.a!==undefined) chevrons++;
  });
  st.drawn = drawn; st.inview = inview; st.merged = merged;
  st.chevrons = chevrons; st.hasAngle = hasAngle; st.noAngle = noAngle;
  st.capped = !!tally.capped;
}

/* THE DRAW ORDER IS A SAFETY ORDER, and it is now four bands rather than three tiers.
   Every area wash on the whole map goes down first, then every line, then the quiet
   marks, then the hazards — so a planning-buffer polygon or the Trust's centreline
   cannot land on top of a lock however the layers are ordered in the table. Before
   this it was per-LAYER (all of tier 3, then all of tier 2, then all of tier 1), which
   meant a tier-2 line was drawn over a tier-3 line and everything was drawn over
   everything in its own tier. With one or two layers on that was invisible. With all
   of them on it is the difference between a map and a mess. */
function crtDraw(ctx, dpr){
  if(typeof TILES==='undefined' || !TILES.last) return;      // no projection this frame
  const W=ctx.canvas.width, H=ctx.canvas.height, ppm=_crtPpm(dpr);
  ctx.setTransform(1,0,0,1,0,0);
  // The window follows the map. One bbox comparison a frame; everything expensive
  // behind three guards inside it.
  try{ const w = crtEnsureWindow(); if(w && w.catch) w.catch(()=>{}); }catch(err){}
  const live = crtAll().filter(e=>e.kind!=='depth' && crtIsOn(e.id));
  live.forEach(e=>{ const st=CRT.state[e.id]; if(st){ st.drawn=0; st.inview=0; st.merged=0; st.capped=false;
                                                     st.chevrons=0; st.hasAngle=0; st.noAngle=0; } });
  const tally = {};
  for(const band of [CRT_BAND.AREA, CRT_BAND.LINE, CRT_BAND.MARK, CRT_BAND.HAZARD]){
    // WITHIN a band, quiet first: tier 3, then 2, then 1. Two hazards on one screen
    // still order among themselves the way they always did.
    for(const tier of [3,2,1]){
      for(const e of live){
        if(e.tier!==tier) continue;
        const t1 = (e.tier===1 || e.hazardish);
        if(band===CRT_BAND.MARK && t1) continue;              // hazards are the band above
        if(band===CRT_BAND.HAZARD && !t1) continue;
        tally[e.id] = tally[e.id] || {capped:false};
        _crtDrawLayerBand(ctx, e, band, dpr, ppm, W, H, tally[e.id]);
      }
    }
  }
}

/* ---- DEPTH -------------------------------------------------------------- */
/* The hatch that says NOMINAL. Built once per canvas context and cached on it:
   a pattern fill costs one fill call, where clipping every cell and stroking
   lines inside it would cost three per cell at 10 Hz. */
function crtHatch(ctx, dpr){
  if(ctx._crtHatch) return ctx._crtHatch;
  const s = Math.max(6, Math.round(7*dpr));
  const c = document.createElement('canvas'); c.width=s; c.height=s;
  const g = c.getContext('2d');
  // Strong enough to READ at a glance over satellite imagery. A first pass drew it at
  // a quarter of this and the hatch simply vanished: the nominal cells then looked
  // exactly like the surveyed ones with the outline turned off, which is precisely
  // the confusion — published versus measured — that the texture exists to prevent.
  g.strokeStyle='rgba(255,255,255,.85)'; g.lineWidth=Math.max(1, dpr*0.9);
  // THREE diagonals, at -s, 0 and +s. One is not enough and the reason is worth
  // writing down: the obvious `moveTo(-s,s) lineTo(s,-s)` crosses the tile at exactly
  // one corner pixel, so the pattern tiled out to an empty fill and the nominal cells
  // came out looking identical to the surveyed ones. The two offset copies are what
  // makes the repeat seamless rather than a row of disconnected strokes.
  g.beginPath();
  for(const o of [-s, 0, s]){ g.moveTo(o-1, s+1); g.lineTo(o+s+1, -1); }
  g.stroke();
  try{ ctx._crtHatch = ctx.createPattern(c, 'repeat'); }catch(e){ ctx._crtHatch = null; }
  return ctx._crtHatch;
}
/* THE DEPTH, UNDER WHATEVER NAME THE PI WROTE IT.

   The two producers name it after what it MEANS, which is right and is the reason
   this reader has to know both: api/nav/soundings.py writes `lower_bound_m` (the
   deepest the sub reached while it was on the bottom — a floor under the water, not
   the bed) and api/nav/nominal.py writes `nominal_depth_m` (the published figure).
   A first pass here read only `depth_m` and found neither, so every cell fell
   through to the no-depth grey: the whole twelve-colour scale switched itself off
   and the two layers were left differing by texture alone, with nothing anywhere
   saying the numbers had been dropped. Caught by client/tests/suites/crt-overlay.js. */
function _crtDepthOf(f){
  const p = (f && f.properties) || {};
  const v = [p.lower_bound_m, p.nominal_depth_m, p.depth_m, p.depth, p.value, p.d]
              .find(x=>typeof x==='number' && isFinite(x));
  return (typeof v==='number') ? v : null;
}
function _crtDepthColor(d){
  const max = (CONFIG.map && CONFIG.map.maxDepthColorM) || 6;
  return (typeof rampColor==='function') ? rampColor((d||0)/max) : '#1f9dff';
}
/* HOW BIG ONE DEPTH CELL IS, IN METRES.

   NOT `properties.cell`. That field is the cell's INDEX along the line — 0, 1, 2 —
   and reading it as a length drew cell 0 as a point and cell 1 one metre across on a
   survey binned in tens. The stretch the sub had actually sounded then came out
   smaller than it is, and the unsounded water beside it looked like water somebody
   had simply not coloured in — which is the exact confusion between "no data" and
   "nothing here" this whole overlay exists to prevent.

   `from_m`/`to_m` are the cell's own extent along the channel and are the most
   local answer available; the survey's `cell_m` lives on the FeatureCollection, one
   level up from where a per-feature reader looks, so it is passed in by the caller
   when there is one. Caught by client/tests/suites/crt-overlay.js. */
function _crtCellM(f, layerCellM){
  const p = (f && f.properties) || {};
  if(typeof p.cell_m==='number' && p.cell_m>0) return p.cell_m;
  if(typeof p.from_m==='number' && typeof p.to_m==='number' && p.to_m>p.from_m) return p.to_m - p.from_m;
  if(typeof layerCellM==='number' && layerCellM>0) return layerCellM;
  return 5;
}
/* HOW WIDE THE NOMINAL BAND IS DRAWN, and why a made-up number is allowed here.

   A nominal feature is not a cell. api/nav/nominal.py passes the WATERWAY SECTION
   geometry straight through — one line per section, hundreds of metres to a couple
   of kilometres of cut each — and attaches the published figure to the whole of it.
   Nothing anywhere publishes how WIDE the channel is, so the band this console
   strokes along that line is a drawing convention of ours, in the same family as the
   standoff ring: the LENGTH is the section's own geometry and is a fact, the WIDTH
   is us saying "about the width of a cut" so the claim is visible over the water it
   is about. Seven metres is a narrow canal at the surface; the row says in its own
   words that this number is ours and not a survey.

   The floor in device pixels is not cosmetic. Zoomed out far enough the band would
   be sub-pixel, and a layer that reports SHOWN while painting nothing is the same
   lie as a layer that reports SHOWN while painting 0.7% of what it describes. */
const CRT_NOMINAL_BAND_M = 7;

/* A depth cell as a screen path: a polygon if the server sent one, otherwise a
   square of the cell's own size centred on it.

   THIS IS FOR CELLS, and a nominal SECTION is not one — see crtDrawDepth, which
   strokes the section's line instead. Sent a 6.3 km section, this falls through to
   the representative point and _crtCellM's 5 m default and paints one 5 m square at
   the middle of the whole length: nine squares for a canal, against a row that said
   the layer was drawn everywhere. */
function _crtCellPath(ctx, f, dpr, ppm, layerCellM){
  const p=_crtParts(f);
  if(p.polys.length){
    ctx.beginPath();
    let any=false;
    for(const poly of p.polys) for(const ring of poly){
      let started=false;
      for(const c of ring){ const s=lonLatToScreen(c[1], c[0]); if(!s) continue;
        started ? ctx.lineTo(s[0],s[1]) : (ctx.moveTo(s[0],s[1]), started=true); any=true; }
      if(started) ctx.closePath();
    }
    return any;
  }
  if(p.rep){
    const s=lonLatToScreen(p.rep[1], p.rep[0]); if(!s) return false;
    const half = _crtCellM(f, layerCellM) * ppm / 2;
    ctx.beginPath(); ctx.rect(s[0]-half, s[1]-half, half*2, half*2);
    return true;
  }
  return false;
}
/* THIS SESSION'S OWN SOUNDINGS.

   The Pi's surveyed layer is built from dive logs that have been written and
   closed; the water the sub is in right now is not in it yet, and that is the
   water the operator is actually looking at. So the live track is binned into the
   same cells and drawn with the same SURVEYED treatment, because it is the same
   quantity measured by the same sensor — the deepest the hull got in that cell,
   which is a floor under the water depth and not the depth of the bed. The row in
   the panel counts these separately from the saved ones so the two can never be
   confused for each other. */
const CRT_LIVE_CELL_M = 3;               // the live bin, metres — and the drawn cell's side

/* IS THE DEPTH ON THIS CONSOLE A MEASUREMENT, OR THE MODEL?

   THE DEFECT THIS CLOSES: crtLiveCells binned MAP.track wherever depth > 0.05 with no
   check on where the depth came from, and painted the result with the SURVEYED
   treatment — solid fill, white outline, the drawing that on this console means "the
   sub touched this". Drive the bench simulator and it painted measured-style survey
   cells over water nothing has ever been in. That is the exact class of claim the rest
   of the file refuses to make.

   NOT A NEW RULE, and deliberately not one. vehicleLinked() is the console's own
   one-second "a real hull is on the other end and nothing here may be synthesised over
   it" test — the same test map.js's mapTick uses to decide whether the client
   integrator is allowed to advance the sub at all, so this agrees with the thing that
   produced the track by construction. vehicleHasSensors() is the second half: a Pi
   running its own bench mock sends telemetry with `mock:true`, which main.js already
   treats as simulated and flags with the SIM badge. A mocked hull is a simulator with
   a longer wire, and a floor under the water is not something either of them measured. */
function crtLiveMeasured(){
  return (typeof vehicleLinked==='function' && vehicleLinked())
      && (typeof vehicleHasSensors==='function' && vehicleHasSensors());
}

/* THE FOUR CORNERS OF A LIVE CELL, as a polygon in lat/lon.

   NOT TWO. Each cell used to be an AXIS-ALIGNED screen rect built over the min/max of
   two DIAGONALLY OPPOSITE projected corners — the SW and the NE and nothing else. The
   axis-aligned extent of a diagonal is not the extent of the square it belongs to: at
   heading θ the rect comes out 3·ppm·|cosθ+sinθ| by 3·ppm·|sinθ−cosθ|, whose area is
   9·ppm²·|cos 2θ|. That is the full 9 m² at 0° and 90°, half of it at 30° and 60°, and
   EXACTLY ZERO at 45°. Under the radar's heading-up rotation the whole survey overlay
   therefore thinned to slivers and swelled again as the sub turned, and panning did it
   too because a pan re-projects every corner.

   Four corners, filled as a quad, makes the painted area a property of the CELL and
   not of the compass. The projection is _crtCellPath's — the same one every other
   depth cell on this map already goes through — because a second copy of the
   lat/lon-to-screen walk living here is a second one to get wrong. */
function _crtLiveCellPoly(cell){
  const h = CRT_LIVE_CELL_M/2, o = MAP.origin;
  const ring = [[-h,-h], [h,-h], [h,h], [-h,h], [-h,-h]].map(d=>{
    const g = toLatLon(cell.x+d[0], cell.y+d[1], o.lat, o.lon);
    return [g.lon, g.lat];                       // GeoJSON order, as _crtCellPath reads it
  });
  return { type:'Feature', properties:{}, geometry:{type:'Polygon', coordinates:[ring]} };
}

function crtLiveCells(){
  if(!MAP.hasOrigin || !MAP.origin || !MAP.track.length) return [];
  // THE CACHE HOLDS METRES AND A LAT/LON RING, so it belongs to ONE datum. rebaseFrame
  // moves the origin and every track point together, which leaves both halves of a
  // cached cell describing water it is no longer about — the corners most of all,
  // because they were baked against the old origin. The origin is part of the key.
  const okey = MAP.origin.lat + ',' + MAP.origin.lon;
  if(CRT.live.cells && (performance.now()-CRT.live.at) < 1000
     && CRT.live.n===MAP.track.length && CRT.live.origin===okey) return CRT.live.cells;
  const c = CRT_LIVE_CELL_M, bins = new Map();
  for(const p of MAP.track){
    // THE GATE. Provenance is recorded ON THE POINT by pushTrack, at the moment the
    // depth arrived, rather than decided here from whatever the link happens to be
    // doing now — and that is the difference between a rule and a rule that works. A
    // track routinely mixes both: a real dive that loses the tether has simulated
    // points appended to it, and asking "is a hull linked?" at draw time would have
    // adopted those the moment it came back. It also means genuinely measured cells
    // STAY on the map after the link drops, which is right: the sub really did go
    // there, and that fact does not expire with the socket.
    if(p.measured !== true) continue;
    if(!(typeof p.depth==='number') || p.depth<=0.05) continue;   // at the surface it says nothing about the water
    const k = Math.floor(p.x/c)+'|'+Math.floor(p.y/c);
    const prev = bins.get(k);
    if(!prev || p.depth>prev.depth) bins.set(k, {x:(Math.floor(p.x/c)+0.5)*c, y:(Math.floor(p.y/c)+0.5)*c, depth:p.depth});
  }
  const out=[]; bins.forEach(v=>out.push(v));
  CRT.live = {cells:out, at:performance.now(), n:MAP.track.length, origin:okey};
  return out;
}
function crtDrawDepth(ctx, dpr){
  if(typeof TILES==='undefined' || !TILES.last) return;
  const ppm=_crtPpm(dpr);
  ctx.setTransform(1,0,0,1,0,0);

  // 1) NOMINAL — washed out and hatched, down the whole length of every section that
  //    carries a figure. A published claim, drawn over the water it is a claim about.
  //
  //    THE SECTION IS A LINE AND THE LENGTH IS THE POINT. This block used to hand
  //    every nominal feature to _crtCellPath, which is a CELL renderer: a whole
  //    waterway section came out as one 5 m square at its midpoint, so 6.3 km of cut
  //    was painted by nine squares — 0.7% of the channel — under a row that told the
  //    operator the layer was drawn everywhere. Unpainted water then looked like
  //    water nobody had guidance for, which is the exact "no data versus none here"
  //    confusion this whole overlay exists to prevent. So a line section is STROKED
  //    along its own geometry, and only a genuine polygon or point still goes through
  //    the cell path.
  const nom = crtEntry('depth_nominal'), nst = nom && CRT.state[nom.id];
  if(nom && crtIsOn(nom.id) && nst && nst.status==='present' && nst.data){
    const hatch = crtHatch(ctx, dpr);
    // The survey's own bin size lives on the FeatureCollection, not on the features.
    const cellM = nst.data.cell_m;
    const W=ctx.canvas.width, H=ctx.canvas.height, m=120*dpr;
    const bandPx = Math.max(2.5*dpr, CRT_NOMINAL_BAND_M * ppm);
    ctx.save();
    ctx.lineJoin='round'; ctx.lineCap='round';
    _crtEach(nst.data, (f)=>{
      const d=_crtDepthOf(f);
      // A section with no published figure is grey, not absent and not shallow: the
      // Trust records some lengths as not fully navigable and nominal.py withholds
      // the guideline rather than colouring a confident metre of water over them.
      const paint = (d===null) ? 'rgba(120,120,140,.5)' : _crtDepthColor(d);
      const p = _crtParts(f);
      if(!p.polys.length && p.lines.length){
        for(const l of p.lines){
          if(!_crtTraceRun(ctx, l, W, H, m)) continue;
          ctx.lineWidth = bandPx;
          ctx.globalAlpha = 0.28; ctx.strokeStyle = paint; ctx.stroke();
          if(hatch){ ctx.globalAlpha = 0.45; ctx.strokeStyle = hatch; ctx.stroke(); }
          // The dashed thread down the middle is the line the figure was actually
          // published against — the one part of this band that is not our invention.
          ctx.globalAlpha = 0.75; ctx.setLineDash([4*dpr,4*dpr]);
          ctx.strokeStyle='rgba(236,227,255,.55)'; ctx.lineWidth=1*dpr; ctx.stroke();
          ctx.setLineDash([]);
        }
        return;
      }
      if(!_crtCellPath(ctx, f, dpr, ppm, cellM)) return;
      ctx.globalAlpha = 0.28;
      ctx.fillStyle = paint;
      ctx.fill();
      if(hatch){ ctx.globalAlpha = 0.45; ctx.fillStyle = hatch; ctx.fill(); }
      ctx.globalAlpha = 0.75; ctx.setLineDash([4*dpr,4*dpr]);
      ctx.strokeStyle='rgba(236,227,255,.55)'; ctx.lineWidth=1*dpr; ctx.stroke();
      ctx.setLineDash([]);
    });
    ctx.restore();
  }

  // 2) SURVEYED — solid, outlined, opaque enough to read as measured. Saved
  //    cells from the Pi first, then this session's, which are the newest truth.
  const sur = crtEntry('depth_surveyed'), sst = sur && CRT.state[sur.id];
  if(sur && crtIsOn(sur.id)){
    ctx.save();
    if(sst && sst.status==='present' && sst.data){
      const cellM = sst.data.cell_m;
      _crtEach(sst.data, (f)=>{
        const d=_crtDepthOf(f);
        if(!_crtCellPath(ctx, f, dpr, ppm, cellM)) return;
        ctx.globalAlpha=0.62; ctx.fillStyle=(d===null)?'rgba(120,120,140,.7)':_crtDepthColor(d); ctx.fill();
        ctx.globalAlpha=0.9; ctx.strokeStyle=CRT_C.surveyEdge; ctx.lineWidth=1.2*dpr; ctx.stroke();
      });
    }
    // THIS SESSION'S OWN CELLS, in the SAME treatment as the Pi's saved survey above —
    // same alpha, same white edge, same width — because they are the same measurement
    // by the same sensor and a second-looking treatment would invite the reading that
    // they are worth less. crtLiveCells returns nothing at all unless the depth behind
    // them was measured, so in sim this loop is empty and the panel row says why.
    const live = crtLiveCells();
    if(live.length && MAP.origin){
      for(const cell of live){
        // Cached per cell: the ring is four toLatLon calls and the cells are rebinned
        // (and the cache dropped) whenever the track grows or the datum moves.
        const f = cell.poly || (cell.poly = _crtLiveCellPoly(cell));
        if(!_crtCellPath(ctx, f, dpr, ppm, CRT_LIVE_CELL_M)) continue;
        ctx.globalAlpha=0.62; ctx.fillStyle=_crtDepthColor(cell.depth); ctx.fill();
        ctx.globalAlpha=0.9; ctx.strokeStyle=CRT_C.surveyEdge; ctx.lineWidth=1.2*dpr; ctx.stroke();
      }
    }
    ctx.restore();
  }
}

/* The key for the two treatments, drawn under the depth colour ramp (map.js
   drawDepthLegend). The colours say how deep; these two swatches say how much the
   number behind the colour is worth, which is the distinction the whole console
   is built on. */
function crtDrawDepthKey(ctx, x, y, bw, dpr){
  const fam = (getComputedStyle(document.body).fontFamily || 'sans-serif');
  const h = Math.round(8*dpr);
  ctx.save();
  ctx.font = (9*dpr)+'px '+fam; ctx.textBaseline='middle'; ctx.textAlign='left';
  // nominal: washed + hatched + dashed
  ctx.globalAlpha=.28; ctx.fillStyle=_crtDepthColor(1.5); ctx.fillRect(x, y, bw, h);
  const hatch=crtHatch(ctx,dpr);
  if(hatch){ ctx.globalAlpha=.45; ctx.fillStyle=hatch; ctx.fillRect(x, y, bw, h); }
  ctx.globalAlpha=1; ctx.setLineDash([3*dpr,3*dpr]); ctx.strokeStyle='rgba(236,227,255,.55)';
  ctx.lineWidth=1; ctx.strokeRect(x+.5, y+.5, bw-1, h-1); ctx.setLineDash([]);
  ctx.fillStyle='rgba(236,227,255,.9)'; ctx.fillText('NOMINAL', x+bw+5*dpr, y+h/2);
  // surveyed: solid + outlined
  const y2 = y + h + Math.round(5*dpr);
  ctx.globalAlpha=.62; ctx.fillStyle=_crtDepthColor(1.5); ctx.fillRect(x, y2, bw, h);
  ctx.globalAlpha=1; ctx.strokeStyle=CRT_C.surveyEdge; ctx.lineWidth=1.2; ctx.strokeRect(x+.5, y2+.5, bw-1, h-1);
  ctx.fillStyle='rgba(236,227,255,.9)'; ctx.fillText('SURVEYED', x+bw+5*dpr, y2+h/2);
  ctx.restore();
  return (y2 + h) - y;                     // height consumed, so the caller can size its backdrop
}

/* ---- credits ------------------------------------------------------------
   The licence wants the words, exactly, wherever the data is used. They live in
   the panel footer (selectable, screen-readable, next to the layers themselves)
   and on the map's own attribution strip beside the imagery credit, which is
   where credits on this console already live. */
const CRT_ATTRIBUTION =
  'Contains Canal & River Trust data (c) Canal & River Trust, licensed under the Open Government Licence v3.0';
function crtAttribution(){ return CRT_ATTRIBUTION; }
/* Is any CRT-derived mark actually on screen? The imagery credit is drawn when
   imagery drew; this follows the same rule. */
function crtAnyPresent(){
  return crtAll().some(e=>{ const s=CRT.state[e.id]; return s && s.status==='present' && crtIsOn(e.id); });
}

/* ============================================================================
   THE LIDAR LAUNCH-BANK LAYER — WHICH BANK YOU COULD GET DOWN, AND WHICH IS A WALL.

   WHAT IT IS FOR. Satellite imagery of a canal is a dark blue ribbon between two
   textures, and nothing in it says which side you could carry a 5 kg sub and a drum of
   cable down to the water. This layer paints that on: Environment Agency LIDAR terrain,
   classified against the water level of the pound it is beside, in TWO colours and no
   more — amber for bank less than 2 m above the water, dark brown for everything higher
   and for the urban fabric behind it. It is built once per area on this handheld at
   bootstrap and drawn here as XYZ tiles in the same scheme the satellite imagery uses,
   so once it is on the disk it costs no network at all, in real mode or in the simulator.

   AMBER IS A GEOMETRIC FACT AND NOT PERMISSION, and that sentence is the layer. "The
   ground here is under 2 m above the water" is a measurement; "you can launch here" is a
   judgement involving a fence, a hedge, a landowner, a drop, whether a car can get within
   a hundred metres of it, and whether the edge is a bank or something you go through. The
   recon that validated this layer amber-classified a RAILWAY CUTTING, which is the
   cleanest possible demonstration: geometrically it is a low bank, operationally it is a
   place you will be arrested. Every sentence this layer says carries that distinction,
   because a colour that quietly came to mean "launch here" would be the most dangerous
   thing on this map.

   AND WATER IS NEVER PAINTED. Not a tint, not an edge, nothing: the imagery shows through
   unaltered on every water pixel, because anything drawn on the water would be read as a
   claim about what is under it and this layer knows nothing whatever about depth. The
   only thing that rides on the water is the detected-level label, which is a height of
   the SURFACE and says so in its own words.

   NO AREA IN ANY PATH, which is the same decision the national chart store made and for
   the same reason: the index reports WHICH areas are painted rather than taking one as a
   parameter, so a console with no launch point still learns what this handheld holds, and
   the map paints whatever it is looking at without first deciding which area that is.

   THE ABSENCES, AND THERE ARE SEVEN, because they are seven different things to do:

     HERE          this water is inside a painted area and the paint is on the glass
     PARTIAL       the area covering this view was painted with holes in it, or the view
                   runs off its edge — part of what you see is unpainted for a reason
                   about the DATA and not about the bank
     NOT HERE      the layer is painted for other areas and this water is not in one
     ABSENT        the handheld was read and no bank layer has been built on it
     UNREADABLE    the record beside an area's tiles is here and will not parse
     CANNOT BUILD  this machine cannot build the layer at all — numpy, scipy or Pillow
                   are not installed, or the module is not in this build. No download
                   fixes it, and the vehicle is in this state permanently by design
     NOT DOWNLOADED / CANNOT TELL   the map service itself said nothing, or nothing usable

   NEVER A SILENT FALLBACK TO BARE SATELLITE. Unpainted imagery and "no low banks here"
   are opposite claims and they look identical, which is exactly the failure the hazard
   layers' ABSENT/NONE MAPPED split exists to prevent — so this row always says which of
   those it is, in the same words the hazard rows use.
   ============================================================================ */

/* THE WIRE. Three paths, in one object, and api/nav/service.py's bank endpoints quote
   this object back as the reason they are spelled the way they are. That is deliberate:
   two halves of one feature spelling an endpoint or a field two ways is how this project
   has silently lost a feature five times, so there is exactly one statement of the
   contract and both halves point at it.

   THE INDEX IS THE GATE, exactly as it is for the chart layers, and here the reason is
   sharper. A tile that is not there is a TRANSPARENT HOLE, and a transparent hole over a
   canal bank reads as "no low bank here" — a survey result nobody produced. So this layer
   is never discovered by asking for tiles and seeing what comes back: the index is read
   first, it says per area what is painted and what is not, and only then is anything
   drawn. The tile TEMPLATE is quoted back by that document and used from there, so the
   day the endpoint moves the document says where to. */
const BANK_API = {
  index:  '/api/bank',                          // what bank paint this handheld holds
  tiles:  '/api/bank/tiles/{z}/{x}/{y}.png',    // fallback template; the index sends one
  pounds: '/api/bank/pounds',                   // ?bbox=W,S,E,N — the detected levels
  timeoutMs: 6000,
  // Below this many device pixels per metre a level label is wider than the pound it
  // names and the canal disappears under its own annotation. Suppressed there, and the
  // row says how many were suppressed — never silently.
  labelPpm: 0.05,
  maxLabels: 14,
  tileBudget: 400,
};
/* THE TWO COLOURS, AND THERE ARE ONLY TWO. The renderer that painted the tiles is
   configured with exactly these (api/nav/config.py lidar_colour_low / lidar_colour_high),
   so the panel key is painted from what the tiles were painted with rather than from a
   hex typed into a stylesheet that drifts away from them. There is deliberately NO
   elevation ramp: a ramp invites reading a height off a colour, and the only claim this
   layer makes is which side of the threshold a pixel falls. */
const BANK_AMBER = '#E39A2E';                   // bank UNDER the launch height above water
const BANK_BROWN = '#453016';                   // higher bank, and the urban fabric
/* The seen-record's key for the bank store, keyed the way CRT_NATIONAL_KEY is and for the
   identical reason: it has no area, so it needs a name no real cut can collide with. It
   is what lets a bank store that HAS answered here go loud when it stops. */
const BANK_SEEN_KEY = '*lidar-bank*';

const BANK = {
  asked:false, ok:false, nothing:false, why:'', at:0,
  status:'',                                    // the index's own word for itself
  tiles:'', poundsUrl:'', minzoom:null, maxzoom:null, thresholdM:null,
  vintage:'', attribution:'',
  libs:{ok:true, why:'', install:''},
  areas:[],                                     // {area,status,bbox,fetched,built,vintage,tiles,why}
  cache:new Map(), inflight:0,
  pounds:null, poundWin:null, poundBusy:false, poundAt:0, poundWhy:'',
  drew:0, tooWide:false, labels:0, labelsHidden:0, on:'',
  _busy:false, _row:'',
};

/* THE OPERATOR'S SWITCH, REMEMBERED WHERE EVERY OTHER LAYER'S IS.

   It rides in CRT.prefs — the same `crt.layers` object in IndexedDB the chart rows use —
   under the key `bank`, which no Trust layer can collide with because layer ids come from
   CRT_LAYERS and there is no row of that name. One store means one load, one save and one
   place a preference can be lost, rather than a second key written by one code path and
   read by none. Drawn by default like everything else: a layer that is held and hidden
   might as well not be held. */
function bankIsOn(){
  const p = CRT.prefs || {};
  return (typeof p.bank === 'boolean') ? p.bank : true;
}
function bankSetOn(on){
  CRT.prefs = CRT.prefs || {};
  CRT.prefs.bank = !!on;
  crtSavePrefs();
  LOG.map('LIDAR launch-bank layer ' + (on ? 'shown' : 'hidden'));
  BANK._row = '';
  bankRenderRow();
}

/* ---- geometry ------------------------------------------------------------ */
function _bankBBox(b){
  return (Array.isArray(b) && b.length===4 && b.every(n=>typeof n==='number' && isFinite(n))) ? b : null;
}
function _bankOverlaps(a, v){ return !(a[2]<v[0] || a[0]>v[2] || a[3]<v[1] || a[1]>v[3]); }
function _bankContains(a, v){ return a[0]<=v[0] && a[1]<=v[1] && a[2]>=v[2] && a[3]>=v[3]; }
/* Every painted area, in the index's own vocabulary: bank.py answers `present`, `partial`
   or `absent` per area and nothing else, and anything it has not been taught is read the
   conservative way — as PARTIAL, which never claims the layer is whole. */
function bankPainted(){
  return (BANK.areas||[]).filter(a=>a.status!=='absent' && a.bbox);
}
/* IS THE WATER ON SCREEN INSIDE SOMETHING THAT WAS PAINTED?

   This is the question that decides whether unpainted imagery means "no low bank" or "no
   data", and it is answered off the bboxes the index publishes rather than off whether
   any paint happened to arrive. A tile that has not loaded yet, a tile that 404s and a
   tile that is genuinely all water are three different things, and only a claim built on
   the published extent can tell them apart. */
function bankAreaForView(){
  const v = crtViewBBox(); if(!v) return null;
  const painted = bankPainted();
  return painted.find(a=>_bankContains(a.bbox, v) && a.status==='present')
      || painted.find(a=>_bankOverlaps(a.bbox, v))
      || null;
}

/* WHAT THIS ROW IS ENTITLED TO SAY, RIGHT NOW.

   THE SERVER'S VOCABULARY AND THIS CONSOLE'S ARE NOT THE SAME WORDS AND MUST NOT BE
   MERGED. `libraries.ok === false` means "this machine cannot BUILD the layer — no numpy,
   no scipy, no Pillow, or no module", which is permanent on the vehicle by design. This
   file's `unavailable` has meant CANNOT TELL — asked, no answer, and there was reason to
   expect one — since the day the hazard rows were written, and it is the loudest word the
   panel has. Spelling both the same way would put the map's alarm on a Pi that is working
   exactly as intended, so the capability gap is read as `no-library` and rendered CANNOT
   BUILD: a job for the handheld, never for the tether. */
function bankStatus(){
  if(!bankIsOn()) return 'off';
  if(!BANK.asked) return 'loading';
  /* WHICH SILENCE IS IT — DECIDED ON THIS STORE'S OWN RECORD, AND NOT THE TRUST'S. Same
     rule as crtHadReasonToExpectAnAnswer(): a store that answered on this handheld before
     and cannot be read now is a FAULT and is loud, and one that has never answered has
     simply not been built here and is quiet. What is deliberately NOT reused is the
     EVIDENCE. These are two different stores — the Trust's vectors are downloaded, the
     bank tiles are computed here — and either can be present without the other. Keyed on
     the shared record, a handheld holding the whole Trust network and no bank paint would
     raise a fault about work that has never once run. */
  if(!BANK.ok) return crtSeenArea(BANK_SEEN_KEY) ? 'unavailable' : 'not-downloaded';
  const painted = bankPainted();
  if(!painted.length){
    // CANNOT BUILD outranks ABSENT, because they send you to different places: one is a
    // job somebody can go and do with an internet connection, and the other is not a job
    // at all. It is only reached with nothing painted — once tiles are on the card the
    // libraries stop mattering, which is exactly what makes the layer serveable by a Pi.
    if(BANK.libs && BANK.libs.ok===false) return 'no-library';
    if((BANK.areas||[]).some(a=>a.status==='unreadable')) return 'unreadable';
    return 'absent';
  }
  const hit = bankAreaForView();
  if(!hit) return crtViewBBox() ? 'outside' : 'held';
  const v = crtViewBBox();
  return (hit.status!=='present' || !_bankContains(hit.bbox, v)) ? 'partial' : 'present';
}
/* The same words the chart rows use, because they are the same facts, plus the three this
   layer needs that a vector layer does not. Built by COPYING rather than by editing
   CRT_STATE_WORDS, so a word added for the bank layer can never turn up in a hazard row
   that has no way to mean it. */
const BANK_WORDS = Object.assign({}, CRT_STATE_WORDS, {
  partial:      'PARTIAL',
  outside:      'NOT HERE',
  unreadable:   'UNREADABLE',
  'no-library': 'CANNOT BUILD',
});

/* ---- the written explanation, which IS the feature -----------------------
   These sentences are this console's own and are shown whatever the server says,
   including when it says nothing at all. The server sends its own — `why`, and per area
   the words bank.py writes — and those are quoted live where they arrive, because the
   producer knows its real threshold, its survey year and which centreline it buffered,
   and this file would go stale the day any of them changed. What must never happen is the
   three claims below being CONDITIONAL on a document turning up: a console showing bare
   imagery with no caveat attached is exactly the failure this layer is guarding. */
const BANK_HEAD_HELP =
  'THE BANK, FROM LIDAR — the one layer on this map that is about the LAND rather than the '
+ 'water, and the only one that does not come from the Canal & River Trust. Environment '
+ 'Agency terrain, classified against the water level of each pound and painted over the '
+ 'imagery in two colours: amber where the bank is under 2 m above the water, dark brown '
+ 'where it is higher or built on. It answers "which side could I get down with the sub and '
+ 'the cable", which is a question a satellite photograph of a canal cannot answer at all. '
+ 'It is built once per area on this handheld and needs no connection to draw.';
const BANK_WHAT =
  'LAUNCH BANKS — Environment Agency LIDAR terrain, 1 m, painted over the satellite imagery '
+ 'in TWO colours and no more. AMBER means the ground there is LESS THAN 2 m above the water '
+ 'level of the pound beside it. DARK BROWN means it is higher than that, or it is building '
+ 'and hard urban fabric. The shading is a hillshade off the same terrain, so what reads as '
+ 'texture is real slope. AMBER IS A GEOMETRIC FACT AND NOT PERMISSION TO LAUNCH: it states '
+ 'a height difference and nothing else — not that there is a way in, not that there is a '
+ 'path, not that you are allowed, and it knows nothing about fences, gates, private land, '
+ 'live railway, reed beds or mud. The recon run that proved this layer painted a RAILWAY '
+ 'CUTTING amber, which is geometrically a low bank and operationally a place you will be '
+ 'arrested. THE WATER IS NEVER PAINTED: the imagery shows through unaltered on every water '
+ 'pixel, because nothing here knows anything whatever about depth and a wash over the water '
+ 'would be read as though it did. THE TERRAIN IS A 2022 SURVEY — banks slump, get rebuilt, '
+ 'get piled and get overgrown, and none of that is in this picture. THE PAINTED CORRIDOR IS '
+ 'BUFFERED FROM THE TRUST\'S OWN CANAL CENTRELINE, so an arm, a basin or a marina that is '
+ 'not in that centreline is not in this layer at all. GROUND WITH NO PAINT ON IT HAS NOT '
+ 'BEEN SURVEYED AND FOUND HIGH — it has not been looked at, and the row beside this one '
+ 'says which of NOT HERE, PARTIAL, ABSENT, UNREADABLE, CANNOT BUILD and NOT DOWNLOADED is '
+ 'the reason. Bare imagery is never left to stand in for "no low banks here".';
const BANK_LABEL_WHAT =
  'The white figures on the water are the DETECTED WATER LEVEL of each sheet of water in '
+ 'metres above Ordnance Datum — the height of the SURFACE, taken from the flat water in the '
+ 'terrain model itself, which is what every bank beside it is measured against. They step '
+ 'down through a lock flight because the canal does. They are heights OF the water and '
+ 'never depths of it: nothing in this layer has ever measured how much water is under that '
+ 'surface, and a detected surface is not a surveyed pound datum.';

function bankGlyphSvg(){
  /* THE KEY IS THE LAYER: two swatches, in the two colours actually painted, because there
     are only ever two. A legend with a ramp on it would invite reading a height off a
     shade, which is the one thing this classification does not carry. */
  return '<svg class="crt-g bank" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">'
       + '<rect x="1.5" y="4" width="8.5" height="12" fill="' + BANK_AMBER + '"/>'
       + '<rect x="10" y="4" width="8.5" height="12" fill="' + BANK_BROWN + '"/>'
       + '<rect x="2" y="4.5" width="16" height="11" fill="none" '
       + 'stroke="rgba(236,227,255,.5)" stroke-width="1"/></svg>';
}

/* WHAT THE ROW SAYS ABOUT RIGHT NOW, on top of the written explanation above.

   THE SERVER'S OWN SENTENCE IS QUOTED WHERE IT SENT ONE, exactly as the chart rows quote
   the index's `why`. It is the half that knows the actual numbers — how many tiles, how
   much of the corridor had terrain behind it, which library is missing, what to type to
   fix it — and a console that paraphrased it would be inventing a second account of the
   same fact, in a different voice, that goes stale on its own schedule. */
function bankSentence(st){
  const painted = bankPainted();
  const hit = bankAreaForView();
  const names = (list)=>list.map(a=>a.area).slice(0,4).join(', ')
                      + (list.length>4 ? (' and ' + (list.length-4) + ' more') : '');
  const vint = BANK.vintage ? ('The terrain is the ' + BANK.vintage + ' survey. ') : '';
  const prov = (a)=>a ? (' Area "' + a.area + '"'
                       + (a.built ? (', painted ' + String(a.built).slice(0,10)) : '')
                       + (a.fetched ? (', terrain fetched ' + String(a.fetched).slice(0,10)) : '')
                       + (a.vintage ? (', from the ' + a.vintage + ' survey') : '')
                       + (typeof a.tiles==='number' ? (', ' + a.tiles.toLocaleString() + ' tiles') : '')
                       + '.' + (a.why ? (' It says: ' + a.why) : '')) : '';
  switch(st){
    case 'present':
      return 'HERE — the bank layer is painted over the water you are looking at.' + prov(hit)
           + (BANK.labels
              ? (' ' + BANK.labels + ' water level' + (BANK.labels===1?'':'s') + ' labelled on it')
              : ' No water level is labelled in this view')
           + (BANK.labelsHidden
              ? (', ' + BANK.labelsHidden + ' suppressed because they would have landed on top of '
                 + 'each other at this zoom — zoom in and they come back') : '')
           + (BANK.poundWhy ? ('. The detected levels could not be read: ' + BANK.poundWhy) : '')
           + '. ' + vint
           + 'Remember what the amber is: a height difference under 2 m, which is a '
           + 'measurement, and not a judgement that anything can be carried down there.';
    case 'partial':
      return 'PARTIAL — this handheld holds bank paint for the water on screen and not for all '
           + 'of it.' + prov(hit)
           + ' Either the view runs off the edge of the painted box or that area was painted '
           + 'with holes in it, so some of what you can see is unpainted for a reason that is '
           + 'about the DATA and not about the bank. Unpainted ground here is NOT bank that was '
           + 'measured and found high — it has not been looked at. ' + vint;
    case 'outside':
      return 'NOT HERE — the bank layer is painted on this handheld for ' + painted.length
           + ' area' + (painted.length===1?'':'s') + ' (' + names(painted) + ') and the water on '
           + 'screen is not inside any of them. So this map is showing bare satellite over the '
           + 'banks in front of you, and bare satellite says nothing at all: it is not "no low '
           + 'banks here", it is "nobody has looked here". Build the layer for a saved area over '
           + 'this stretch and it will paint.';
    case 'held':
      return 'HELD — this handheld holds the bank layer for ' + painted.length + ' area'
           + (painted.length===1?'':'s') + ' (' + names(painted) + '), and this map has no '
           + 'position of any kind yet, so there is nowhere to draw it. Nothing is missing and '
           + 'nothing has failed: it paints the moment the map knows where it is looking.';
    case 'no-library':
      return 'CANNOT BUILD — this machine cannot make a launch-bank layer at all'
           + ((BANK.libs && BANK.libs.why) ? (': ' + BANK.libs.why) : '') + '. '
           + ((BANK.libs && BANK.libs.install) ? (BANK.libs.install + ' ') : '')
           + 'That is a missing CAPABILITY and not a survey result: no download and no '
           + 'reconnection changes it, and nothing here claims a bank is low or claims it is '
           + 'high. The classification is arithmetic over a terrain model and the libraries '
           + 'that do it are a handheld job — the vehicle is in this state permanently and on '
           + 'purpose, because a Pi 3B+ on the end of a tether has a sub to run. Tiles already '
           + 'built elsewhere are served here perfectly well; only building needs them.';
    case 'unreadable':
      return 'UNREADABLE — the record beside an area\'s bank tiles is on this handheld and will '
           + 'not parse. Nothing on it can be dated or attributed, so none of it is drawn as a '
           + 'survey. That is not a claim about the banks in either direction — rebuild the '
           + 'layer for that area before trusting any paint on it.'
           + (BANK.why ? (' It says: ' + BANK.why) : '');
    case 'absent':
      return 'ABSENT — ' + mapDataName() + ' was read and no launch-bank layer has been built on '
           + 'this handheld' + (BANK.why ? ('. It says: ' + BANK.why) : '.')
           + ' Nothing on this map is telling you which bank you could get down, so unpainted '
           + 'ground means NO DATA and never "no low bank there". It is fixable, at home, with '
           + 'internet: the terrain is fetched once and painted once, per area.';
    case 'unavailable':
      return 'CANNOT TELL — the bank layer has been read on this handheld before and cannot be '
           + 'read now' + (BANK.why ? (': ' + BANK.why) : '') + '. Something that was working is '
           + 'not: the map service has stopped, or the paint has been deleted or damaged. '
           + 'Nothing has been ruled in or out, and this map is not showing banks it was '
           + 'showing a minute ago.';
    case 'not-downloaded':
      return 'NOT DOWNLOADED — nothing this console can reach holds a launch-bank layer'
           + (BANK.why ? (' (' + BANK.why + ')') : '') + '. Nothing has failed. It is a one-time '
           + 'job per saved area, done on this handheld, and it needs internet once: the terrain '
           + 'is fetched, classified and prerendered here, and after that it draws with no '
           + 'connection at all. Until it has run, the banks on this map are bare satellite, '
           + 'which is not a statement that there are no low banks.';
    case 'loading':
      return 'asking ' + mapDataName() + ' what bank paint this handheld holds';
    default:
      return 'NOT ASKED — you switched this layer off on this handheld. It ships on, like every '
           + 'layer here, so this is your choice and not a default. It is not a claim that the '
           + 'data is missing and it says nothing about the banks: switch it back on and the row '
           + 'will report which of HERE, PARTIAL, NOT HERE, ABSENT and NOT DOWNLOADED is true.';
  }
}

/* ---- the panel row ------------------------------------------------------- */
function bankBuildRow(list){
  if(!list) return;
  const head = document.createElement('div');
  head.className = 'crt-head tb';
  head.id = 'crt-tier-bank';
  head.textContent = 'THE BANK — LIDAR, NOT THE TRUST';
  head.dataset.help = BANK_HEAD_HELP;
  head.title = BANK_HEAD_HELP;
  head.setAttribute('aria-label', BANK_HEAD_HELP);
  list.appendChild(head);

  const row = document.createElement('div');
  row.className = 'crt-row bank';
  row.id = 'crt-row-bank';
  row.dataset.layer = 'bank';
  row.dataset.help = BANK_WHAT + ' ' + BANK_LABEL_WHAT;
  row.innerHTML =
    '<span class="crt-glyph">' + bankGlyphSvg() + '</span>' +
    '<span class="crt-name">LAUNCH BANKS</span>' +
    '<span class="crt-state" id="crt-state-bank"></span>' +
    '<button class="crt-toggle" id="crt-toggle-bank" type="button" role="switch"></button>';
  list.appendChild(row);
  const b = row.querySelector('.crt-toggle');
  if(b) b.addEventListener('click', (ev)=>{ ev.stopPropagation(); bankSetOn(!bankIsOn()); });
  BANK._row = '';
  bankRenderRow();
}
function bankRenderRow(){
  const row = $('crt-row-bank'); if(!row) return;
  const st = bankStatus(), on = bankIsOn();
  row.classList.toggle('on', on);
  ['absent','unavailable','not-downloaded','partial','outside','held','unreadable','no-library']
    .forEach(c=>row.classList.toggle(c, st===c));
  row.classList.toggle('shown', on && st==='present');
  const pill = $('crt-state-bank');
  if(pill){
    const word = BANK_WORDS[st] || String(st).toUpperCase();
    // The count is AREAS PAINTED, not tiles and not features: it is the only number here
    // an operator can act on, because it is the one that answers "have I built this for
    // the cut I am going to".
    const n = bankPainted().length;
    pill.textContent = ((st==='present'||st==='held'||st==='partial') && n) ? (word+' · '+n) : word;
    pill.className = 'crt-state s-' + st;
  }
  const btn = $('crt-toggle-bank');
  if(btn){
    btn.textContent = on ? 'ON' : 'OFF';
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-checked', on ? 'true' : 'false');
    const s = (on ? 'DRAWN. Tap to stop painting the LIDAR bank layer over the imagery. '
                  : 'SWITCHED OFF BY YOU — the tiles are still on this handheld and are simply '
                  + 'not being painted. Tap to draw them. ')
            + 'The choice is remembered on this handheld, and this layer ships ON like every '
            + 'other, so switching it off is always your decision and never a default. '
            + BANK_WHAT;
    btn.dataset.help = s; btn.title = s; btn.setAttribute('aria-label', s);
  }
  liveTitle(row, bankSentence(st));
}

/* ---- reading the index --------------------------------------------------- */
async function bankLoad(why){
  if(BANK._busy) return;
  BANK._busy = true;
  try{
    /* AWAITED BEFORE ANYTHING IS CLASSIFIED, for the reason crtLoadAll gives: the
       never-had-it / lost-it decision reads a PERSISTED record, and a read that raced the
       store would file a handheld which has been painting these banks all month as one
       that has never held them — the alarm switched off by a timing accident. */
    await crtSeenReady();
    LOG.map('LIDAR bank layer: reading what this handheld holds (' + (why||'refresh') + ')');
    const r = await _crtGet(mapDataBase() + BANK_API.index, BANK_API.timeoutMs);
    BANK.asked = true; BANK.at = Date.now();
    if(!r.ok || !r.json || typeof r.json!=='object'){
      BANK.ok = false;
      BANK.areas = [];
      /* A 404 IS AN ANSWER, and it is the same distinction crtFetchIndex draws. This
         endpoint is documented never to 404 — it answers with a status and a sentence
         even when it holds nothing — so a 404 here is a build of the map service that
         predates the layer, which is still "nothing this console can reach holds it".
         Either way the seen-record decides the VOLUME, and it is not stamped, so a
         console that has never had a bank document is quiet. */
      BANK.nothing = (r.status===404 || r.status===501);
      BANK.why = BANK.nothing
        ? (mapDataName() + ' answered ' + r.status + ' for the launch-bank layer, which is a '
           + 'map service that does not serve one')
        : (r.err || (mapDataName() + ' answered ' + (r.status||'nothing')
                     + ' for the launch-bank layer'));
      return;
    }
    const j = r.json;
    BANK.ok = true; BANK.nothing = false;
    BANK.status  = String(j.status || 'present');
    BANK.tiles   = String(j.tiles || BANK_API.tiles);
    // The URL, kept under a name of its own: BANK.pounds is the FeatureCollection
    // itself, and one field meaning both a document and the address of that document
    // is exactly the collision this file keeps warning about.
    BANK.poundsUrl = String(j.pounds || BANK_API.pounds);
    BANK.minzoom = (typeof j.minzoom==='number') ? j.minzoom : null;
    BANK.maxzoom = (typeof j.maxzoom==='number') ? j.maxzoom : null;
    BANK.thresholdM = (typeof j.threshold_m==='number') ? j.threshold_m : null;
    BANK.vintage = String(j.vintage || '');
    BANK.why     = String(j.why || '');
    BANK.attribution = String(j.attribution || '');
    /* WHETHER THIS MACHINE COULD BUILD THE LAYER AT ALL, which is a different question
       from whether it holds any. `ok:false` is CANNOT BUILD and carries the exact pip
       line; `install` is quoted verbatim rather than reworded, because an operator told
       to install numpy when scipy is what is missing runs the command, sees no change,
       and concludes the layer is broken. */
    const L = (j.libraries && typeof j.libraries==='object') ? j.libraries : null;
    BANK.libs = L ? {ok: L.ok!==false, why:String(L.why||''), install:String(L.install||'')}
                  : {ok:true, why:'', install:''};
    BANK.areas = (Array.isArray(j.areas) ? j.areas : []).map(a=>({
      area:    String((a && (a.area||a.name)) || ''),
      status:  String((a && (a.status||a.state)) || 'absent'),
      bbox:    _bankBBox(a && a.bbox),
      fetched: String((a && a.fetched) || ''),
      built:   String((a && a.built) || ''),
      /* PER-AREA VINTAGE OVERRIDES THE INDEX'S. Areas are painted at different times off
         different survey years, and one date printed over all of them would be a claim
         about ground nobody looked at in that year. */
      vintage: String((a && a.vintage) || j.vintage || ''),
      tiles:   (a && typeof a.tiles==='number') ? a.tiles : null,
      pounds:  (a && typeof a.pounds==='number') ? a.pounds : null,
      why:     String((a && a.why) || ''),
    })).filter(a=>a.area);
    // The licence line travels with the data, into the same credit box the Trust's does.
    if(BANK.attribution) crtNoteCredit({attribution: BANK.attribution}, null);
    /* THE SEEN-RECORD IS STAMPED ONLY BY A DOCUMENT THAT PARSED, never by a 404. Marking
       a 404 as seen would mean a map service that has told this console it holds no bank
       paint, and then gone away, produces CANNOT TELL — the map's loudest word — about
       data that has never once existed here. That is the bench alarm this file spent a
       round removing, and crtFetchIndex draws the same line. */
    crtMarkSeen(BANK_SEEN_KEY);
    const painted = bankPainted();
    LOG.map('LIDAR bank layer: ' + painted.length + ' of ' + BANK.areas.length
          + ' area(s) painted' + (BANK.minzoom!==null ? (', zoom '+BANK.minzoom+'-'+BANK.maxzoom) : '')
          + (BANK.vintage ? (', ' + BANK.vintage + ' survey') : '')
          + (painted.length ? (' — ' + painted.map(a=>a.area+' ('+a.status+')').join(', ')) : ''));
  }catch(e){
    BANK.asked = true; BANK.ok = false;
    BANK.why = 'the bank index could not be read (' + ((e&&e.message)||e) + ')';
  }finally{
    BANK._busy = false;
    BANK._row = '';
    bankRenderRow();
  }
}

/* ---- the detected water levels -------------------------------------------
   Windowed exactly like the Trust's vector layers: asked for around where the map is
   looking, with the same slack, and re-asked when the view leaves it. On a failure the
   window is recorded ANYWAY, so a map service with no pounds endpoint is asked once per
   window rather than once per second forever — a poll whose answer cannot change is the
   thing this file refuses to do everywhere else. */
async function bankEnsurePounds(){
  if(!bankIsOn() || !BANK.ok || BANK.poundBusy) return;
  const view = crtViewBBox(); if(!view) return;
  if(BANK.poundWin && crtWindowCovers(BANK.poundWin, view)) return;
  if((Date.now() - BANK.poundAt) < 1500) return;
  BANK.poundBusy = true; BANK.poundAt = Date.now();
  const win = crtWindowBBox();
  try{
    const url = mapDataBase() + (BANK.poundsUrl || BANK_API.pounds) + '?' + crtBBoxParam(win);
    const r = await _crtGet(url, BANK_API.timeoutMs);
    if(r.ok && r.json && r.json.type){ BANK.pounds = r.json; BANK.poundWhy = ''; }
    else {
      BANK.pounds = null;
      BANK.poundWhy = r.err || (mapDataName() + ' answered ' + (r.status||'nothing')
                                + ' for the detected water levels');
    }
    BANK.poundWin = win;
  }catch(e){
    BANK.pounds = null; BANK.poundWin = win;
    BANK.poundWhy = 'the detected water levels could not be read (' + ((e&&e.message)||e) + ')';
  }finally{ BANK.poundBusy = false; }
}
/* The level of one sheet of water, under the name api/nav/bank.py actually writes:
   `level_m_od`, metres above Ordnance Datum. The two synonyms are read as well because a
   field one half of a feature writes and the other half does not read is how this project
   has silently lost a feature five times, and an unlabelled canal is a bad way to find
   out. `label` is the producer's own rendering of the number and wins for the TEXT, so
   the figure on the glass is the figure the producer meant to print. */
function _bankLevelOf(f){
  const p = (f && f.properties) || {};
  const v = (typeof p.level_m_od==='number') ? p.level_m_od
          : (typeof p.level_m==='number') ? p.level_m
          : (typeof p.water_level_m==='number') ? p.water_level_m : null;
  return (typeof v==='number' && isFinite(v)) ? v : null;
}
function _bankLabelOf(f, lvl){
  const p = (f && f.properties) || {};
  return (typeof p.label==='string' && p.label.trim()) ? p.label.trim() : (lvl.toFixed(1)+' m OD');
}

/* ---- drawing -------------------------------------------------------------
   ONE TILE CACHE OF ITS OWN, and not tiles.js's. The two layers are different pyramids
   with different zoom ranges and, crucially, different meanings for a missing tile: a
   missing SATELLITE tile is drawn from its nearest cached ancestor, because imagery
   upscaled is still imagery, and a missing BANK tile is drawn as NOTHING, because
   upscaling a classification paints amber over ground nobody classified. That difference
   is the whole reason this is not a second call into drawTiles. */
function bankTileUrl(z, x, y){
  return mapDataBase() + String(BANK.tiles || BANK_API.tiles)
    .replace('{z}', z).replace('{x}', x).replace('{y}', y);
}
function _bankTile(z, x, y){
  const n = 1<<z;
  if(z<0 || x<0 || y<0 || x>=n || y>=n) return null;
  const key = z+'/'+x+'/'+y;
  let e = BANK.cache.get(key);
  if(e) return e;
  e = { img:new Image(), state:'load' };
  /* No crossOrigin, for the same reason tiles.js omits it: the map service may be on a
     different origin on this handheld, and a CORS header nobody here controls must not be
     what decides whether the operator can see the bank. Nothing reads these pixels back;
     the screenshot path re-renders without raster layers (map.js drawCanvas noTiles). */
  e.img.onload  = ()=>{ e.state='ok';  BANK.inflight--; };
  e.img.onerror = ()=>{ e.state='err'; BANK.inflight--; };
  BANK.cache.set(key, e);
  BANK.inflight++;
  e.img.src = bankTileUrl(z, x, y);
  return e;
}
function bankDraw(ctx, dpr){
  BANK.drew = 0; BANK.tooWide = false;
  if(!bankIsOn() || !BANK.ok){ bankNoteRow(); return false; }
  const L = (typeof TILES!=='undefined') ? TILES.last : null;
  if(!L || !L.worldTP || !L.k){ bankNoteRow(); return false; }
  try{ const p = bankEnsurePounds(); if(p && p.catch) p.catch(()=>{}); }catch(err){}
  /* NOT ONE TILE IS ASKED FOR OVER WATER NO AREA COVERS. The index publishes the painted
     boxes, so a console looking at a canal nobody has processed spends nothing and says
     NOT HERE in the panel — rather than firing a screenful of requests to collect a
     screenful of 404s and leaving a hole that looks exactly like "no low banks". */
  const a = bankAreaForView();
  if(!a){ BANK.on=''; bankNoteRow(); return false; }
  BANK.on = a.area;

  // The zoom the IMAGERY is being drawn at, recovered from the projection it left behind,
  // so the two layers always reason about the same world and the paint cannot drift off
  // the photograph it is annotating.
  const z0 = Math.round(Math.log2(L.worldTP/256));
  const zmin = (typeof BANK.minzoom==='number') ? BANK.minzoom : 0;
  const zmax = (typeof BANK.maxzoom==='number') ? BANK.maxzoom : z0;
  /* PAST THE BOTTOM OF THE PYRAMID, NOTHING IS DRAWN AND THE ROW SAYS SO. The overlay is
     prerendered down to area-overview zoom and no further; below that there is no level to
     draw, and synthesising one would mean classifying by arithmetic instead of by terrain.
     Silence here would be indistinguishable from "no low banks in this county". */
  if(z0 < zmin){ BANK.tooWide = true; bankNoteRow(); return false; }
  const z = Math.min(zmax, z0);
  // Above the pyramid's top level the coarser tile is stretched, which is honest: it is
  // the classification this handheld holds, drawn softer. It is never sharpened from a
  // level that does not exist and never invented from a neighbour.
  const f = Math.pow(2, z0 - z);
  const k = L.k * f, cx = L.cxTP / f, cy = L.cyTP / f;
  const W = ctx.canvas.width, H = ctx.canvas.height;
  const halfTP = Math.hypot(W,H)/2 / k + 256;
  const minTx=Math.floor((cx-halfTP)/256), maxTx=Math.floor((cx+halfTP)/256);
  const minTy=Math.floor((cy-halfTP)/256), maxTy=Math.floor((cy+halfTP)/256);
  let drew=0, budget=BANK_API.tileBudget;
  ctx.save();
  ctx.setTransform(1,0,0,1,0,0);
  ctx.translate(W/2, H/2); ctx.rotate(L.rot||0);
  ctx.imageSmoothingEnabled = true;
  for(let tx=minTx; tx<=maxTx && budget>0; tx++){
    for(let ty=minTy; ty<=maxTy && budget>0; ty++){
      budget--;
      const t = _bankTile(z, tx, ty);
      if(t && t.state==='ok'){
        ctx.drawImage(t.img, (tx*256-cx)*k, (ty*256-cy)*k, 256*k, 256*k);
        drew++;
      }
    }
  }
  ctx.restore();
  BANK.drew = drew;
  // The cache cannot grow without limit on a long pan — the same rule tiles.js follows.
  if(BANK.cache.size > 900){
    const it = BANK.cache.keys();
    for(let i=0;i<220;i++){ const key = it.next().value; if(key===undefined) break; BANK.cache.delete(key); }
  }
  bankDrawLabels(ctx, dpr);
  bankNoteRow();
  return drew > 0;
}

/* THE DETECTED WATER LEVEL, ON THE WATER IT BELONGS TO.

   White with a dark halo because it is drawn over satellite imagery of water, which is
   near-black in one half of a frame and near-white in the other — a single fill colour is
   legible over one of those and invisible over the other.

   SUPPRESSED WHERE IT WOULD BE CLUTTER RATHER THAN INFORMATION, and the row says how many
   were suppressed. Two rules: a floor on the zoom, because below it the label is wider
   than the pound it names; and a greedy collision test, so a lock flight at middling zoom
   shows the levels it has room for instead of a smear. Nothing is dropped — zoom in and
   the rest appear. */
function bankDrawLabels(ctx, dpr){
  BANK.labels = 0; BANK.labelsHidden = 0;
  const gj = BANK.pounds;
  if(!gj) return;
  const feats = (gj.type==='FeatureCollection') ? (gj.features||[])
              : (gj.type==='Feature' ? [gj] : []);
  if(!feats.length) return;
  const ppm = _crtPpm(dpr);
  if(!(ppm > BANK_API.labelPpm)){ BANK.labelsHidden = feats.length; return; }
  const W = ctx.canvas.width, H = ctx.canvas.height;
  const placed = [];
  ctx.save();
  ctx.setTransform(1,0,0,1,0,0);
  ctx.font = '700 ' + (11*dpr) + 'px ' +
             ((typeof getComputedStyle==='function' && getComputedStyle(document.body).fontFamily)
              || 'sans-serif');
  ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.lineJoin='round'; ctx.miterLimit=2;
  for(const feat of feats){
    const lvl = _bankLevelOf(feat);
    if(lvl===null) continue;
    const p = _crtParts(feat);
    if(!p.rep) continue;
    const s = lonLatToScreen(p.rep[1], p.rep[0]);
    if(!s || !(s[0]>0 && s[0]<W && s[1]>0 && s[1]<H)) continue;
    if(BANK.labels >= BANK_API.maxLabels){ BANK.labelsHidden++; continue; }
    const txt = _bankLabelOf(feat, lvl);
    const w = ctx.measureText(txt).width + 10*dpr, h = 15*dpr;
    if(placed.some(b=>Math.abs(b.x-s[0]) < (b.w+w)/2 && Math.abs(b.y-s[1]) < (b.h+h)/2)){
      BANK.labelsHidden++;
      continue;
    }
    placed.push({x:s[0], y:s[1], w:w, h:h});
    ctx.lineWidth = 3.2*dpr; ctx.strokeStyle = 'rgba(6,2,16,.9)';
    ctx.strokeText(txt, s[0], s[1]);
    ctx.fillStyle = '#ffffff';
    ctx.fillText(txt, s[0], s[1]);
    BANK.labels++;
  }
  ctx.restore();
}

/* THE ROW FOLLOWS THE MAP. Coverage is a question about WHERE THE VIEW IS, so panning off
   the end of a painted area changes the honest answer with no fetch involved and no event
   to hang off. Re-rendered only when the answer actually changes — one string comparison a
   frame, the same guard crtRenderBadge uses. */
function bankNoteRow(){
  const key = bankStatus() + '|' + BANK.labels + '|' + BANK.labelsHidden + '|'
            + (BANK.tooWide?'w':'') + '|' + BANK.on;
  if(key === BANK._row) return;
  BANK._row = key;
  bankRenderRow();
}

/* The Environment Agency's licence line, shown wherever their data is — the same rule the
   Trust's credit follows, on the same strip, for the same reason. The index's own
   statement wins when it sends one, so a change of terms on the producing side does not
   need this file edited in the same breath. Drawn only when the layer reached the glass. */
const BANK_ATTRIBUTION =
  'Contains Environment Agency LIDAR Composite DTM 1 m data (c) Environment Agency, licensed '
+ 'under the Open Government Licence v3.0';
function bankAttribution(){ return BANK.attribution || BANK_ATTRIBUTION; }
function bankAnyDrawn(){ return bankIsOn() && BANK.ok && BANK.drew > 0; }
