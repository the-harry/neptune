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

   THREE TIERS, AND THE TIER IS A SAFETY DECISION, not a display preference:

     1  HAZARDS       always drawn, NOT toggleable. Entrainment, suction and
                      no-retrieval structures. There is no operator preference
                      that makes it right to hide these.
     2  OPERATIONS    on by default, toggleable. Where you can get in, get out,
                      turn round, and what is moored in the way.
     3  EXTRAS        off by default. Worth keeping, not worth the clutter.

   TWO HONESTY RULES RUN THROUGH THE WHOLE FILE.

   NO FLOW IS EVER SHOWN. CRT publish no flow measurement of any kind, so the
   hazard marks are the honest proxy for "expect current here" — and every one of
   them says exactly that in its own words. An inference is not a measurement, and
   a mark that implied a measured current at a weir would be inventing the single
   number an operator would most like to have been given.

   AN ABSENT LAYER SAYS ABSENT. "No locks here" and "no lock data here" are
   opposite claims and only one of them is safe to act on, so: a layer whose file
   is not on the Pi reports ABSENT; a layer that IS on the Pi with nothing inside
   this area reports NONE MAPPED; and a Pi that cannot be asked at all reports
   CANNOT TELL. Three states, three different words, and never a quietly empty map.

   TWO-PHASE, like areas.py and satellite.py: nothing here touches the internet.
   Every request goes to the Pi over the tether, carries its own timeout, and a Pi
   that does not answer produces CANNOT TELL rather than a hung overlay.
   ============================================================================ */

/* WHERE THE DATA COMES FROM.

   The INDEX is the gate, and that is deliberate. If a per-layer GET 404s we would
   like to say ABSENT — "the Pi looked, and that file is not on the disk" — but a
   404 also happens when the Pi has no chart service at all, and those two are not
   the same claim. So the index is asked first: it answering is what earns the
   console the right to report per-layer absence. No index, no claim: everything
   reads CANNOT TELL, which is the truth about a console that has not been able to
   ask anybody anything.

   Paths are here, in one object, and not in config.js — that file belongs to
   another owner this round. The index may also hand back its own `path` per layer,
   which wins over these, so the server side can move an endpoint without this file
   having to be edited in the same breath. */
const CRT_API = {
  index:     '/api/areas/{area}/crt',
  layer:     '/api/areas/{area}/crt/{layer}',
  timeoutMs: 4000,        // a canal-side Pi that is thinking, versus one that is gone
  retryMs:   30000,       // how often a CANNOT TELL layer is quietly re-asked
  maxDraw:   1500,        // features drawn per layer per frame; the row says when it truncates
};

/* Clauses that every hazard mark must carry, kept in one place so a hazard added
   next year cannot be the one that forgets to say them. */
const CRT_NO_FLOW =
  'The Canal & River Trust data carries no flow measurement of any kind, so this mark says a '
+ 'STRUCTURE is here — it never says water is moving through it now. Read it as a place to '
+ 'EXPECT current, never as a reading of one.';
const CRT_RING =
  'The dashed ring is a fixed standoff this console draws around the mark, chosen by us; it is '
+ 'not a surveyed danger area and the real one may be larger.';

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

  /* ---- TIER 3 — EXTRAS. Off by default; the console has not even asked for them. ---- */
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
       + 'a fixed list and this is not on it — so it is drawn in the hazard colour when you switch '
       + 'it on, and treating a pumping station as a place an intake may be is the safe reading.' },
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
    what:'TOWPATH — the path itself, drawn as a line. Off by default because it runs the length of '
       + 'everything and buries the marks that matter; switched on, it shows which bank you can '
       + 'walk while the sub works the other one.' },
  { id:'docks', tier:3, mark:'DK', name:'DOCKS',
    aliases:['dock','docks'],
    what:'DOCK — an enclosed basin off the main line. Sheltered water, hard vertical edges, and '
       + 'usually the deepest thing on the stretch; also where boats are moved about under power.' },
  { id:'boat_lifts', tier:3, mark:'BL', name:'BOAT LIFTS', hazardish:true,
    aliases:['boat_lift','boat_lifts','lift'],
    what:'BOAT LIFT — a machine that raises whole boats between levels, with caissons and gates '
       + 'that move a great deal of water when they operate. It sits in EXTRAS because the hazard '
       + 'tier is a fixed list and this is not on it, so it is drawn in the hazard colour once you '
       + 'switch it on: treat it as a lock that is several times the size.' },
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
    what:'CANAL LINE — the Trust publishes its own centreline for the navigation, by name and by distance. '
       + 'Off by default because this console already draws a centreline of its own from the '
       + 'downloaded area and two lines down one cut is one line too many; switch it on to check '
       + 'the two agree, which is a real question when snapping is moving the sub onto one of them.' },
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
       + 'any dive log has been saved, and the row says how many of each you are looking at.' },
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
  live:{cells:null, at:0},   // this session's own soundings, binned
  _busy:false, _badge:'', _rowsBuilt:false, _building:false,
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
function crtWhat(e){
  if(!(e.tier===1 || e.hazardish || e.flowProxy)) return e.what;
  let s = e.what;
  if(s.indexOf('standoff this console draws') < 0 && e.standoffM) s += ' ' + CRT_RING;
  if(s.indexOf('no flow measurement') < 0) s += ' ' + CRT_NO_FLOW;
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
      : 'THIS CONSOLE HAS NO ENTRY FOR THIS LAYER. The Pi published it, so it is offered here and '
      + 'drawn as a plain mark when switched on, but nothing on this handheld knows what it means '
      + 'for the vehicle — which is exactly why it is off by default and why this sentence does not '
      + 'pretend otherwise. It is emphatically not a claim that the layer is unimportant.')
      + (duplicate
         ? ' It also arrived SECOND for a row that was already taken: the Pi is carrying more than '
         + 'one file for this kind of feature (the Trust publishes near-duplicate services), and '
         + 'this is the other one, given its own row so that neither can quietly overwrite the other.'
         : ''),
  };
  CRT.extra.push(e);
  return e;
}

/* ---- preferences (kv, like the origin) ---------------------------------- */
function crtDefaultOn(e){ return e.tier===1 ? true : e.tier===2; }
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
    crtFetchLayer(e).then(()=>{ crtRenderRows(); crtRenderBadge(); });
  crtRenderRows(); crtRenderBadge();
}

/* ---- status vocabulary --------------------------------------------------
   off          the layer is switched off, so nothing has been ASKED. Not a claim.
   loading      asked, waiting
   present      the Pi has the file and it has features in this area
   empty        the Pi has the file and there is nothing here — "none mapped"
   absent       the Pi looked and the file is not on the disk
   unavailable  nobody could be asked at all — CANNOT TELL
   no-area      there is no active map area, so there is nothing to ask about
   -------------------------------------------------------------------------- */
const CRT_STATE_WORDS = {
  off:         'NOT ASKED',
  loading:     'ASKING…',
  present:     'SHOWN',
  empty:       'NONE MAPPED',
  absent:      'ABSENT',
  unavailable: 'CANNOT TELL',
  'no-area':   'NO AREA',
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
  const n = (typeof crtLiveCells==='function') ? crtLiveCells().length : 0;
  if(!n) return '';
  return ' Plus ' + n + ' cell' + (n===1?'':'s') + ' measured by the sub during THIS session, '
       + 'drawn the same way because they are the same kind of measurement, and counted apart '
       + 'because no dive log has been written for them yet.';
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
      let s = (st.n===1 ? '1 feature' : st.n+' features') + ' loaded from the Pi';
      if(!on) s += ' (held, but switched off)';
      else if(st.capped)
        s += ', and this frame stopped after drawing ' + st.drawn + ' of them — the console\'s own '
           + 'per-frame limit, not the end of the file. The rest are loaded and held.';
      else if(st.drawn && st.drawn < st.n)
        s += ', of which ' + st.drawn + ' are inside the view you are looking at. The others are '
           + 'loaded too and simply off screen — pan or zoom out and they draw.';
      return s + live;
    }
    case 'empty':
      return 'NONE MAPPED HERE — the Pi HAS this layer and there is nothing of this kind inside '
           + 'the downloaded area. That is a real answer and it is not the same as the layer being '
           + 'missing.' + live;
    case 'absent':
      return 'ABSENT — the Pi looked and the file for this layer is not on the disk, so nothing on '
           + 'this map is telling you where these are. An empty map here means NO DATA, not NONE.'
           + (st.why ? (' The Pi says: ' + st.why) : '') + live;
    case 'unavailable':
      return 'CANNOT TELL — ' + (st.why || 'the Pi could not be asked') + '. Nothing has been '
           + 'ruled in or out; this is the console admitting it does not know.' + live;
    case 'loading':  return 'asking the Pi for it now';
    case 'no-area':  return 'no map area is active, so there is nothing to ask about yet';
    default:
      return 'NOT ASKED — this layer is switched off, so the console has not requested it. That is '
           + 'not a claim that it is missing; switch it on and it will say which.';
  }
}

/* ---- fetching (two-phase safe: timeout, no hostnames, no internet) ------- */
function _crtUrl(tmpl, layer){
  return (state.httpBase||'') + String(tmpl)
    .replace('{area}', encodeURIComponent(CRT.area||''))
    .replace('{layer}', encodeURIComponent(layer||''));
}
async function _crtGet(url){
  let ctl=null, timer=null;
  try{ ctl = (typeof AbortController!=='undefined') ? new AbortController() : null; }catch(e){}
  if(ctl) timer = setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, CRT_API.timeoutMs);
  try{
    const r = await fetch(url, ctl ? {signal:ctl.signal, cache:'no-store'} : {cache:'no-store'});
    if(timer) clearTimeout(timer);
    let j=null; try{ j = await r.json(); }catch(e){}
    return { ok:r.ok, status:r.status, json:j };
  }catch(e){
    if(timer) clearTimeout(timer);
    const msg = (e && e.name==='AbortError')
      ? ('the Pi did not answer within '+Math.round(CRT_API.timeoutMs/1000)+' s')
      : ('the request never reached the Pi ('+((e&&e.message)||'no route')+')');
    return { ok:false, status:0, err:msg };
  }
}
function _crtSet(id, status, patch){
  const cur = CRT.state[id] || {};
  CRT.state[id] = Object.assign({n:0, drawn:0, capped:false, why:'', data:null, at:Date.now()},
                                cur, patch||{}, {status, at:Date.now()});
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

/* THE INDEX. Its own answer is what licenses every per-layer claim below it. */
async function crtFetchIndex(){
  const r = await _crtGet(_crtUrl(CRT_API.index));
  if(!r.ok){
    if(r.status===404 || r.status===501)
      return { ok:false, why:'this Pi has no chart service on it (the layer index answered '+r.status+')' };
    if(r.status) return { ok:false, why:'the Pi answered '+r.status+' for the layer index' };
    return { ok:false, why:r.err || 'the Pi could not be reached' };
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
  crtNoteCredit(r.json, null);                  // the index carries the area's own attribution
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
    return { ok:false, why:'the layer index answered with something this console cannot read' };
  }
  return { ok:true, layers:out };
}

async function crtFetchLayer(e, idxMeta){
  if(!CRT.area){ _crtSet(e.id, 'no-area'); return; }
  if(!CRT.indexOk){
    _crtSet(e.id, 'unavailable', {why: CRT.indexWhy || 'the chart index could not be read'});
    return;
  }
  if(idxMeta && idxMeta.present===false){
    _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(idxMeta)});
    return;
  }
  _crtSet(e.id, 'loading');
  const tmpl = (idxMeta && idxMeta.path) || e.path || CRT_API.layer;
  // The URL asks for the layer by the PI's name for it, never by this console's:
  // `locks-0` is a filename on the Pi and `locks` is a row in the table above, and
  // conflating them is a 404 on every hazard layer we can actually draw.
  const r = await _crtGet(_crtUrl(tmpl, e.wire || e.id));
  if(!r.ok){
    // The index answered, so the service exists — a 404 on one layer really is
    // "that file is not on the disk", which is the one case we are entitled to
    // call ABSENT.
    if(r.status===404) _crtSet(e.id, 'absent', {data:null, n:0});
    else if(r.status)  _crtSet(e.id, 'unavailable', {why:'the Pi answered '+r.status+' for this layer', data:null});
    else               _crtSet(e.id, 'unavailable', {why:r.err||'the Pi could not be reached', data:null});
    return;
  }
  // The Pi answers an absent layer with 200 and a body that says so, and that body
  // carries WHY: skipped on purpose, fetch never run here, file deleted since. Those
  // are different facts with different remedies and the console has no way to know
  // any of them — so it quotes the Pi rather than paraphrasing it into one word.
  if(_saysAbsent(r.json)){ _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(r.json)}); return; }
  const n = _featureCount(r.json);
  if(n===null){ _crtSet(e.id, 'unavailable', {why:'the Pi sent something that is not GeoJSON', data:null}); return; }
  crtNoteCredit(r.json, idxMeta);
  if(n===0){ _crtSet(e.id, 'empty', {data:r.json, n:0}); return; }
  _crtSet(e.id, 'present', {data:r.json, n:n});
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
function crtNoteCredit(gj, meta){
  const s = (gj && (gj.attribution || (gj.properties && gj.properties.attribution)))
         || (meta && meta.attribution) || '';
  if(!s || typeof s!=='string') return;
  const t = s.trim();
  if(!t || t===CRT_ATTRIBUTION || CRT.credits.indexOf(t)>=0) return;
  CRT.credits.push(t);
  crtRenderCredits();
}
function crtRenderCredits(){
  const el=$('crt-credit'); if(!el) return;
  const base = el.dataset.base || (el.dataset.base = el.textContent.trim());
  el.textContent = CRT.credits.length ? (base + '  ·  ' + CRT.credits.join('  ·  ')) : base;
}

async function crtLoadAll(why){
  if(CRT._busy) return;
  CRT._busy = true;
  try{
    if(!CRT.area){
      crtAll().forEach(e=>_crtSet(e.id, 'no-area'));
      CRT.indexOk=false; CRT.indexWhy='no map area is active';
      return;
    }
    LOG.map('CRT: fetching chart layers for area "'+CRT.area+'" ('+(why||'refresh')+')');
    const idx = await crtFetchIndex();
    CRT.indexOk = !!idx.ok; CRT.indexWhy = idx.why || '';
    if(!idx.ok){
      // NOT "absent". Nobody was asked, so nothing may be reported as missing.
      crtAll().forEach(e=>_crtSet(e.id, 'unavailable', {why:idx.why, data:null}));
      LOG.warn('CRT: chart layers CANNOT TELL — '+idx.why);
      return;
    }
    for(const e of crtAll()){
      const meta = idx.layers[e.id] || null;
      if(!crtIsOn(e.id) && e.tier!==1){
        // Off means not asked — unless the index has already told us it is absent,
        // in which case that is a fact and worth keeping. The Pi's REASON is kept
        // with it, exactly as crtFetchLayer keeps it: one absence must not produce
        // two different sentences depending on whether the operator happened to have
        // the layer switched off. Dropping it here left the row reciting this
        // console's generic line about a file not being on the disk, over a Pi that
        // had already said which of four quite different things had happened.
        if(meta && meta.present===false) _crtSet(e.id, 'absent', {data:null, n:0, why:_crtWhyOf(meta)});
        else _crtSet(e.id, 'off', {data:null, n:0});
        continue;
      }
      // THE INDEX IS THE PI'S INVENTORY, so a layer it does not mention is a layer
      // the Pi does not have — say so from the index rather than firing a GET at
      // every unlisted layer just to be told 404 fifteen times over a tether. The
      // exception is a layer with a path of its own (the depth pair): those may be
      // served by a different part of the Pi that never appears in this index, and
      // "not in the CRT inventory" would be the wrong thing to conclude about them.
      if(!meta && !e.path){ _crtSet(e.id, 'absent', {data:null, n:0}); continue; }
      await crtFetchLayer(e, meta);
    }
    const bad = crtAll().filter(e=>{ const s=CRT.state[e.id]; return s && (s.status==='absent'||s.status==='unavailable'); });
    LOG.map('CRT: '+crtAll().filter(e=>(CRT.state[e.id]||{}).status==='present').length+' layer(s) drawn, '
           + bad.length+' reporting absent or cannot-tell');
  } finally {
    CRT._busy = false;
    crtRenderRows(); crtRenderBadge();
  }
}

/* The active area changed (or first appeared). */
function crtSetArea(name){
  const n = name || null;
  if(n === CRT.area) return;
  CRT.area = n;
  // The adopted rows, the wire bindings and the borrowed credits all belonged to the
  // OLD area's files. Carrying them over would leave the last canal's layer list on
  // screen over this one's water, which is the same class of error as leaving its
  // hazards drawn.
  CRT.extra = []; CRT.bind = {}; CRT.claimed = {}; CRT.credits = [];
  crtRenderCredits();
  crtAll().forEach(e=>_crtSet(e.id, n ? 'off' : 'no-area', {data:null, n:0}));
  crtBuildPanel(); crtRenderBadge();
  crtLoadAll('area changed');
}

/* ---- the panel ----------------------------------------------------------
   Rows are built from the table, so a layer added above appears here with its
   sentence attached and cannot ship as a bare glyph. Every row carries the title
   AND the aria-label, and the live state is appended through liveTitle so the
   explanation survives thetext being rewritten every few seconds.
   -------------------------------------------------------------------------- */
function crtGlyphSvg(e){
  const cls = 'crt-g' + (e.tier===1 || e.hazardish ? ' t1' : e.tier===2 ? ' t2' : ' t3');
  const shape = (e.tier===1 || e.hazardish)
    ? '<polygon points="7,2 13,2 18,7 18,13 13,18 7,18 2,13 2,7"/>'
    : e.tier===2 ? '<rect x="2.5" y="2.5" width="15" height="15" rx="3.5"/>'
                 : '<circle cx="10" cy="10" r="7.5"/>';
  const fs = (e.mark||'?').length>1 ? 7.5 : 9.5;
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
    ['OPERATIONS — ON BY DEFAULT',
     'TIER 2: where you can get the sub in and out, turn it round, and what is moored in its way. '
   + 'Shown unless you switch them off, and the choice is remembered on this handheld.'],
    ['EXTRAS — OFF BY DEFAULT',
     'TIER 3: everything else the Trust publishes that is worth keeping but not worth the clutter. '
   + 'Off means the console has not even asked the Pi for it — which is why these rows say NOT '
   + 'ASKED rather than anything that could be mistaken for "there is nothing there".'],
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
    row.classList.toggle('empty', st.status==='empty');
    row.classList.toggle('shown', on && st.status==='present');
    const pill = $('crt-state-'+e.id);
    if(pill){
      const word = CRT_STATE_WORDS[st.status] || st.status;
      // The saved survey and this session's own soundings are two counts, never one:
      // "+6" is the water this sub has been through since the last dive log was
      // written, and the row's sentence says so in full.
      const live = on ? crtLiveClause(e) : '';
      const n2 = live ? live.replace(/^ Plus (\d+) cell.*$/, ' +$1') : '';
      pill.textContent = ((st.status==='present' && on) ? (word+' · '+st.n) : word) + n2;
      pill.className = 'crt-state s-'+st.status + (n2 ? ' live' : '');
    }
    const btn = $('crt-toggle-'+e.id);
    if(btn){
      btn.textContent = on ? 'ON' : 'OFF';
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-checked', on ? 'true' : 'false');
      const s = (on ? 'SHOWN. Tap to hide ' : 'HIDDEN. Tap to show ') + e.name.toLowerCase()
              + ' on the map; the choice is remembered on this handheld. ' + crtWhat(e);
      btn.dataset.help = s; btn.title = s; btn.setAttribute('aria-label', s);
    }
    liveTitle(row, crtStateSentence(e));
  });
  if(missing) crtBuildPanel();
}

/* THE BADGE — absence, said on the MAP and not only inside a panel nobody opened.

   The whole point of the doctrine is that a layer which is not there must not be
   readable as a stretch of water with nothing in it, and a panel the operator has
   to open first cannot deliver that. Tier 1 gets its own, louder wording: if the
   hazard layers are not on this Pi then the map is not showing hazards at all, and
   that has to be the first thing anybody notices about it. */
function crtRenderBadge(){
  const el = $('crt-absent'); if(!el) return;
  if(!CRT.area){ el.classList.remove('on'); CRT._badge=''; return; }
  const t1 = crtTierList(1).filter(e=>{ const s=CRT.state[e.id]||{}; return s.status==='absent'||s.status==='unavailable'; });
  const rest = crtAll().filter(e=>e.tier!==1).filter(e=>{ const s=CRT.state[e.id]||{}; return s.status==='absent'||s.status==='unavailable'; });
  const roomy = MAP.expanded || MAP.blind;
  let text='', cls='', help='';
  if(t1.length){
    const allCannot = t1.every(e=>(CRT.state[e.id]||{}).status==='unavailable');
    text = roomy
      ? (allCannot ? 'HAZARD&nbsp;LAYERS&nbsp;·&nbsp;CANNOT&nbsp;TELL' : 'HAZARD&nbsp;LAYERS&nbsp;ABSENT&nbsp;('+t1.length+')')
      : 'NO&nbsp;HAZARD&nbsp;DATA';
    cls = 'tier1';
    help = (allCannot
      ? 'THE HAZARD LAYERS COULD NOT BE ASKED FOR. '
      : 'HAZARD LAYERS ARE MISSING FROM THIS PI. ')
      + t1.map(e=>e.name).join(', ') + ' — '
      + (allCannot
         ? 'nothing has been ruled in or out, so this map is not showing locks, weirs, sluices, '
         + 'culverts, tunnel portals or outfalls and cannot say whether there are any.'
         : 'the Pi looked and those files are not on the disk. An empty-looking stretch on this map '
         + 'therefore means NO DATA, not "nothing there".')
      + ' Do not read the absence of a mark as clear water. Open the LAYERS panel for the row-by-row '
      + 'answer.';
  } else if(rest.length){
    text = roomy ? (rest.length+'&nbsp;CHART&nbsp;LAYER'+(rest.length>1?'S':'')+'&nbsp;NOT&nbsp;SHOWN') : 'CHART&nbsp;GAPS';
    help = 'These chart layers are not being drawn because the Pi does not have them or could not be '
         + 'asked: ' + rest.map(e=>e.name).join(', ') + '. The hazard layers ARE loaded — this is '
         + 'about the operational and extra layers. Open the LAYERS panel to see which is which.';
  }
  const key = text+'|'+cls;
  if(key !== CRT._badge){
    CRT._badge = key;
    el.innerHTML = text;
    el.className = 'crt-absent' + (cls?(' '+cls):'') + (text?' on':'');
    if(help){ el.dataset.help = help; el.title = help; el.setAttribute('aria-label', help); }
  }
}

function crtTogglePanel(force){
  const p = $('crt-panel'); if(!p) return;
  CRT.open = (force===undefined) ? !CRT.open : !!force;
  p.classList.toggle('on', CRT.open);
  const b = $('map-crt-toggle');
  if(b){ b.classList.toggle('on', CRT.open); liveTitle(b, CRT.open ? 'panel open' : 'panel closed'); }
  if(CRT.open){ crtRenderRows(); crtRenderBadge(); }
}

function crtInit(){
  crtBuildPanel();
  const b = $('map-crt-toggle');
  if(b) b.addEventListener('click', (e)=>{ e.stopPropagation(); crtTogglePanel(); });
  const x = $('crt-close');
  if(x) x.addEventListener('click', (e)=>{ e.stopPropagation(); crtTogglePanel(false); });
  const r = $('crt-refresh');
  if(r) r.addEventListener('click', (e)=>{ e.stopPropagation(); crtLoadAll('operator asked'); });
  crtLoadPrefs().then(()=>{ CRT.ready=true; crtRenderRows(); crtLoadAll('boot'); });
  // A CANNOT TELL is a question, not a verdict: the Pi comes back, the tether is
  // replugged, and the layers should appear without the operator having to know
  // there is a refresh button. Bounded and slow — this is a background retry, not
  // a poll, and it never runs while a fetch is already in flight.
  setInterval(()=>{
    if(CRT._busy || !CRT.area) return;
    const stale = crtAll().some(e=>{ const s=CRT.state[e.id]; return s && s.status==='unavailable'; });
    if(stale) crtLoadAll('retrying the layers that could not be asked for');
  }, CRT_API.retryMs);
  LOG.state('CRT chart layers initialised ('+CRT_LAYERS.length+' known layers, hazards always on)');
}

/* ============================================================================
   DRAWING
   ============================================================================ */
const CRT_C = {
  hazard:'#ff8c1a', hazardDim:'rgba(255,140,26,.55)',
  ops:'#4dffa6',    opsDim:'rgba(77,255,166,.6)',
  extra:'rgba(200,170,255,.75)',
  ink:'#0c0118',
  surveyEdge:'rgba(255,255,255,.55)',
};
function _crtColor(e){ return (e.tier===1 || e.hazardish) ? CRT_C.hazard : e.tier===2 ? CRT_C.ops : CRT_C.extra; }

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
function _crtEach(gj, fn){
  if(!gj) return;
  const fs = gj.type==='FeatureCollection' ? (gj.features||[]) : (gj.type==='Feature' ? [gj] : []);
  for(let i=0;i<fs.length;i++) fn(fs[i], i);
}
/* One run of [lon,lat] traced into the current path. Returns false when none of it
   is anywhere near the frame, so a canal that leaves the screen costs a walk rather
   than a stroke — and the caller can skip the two or three paints that follow. */
function _crtTraceRun(ctx, coords, W, H, m){
  ctx.beginPath();
  let started=false, near=false;
  for(const c of coords){
    const s=lonLatToScreen(c[1], c[0]); if(!s) continue;
    if(s[0]>-m && s[0]<W+m && s[1]>-m && s[1]<H+m) near=true;
    started ? ctx.lineTo(s[0],s[1]) : (ctx.moveTo(s[0],s[1]), started=true);
  }
  return started && near;
}

/* THE KEEP-AWAY MARK. Shape first, colour second — the same rule the leak drop and
   the ROV glyph follow, and for the same reason: an operator who cannot pick orange
   out of green still has to be able to tell a lock from a slipway. */
function _crtMark(ctx, e, x, y, dpr){
  const col=_crtColor(e), t1=(e.tier===1 || e.hazardish);
  const r = (t1 ? 9 : e.tier===2 ? 7 : 5.5) * dpr;
  ctx.save();
  ctx.beginPath();
  if(t1){                                             // octagon: the stop-sign shape
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
  ctx.lineWidth = 1.6*dpr; ctx.strokeStyle = t1 ? CRT_C.ink : col; ctx.stroke();
  if(e.mark && (t1 || e.tier===2)){
    ctx.fillStyle = t1 ? CRT_C.ink : col;
    ctx.font = '800 '+((e.mark.length>1 ? 7.5 : 9.5)*dpr)+'px '+
               ((typeof getComputedStyle==='function' && getComputedStyle(document.body).fontFamily) || 'sans-serif');
    ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(e.mark, x, y+0.5*dpr);
  }
  ctx.restore();
}
function _crtStandoff(ctx, e, x, y, dpr, ppm){
  if(!e.standoffM) return;
  const rpx = e.standoffM * ppm;
  if(!(rpx>6) || rpx>4000) return;             // off-scale: no ring rather than a wall or a dot
  ctx.save();
  ctx.setLineDash([5*dpr, 4*dpr]);
  ctx.lineWidth = 1.4*dpr;
  ctx.strokeStyle = CRT_C.hazardDim;
  ctx.beginPath(); ctx.arc(x, y, rpx, 0, Math.PI*2); ctx.stroke();
  ctx.restore();
}

/* One layer. Lines and polygon outlines are stroked in the layer's colour (a weir
   is often a line across the cut, and where it IS a line the line is the useful
   drawing), then exactly one glyph per feature on top. */
function _crtDrawLayer(ctx, e, dpr, ppm, W, H){
  const st = CRT.state[e.id];
  if(!st || st.status!=='present' || !st.data) return;
  const col=_crtColor(e), t1=(e.tier===1 || e.hazardish);
  const m = 120*dpr;
  let drawn=0;
  let capped=false;
  _crtEach(st.data, (f)=>{
    if(drawn >= CRT_API.maxDraw){ capped = true; return; }
    const p=_crtParts(f);
    // lines + polygon rings
    const strokeRun=(coords)=>{
      if(!_crtTraceRun(ctx, coords, W, H, m)) return;
      ctx.strokeStyle='rgba(0,0,0,.55)'; ctx.lineWidth=(t1?5:3.5)*dpr; ctx.stroke();
      ctx.strokeStyle=col;               ctx.lineWidth=(t1?2.2:1.6)*dpr; ctx.stroke();
    };
    ctx.lineJoin='round'; ctx.lineCap='round';
    for(const l of p.lines) strokeRun(l);
    for(const poly of p.polys) for(const ring of poly) strokeRun(ring);
    if(p.rep){
      const s=lonLatToScreen(p.rep[1], p.rep[0]);
      if(s && s[0]>-m && s[0]<W+m && s[1]>-m && s[1]<H+m){
        _crtStandoff(ctx, e, s[0], s[1], dpr, ppm);
        _crtMark(ctx, e, s[0], s[1], dpr);
        drawn++;
      }
    }
  });
  // TWO NUMBERS, BECAUSE THE ROW SAYS TWO DIFFERENT THINGS ABOUT THEM. `drawn` is
  // how many marks this frame put on the glass, which is mostly a fact about where
  // the map is pointed; `capped` is the only case where this console decided to stop
  // short of the file. Conflating them had the row telling the operator a layer was
  // truncated every time they zoomed in.
  st.drawn = drawn; st.capped = capped;
}

/* Draw order is a safety order: extras, then operations, then hazards on top. A
   mooring glyph must never be able to sit over a lock. */
function crtDraw(ctx, dpr){
  if(typeof TILES==='undefined' || !TILES.last) return;      // no projection this frame
  const W=ctx.canvas.width, H=ctx.canvas.height, ppm=_crtPpm(dpr);
  ctx.setTransform(1,0,0,1,0,0);
  for(const tier of [3,2,1]){
    for(const e of crtTierList(tier)){
      if(e.kind==='depth') continue;                         // drawn underneath, by crtDrawDepth
      if(!crtIsOn(e.id)) continue;
      _crtDrawLayer(ctx, e, dpr, ppm, W, H);
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
function crtLiveCells(){
  if(!MAP.hasOrigin || !MAP.origin || !MAP.track.length) return [];
  if(CRT.live.cells && (performance.now()-CRT.live.at) < 1000 && CRT.live.n===MAP.track.length) return CRT.live.cells;
  const c = 3, bins = new Map();
  for(const p of MAP.track){
    if(!(typeof p.depth==='number') || p.depth<=0.05) continue;   // at the surface it says nothing about the water
    const k = Math.floor(p.x/c)+'|'+Math.floor(p.y/c);
    const prev = bins.get(k);
    if(!prev || p.depth>prev.depth) bins.set(k, {x:(Math.floor(p.x/c)+0.5)*c, y:(Math.floor(p.y/c)+0.5)*c, depth:p.depth});
  }
  const out=[]; bins.forEach(v=>out.push(v));
  CRT.live = {cells:out, at:performance.now(), n:MAP.track.length};
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
    const live = crtLiveCells();
    if(live.length && MAP.origin){
      const half = 1.5;                                  // the 3 m bin, halved
      for(const cell of live){
        const a=toLatLon(cell.x-half, cell.y-half, MAP.origin.lat, MAP.origin.lon);
        const b=toLatLon(cell.x+half, cell.y+half, MAP.origin.lat, MAP.origin.lon);
        const s0=lonLatToScreen(a.lat, a.lon), s1=lonLatToScreen(b.lat, b.lon);
        if(!s0 || !s1) continue;
        ctx.beginPath(); ctx.rect(Math.min(s0[0],s1[0]), Math.min(s0[1],s1[1]),
                                  Math.abs(s1[0]-s0[0]), Math.abs(s1[1]-s0[1]));
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
