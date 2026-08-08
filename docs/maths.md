# The mathematics of NEPTUNE

## How to read this

Everything in here is explained twice.

First comes **the story**: what the number is for, what goes wrong without it, and why
the code does the slightly strange thing it does. Stories are plain words and pictures.
There is no notation in them, nothing to decode, and nothing you need to have read
earlier in order to follow. If you are stood at a canal bank with cold hands trying to
work out why the depth gauge has gone blank, the story is the part written for you.

Then comes **the formal bit**, at the end of each topic under its own heading. That is
the same idea written as equations — the exact ones the code runs, with the same
constants, the same clamps, the same rounding, and a pointer to the file and function
where they live. It is there so that somebody comparing this document against the source
can check it line by line and catch us if we have drifted.

**You may skip every single formal bit.** Not "skim" — skip. They are deliberately
placed last in each topic and boxed off behind their own heading so you can jump clean
over them, and nothing later in the document ever depends on having read one. Read only
the stories and you will have the whole picture, including all of the interesting parts.
The formal bits carry the arithmetic; the stories carry the meaning, and the meaning is
where this system is unusual.

Because here is the thing to know going in. **The arithmetic in this codebase is easy.**
Multiplication, division, one sine and one cosine, a few running totals, and a single
two-by-two matrix that a first-year engineering student would recognise. If you came for
clever maths you will be disappointed within a page.

What is not easy — what this document is actually about — is a different question asked
over and over: *what is this number allowed to claim?* A tether measurement that bounds
where the sub can be without ever saying where it is. A map correction that suggests a
position without overwriting one. A speed model that is forbidden from being used as
evidence in the one test it would always pass. A sensor that has stopped answering and
must produce no number at all, rather than a comfortable one. Every odd-looking thing in
the code is odd on purpose for a reason of that kind, and where that happens the story
says so plainly instead of tidying it away.

The fourteen topics run roughly in the order the numbers get built: the graph paper and
the running total drawn on it (1–5), where the models those sums depend on come from and
how they are tested and measured (6–8), the two filters that tidy up the raw instruments
before the sums see them (9–10), the safety check that catches the sub being held still
by something it cannot see (11), the score that grades everything else (12), the one
quantity nobody has to build at all (13), and then the rule that has been running
underneath every one of them (14). They
cross-reference each other, so you can equally well start at whichever topic is
currently on fire and follow the links out.

So: read the stories. Skip the formal bits with a clear conscience. And if a symbol does
ambush you, the table below is the whole vocabulary in one place.

## Six places where this document describes something that is not built

Each of the six is set out **once**, in the section it belongs to, with the reasoning that
makes it interesting. This is not a summary of them and deliberately does not restate one;
it is an index, and it exists so that this document and the backlog cannot quietly disagree
about what is still open. All six are parked in the Open table of
[`.specs/tasks.md`](../.specs/tasks.md) — four of them as one labelled **pre-first-dive
batch**, because they are the ones that stop being fixable once the vehicle is in water.
If you fix one, close the row and delete the line here in the same commit.

| What | Where it is explained | Parked as |
|---|---|---|
| The payout that bounds the leash is a model on a stock hull, and the journal keeps it under the encoder's own name | [§4](#4-the-tether-clamp--the-leash-is-a-fact), and the footnote in [§15](#15-what-a-number-is-allowed-to-claim) | pre-first-dive batch (2 of 4) — *api* |
| The drift penalty is nested inside the snap branch | [§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie), [§12](#12-confidence--the-humility-score) | pre-first-dive batch (1 of 4) — *api* |
| The calibration tool takes the encoder over the operator's own tape measure | [§8](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no) sets out the three witnesses and the encoder's known bias; the precedence between them is stated only in the parked row | pre-first-dive batch (3 of 4) — *api* |
| The simulator's true speed table is the estimators' own, so the A/B gate runs in a world where the speed model is exactly right | [§7](#7-the-simulators-dirty-tricks--a-liar-with-a-fixed-seed), [§15](#15-what-a-number-is-allowed-to-claim) | pre-first-dive batch (4 of 4) — *api tests* |
| The snapped position, and the size of the correction, never reach the screen | [§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie) | *Snapping is invisible on the console* — client, post-hardware audit |
| Confidence is computed every tick, logged, and drawn nowhere | [§12](#12-confidence--the-humility-score) | *Confidence is never rendered* — client, after real dive logs |

## Contents

**Front matter** — [How to read this](#how-to-read-this) · [Six places where this document
describes something that is not
built](#six-places-where-this-document-describes-something-that-is-not-built) · [The symbols,
in plain words](#the-symbols-in-plain-words)

1. [Flat-earth geometry — the graph paper on the water](#1-flat-earth-geometry--the-graph-paper-on-the-water)
2. [Dead reckoning — counting strokes with your eyes shut](#2-dead-reckoning--counting-strokes-with-your-eyes-shut)
3. [Current compensation — the sub is walking on a travelator](#3-current-compensation--the-sub-is-walking-on-a-travelator)
4. [The tether clamp — the leash is a fact](#4-the-tether-clamp--the-leash-is-a-fact)
5. [Centreline snapping — the magnet that is not allowed to lie](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie)
6. [The speed lookup table — four dots and a tape measure](#6-the-speed-lookup-table--four-dots-and-a-tape-measure)
7. [The simulator's dirty tricks — a liar with a fixed seed](#7-the-simulators-dirty-tricks--a-liar-with-a-fixed-seed)
8. [Calibration forensics — the tool that is allowed to say no](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no)
9. [The heading complementary filter — two witnesses, one of them drunk](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk)
10. [The speed Kalman filter — a weather forecast, argued out ten times a second](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second)
11. [The snag detector — an if-statement with the instincts of a lie detector](#11-the-snag-detector--an-if-statement-with-the-instincts-of-a-lie-detector)
12. [Confidence — the humility score](#12-confidence--the-humility-score)
13. [Depth from pressure — the one number nobody has to build](#13-depth-from-pressure--the-one-number-nobody-has-to-build)
14. [The launch bank — measuring a bank against the water beside it](#14-the-launch-bank--measuring-a-bank-against-the-water-beside-it)
15. [What a number is allowed to claim](#15-what-a-number-is-allowed-to-claim)

## The symbols, in plain words

One line each, for looking up mid-equation without losing your place. Every formal bit
also restates the symbols it uses, so you should never have to come back here — but this
is the master list if you do.

**Conventions that apply everywhere.** Angles are degrees unless a formula says
otherwise. Headings use the compass convention: 0 is north, 90 is east, increasing
clockwise — which is why east comes out of a sine and north out of a cosine, the
opposite way round from school trigonometry. Any difference between two headings is
folded into the range minus-180 to plus-180 before it is used. And "absent" is not a
number: it is the code's `None`, meaning *nothing measured this*, and it is never a zero
in disguise.

### Time and motion

| Symbol | Plain words |
|---|---|
| $t$ | Timestamp of this reading, in seconds since the run began. |
| $dt$ | Seconds elapsed since the previous reading. Everything that accumulates, accumulates over one of these. |
| $u$ | Throttle, from minus one (full astern) to plus one (full ahead). A command, not a measurement. |
| $T$ | Thrust level, zero to one: how hard the motors are *actually* pushing, taken as the larger of the two thruster outputs. |
| $v$ | Forward speed through the water, metres per second, positive ahead. |
| $a$ | Measured forward acceleration, metres per second squared, positive ahead. |
| $h$ | Heading — the direction the sub is pointing, degrees clockwise from north. |
| $g$ | Measured turn rate about the vertical axis, degrees per second, positive clockwise. |

### Inside the filters

| Symbol | Plain words |
|---|---|
| $b$ | The gyroscope's learned zero-offset, degrees per second — what it reads while sitting perfectly still. |
| $b_a$ | The accelerometer's learned zero-offset, metres per second squared. |
| $e$ | Heading disagreement: how far the compass is from the filter's current answer, degrees, folded to ±180. |
| $\tau$ | Blend time constant, seconds — roughly how long the heading filter takes to lean most of the way towards the compass. |
| $\alpha$ | The per-tick blend fraction worked out from $\tau$ and $dt$: 0 means ignore the compass this tick, 1 means adopt it wholesale. |
| $z$ | The one measurement being offered to a filter this tick. |
| $P$ | How unsure the filter is of its own estimate (a variance, or a small matrix of them). Large $P$ means "I am guessing". |
| $Q$ | How much fresh uncertainty one tick of the real world adds. Large $Q$ means "anything could have happened since last time". |
| $r$ | How noisy a measurement is believed to be. Large $r$ means "do not listen to this too hard". Lower-case, as in the code. |
| $k_0$, $k_1$ | The gain: what fraction of the disagreement the filter accepts this tick — one per thing being estimated. `k0` and `k1` in the code. |

### Position and the map

| Symbol | Plain words |
|---|---|
| $x$, $y$ | Position in metres from the launch point: $x$ east, $y$ north. |
| $r$ | Straight-line range from the launch point to the estimated position, metres. Called `rng` in the code. |
| $L$ | Tether paid out, metres. An upper bound on range — never a position. Called `encoder_m` in the code. |
| $c$ | Confidence in the position estimate, zero to one. Only ever pulled *down*, never up. |
| $\text{lat}$, $\text{lon}$ | Latitude and longitude, degrees. $\text{lat}_0$, $\text{lon}_0$ are the launch origin's. |
| $R_E$ | Earth's radius in metres, the WGS84 equatorial figure, 6378137. |
| $m_p$ | Metres of travel per paddlewheel pulse — a calibration constant, measured per hull. |
| $W$ | The paddlewheel's averaging window, in seconds. |
| $N$ | A count of things: pulses in a paddlewheel window, samples in a calibration segment. |
| $\text{cal}$ | The magnetometer's own calibration score, 0 to 3. A reading in its own right, including at 0. |

### Depth and the hull

| Symbol | Plain words |
|---|---|
| $p$ | Absolute pressure at the sensor, pounds per square inch. |
| $p_0$ | The configured surface pressure — what "no water above me" reads today, same units. |
| $k_p$ | Pressure added per metre of water, pounds per square inch per metre. |
| $d$ | Depth below the surface, metres. |
| $D_1$, $D_2$ | The depth chip's raw pressure and temperature words, straight off the wire. |
| $C_1 \ldots C_6$ | The depth chip's factory calibration coefficients, read from its own memory at start-up. |

**Seven clashes worth knowing about, three of them because the code has them too.** $r$ is
a range in metres everywhere except inside the speed filter, where it is that filter's
distrust of the measurement — which is the name the code itself uses, so the document
follows it rather than inventing a tidier one. $y$ is a northing when we are talking
about position, but an *innovation* (a measured-minus-expected disagreement) inside the
speed filter. And $T$ is thrust level everywhere except in the depth chip's own
compensation arithmetic, where it is the chip's temperature, straight off the datasheet.

Four more are the alphabet simply running out. $k$ is a scale factor in §4, a lag
fraction in §7, the depth model's slope in §8, and a pair of filter gains ($k_0$, $k_1$)
in §10. $m$ is the magnetic disturbance the simulator injects in §7 and the measured
magnetic heading in §9 — and $m_p$, distinct from both, is metres per paddlewheel pulse.
$D$ is a distance along the centreline in §5, a true depth in §7, an
operator-measured distance in §8, and the depth chip's raw words ($D_1$, $D_2$) in §13;
$s$ is a sign in §6, a snapped coordinate in §5, one log sample in §8, and the random
generator's state in §7. Each formal bit restates the symbols it uses before it uses
them, so the local list always wins — and where a symbol is doing an unusual job locally
the list says so in brackets.

## 1. Flat-earth geometry — the graph paper on the water

Nobody lays out a tennis court with a globe. You bang a peg into the ground, take a tape
measure, and everything after that is "so many paces from the peg, in this direction".
The court is on a round planet and the planet does not care, because the court is
twenty-four metres long and the planet is forty million.

The sub works at tennis-court scale. It is on a tether, in a canal, a few hundred metres
from a person standing on the towpath. So the navigation code does the tape-measure
thing. The moment an origin is set — a phone fix, a tap on the map, a hand-entered pair
of numbers — a peg goes into the world and a sheet of imaginary graph paper is laid over
the water with the peg at nought-nought. From then on the sub's position is two numbers:
metres east of the peg, metres north of the peg. That is the whole coordinate system. No
great circles, no ellipsoids, no geodesics. Two numbers and a peg.

Latitude and longitude only reappear at the edges, where the graph paper has to meet
something that speaks map: drawing the track over satellite imagery, writing the dive
log, importing the canal's centreline so it lands on the same sheet. So there are two
translations, one each way, and they are built as exact mirrors of each other — the same
constants used forwards and backwards. Convert out and back and you get precisely the
number you started with. That matters more than it sounds, because the console does the
round trip constantly: the server sends metres, the browser turns them into map
coordinates, and if the two directions disagreed even slightly the sub would visibly
crawl away from its own track.

There is exactly one place where the code admits the Earth is round, and it is a good
one. Going north, a metre is a metre anywhere on the planet — the lines of latitude are
evenly spaced all the way up. Going east, they are not. Lines of longitude all meet at
the poles, so they squeeze together as you go north: a degree of longitude buys you
about 111 kilometres at the equator, and nothing at all at the North Pole. So east-west
distances get multiplied by a squeeze factor — the cosine of the launch latitude, which
is just a number that starts at one at the equator and shrinks to zero at the pole. At
British canal latitudes it is about 0.62, which is to say a degree of longitude up here
is worth only about six-tenths of the ground a degree of latitude is worth. Turn that
around and it is the thing the code actually needs: to walk one metre east you have to
spend about one and a half degrees' worth of longitude for every one degree's worth of
latitude a metre north would cost you. Cramped lines mean each step eats more of them.
That single number is the entire round-earth tax, and it is levied once, on one line.

Levied once *literally*: the squeeze factor is computed from the origin's latitude and
never touched again, even as the sub moves. This looks like laziness and is not. Move
500 metres north of a British canal launch and the true squeeze changes by about a
hundredth of a percent — five centimetres across a box 500 metres wide. Recomputing it
every tick would buy back those five centimetres at the price of making the east
coordinate depend on the north coordinate, which is a position quietly feeding back into
itself. Frozen at the origin, the graph paper is a fixed, honest grid: a bit stretched,
but stretched by the same amount everywhere, forever.

So how wrong is the flat sheet? The globe it uses is a perfect sphere with the Earth's
*equatorial* radius, and the real Earth is slightly squashed, so at canal latitudes the
sheet is off by well under a metre per kilometre north-south and around two metres per
kilometre east-west. Crucially that is a *stretch*, not a wander — a constant scale
error, the same on every tick, not something that accumulates or wobbles.

Now put that next to the other numbers in the room. The origin fix itself is refused
unless the phone claims fifteen metres of accuracy or better — you can insist, but you
have to say so out loud — and fifteen metres is the *floor* under everything that
follows: the whole sheet of graph paper is nailed down to that peg and cannot be more
accurate than it. And the dead reckoning that draws the track is good to five-to-fifteen
percent of the distance travelled, which at 300 metres is fifteen to forty-five metres.
The flat-earth approximation is, by a wide margin, the smallest source of error in the
system. Spending effort on proper geodesy here would be polishing the least dirty window
on the boat.

And when there is no peg? Then there is no answer. There is no default origin, no
"assume we launched at the last place", no zero-zero. Without a measured origin the
estimator is not built at all and the map has nothing to draw. That is the shape of
every decision in this document: a missing measurement produces a missing answer, never
a convenient one.

### The formal bit

where:

- $R_E$ — Earth radius constant, $6378137.0$ m (WGS84 equatorial), from config
- $x$ — metres **east** of the origin
- $y$ — metres **north** of the origin
- $\text{lat}_0, \text{lon}_0$ — origin latitude and longitude, degrees
- $\text{lat}, \text{lon}$ — the point's latitude and longitude, degrees

Local metres to geographic (`to_latlon`):

$$\text{lat} = \text{lat}_0 + \frac{y}{R_E} \cdot \frac{180}{\pi}$$

$$\text{lon} = \text{lon}_0 + \frac{x}{R_E \cdot \cos(\text{lat}_0)} \cdot \frac{180}{\pi}$$

Geographic to local metres (`to_local`):

$$y = (\text{lat} - \text{lat}_0) \cdot \frac{\pi}{180} \cdot R_E$$

$$x = (\text{lon} - \text{lon}_0) \cdot \frac{\pi}{180} \cdot R_E \cdot \cos(\text{lat}_0)$$

Conventions and properties as implemented:

- $\cos(\text{lat}_0)$ takes $\text{lat}_0$ in **radians**; every other latitude and
  longitude in these four lines is in **degrees**. The conversion factors $180/\pi$ and
  $\pi/180$ are written out explicitly rather than folded into a constant.
- The cosine is evaluated at the **origin latitude only**, in both directions. It is
  never re-evaluated at the point's own latitude, so the two functions are exact
  inverses: `to_local(to_latlon(x, y, ...), ...)` returns $(x, y)$ up to floating-point
  round-off.
- No ellipsoid, no flattening term, no meridian-arc series. $R_E$ is the equatorial radius,
  not the mean radius and not the local radius of curvature.
- Resulting scale error against WGS84 at $\text{lat}_0 = 51.5°$: north-south
  $\approx +0.055\%$ ($0.55$ m per km), east-west $\approx -0.21\%$ ($2.05$ m per km).
  The frozen $\cos(\text{lat}_0)$ contributes a further relative error of
  $\tan(\text{lat}_0) \cdot y / R_E$, which is $\approx 0.01\%$ at $y = 500$ m — about
  $5$ cm across a $500$ m box. All three are systematic scale errors, not random walk.
- Compare: origin gate $15$ m (`max_origin_accuracy_m`, enforced as HTTP 422 on
  `POST /api/origin` unless `?override=true`), dead-reckoning error $5\text{–}15\%$ of
  distance travelled.

`api/nav/geo.py — to_latlon()`, `to_local()`; constant `EARTH_R` in
`api/nav/config.py`; mirrored line-for-line in the browser at `client/js/core.js` —
`toLatLon()`, `toLocal()`.

## 2. Dead reckoning — counting strokes with your eyes shut

Swim a length of a pool blindfolded. You know roughly which way you are pointing and
roughly how fast you are going, so every second you can say "about a metre that way" and
add it to a running total. That is dead reckoning, and it is how this sub knows where it
is. Radio does not reach through water, so there is no satellite fix to check against —
once the sub is under, the running total is all there is.

Each tick the estimator asks three questions. Which way am I pointing? How fast am I
going? How long since I last asked? Multiply the speed by the time to get a distance,
use the heading to split that distance into an east part and a north part, add both to
the running total. That is the whole of it. The arithmetic is Tuesday-level on purpose;
every interesting decision in this file is about what the estimator is *allowed to
claim*, not about the sums. Where the first two answers come from is a story each: the
heading straight off the compass, or out of the filter in
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk); the speed
out of the table in [§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure), or
out of the paddlewheel-backed filter in
[§10](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second).

Errors pile up, and it is worth being clear about why, because it is not that the
estimator is bad. Each step's error is small, and each step's error is *added to the
total and never taken out again*. Nothing ever comes along and says "actually, you are
here". So the error grows roughly in step with the distance travelled — about five to
fifteen percent of it — which is a fair deal you can plan a dive around: keep the runs
short, surface and re-fix.

The alternative deal is much worse, and avoiding it is the first sacred rule. The sub
carries an accelerometer. You could, in principle, add up acceleration to get speed and
then add up speed to get position — integrating twice. Do not. Adding up a number twice
means adding up its *errors* twice, and a small steady bias in an accelerometer becomes
a growing speed error, which becomes a position error that grows faster and faster.
Minutes of that and the sub is hundreds of metres out. So the accelerometer is logged,
and it feeds the speed filter, and it is never allowed anywhere near the position. The
code says so in four separate places, one of which is addressed directly to whoever next
thinks it would be clever.

The second sacred rule: depth is *measured*, never worked out. There is a pressure
sensor and it answers, or it does not answer. When it does not, the reported depth is
nothing at all — not the last depth, and above all not zero, because zero means "at the
surface", which is the one depth a descending submarine is definitely not at. The
temptation is enormous, this system has paid for it twice, and both invoices are
itemised in [§13](#13-depth-from-pressure--the-one-number-nobody-has-to-build).

Then there is the convention swap, which deserves its own paragraph because it is a
career-long generator of bugs. Every maths textbook measures angles anticlockwise from
east. Every compass measures them clockwise from north. The sub has a compass, so
navigation uses the compass convention — and that means the east-west part of a heading
uses sine where a textbook would use cosine, and the north-south part uses cosine where
a textbook would use sine. They are swapped. If you ever meet a track where the sub
turns right and the map turns left, or where a heading of ninety degrees sends the dot
due north, this swap is where to look first. The code marks the ends of both lines with
the word "east" and the word "north", and the simulator carries the same comment,
precisely because everyone gets this wrong once.

Now the part that is recent and matters most. **With no heading there is no track.**

Speed is one number. A position needs a direction to spend that number on. When the
compass stops answering — a dead chip, a loose connector, a hull that has gone quiet —
the estimator has a speed and no direction, and its options are: use the last heading,
use the heading the dive launched on, or use north. Every one of those is a specific
claim about the world. The last heading claims the sub held its course. The launch
heading claims it turned back to the bearing it set off on. North claims north. Not one
of them was measured, and the operator will drive on whichever one is drawn.

So the estimator does none of them. It holds. The dot stops where it was, confidence
drops to a tenth, and a flag on the frame says in as many words that the position was
held — because "the map has stopped following the sub" is the sentence the operator
needs and no amount of greying-out a bearing readout says it. A held track is visibly
stale. A moving track is invisibly false. The reasoning written in the file is that the
sub is now somewhere within speed-times-time-held of that dot, which is exactly the
instruction a person needs in order to go and look.

That tenth is chosen against the other confidence floors rather than plucked out of the
air, and it is the lowest rung on the ladder: a snagged sub, which is still being
tracked, deliberately scores *higher*. The whole ladder, and the reason each rung sits
where it does, is [§12](#12-confidence--the-humility-score).

Two small consequences of the same principle. The current correction is held too, even
though a current is a thing that keeps flowing whether the compass works or not —
because that current is a number a human typed in, not something the sub measured, and
letting it creep the dot along while every instrument is silent is the same invented
position wearing a smaller number. And the *speed* is still reported, right there on the
frame, because the paddlewheel really did measure it and it is really true. A
measurement is not suppressed for want of a companion. It simply is not allowed to
become a position.

Finally, time. The gap between ticks is the difference between this sample's clock and
the last one's. The very first sample of a dive has no predecessor, so its gap is zero
and nothing moves — the first tick establishes the clock rather than the track. And a
*negative* gap, which happens when a log is replayed or a clock is reset, is floored at
zero rather than being allowed to run the track backwards. The clock itself is the
navigation loop's own metronome — the sensor source adds one nominal period per read
rather than reading a wall clock — so the integration runs on the schedule the loop
intends, not on however long the Pi actually took.

### The formal bit

where:

- $t_k$ — sample timestamp, seconds since dive start (loop-nominal clock)
- $\Delta t_k$ — integration interval for tick $k$, seconds
- $v$ — speed through the water, m/s (signed by throttle)
- $h$ — heading, **degrees, compass convention**: $0° = $ north, $90° = $ east, clockwise
- $x, y$ — running position, metres east and metres north of the origin
- $c_x, c_y$ — the current-compensation velocity components, m/s (see [§3](#3-current-compensation--the-sub-is-walking-on-a-travelator))
- $r$ — straight-line range from origin, metres
- $c$ — confidence, $0$ to $1$

Interval, with the floor and the first-tick case:

$$\Delta t_k = \max(0,\; t_k - t_{k-1}), \qquad \Delta t_0 = 0$$

Integration, **only when a heading was measured** ($h \ne \text{None}$):

$$v_x = v \sin(h) + c_x \qquad \text{(east)}$$

$$v_y = v \cos(h) + c_y \qquad \text{(north)}$$

$$x_k = x_{k-1} + v_x \, \Delta t_k, \qquad y_k = y_{k-1} + v_y \, \Delta t_k$$

When $h = \text{None}$: $x_k = x_{k-1}$, $y_k = y_{k-1}$, and $c \leftarrow 0.1$
(`NO_HEADING_CONFIDENCE`). The current terms $c_x, c_y$ are **not** applied. The
`no_heading` flag is set on the output frame.

Depth is assigned, never integrated: $d_k = s.\text{depth\_m}$, including $\text{None}$.
There is no fallback to $d_{k-1}$ and no $0.0$ default.

Range, computed after integration and before the tether clamp:

$$r = \sqrt{x_k^2 + y_k^2}$$

Confidence is a set of floors combined by minimum — the lowest applicable wins. It
starts at $1.0$ every tick and is knocked down here in the order $0.1$ (no heading),
$0.5$ (tether clamp), $0.6$ (`mag_cal`), $0.7$ (snap offset); the $0.4$ snag floor is
applied later, by the estimator wrapper. The full cascade, with what each value means
and what to do about it, is [§12](#12-confidence--the-humility-score).

The `mag_cal` test is written as `is None or < 2`, in that order, so a missing IMU is
handled before the comparison rather than raising on it — $\text{None}$ is "no IMU
answered", which is a different and worse claim than $0$ ("a compass answered and says
do not trust it").

Rounding happens **only on the way out**. The frame carries latitude and longitude to
$7$ decimal places ($\approx 1$ cm), $x$ and $y$ to $2$, heading to $1$, depth to $2$,
speed to $3$, confidence to $2$. The estimator's internal $x$ and $y$ keep full precision
— rounding the running total would make the round-off itself accumulate. The depth
rounding is the estimator's own, applied on top of the $3$ already applied by the sensor
source, which is why the dive log's depth is centimetres where the sample it came from
was millimetres (see [§13](#13-depth-from-pressure--the-one-number-nobody-has-to-build)).

Where the accelerometer goes, since this section's first sacred rule is about exactly
that: `accel_fwd_ms2` reaches one consumer in the estimator path, `SpeedKF.update()`,
and is otherwise only written to the logs. It reaches no position term and no snag term
— `SnagDetector.update()` takes a timestamp, two thruster outputs and a speed, and has
no acceleration argument at all. The field comment at `api/nav/models.py` still says the
accelerometer feeds the snag detector; that half of the comment is stale, and the
routing above is what the code does.

`api/nav/deadreckoning.py — DeadReckoner.update()`; the no-heading rule is stated in
the module docstring and the constant is `NO_HEADING_CONFIDENCE`. Same trigonometric
convention in `api/nav/sim.py — Simulator.step()` and `client/js/map.js` (sim-only
fallback integrator). Acceleration's single consumer: `api/nav/estimator.py` —
`FilteredEstimator.update()`.

## 3. Current compensation — the sub is walking on a travelator

Airport travelator. Your legs are doing a steady one-and-a-bit metres per second. The
belt is doing another two-thirds of that. Walk with the belt and you cross the terminal
at over two metres per second; walk against it and you barely move; stand still and you
still get somewhere. Your legs know nothing about any of this. They are doing exactly
the same work in all three cases.

The sub is on a travelator made of water. Its propeller pushes against the water and
both of the things that estimate its speed — the throttle model and the paddlewheel —
are measuring the legs, not the ground. They tell you how fast the sub is moving
*through the water*, which is precisely the number that cannot tell you where the sub
has got to if the water itself is going somewhere. On a still pond the two are the same.
In a canal after rain, or anywhere near a lock, they are not.

So the estimator adds a second arrow. The first arrow is the sub's own motion through
the water, pointing along the heading. The second is the water's own motion, pointing
along its own bearing at its own speed. Lay them tip to tail and the arrow from start to
finish is motion over the ground, which is what the dot on the map is entitled to move
by. That is all current compensation is: one extra arrow, added every tick.

The blunt truth about the second arrow is that today somebody types it in. It is one
constant — a bearing and a speed — set once for the whole dive through the navigation
API, and it defaults to nothing at all. Shipping with zero means the shipped assumption
is "still water", which is the right default for a pond and a known simplification for a
canal on a wet week. It is also worth saying plainly that there is no control on the
console for it: setting a current today means making an HTTP call by hand.

Why not learn it? Because learning a current means catching the sub disagreeing with
itself, and there is nothing available to disagree with. The paddlewheel measures
through the water, so it cannot see the belt any more than your legs can — you cannot
detect a travelator by studying your own stride. Satellites do not reach underwater. The
only two independent statements about where the sub actually got to are a surface re-fix
and the waterway centreline, and the centreline is explicitly a suggestion that is not
allowed to feed back into the estimate
([§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie)). A current
inferred from the estimator's own output would be the model marking its own homework —
the trap that [§8](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no) is
built entirely around, and one this codebase does not let a model walk into.

So it stays a typed-in constant: an operator's guess, labelled as an operator's guess,
held fixed, and — for a dive that finishes normally — written into the dive record so
that a year later someone can argue with it. Learning it automatically is future work,
and it is future work that needs a second, independent source of position before it is
even coherent, not just more code.

One honest wrinkle. The current is recorded in the finished dive file but is *not* in
the header of the crash-proof journal that gets written as the dive happens. A dive that
ends badly and is rebuilt from that journal comes back without the current that was in
force, and the replay tool re-runs such logs with zero flow and says so in its own notes
rather than quietly pretending.

### The formal bit

where:

- $c_s$ — current speed, m/s (`FlowVector.speed_ms`, default $0.0$)
- $c_b$ — current bearing, degrees, compass convention ($0° = $ north, clockwise) (`FlowVector.bearing_deg`, default $0.0$)
- $c_x, c_y$ — current velocity, east and north components, m/s
- $v, h$ — the sub's through-water speed and heading, as in [§2](#2-dead-reckoning--counting-strokes-with-your-eyes-shut)
- $v_x, v_y$ — over-ground velocity, east and north, m/s

The current as a vector, using the same compass convention (east takes sine, north takes
cosine):

$$c_x = c_s \sin(c_b), \qquad c_y = c_s \cos(c_b)$$

Vector addition — through-water velocity plus water velocity equals over-ground
velocity, which is what gets integrated:

$$v_x = v \sin(h) + c_x, \qquad v_y = v \cos(h) + c_y$$

Properties as implemented:

- $c_s$ and $c_b$ are **constants for the dive**. They are functions of neither $t$ nor
  $x$ nor $y$, so over a dive of duration $T$ the current contributes a pure displacement
  of $c_s T$ along $c_b$, independent of the route taken.
- Both terms are applied **only on ticks where a heading exists**. On a held tick they are
  skipped entirely, so a held position is held completely.
- There is no estimation step, no residual, no update from observed drift. The values are
  whatever was last POSTed.

`api/nav/deadreckoning.py — DeadReckoner.update()` (the two velocity lines); the vector
is `api/nav/models.py — FlowVector`, set live by `POST /api/nav/flow` in
`api/nav/service.py — set_flow()`, and applied to ground truth by the identical two
lines in `api/nav/sim.py — Simulator.step()`.

## 4. The tether clamp — the leash is a fact

The sub is on a cable, and the cable comes off a spool with a counter on it. The counter
knows how many metres have left the drum. That is not a sensor reading about where the
sub is — it is a fact about the world, and the difference is everything.

Start with the counter, because everything in this section is built on what a counter is
allowed to say. Then hold on to the fact that hardly any hull has one fitted yet, which
is the last thing this section deals with and the one that changes what you should
believe on a stock install.

Here is the fact: the sub cannot be further from the spool than the amount of cable that
has left the spool. Cable is not a laser. It sags, it loops, it drapes over a shopping
trolley, it wanders round a bend. So forty metres of payout tells you the sub is *no
more than* forty metres away. It never tells you the sub *is* forty metres away, and it
says nothing whatsoever about which direction. It is a bound, in one direction, and that
is the whole of its testimony.

Now suppose the estimator's running total says the sub is fifty metres out and only
forty metres of cable have been paid. One of the two is wrong, and it is not the cable.
So the code pulls the dot in.

The clever bit is *how*. Multiply the east number and the north number by the same
factor. That is it. Scaling both coordinates by one number is exactly the operation
"slide the dot along the straight line from the origin to where you thought it was,
until it is at the right distance" — the distance shrinks by that factor and the
direction does not move at all, to the last decimal place. Which is the correct shape
for the argument, because the leash disputes how far and has no opinion about which way.
The arithmetic was chosen so that it is *incapable* of saying anything the cable did not
say. Adjust the coordinates independently and you would be inventing a bearing
correction out of a distance measurement.

And getting caught costs confidence — down to a half
([§12](#12-confidence--the-humility-score)). This is the part people skip, and it is the
point. If the clamp had to fire, the speed model was lying: the estimator integrated
more distance than there is cable to support it. Maybe the throttle-to-speed table is
calibrated high, maybe the paddlewheel's metres-per-pulse is a guess, maybe the heading
has been drifting and the track has been curving off into nowhere. The clamp fixes the
*symptom* — a dot standing outside its own leash — and the confidence drop is the
system's honest admission that the *cause* is still there and will do the same thing
again next tick. A clamped position that still read as a healthy fix would be the
estimator covering for itself.

Two more deliberate details. A payout of zero is treated as no bound at all and the
clamp is skipped. Ask a hull whose spool encoder is not wired, or has died, how much
cable has gone over the side and it answers zero — and zero, taken literally, would mean
"no cable has left the drum, therefore the sub is exactly at the origin", which would
drag the dot home and hold it there. So a silent spool *loosens* the leash rather than
tightening it to nothing. A bound you cannot read is not a bound of zero.

**And here is the part that has to be said out loud, because on a stock install the
leash is not a measurement at all.** Almost no hull has a spool encoder fitted yet, and
the shipped configuration knows it. It asks the spool first — a real length always wins,
every tick, no argument — and when the spool has nothing to say it falls back on a
model: add up how hard the motors are being asked to push, multiply by the seconds,
multiply by one-and-a-bit, and call that the cable. Which it is not. It is a guess about
the cable, and on the default install it is the guess, not the counter, that sets the
leash.

What makes the guess admissible is that it is deliberately too big. The one-and-a-bit
credits the sub with a metre a second at full throttle and then adds twenty per cent on
top, which is more distance than the shipped speed table would ever admit to at that
lever position. That asymmetry is the entire licence. A bound that is too generous can
only ever fail to catch a lie; it cannot manufacture one. An under-estimate would do the
opposite — haul a perfectly good dot inwards and dock its confidence on the strength of
arithmetic nobody measured — and that is the direction this system never lets a model be
wrong in.

Which is a rarer permission than it looks, and worth comparing with the one other place
a model gets to lean on an estimate. The speed filter of
[§10](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second)
also lets the throttle table stand in when the paddlewheel goes quiet — but it labels
that speed as the table's on every frame it leaves in, and it widens the table's error
bars in proportion to its own cheek. Here there is no label and no error bar: the clamp
cannot tell a modelled bound from a counted one and does not try to. The whole of the
justification is that it is one-sided, which is why it is worth reading this section
knowing which of the two you have got.

Two consequences worth carrying to the bank. The model keeps running even while a real
encoder is answering, so a spool counter that dies mid-dive drops back to a loose bound
instead of a suddenly, wrongly tight one. And the modelled figure only ever climbs: it
has no idea the cable is being wound back in, because it never knew about cable. A real
spool does know, and counts back down. Haul fifty metres in on a hull with no encoder
and the leash stays as slack as it was at the far end of the run — which is the safe
direction to be wrong, and still wrong.

And unlike the map-snapping of
[§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie), the clamp writes
back. It changes the estimator's own running position, so the next tick continues from
the clamped dot. That is not an inconsistency, it is the same principle applied to a
different kind of statement: the cable is a physical fact about where the sub can
possibly be, and a fact is allowed to correct the ledger. A drawn line on a map is an
opinion about where the sub probably is, and an opinion is not.

### The formal bit

where:

- $x, y$ — running position after integration, metres east and north of the origin
- $r$ — straight-line range from origin, metres
- $L$ — tether payout as the estimator receives it, metres, cumulative (`SensorSample.encoder_m`)
- $L_e$ — the spool encoder's own reading, metres; $0$ when no encoder answers
- $\hat L$ — the modelled payout the default sensor source carries, metres
- $u$ — throttle as the sensor source reports it: the mean of the two thruster outputs, so $0$ while disarmed
- $k$ — the scale factor applied to both coordinates (a scale factor here, not a lag or a gain)
- $c$ — confidence

$$r = \sqrt{x^2 + y^2}$$

The clamp fires only when there is a bound and the bound is violated — $L > 0$ **and**
$r > L$:

$$k = \frac{L}{r}, \qquad x \leftarrow k\,x, \qquad y \leftarrow k\,y, \qquad r \leftarrow L$$

$$c \leftarrow \min(c,\; 0.5)$$

Properties as implemented:

- $0 < k < 1$ whenever the clamp fires, and since $k > 0$ the bearing is exactly
  preserved: $\text{atan2}(k y,\, k x) = \text{atan2}(y,\, x)$. Only the magnitude
  changes.
- $L \le 0$ disables the clamp entirely. It is not treated as "the sub is at the origin".
- The clamp mutates the estimator's persistent $x$ and $y$, so tick $k+1$ integrates from
  the clamped position.
- It runs **after** integration and **before** snapping, and it runs on held (no-heading)
  ticks too, since $r$ is recomputed from the held coordinates every tick.
- $r$ is only replaced by $L$ when the clamp fires; otherwise the reported range is the
  plain distance from the origin.

**Where $L$ comes from, which depends on the sensor backend.** `RealSensorSource`
publishes the encoder and nothing else, $L = \max(0,\ L_e)$, with $0$ substituted when
the readback is absent. The **shipping default** is `VehicleSensorSource`
(`NAV_SENSORS` $=$ `vehicle`, `api/nav/config.py`), which integrates a model every tick
and uses it whenever the spool is silent:

$$\hat L \leftarrow \hat L + 1.2\,|u|\,\Delta t, \qquad L = L_e \ \text{ if } L_e > 0, \ \text{ else } \hat L$$

Properties of that substitution, as implemented:

- The shipped default table satisfies $\text{LUT}(|u|) \le 1.2\,|u|$ for every
  $|u| \in [0, 1]$: it starts at $(0,0)$ and every one of its points lies below the line
  $1.2\,t$, so every straight segment joining them does too. Hence $\hat L$ over-states
  the distance the same throttle would have contributed through the table, and the bound
  stays one-sided — which is the whole justification for using a model here at all.
  The guarantee is against the **table** only: a paddlewheel-driven speed
  ([§10](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second))
  and a hand-entered current ([§3](#3-current-compensation--the-sub-is-walking-on-a-travelator))
  are both outside it, and either can in principle push $r$ past $\hat L$ on its own.
- $\hat L$ is monotonically non-decreasing. It has no mechanism for the drum rewinding
  and never falls. $L_e$ does: `RealHardware.read_payout_m()` carries no high-water mark
  and clamps only at $0$.
- $\hat L$ accumulates whether or not the encoder is answering, so an encoder that
  fails mid-dive falls back to a loose bound rather than a tight one.
- $u$ here is $(left + right)/2$, the outputs the software is commanding, which are $0$
  while disarmed or in failsafe — so a jammed joystick on a disarmed sub pays out no
  modelled cable.

`api/nav/deadreckoning.py — DeadReckoner.update()`; the payout reading and its
deliberate zero-means-absent behaviour, `api/nav/sensors.py` —
`RealSensorSource.read()`, `VehicleSensorSource.read()`, `get_sensor_source()`;
the encoder itself, `api/hardware.py — RealHardware.read_payout_m()`.

## 5. Centreline snapping — the magnet that is not allowed to lie

A canal is a one-dimensional world. It is four metres wide and forty kilometres long,
and whatever the estimator's arithmetic says, the sub is in the water. So if the console
has the drawn line of the waterway — and it does, imported from OpenStreetMap when the
offline area was cached — it can take the estimate and put it on the line. Sideways
error simply disappears, and the only question left is how far along you are, which is
the only question a canal actually asks.

The geometry is a ball and a roof. The waterway arrives as a chain of straight segments,
so take one segment and treat it as a sloped roof. Drop a ball straight down onto it: it
lands at one specific spot, the point on the roof closest to where the ball started. Do
that for every segment in the chain, keep whichever landing spot is closest, and that is
the snapped position.

The wrinkle is falling off the end. If the ball is past the ridge, the spot "directly
beneath" it is out in mid-air past the end of the roof — which would put the sub on an
imaginary extension of a canal that stops there. So the landing spot is pinned to the
segment: past one end, you land on that end; past the other, on the other. Every answer
is on a piece of waterway that exists. It also means a corner between two segments is
never a hole — the ball lands on the shared endpoint from both sides.

Then come the honesty valves, and they are the reason this section exists.

**Twenty-five metres and the magnet lets go.** If the nearest water is more than
twenty-five metres from the estimate, nothing is snapped at all. Something has gone
genuinely wrong — the estimate has drifted badly, or the imported centreline does not
describe the water the sub is in — and hauling the dot twenty-five metres sideways to
make the picture tidy would be manufacturing a position out of a map. The threshold is
configurable, and it defaults to twenty-five.

**The gap is an instrument.** Between eight and twenty-five metres, the snap happens
*and* confidence is knocked to seven-tenths ([§12](#12-confidence--the-humility-score)).
The distance between where you reckoned you were and where the water actually is, is the
only direct measurement of drift anywhere on this vehicle — nothing else aboard can see
its own error. So it is read as a drift-o-meter, and the correct response to it climbing
is to surface and take a fresh fix, not to admire how neatly the track now follows the
canal.

There is a real oddity in that pair of numbers, and it is worth stating rather than
smoothing: past twenty-five metres the magnet lets go, and the confidence knock goes
with it. The drift-o-meter reads between eight and twenty-five metres and then stops
reading exactly when the drift is worst. The estimate that is furthest from the water is
the one that gets no penalty at all.

**The snap never overwrites the reckoning.** This is the big one. The estimator's
running position is not touched by the snap — not nudged, not blended, not one bit. The
snapped coordinates go out on the frame *beside* the raw un-snapped ones, and the
permanent dive log stores the un-snapped numbers with a flag noting that a snap was
available. Next tick starts from where the reckoning actually got to — the exact
opposite of the tether clamp in [§4](#4-the-tether-clamp--the-leash-is-a-fact), which
does write back, for the reason set out there.

Which sounds fussy until you imagine the alternative. Feed the snapped position back in
and every tick starts from the canal, so the track follows the canal perfectly, forever,
whatever the sub does. It would look magnificent. It would be a track of the *canal*,
not of the sub, and the moment the estimate broke — a dead compass, a snagged prop, a
speed model calibrated wrong — the picture would look exactly as good as it did when
everything worked. Snapping that can rewrite the estimate is snapping that can hide the
failure of the estimate. So it may suggest, forever, and it may never overwrite.

Two things about what is actually built today. The projection routine also works out how
far *along* the waterway the snapped point is — the natural coordinate for a
one-dimensional world — and nothing currently reads that number. And on the console, the
faint un-snapped dot the design calls for is not drawn yet: the browser positions the
sub from the raw east/north numbers and ignores both the snapped coordinates and the
drift figure, which the API's own frame audit records honestly as ignored. Server-side
the confidence knock is real and lands in the dive log; the drift reading itself has not
finished its journey to the screen.

### The formal bit

where:

- $p_x, p_y$ — the estimate's position, metres east and north of the origin
- $a_x, a_y$ and $b_x, b_y$ — the two endpoints of one centreline segment, same metres
- $L^2$ — squared length of that segment (a segment length here, not the tether payout)
- $t$ — position along the segment, $0$ at $a$ and $1$ at $b$ (a fraction here, not a time)
- $s_x, s_y$ — the projected (snapped) point on that segment
- $d$ — distance from the estimate to that projected point, metres
- $D$ — cumulative distance along the polyline to the projected point, metres
- $c$ — confidence

Per segment:

$$L^2 = (b_x - a_x)^2 + (b_y - a_y)^2$$

$$t = \frac{(p_x - a_x)(b_x - a_x) + (p_y - a_y)(b_y - a_y)}{L^2}$$

Clamped to the segment, so the projection can never land beyond an endpoint:

$$t \leftarrow \min(1,\; \max(0,\; t))$$

$$s_x = a_x + t\,(b_x - a_x), \qquad s_y = a_y + t\,(b_y - a_y)$$

$$d = \sqrt{(p_x - s_x)^2 + (p_y - s_y)^2}$$

Degenerate case, as implemented: if $L^2 = 0$ (two identical points in the centreline)
the segment returns $s = a$ with zero along-distance, and no division is attempted.

Over the whole polyline, the winner is the segment with the smallest $d$ (first one wins
on ties, since the test is strictly less-than), and its along-distance is the sum of the
lengths of all preceding segments plus $t \sqrt{L^2}$.

Application, in the estimator:

$$\text{snap if } d \le 25.0 \text{ m} \quad (\texttt{snap\_max\_dist\_m})$$

$$c \leftarrow \min(c,\; 0.7) \quad \text{if } d > 8.0 \text{ m}$$

Properties as implemented:

- The confidence knock is nested **inside** the snap test, so it applies only for
  $8.0 < d \le 25.0$. For $d > 25.0$ there is no snap and no confidence effect.
- $8.0$ is a literal in the estimator; $25.0$ is a config setting (`NAV_SNAP_MAX_M`).
- On a snap, the output latitude and longitude are computed from $(s_x, s_y)$, while
  `raw_lat`/`raw_lon` are computed from $(p_x, p_y)$ and `snap_offset_m` $= d$. The
  estimator's persistent $x, y$ remain $(p_x, p_y)$ — **no write-back**, in contrast with
  the tether clamp.
- $(p_x, p_y)$ here is the position *after* the tether clamp, so "raw" means un-snapped,
  not un-clamped.
- Snapping runs only when a centreline is loaded and `snapping_enabled` is true
  (`NAV_SNAP` $\ne$ `off`) — unless the estimator was constructed with an explicit
  `snapping` argument, which replaces the setting outright
  (`settings.snapping_enabled if snapping is None else snapping`). A polyline of fewer
  than two points returns nothing either way.
- The dive log records $x_m, y_m$ — the un-snapped values — plus the `snapped` flag and
  the confidence.

`api/nav/snap.py — _project_point_segment()`, `nearest_on_polyline()`; applied in
`api/nav/deadreckoning.py — DeadReckoner.update()`; thresholds in
`api/nav/config.py — snap_max_dist_m`, `snapping_enabled`; centreline import in
`api/nav/service.py — _centreline_from_geojson()`.

## 6. The speed lookup table — four dots and a tape measure

There are two ways to find out how fast your submarine goes.

The first is to derive it. Hull drag depends on wetted area, and wetted area depends on
how the thing floats, which depends on ballast; thrust depends on propeller pitch and
blade area and the motor's torque curve and the voltage sagging under load; and the
whole lot sits inside a coefficient that engineers determine by putting a scale model in
a towing tank. You can do all this. At the end you will have a beautiful equation about
a boat that is not yours.

The second is to walk down the canal bank with a tape measure, mark twenty metres, put
the sub in the water, hold one throttle position, and time it. Then do that again at
three more throttle positions. Now you have four numbers, and every one of them is about
*this hull*, in *this water*, with *these motors*, on the day you measured it.

NEPTUNE does the second thing. The speed model is a table of dots — throttle here,
metres per second there — and everything between the dots is a straight line. Connect
the dots. That is the entire model. A twelve-year-old with a stopwatch produces a better
speed estimate than a naval architect who has never seen your sub, and the code says so
out loud: the docstring calls it "the single largest accuracy win in the whole system",
which is why it is stored per hull as a file you can open and read rather than buried as
a constant somebody typed once.

Why does it matter so much? Because of *what kind* of wrong it is when it is wrong. Most
sensor errors are noise: they wobble either side of the truth, and over a long run they
largely cancel. A wrong speed model does not wobble. If your table says 1.0 m/s and the
hull actually does 0.85, then every single metre the sub travels is logged 18% too long,
always in the same direction, forever. It is a tape measure printed slightly wrong.
Measure the same wall twice and you get the same wrong answer twice, which is exactly
what makes it invisible. Fly a 200-metre dive on a table that is 18% fast and the sub
finishes some 36 metres short of where the map swears it is — not scattered around the
right place, but confidently, consistently, in the wrong place. This is the biggest
single error term in the whole navigation stack, and it is also the cheapest one to fix,
and those two facts together are why the calibration tool of
[§8](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no) exists at all.

Two things the table does that are worth staring at.

**It refuses to guess past the last dot.** Ask for a throttle beyond the highest one you
measured and it does not extend the trend — it hands back the last speed you actually
timed and stops. Extrapolation is the polite word for making things up, and a table
whose whole claim to authority is "these are measurements" cannot afford to invent a
fifth dot from the slope between the third and fourth.

**Reverse is a mirror of forward, and that is a lie.** Ask for −0.6 throttle and the
table looks up +0.6 and puts a minus sign on the front. Real hulls do not work like
this. A propeller pushing backwards is meaningfully less efficient than the same
propeller pushing forwards; the hull is a different shape to the water going the other
way; thrusters commonly give sixty to eighty per cent of their forward push in reverse.
So the model is knowingly optimistic every time the sub backs up.

It is tolerated for three reasons, and it is worth being precise about them because "we
tolerate a known lie" is a different posture from "we didn't think about it". First,
reverse on a canal ROV is a nudge, not a journey — you back off a snag, you reposition,
you do not reverse for two hundred metres. Second, honest reverse numbers would double
the calibration work, and calibration you do not do is worse than a stated
approximation: an empty reverse branch would fill itself with a guess, and a guess
wearing the same clothes as a measurement is precisely what this codebase spends its
whole life preventing. Third — and this is the real one — the lie is *written down*. It
is a single line in one file with a comment on it, so anyone chasing a track that drifts
long on reverse legs finds it in ten seconds. A known white lie in a docstring is a much
smaller problem than an unknown truth in a coefficient.

There is one more thing the table cannot do, and the honest place to say it is here: the
lookup table cannot notice a headwind, a fouled propeller, or a shopping trolley. It is
a function of the throttle lever and nothing else, so a sub bolted immovably to a
supermarket trolley on the canal bed reports a healthy cruise all the way to the
horizon. That is not a defect of the table — it is the definition of an open-loop model
— and it is why the paddlewheel was bought, why the snag detector of
[§11](#11-the-snag-detector--an-if-statement-with-the-instincts-of-a-lie-detector)
refuses to accept table speed as evidence, why the filter of
[§10](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second)
demotes it to a rumour the moment the wheel goes quiet under thrust, and why every frame
that carries a table speed is labelled as such on its way to the screen. The table is
allowed to be the answer. It is never allowed to pretend it was measured.

### The formal bit

where:
- $u$ — commanded throttle, $-1 \le u \le 1$ (values outside are saturated)
- $s$ — sign carried out to the result (a sign here, not a snapped point or a log sample)
- $a$ — throttle magnitude used for the lookup
- $(t_i, v_i)$ — table point $i$: throttle $t_i$ (a throttle here, not a time), speed $v_i$ in m/s, sorted ascending by $t$
- $n$ — index of the last table point, so the table holds $n + 1$ points
- $f$ — interpolation fraction within the bracketing pair
- $v(u)$ — signed water-relative speed, m/s
- $D$ — the operator's measured run distance, metres, supplied on the command line
- $\Delta t_i$ — seconds the hull took to cover $D$ at throttle $t_i$

Sign and magnitude are separated first:

$$s = +1 \text{ if } u \ge 0, \qquad s = -1 \text{ if } u < 0$$

$$a = \min(1,\ |u|)$$

Let $i$ be the smallest index $\ge 1$ with $a \le t_i$. Then:

$$f = \frac{a - t_{i-1}}{t_i - t_{i-1}}, \qquad v(u) = s \cdot \left( v_{i-1} + f \cdot (v_i - v_{i-1}) \right)$$

Special cases, all as implemented:

- duplicate throttle points ($t_i = t_{i-1}$): $v(u) = s \cdot v_i$ — the later point wins, no division by zero
- no such $i$ (i.e. $a$ exceeds every $t_i$): $v(u) = s \cdot v_n$ — **hold at the last point, never extrapolate**
- reverse: the magnitude is the forward magnitude. There is no separate reverse table

Anchoring. The constructor sorts the points and prepends $(0,0)$ when the list is empty
or when $t_0 > 0$. It does **not** prepend when $t_0 \le 0$, so a table containing a
negative-throttle point is left unanchored and the mirror rule above still governs the
sign.

The shipped default table (a small canal sub, full throttle $\approx 1$ m/s), used by
the dead reckoner, the filtered estimator and the simulator whenever no per-hull table
is loaded:

$$(0,\ 0),\quad (0.25,\ 0.28),\quad (0.5,\ 0.55),\quad (0.75,\ 0.82),\quad (1.0,\ 1.0)$$

Building a table from timed runs. Each command-line pair is a throttle and the seconds
that throttle took to cover one measured distance $D$; the point is the plain quotient,
rounded to three decimal places as it is stored, and $(0,0)$ is prepended
unconditionally by the tool before the constructor sees the list — so a pair given at
throttle $0$ arrives as a duplicate point and resolves through the $t_i = t_{i-1}$
branch above rather than dividing by zero:

$$v_i = \mathrm{round}\!\left(\frac{D}{\Delta t_i},\ 3\right)$$

`api/nav/speedlut.py — SpeedLUT.speed()`, `SpeedLUT.__init__()`, `DEFAULT_LUT`;
`api/nav/cli.py — _speed_cal()`. Consumers: `api/nav/deadreckoning.py —
DeadReckoner.update()`, `api/nav/estimator.py — FilteredEstimator.update()`.

## 7. The simulator's dirty tricks — a liar with a fixed seed

Most simulators are demos. You run them to show that the software does something,
ideally something that looks nice on a screen. This one is not that. This one is a liar,
deliberately, on a schedule, and its lies are the specification.

The idea is simple and slightly wicked. If you want to know whether your navigation code
survives a compass that goes mad near the thrusters, you can wait for the canal — or you
can build a fake submarine that goes mad near the thrusters *by exactly 22 degrees at
full power*, keep the real answer in a locked drawer, and check afterwards. The
simulator holds true position, true depth, true heading and true speed internally, and
hands the navigation code nothing but the same grubby sensor readings a real hull would
produce. Then a test opens the drawer.

That drawer has a lock on it too. When ground truth is written into a replay log it goes
in under names prefixed `true_`, sitting on the same line as the estimator's own
guesses, because — as the code puts it — anything that can be mistaken for an estimate
eventually will be. A truth that stays inside the simulator cannot settle an argument; a
truth that looks like an estimate will one day *lose* one.

Here is the cast of lies.

**The compass is an honest friend who is unreliable at parties.** Not broken —
situational. Sitting still, it is fine and it knows it is fine. Push the throttles and
the motors' magnetic fields swing the reading by up to 22 degrees, wandering slowly in
and out as a slow wobble on top. Back off the power and it returns, unbothered, telling
the truth again. This is the nastiest possible failure mode, far worse than a compass
that is simply dead, because it is wrong *precisely when you are moving* and right
precisely when you are not — so a filter that only checks the compass while parked will
pass every test and fail every dive.

The important detail: the error follows the thrust the motors are *actually producing*,
not the throttle the operator asked for. Those differ during a turn, when one motor is
pushed harder than the other, and they differ completely on a disarmed sub — where the
lever may be forward and the motors are doing nothing at all. Blaming the disturbance on
the lever rather than the motors would put magnetic garbage in a stream where no current
is flowing.

**And the compass admits it.** The simulated calibration status degrades in lockstep
with the thrust: good when idling, middling at cruise, bad at full power. So the compass
does not just lie — it hangs a sign around its neck saying *do not trust me right now*.
That pairing is the whole exercise. The heading filter of
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk) — the thing
all of this is built to test — is not being asked to detect a liar by intuition; it is
being asked to *read the sign*, stop believing the compass, and coast on the gyroscope,
which the thrusters cannot touch. A simulator that injected the error without the
warning would be testing clairvoyance.

**The friend who is always ninety seconds fast.** On top of everything else the heading
carries a flat one-and-a-half-degree bias, all day, every reading. Constant errors and
situational errors need different medicine, so both are in the stream.

**The gyroscope's bias is switched off, on purpose.** A drifting gyro is exactly the
error a heading filter is supposed to learn away, so you might expect it to be on by
default. It is not — and the reason is the best epistemics in the file. The filter can
only learn its gyro's drift while the compass is trustworthy, and on a path that runs
the thrusters hard the compass almost never is. Leave the drift switched on and the
head-to-head test between filtered and unfiltered navigation stops measuring the filter
and starts measuring the injected drift. So the default is zero, and the one test that
cares about drift injects its own, deliberately, and says so. You do not stack the deck
in favour of the thing you are trying to prove.

**The pressure sensor is honest, and the simulator says so.** Depth gets three
centimetres of jitter and nothing else. Not every channel gets a dramatic failure;
pretending the depth sensor is as bad as the compass would make the simulator a worse
description of the vehicle, not a tougher test of it.

**The tether always pays out too much.** The encoder reports six per cent more cable
than the distance actually travelled, and never less. This is the truth about string: it
sags, it curves, it snakes. It matters enormously that the error has a *known sign*.
Payout that is always at least the distance travelled is a genuine upper bound — the sub
cannot be further away than the cable is long — and an upper bound is a fence, not a
position. Bound it, never set it. Had the simulator made payout occasionally short, the
fence would sometimes cut through the sub, and the code that trusts it as a limit would
be wrong in a way no test would catch.

**The current moves the sub but not the paddlewheel.** A constant drift vector is added
to the sub's motion over the ground, and is deliberately *absent* from the paddlewheel
reading, because a paddlewheel measures the water going past — and the water is what is
drifting. This one line encodes the entire reason a speed sensor cannot detect a
current, and it means any test that expects the paddlewheel to notice a drift fails,
correctly.

**The paddlewheel goes silent rather than saying zero.** Below about a tenth of a metre
per second the wheel simply stops turning: no magnet passes the sensor, nothing is
counted. The simulator emits *nothing* for that sample — not a zero. Silence is not "the
sub is stationary", it is "slower than I can see", and the difference is the whole
document. Hand the estimator a 0.0 and it takes it as a measurement, and a measurement
of zero speed is a very strong claim.

**The hull has mass.** The throttle does not become speed instantly; speed chases the
table value with about a second and a half of lag. This is not garnish. Without it,
acceleration would be a nonsense spike at every leg boundary and nothing downstream that
reads acceleration would be receiving a real signal.

**The sub floats for four seconds before it does anything.** This looks like a
decorative touch and is in fact a measured result recorded in the code. No dive begins
at sixty per cent throttle from a standing start, and starting one does more damage than
it seems: the thrusters poison the compass, so a run that opens under power hands the
heading filter a compass that is already ten degrees out and then never gives it a quiet
window to walk that back. It coasts on the gyro carrying the initial error for the
entire dive. The numbers are in the comment: under power from the first instant, the
filtered estimator finishes 17.9 metres from truth against dead reckoning's 20.1 — a
coin toss, i.e. the filter is worth nothing. With four seconds of float first, it
finishes 0.9 metres out. Those seconds are also the only stretch where the throttle is
genuinely zero and the wheel genuinely stalled, which is the one condition under which
the speed filter can lock onto a known zero and kill its accelerometer's drift.

**And the one that changed recently: the sub now *turns*, it does not teleport.** The
scripted path is a list of legs, each with a heading to hold, and the obvious
implementation is to adopt the new heading the moment a new leg begins. Obvious, simple,
and quietly catastrophic for testing. A snapped turn produces the one shape a gyroscope
can never see: a rate of turn that is zero everywhere except for a single impossible
sample. Feed that to a filter that navigates by integrating turn rate and it scores
perfectly *while doing nothing whatsoever* — the heading it has never been asked to
change matches the heading that never changed. The head-to-head test passes without
testing anything.

So the hull now swings toward the new heading at a bounded rate — twelve degrees per
second, roughly what a small ROV can actually manage — which puts real, sustained
turning into the stream for a filter to get right or wrong. The consequence that matters
is one line further down: true position is integrated along *the heading the hull has
actually swung to*, not the heading the leg asked for, so the ground truth itself
contains curved corners. The estimators are now scored against a track with real corners
in it. The old version scored them against a track made of perfectly straight lines
joined by teleports, which is not a canal, and is not a test.

The same swing also generates the steering command and, from it, the differential
thrust: to turn, one motor pushes harder than the other. That is what the compass
disturbance is computed from, what the heading filter's trust gate reads, and what the
snag detector watches. A simulator that emitted equal thrust while the hull visibly
turned would leave all three blind.

Finally, the liar is reproducible. No wall-clock, no system randomness — a tiny
hand-rolled generator with a fixed seed, so run number four hundred is bit-for-bit run
number one. This is what makes cross-examination possible: when a test fails, it failed
on a specific lie you can go and inspect, not on a bad afternoon. And time is not
allowed to run backwards; a negative time step is clamped to zero rather than rewinding
ground truth, because a truth that disagrees with the stream it came from is far harder
to spot than an obviously wrong number.

**Where the lying stops, which is worth knowing.** The simulator's speed table is, by
default, the *same* table of
[§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure) that the estimators use,
and nothing in the repository currently passes it a different one. So the biggest
real-world error term in the entire system — a speed model that is a few per cent off —
is the one disturbance the default simulation does not contain. It can be injected in
one argument; it is not injected today. In the same spirit: no chip on the simulated hull
ever *fails* mid-run. Readings do go absent, and one of them goes absent mid-run for a
perfectly good reason — the scripted route reverses through a standstill, and the
paddlewheel stops reporting every time the hull passes below its stall speed, because a
wheel that is not turning has nothing to say. That is the instrument working. What the
simulator never does is break one: no chip that is healthy this second is dead the next,
the ballast channel says "no such instrument" for a syringe that was never fitted rather
than one that quit, and the hull never gets snagged. Those gaps are covered by other
fixtures, hand-built elsewhere. It is worth knowing which lies you are testing against and which
you have merely thought about.

### The formal bit

where:
- $t$ — simulated time, s; $\Delta t$ — step size, s, clamped to $\Delta t \ge 0$
- $h$ — true heading, degrees, compass convention ($0 = $ north, $90 = $ east)
- $h_L$ — the current leg's commanded heading; $r_{max} = 12$ °/s — turn rate cap
- $\text{cap}$ — the most the heading may move this step, degrees
- $\delta$ — the heading change actually taken this step, degrees
- $\omega$ — true yaw rate this step, °/s, positive clockwise
- $q$ — derived steering command, $-1 \dots 1$ (`turn_demand` in the code)
- $u$ — leg throttle; $T_L$, $T_R$ — left and right thruster outputs; $T$ — thrust level
- $v$ — true water-relative speed, m/s; $\tau = 1.5$ s — hull speed lag
- $k$ — the lag fraction applied this step, $0..1$ (a lag fraction here, not a scale factor or a gain)
- $\Delta v$ — the speed change taken this step, m/s
- $a$ — true forward acceleration, m/s²
- $x$, $y$ — true position, metres east and north; $S$ — cumulative true path length, m
- $c_s$, $c_b$ — current speed (m/s) and bearing (degrees), as in [§3](#3-current-compensation--the-sub-is-walking-on-a-travelator)
- $D$ — true depth, m (a depth here, not a distance); $D_L$ — the leg's target depth
- $m$ — injected magnetic disturbance, degrees (a disturbance here, not a measured heading)
- $b$ — injected gyro bias, °/s, $0$ by default
- $s$ — the pseudo-random generator's 32-bit state (a state here, not a sign)
- $\xi$ — a fresh uniform random draw in $[-1, 1)$
- $\text{LUT}(u)$ — the speed table of [§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure)

Shortest signed angle, through which every heading subtraction passes:

$$\text{wrap180}(d) = ((d + 180) \bmod 360) - 180$$

Turning — bounded rate, never a snap:

$$\text{cap} = r_{max}\,\Delta t, \qquad \delta = \text{clamp}\left(\text{wrap180}(h_L - h),\ -\text{cap},\ +\text{cap}\right)$$

$$\omega = \frac{\delta}{\Delta t}, \qquad h \leftarrow (h + \delta) \bmod 360, \qquad q = \text{clamp}\left(\frac{\delta}{\text{cap}},\ -1,\ 1\right)$$

Speed — first-order lag toward the table value:

$$k = \min\left(1,\ \frac{\Delta t}{\tau}\right), \qquad \Delta v = (\text{LUT}(u) - v)\,k, \qquad a = \frac{\Delta v}{\Delta t}, \qquad v \leftarrow v + \Delta v$$

Position — water-relative motion plus current, integrated once:

$$v_x = v \sin h + c_s \sin c_b, \qquad v_y = v \cos h + c_s \cos c_b$$

$$x \leftarrow x + v_x \Delta t, \qquad y \leftarrow y + v_y \Delta t, \qquad S \leftarrow S + \sqrt{v_x^2 + v_y^2}\ \Delta t$$

Depth — first-order approach to the leg target:

$$D \leftarrow D + (D_L - D)\cdot\min(1,\ 0.5\,\Delta t)$$

Differential thrust:

$$T_L = \text{clamp}(u + 0.35\,q,\ -1,\ 1), \qquad T_R = \text{clamp}(u - 0.35\,q,\ -1,\ 1), \qquad T = \max(|T_L|,\ |T_R|)$$

Emitted sensor readings, with their real constants:

$$m = 22.0 \cdot T \cdot \left(0.6 + 0.4 \sin(1.7\,t)\right) \quad \text{(magnetic disturbance, degrees)}$$

$$h_{meas} = \left(h + 1.5 + m + 0.4\,\xi\right) \bmod 360$$

$$\text{mag\_cal} = 3 \text{ if } T < 0.4; \quad 1 \text{ if } T > 0.7; \quad 2 \text{ otherwise}$$

$$D_{meas} = \max\left(0,\ D + 0.03\,\xi\right)$$

$$\text{encoder} = S \cdot 1.06 \qquad \text{(payout} \ge \text{path length, always)}$$

$$\text{gyro} = \omega + b + 0.15\,\xi, \qquad b = 0 \text{ by default}$$

$$\text{accel}_{meas} = a + 0.05\,\xi$$

$$\text{paddle} = \max(0,\ |v| + 0.02\,\xi) \text{ if } |v| \ge 0.10; \quad \text{no reading if } |v| < 0.10$$

The current $(c_s, c_b)$ appears in $v_x, v_y$ and nowhere in the paddlewheel term.
`steer`, `left` and `right` are emitted as $q$, $T_L$, $T_R$; `armed` is always true;
ballast level is emitted as "no such instrument".

Randomness — xorshift32, seeded 1234, no wall-clock, all arithmetic 32-bit:

$$s \leftarrow s \oplus (s \ll 13), \qquad s \leftarrow s \oplus (s \gg 17), \qquad s \leftarrow s \oplus (s \ll 5)$$

$$\xi = \frac{2s}{2^{32}-1} - 1$$

Ground truth written to a replay log, under names prefixed `true_`: $t$, $x$, $y$, $D$,
$h$, $v$, $\omega$.

Zero guards, all as implemented, since three of the expressions above are divisions and
the code never performs them blind: at $\Delta t = 0$ (and therefore $\text{cap} = 0$)
the derived quantities $\omega$, $q$ and $a$ are taken as $0$ rather than divided, and a
non-positive $\tau$ collapses the lag to $k = 1$ — the hull adopts the table speed
immediately instead of dividing by nothing.

`api/nav/sim.py — Simulator.step()`, `Simulator.truth_row()`, `_wrap180()`,
`DEFAULT_PATH`.

## 8. Calibration forensics — the tool that is allowed to say no

Every constant in the motion model started life as a guess. Full speed is "about a metre
a second". Turn rate is "about forty degrees a second". Full ballast gets you "about
nine metres down". These are not stupid guesses — they are the right order of magnitude
and they were made by someone who has seen the hull — but they are guesses, and
[§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure) explained why a guessed
speed model in particular is the worst kind of wrong there is.

So there is a tool that reads a real dive log and hands back real constants. Its
arithmetic is trivial: some subtractions, a division, and one straight line drawn
through a scatter of dots — the sort of fit you could do on paper with a ruler and a bit
of squinting. Almost nothing in this section is mathematically interesting. What is
interesting is everything the tool *refuses to do*, because a calibration tool that
always produces an answer is worse than no tool at all. The one that refuses sends you
back to the water. The one that always answers sends you into the water with a number
you now believe.

**You cannot time a runner who is tying their shoes.** Before any constant can be
extracted, the log has to be cut into segments where something was actually held still
long enough to mean anything. A usable segment needs the control input held roughly
constant, the sub armed, at least eight samples, and at least three seconds — shorter
than that and you are measuring the startup wobble rather than the steady state. The
moment the operator moves the lever, the segment ends and a new one begins.

Two refinements that look small and are not. The tolerance is measured against the
*first* sample of the segment, not the previous one, so a slow creep on the lever
eventually breaks the segment instead of being quietly averaged across. And a missing
reading also ends a segment — which now covers more than a dead chip. The ballast
syringe is driven by a stepper motor with no position sensor at all, so from power-on
until it has been homed against its limit switch there is genuinely *no position to
report*. A segment that straddled the homing would otherwise be measured against a
syringe level that nobody, including the syringe, knew.

**A hole in the middle takes the whole segment with it.** The dive journal writes a
channel as "nothing" the instant its chip stops answering — the depth sensor that drops
off the bus at 4.23 m, the compass that browns out under the thrusters — so a segment
can have a hole punched through the middle of it. The tempting fix is to throw away the
empty samples and close the gap. The tool refuses, and drops the entire segment instead,
because both measurements it makes are computed across a segment's *span* rather than
sample by sample.

Consider the turn rate: it is the heading at the end minus the heading at the start,
over the elapsed time. A hole in the middle can hide an entire rotation. That is not
"slightly less certain" — the answer can be wrong by a full circle's worth of turning,
in either direction, and it will look completely plausible. And the depth model asserts
that the sub *settled* at a ballast setting, which is a claim about the whole hold; a
hold the sensor was absent for is not a hold anybody watched settle.

Every dropped segment is counted, and the count is printed. A constant fitted to the
stretch where the instrument happened to be alive, with no mention that the other
stretches existed, is the same lie as a substituted zero wearing a better suit.

**The clock problem.** Before any heading arithmetic happens, the sequence of bearings
has to be unwrapped. It is the mirror image of the fold-to-the-short-way-round that
every live heading comparison passes through in
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk), run over a
whole recorded series instead of one pair. If the compass reads 358, then 359, then 0,
then 1, the sub turned three degrees to the right. Read literally, it turned 357 degrees
to the *left*, at a violent pace, in a third of a second. This is the same fact as 11:58
to 12:02 being four minutes rather than minus eleven hours fifty-six: the numbers went
down, the time went up, and only a human knows that midnight is not a cliff.

The fix is to walk the sequence and keep a running total: every time a step looks bigger
than half a circle, assume you crossed the seam and shift the accumulator by a full
circle. Out comes a heading that just keeps climbing — 358, 359, 360, 361 — which
subtracts correctly. The unwrapper is also, deliberately, unable to cope with a gap, and
it says so in its own docstring: it is a running accumulator, so a hole can conceal a
wrap, and the only thing it could do locally is invent a bearing nobody measured. It
demands that its caller has already thrown out any segment with a hole. That is the
null-handling rule pushed *upstream* rather than patched at the point of pain, and it is
the fix for a real crash: the tool used to die on any dive whose compass stopped.

**Divide first, then average.** Now the turn rate. Each usable segment gives you a total
heading change and a duration, so it gives you a rate — but different segments were
flown at different amounts of steering, and a rate at full lock is not comparable with a
rate at half lock. The quantity that ought to be the *same* across every segment is
degrees per second *per unit of steering*, so that is what is computed for each segment,
and only then averaged.

This is the whole trick and it is easy to get backwards. Average the rates and then
divide by the average steering, and you have averaged a set of numbers that had no
business being averaged, weighted by nothing in particular. Divide first and you are
averaging the quantity you actually believe is constant. Segments steered by less than a
tenth are thrown out entirely — dividing by a whisper amplifies every wobble into a
headline.

And the tool reports the *spread* alongside the average, and the report prints a warning
when the spread is more than half the value. A constant handed over with its own doubt
attached is a different object from a constant handed over bare. If the segments
disagree wildly, the honest reading is "this is a first estimate", not "this is the
number".

There is a refusal here too. If the heading never changed under steering — every
per-unit rate essentially zero — the answer is not "the turn rate is zero". It is "no
compass is fitted, or the sub was never armed, and turn rate is not measurable from this
log". Zero degrees per second is a measurement. Nothing is not.

**Wait for it to settle.** The depth model asks a simple question: how deep does the sub
go for a given ballast setting? Simple, but the log is mostly full of the wrong part of
the answer. When the syringe fills, the sub starts descending, and for the next thirty
seconds the depth sensor is describing a *journey*. The number the model wants is the
*destination*. So each ballast hold is examined at its tail: the last handful of samples
must all sit within five centimetres of each other before the hold counts as settled. If
the depth is still moving, the segment is skipped — no partial credit, no "close
enough", because a descent caught halfway reads as a shallower equilibrium than the sub
actually has, and shallower is the dangerous direction to be wrong in.

Then the settled points are fitted with a straight line, and the line is forced through
the origin. This is the single most opinionated piece of arithmetic in the file. An
ordinary line fit has two free parameters — a slope and an intercept — and an intercept
here would be a claim that some ballast setting other than zero corresponds to the
surface. But zero ballast *is* the surface. That is not an experimental result, it is
what the ballast scale means. Letting the fit invent an offset would let a handful of
noisy points quietly relocate the waterline, and a model whose zero is not the surface
will confidently report the sub at 0.4 m while it bobs on top. So there is one free
parameter, the slope, and the slope *is* the constant everyone wants: how deep at full
ballast.

**Three refusals, three different jobs.** Here is where the tool earns its keep. It has
several ways to decline, and each one prints a different sentence, because each sends
the operator to do a different thing.

*One settled point is not a curve.* A dive that homed, filled once, and stayed down —
which is the ordinary shape of a working dive — produces exactly one settled ballast
setting. That is not enough to fit a line through, and the tool says so. It used to say
something much worse: it fell through to the "no pressure sensor fitted" message, so an
operator would be sent to install a part that was sitting right there reading 9.00 m on
every one of several hundred samples. The refusal was right; the *diagnosis* was
invented, and the data never supported it.

*Every point at the same depth* has two possible readings and they are opposites. Either
there is no depth sensor at all, or there was one, it worked, and the segments that
would have shown the sub descending were dropped because it died inside them — leaving
only the surface segments behind, which look exactly like a hull with no sensor. The
count of dropped segments decides which sentence gets printed. One sends you to fit a
part. The other sends you to find out why a fitted part stopped. Getting this wrong is
the same class of harm as getting a number wrong.

*And underneath all of it, three states that arrive as the same silence.* A column that
was never in the log at all means the dive predates that channel — fly it again on a
newer build; the part may well have been fitted and fine, nothing here recorded it. A
column that is present and empty on every single sample means the part never answered
once — go and fit it, or find the connector nobody plugged in. A column that answered
and *then stopped* means the part is fitted, it worked, and something killed it
mid-dive: the only one of the three that is an incident. The tool tells them apart and
names which it found. Collapsing them into one message sends someone to do the wrong job
— which, the code notes, is the same class of harm as collapsing them into one number.

**The circularity trap.** Now the important one, and the reason the speed section of
this tool looks so different from the rest.

The dive log contains the sub's position at every instant. It is *right there*. Position
over time is speed; you could measure the speed model straight out of the track in about
four lines. And it would be perfectly, cheerfully, catastrophically wrong — because
those coordinates were *produced by the speed model in the first place*. The track is
the speed model, integrated. Measuring the speed model against the track is asking the
model to grade its own exam, and it will pass, every time, with full marks, no matter
how wrong it is. Set the table to double the truth and the track doubles with it and the
check still agrees. Circular reasoning does not merely fail to detect the error; it
actively certifies it.

So speed needs an outside witness — something in the log that did not come from the
model. There are three:

*A tape measure and a stopwatch.* You marked twenty metres of bank, held one throttle,
and ran it. The log supplies the throttle and the duration; you supply the distance. The
distance is the outside witness, and it is a good one: no hardware, no assumptions, and
it is exactly the procedure the speed table was always documented as needing.

*The tether spool encoder.* Cable paid out is a real length of physical cable, counted
by a wheel that has never heard of the speed model. Best of the three when it is fitted
— it works on any ordinary dive with no special procedure. One honest caveat the tool
does not correct for: payout is always at least the distance travelled and usually a bit
more, so a speed derived from it reads slightly fast. It is the bound of
[§4](#4-the-tether-clamp--the-leash-is-a-fact) pressed into service as a measurement,
and it is biased in a known direction.

*Position fixes at the surface.* Named as an option in the tool's own documentation. It
is not implemented; there is no code path for it today.

Two constants are exempt from all this, and it is worth saying why: turn rate and the
depth model do not have the circularity problem at all, because heading comes from the
compass and depth comes from the pressure sensor, and neither of those instruments has
any idea what the motion model believes. They are calibratable from any ordinary dive.
Only speed is contaminated, because only speed is what the track was *built from*.

One caveat on that exemption, stated because this document is about what a number is
allowed to claim. The heading column the turn-rate calculation reads is the one the
*estimator* published, and on a hull running the filtered navigation backend that
heading has already been part-produced by integrating the gyroscope. It remains entirely
independent of the speed model — the circularity the tool is guarding against is
genuinely absent — but it is not purely the instrument either. The journal writes the
untouched compass reading on the very same line, under its own name, and that is the
column with no filter in it at all. When there is no outside witness in the log, the
tool prints the refusal *and the reason* — go and run a measured stretch, or fit an
encoder — rather than reaching for the coordinates sitting right there.

**Proving the tool, including proving it can say no.** Finally, the self-test, which is
short and does something slightly unusual.

It fabricates a dive from known constants — forty degrees per second, nine metres at
full ballast, 0.8 metres per second — writing out a log exactly as the real vehicle
would, then hands it to the analysis *blind* and checks that the constants come back.
They do, to within a degree per second, twenty centimetres, and five centimetres per
second. That is the ordinary half.

The unusual half is that it also checks the tool can produce *nothing*. It builds a log
that looks superficially fine but is sensorless — heading pinned at zero, depth pinned
at zero, no encoder — and asserts that both the turn rate and the depth model come back
empty. A tool that always produces an answer is worse than no tool, so "produces no
answer when it shouldn't" is a feature with its own test, at the same rank as the
numbers.

And then the case that motivated the recent work: the chip that died *mid-dive*. Take
the good synthetic dive, cut it in half, and blank the compass and depth sensor from the
midpoint on — the exact shape the journal writes when hardware stops. This used to crash
outright, and the reason is a genuinely sharp edge worth carrying away: the code
defended itself with a *default value for a missing column*, and the column was not
missing. It was present, and empty. The default therefore never fired, and the emptiness
went straight into a subtraction.

Four things are asserted on that fixture, and the last three matter more than the first.
It must not crash — but a version that caught the error and carried on with zero would
also not crash, and would be *worse* than the crash, because zero degrees is due north:
a dead compass would read as the sub snapping to north and back, which is an enormous
measured turn rate, in a tool whose entire output is a turn rate. So: the half that was
measured must still return exactly the truth the log was built from, undragged by any
invented heading; the half that was not must *refuse*, naming the emptiness rather than
fitting zero metres as "the surface"; and the gap must be **reported**, both channels
named, not silently stepped over. A skip nobody mentions is a quiet lie about what the
number covers.

The fixture is built so that this asymmetry is forced: the synthetic dive turns in its
first half and ballasts in its second, so cutting the sensors at the midpoint leaves the
turn rate measurable and takes the depth model away entirely. One section must still
answer. The other must refuse. Getting both from one log is the whole claim.

### The formal bit

where:
- $s_j$ — sample $j$ of the dive log, in time order
- $\kappa$ — the control channel a segment is cut on (`steer`, `ballast` or `throttle`)
- $\epsilon$ — control tolerance: $0.05$ in general, $0.03$ for ballast
- $N_{min} = 8$ samples, $T_{min} = 3.0$ s — minimum segment size and span
- $h_j$ — measured heading of sample $j$, degrees; $\tilde h_j$ — its unwrapped value
- $A$ — lap accumulator carried by the unwrapper, degrees
- $\sigma$ — the steering command held during a segment; $\rho$ — that segment's turn rate, °/s
- $b_i$, $d_i$ — ballast level and settled depth of accepted hold $i$ (a ballast level here, not the gyro bias of [§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk))
- $k$ — depth model slope, metres at full ballast (a slope here, not a scale factor or a gain)
- $D$ — operator-measured distance, m (a distance here, not a depth); $\Delta t$ — segment duration, s
- $E_0$, $E_1$ — tether payout at the start and end of a segment, m

**Segment acceptance.** A run of consecutive samples is a segment when, for every sample
in it, the channel $\kappa$ has a reading, `armed` is true, and

$$|\kappa_j - \kappa_{\text{first}}| \le \epsilon$$

and the run satisfies

$$n \ge N_{min} \quad \text{and} \quad t_{last} - t_{first} \ge T_{min}$$

A sample with no reading for $\kappa$, or not armed, or outside tolerance, terminates
the segment. A segment is discarded entirely (and counted) if any sample in it lacks a
reading for the *measured* channel about to be used.

**Unwrapping.** Starting with $A = 0$, for each successive pair:

$$A \leftarrow A - 360 \ \text{ if } \ h_j - h_{j-1} > 180, \qquad A \leftarrow A + 360 \ \text{ if } \ h_j - h_{j-1} < -180$$

$$\tilde h_j = h_j + A$$

**Turn rate.** Segments cut on `steer`, keeping only those with $|\sigma| \ge 0.1$. Per segment:

$$\rho = \frac{\tilde h_{last} - \tilde h_{first}}{t_{last} - t_{first}}$$

Per-unit rate is formed *before* averaging, over $M$ accepted segments:

$$r_i = \frac{|\rho_i|}{|\sigma_i|}, \qquad \bar r = \frac{1}{M}\sum_{i=1}^{M} r_i, \qquad \text{spread} = \max_i r_i - \min_i r_i$$

Refusal: if $\max_i r_i \le 0.01$, no value is returned. The report warns when
$\text{spread} > 0.5\,\bar r$.

**Depth model.** Segments cut on `ballast` with $\epsilon = 0.03$. A hold is *settled*
when its last $5$ depth samples (at least $3$ must exist) satisfy

$$\max(d_{tail}) - \min(d_{tail}) \le 0.05 \ \text{m}$$

and the hold's depth is then the mean of that tail. With $M \ge 2$ settled points and a
depth range across them of at least $0.05$ m, least squares **through the origin**:

$$k = \frac{\sum_i b_i\, d_i}{\sum_i b_i^2}$$

There is no intercept term — $b = 0$ is the surface by definition, not by fit. Note that
this form weights points by ballast, so deep holds dominate the slope.

**Speed from a measured distance.** The longest accepted `throttle` segment with $|u| > 0.05$ is used, and the operator's distance is assumed to belong to it:

$$v = \frac{D}{\Delta t}, \qquad v_{full} = \frac{v}{|u|}$$

The second expression is a straight-line extrapolation to full throttle and is labelled
as such in the report, which also advises repeating the run per throttle step to build a
proper table.

**Speed from the encoder.** Per accepted `throttle` segment with $|u| \ge 0.05$, $E_1 > E_0$ and $\Delta t > 0$:

$$v = \frac{E_1 - E_0}{\Delta t}$$

Results are bucketed by throttle rounded to one decimal and averaged within each bucket,
producing table points directly.

**The self-test's synthetic dive.** Generated at $\Delta t = 0.1$ s with
$\text{turn} = 40$ °/s, $\text{max\_depth} = 9$ m, $\text{speed} = 0.8$ m/s:

$$h \leftarrow (h + \sigma \cdot \text{turn} \cdot \Delta t) \bmod 360, \qquad E \leftarrow E + |u| \cdot \text{speed} \cdot \Delta t$$

Recovery tolerances asserted: turn rate within $1.0$ °/s, depth within $0.2$ m, encoder
speed at full throttle within $0.05$ m/s. Also asserted: a sensorless log returns
nothing from both the turn rate and the depth model; a log whose channels go empty at
the midpoint does not raise, still recovers $40.0$ °/s from the half that was measured,
refuses the depth model with a message naming the emptiness, and reports both affected
channels as gaps.

**Columns read.** $h$ is the journal's `heading_deg` (the estimator's published heading;
identical to the instrument on the `dr` backend, filter-derived on `filtered` — the
untouched instrument is `raw_heading_deg`); depth is `depth_m`; ballast is `ballast`;
payout is `encoder_m`; controls are `steer`, `throttle`, `armed`. The `armed` gate
applies to every extraction, including the depth model.

`api/nav/calibrate.py — _runs()`, `_null_free()`, `_column_state()`,
`_why_null()`, `_unwrap()`, `turn_rate()`, `depth_model()`, `speed_from_ground_truth()`,
`encoder_speed()`, `_gaps()`, `report()`, `_synthetic()`, `selftest()`.

## 9. The heading complementary filter — two witnesses, one of them drunk

Ask the sub which way it is pointing and two instruments answer, both of them liars, in
opposite ways.

The **compass** is the magnetic sensor inside the sub's one motion chip — the part that
feels the Earth's field and works out which way is north — and it is right on average.
It knows where north is and it never forgets. But it is wrong in bursts, and the bursts
are not random: the thrusters are electric motors sitting a few centimetres away, and
when they run they throw a magnetic field that drags the compass around. The simulator
of [§7](#7-the-simulators-dirty-tricks--a-liar-with-a-fixed-seed) injects twenty-two
degrees of it at full throttle — this filter is the thing that lie was built to be
beaten by — and it injects it because that is what the real thing does. The compass lies
hardest exactly while the sub is doing the one thing that moves it.

The **gyro** is the spin sensor on the same chip. It cannot be magnetised, so the
thrusters are invisible to it. But it does not know where north is; it only knows how
fast the sub is turning right now. To get a bearing out of it you have to add up every
turn since the start, and any tiny constant error in "how fast am I turning" adds up
too. It drifts like a shopping trolley with a bent wheel: perfectly usable for the
length of one aisle, hopeless by the time you reach the car park.

So: **believe the gyro moment to moment, and let the compass pull you back with tiny
nibbles, but only when the compass is sober.** Sober means two things at once. The
chip's own calibration score, which runs nought to three, must be at least two — if the
compass says it does not trust itself, neither do we. And the thrusters must be running
at less than half power. Note *running*, not *commanded*: the trust gate reads the
actual output going to the motors, because a disarmed sub can have a joystick jammed at
full deflection and not a milliamp in the windings, and that is a moment when the
compass is perfectly clean.

The nibble is deliberately small. Each tick the filter takes a fraction of the
disagreement, sized so that a standing error would fade with a character of about two
seconds. Two seconds because a canal sub's real turns are slower than the magnetic
disturbance is — the filter is tuned to be quicker than the sub and slower than the lie.

And on top of the nibble sits the **slew cap**: whatever the maths asks for, the heading
may never move more than five degrees per second on account of a correction. Suppose the
sub coasts on the gyro through a long hard run, the compass comes back, and the two
disagree by twenty degrees. The honest thing is to admit the twenty degrees. The
*useful* thing is to walk them back over four seconds rather than teleport. An operator
who watches the heading jump concludes the instrument is broken and stops trusting the
map — all of it, permanently, including the parts that were fine. A lag is forgivable. A
jump is not. There is exactly one exception, and it is principled: after a gap in the
data the filter does snap to the compass, because after a hole there is no continuous
estimate left to protect.

Now the elegant part. Those sober moments are not just spent correcting the heading —
they are spent **teaching the gyro its own bias**. If the compass keeps insisting we
have crept clockwise, the most likely explanation is that the gyro reads a hair fast,
and that hair can be measured and subtracted from every future reading. So the quiet
moments buy accuracy for the loud ones: the gyro lies less during the drunk stretches
*because* of what the compass taught it while it was sober. The learning is switched off
whenever the disagreement is larger than ten degrees, because a big disagreement is a
magnetic disturbance or a bad calibration, and feeding that to the bias estimator
teaches the gyro a lie it will then coast on for the rest of the dive.

**The sign of that lesson is backwards, on purpose, and this is the most load-bearing
oddity in the file.** The obvious way to write the learning step is to nudge the creep
the same way the disagreement points. The obvious way is wrong, and the reason has no
arithmetic in it at all.

The filter *takes the learned creep away* from every gyro reading, because the creep is
the gyro's fault and the whole point is to subtract it. So stand a sub on a bench, dead
still, pointing at the workshop wall, with a gyro quietly insisting it is turning to the
right. Nothing is turning. But the filter believes the gyro moment to moment, so its
idea of the heading walks off to the right, away from the compass — and what turns up as
the disagreement is the compass sitting *behind* it, off to the left.

That left-pointing disagreement is not evidence of a gyro creeping left. It is evidence
of exactly the opposite: the gyro has been running ahead to the right, and the filter has
loyally followed it there. The disagreement always points back the way the filter came,
never the way the gyro is pulling. So the correction that fixes the *cause* has to be
applied against the disagreement rather than with it — and the whole rule fits in one
sentence you can carry around: **the disagreement tells you where the filter has got to,
and the creep to be blamed for it is on the far side.**

The original build note — the design brief this system was written from — had it the
other way round, and the code says so in block capitals with a derivation and a bench
measurement attached, both of which are set out in the formal bit below. The failure of
the obvious version is worth knowing because it is so undramatic: it does not explode,
and it does not even end up pinned against the safety limit where somebody might notice.
It walks the wrong
way for about a minute, the disagreement grows past the ten-degree gate, the gate
switches the bias learner off — and the filter freezes there, holding a standing heading
error of roughly ten degrees that can never be corrected, because the only machinery
capable of correcting it has just gated itself out. The guard does not save the filter.
It embalms it.

Then there is the question everyone forgets to ask: **what if there is no gyro?** A
coast is an integration, and an integration needs something to integrate. Both the
compass reading and the spin rate come off the *same* chip, so the interesting failure
is not "the compass is poisoned" — it is "the chip is gone", and then there is nothing
to coast on either. This filter used to be handed a zero in that case, read it as
"measured: running dead straight", and integrated it into a bearing that sat perfectly
still while the sub turned underneath it. So every branch now asks whether each input
*exists* before asking what it says:

- **Gyro dead, compass alive.** The filter tracks the compass even while distrusting it. A
  bearing twenty-two degrees out under full throttle is a poor measurement, and a poor
  measurement still beats a number nothing is measuring at all. Nothing is concealed by
  this: the calibration score rides out on every frame, and where that score is itself the
  reason for the doubt, the confidence score is floored on it.
- **Compass dead, gyro alive.** This is the real coast, and the only branch allowed to be
  one. The badge that tells the operator "ignoring the compass on purpose" only lights when
  a gyro is genuinely answering — otherwise it would put a *deliberate, under control*
  sticker on a dead chip.
- **Both dead.** The filter returns nothing. Not its last opinion: the last opinion is the
  bearing from whenever the chip last spoke, and the radar display is heading-up, so handing
  that out turns the whole map to face a direction nobody measured. It also marks itself
  uninitialised, so when the chip comes back it re-seeds from the first fresh compass
  reading rather than walking home from a pre-blackout bearing at five degrees a second for
  twenty seconds. The sub turned during the hole. Nothing watched it.
- **No compass has ever answered.** The filter stays uninitialised and answers nothing,
  however healthy the gyro is. A gyro measures *change*, not bearing — there is no origin
  for the sum. Starting from zero would launch every such dive pointing due north.

One thing deliberately survives all of that: the learned gyro creep is never reset. It
is a property of the chip, not of the run, so a reseated connector should not cost the
several minutes of quiet, trusted samples it took to learn.

Finally, the timing rules. If no time has passed, or time went backwards — a duplicated
sample, a clock step when the network time lands mid-dive, a replay seeking — the filter
integrates nothing, but it still moves its clock forward. Hanging on to a timestamp from
the future would wedge the filter shut until real time caught up, which on a Pi with no
battery-backed clock can be an entire dive. And if more than half a second has passed,
that is a **gap**, not a long tick. Integrating a spin rate across a hole assumes the
sub held that rate for the whole hole, and the one thing known about the missing seconds
is that nothing was known about them. So the filter takes the compass's word if it is
worth taking, and otherwise holds.

Underneath every one of these comparisons sits one tiny function: **fold the difference
to the short way round the circle.** Three hundred and fifty-nine degrees and one degree
are two degrees apart, not three hundred and fifty-eight. Get that wrong and it does not
look like a bug — it looks like the sub briefly spun on the spot, and the filter will
politely spend the next minute steering the whole track sideways to explain what it saw.
The calibration tool of
[§8](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no) does the mirror image
of this: given a whole recorded series of bearings it *adds the laps back on*, so a sub
that turned through north reads as one continuous sweep instead of a cliff. Same idea
from opposite ends — one refuses to count a lap that never happened, the other refuses
to lose a lap that did.

### The formal bit

where:

- $t$ — sample timestamp, seconds; $dt = t - t_{prev}$
- $h$ — filtered heading, degrees, held on $0..360$
- $b$ — estimated gyro bias, degrees per second
- $g$ — measured yaw rate `gyro_z_dps`, degrees per second, positive clockwise; may be absent
- $m$ — measured magnetic heading `heading_deg`, degrees (a heading here, not the simulator's injected disturbance); may be absent
- $\text{cal}$ — magnetometer calibration status `mag_cal`, integer $0..3$; may be absent
- $T$ — thrust level, $0..1$
- $e$ — innovation (compass minus filter), degrees, folded short-way-round
- $\alpha$ — first-order blend fraction for this tick
- $\tau = 2.0$ s, slew cap $= 5.0$ °/s, $k_b = 0.01$ /s, $b_{max} = 3.0$ °/s, bias-gate $= 10.0$°, gap $= 0.5$ s, trust thresholds $\text{cal} \ge 2$ and $T < 0.5$

$g$, $m$ and $\text{cal}$ all come off the same IMU part (a BNO085), which is why they go
absent together and why "the compass is poisoned" and "the chip is gone" are different
branches below.

Angle helpers, and every heading subtraction in the system goes through the second one:

$$\mathrm{wrap180}(d) = ((d + 180) \bmod 360) - 180 \qquad \mathrm{wrap360}(d) = d \bmod 360$$

$$\mathrm{heading\_delta}(a, b) = \mathrm{wrap180}(a - b)$$

Thrust level, from the actual motor outputs and not the command:

$$T = \max(|left|, |right|)$$

Gates, evaluated before anything is read:

$$\text{trusted} = (m \text{ present}) \wedge (\text{cal present}) \wedge (\text{cal} \ge 2) \wedge (T < 0.5)$$

$$\text{gyro\_only} = (g \text{ present}) \wedge \neg\text{trusted} \qquad \text{unknown} = \neg(m \text{ present}) \wedge \neg(g \text{ present})$$

Order of the tick, exactly as implemented. Each early return sets $t_{prev} = t$ first.

1. **unknown** → set uninitialised, return *no heading*.
2. **not initialised** → if $m$ absent, return *no heading*; else $h \leftarrow \mathrm{wrap360}(m)$, mark initialised, return $h$. $b$ is not reset.
3. **$dt \le 0$** → return $h$ unchanged.
4. **$dt > 0.5$** (gap) → if $\text{trusted}$ or ($m$ present and $g$ absent), $h \leftarrow \mathrm{wrap360}(m)$; return $h$. No slew cap on this path.
5. **Predict**, only if $g$ is present:

$$h \leftarrow \mathrm{wrap360}\big(h + (g - b)\,dt\big)$$

6. If $m$ is absent, return $h$ — this is the coast.
7. **Innovation:**

$$e = \mathrm{wrap180}(m - h)$$

8. **Correct**, when $\text{trusted}$ **or** $g$ is absent:

$$\alpha = dt / (\tau + dt) \qquad h \leftarrow \mathrm{wrap360}\big(h + \mathrm{clamp}(\alpha e,\; -5\,dt,\; +5\,dt)\big)$$

9. **Learn the bias**, only when $\text{trusted}$ **and** $g$ is present **and** $|e| < 10$:

$$b \leftarrow \mathrm{clamp}(b - k_b\, e\, dt,\; -3,\; +3)$$

**Why the minus, worked on the bench case the code records.** Sub stationary at a true
$h = 100°$, gyro reading a steady $g = +2$ °/s of pure bias, $b$ starting at $0$. Step 5
walks $h$ to $100.2$ while $m$ still reads $100$, so

$$e = \mathrm{wrap180}(100 - 100.2) = -0.2$$

The bias that needs learning is $+2$, and $e$ is negative, so the update must drive $b$
*upwards* on a negative $e$: $b - k_b e\, dt$, not $b + k_b e\, dt$. The brief's plus is
a defect.

**Why the minus is also the stable one.** Two symbols are borrowed locally for this
argument only, because it is about errors rather than positions: write $\epsilon_h = h -
h_{true}$ for the heading error and $\epsilon_b = b - b_{true}$ for the bias error (NOT
the $x$ easting and $y$ northing of the rest of the document), and note that
$e = -\epsilon_h$. A dot over a symbol means its rate of change per second:

$$\dot{\epsilon_h} = -\epsilon_h/\tau - \epsilon_b \qquad \dot{\epsilon_b} = +k_b \epsilon_h \quad \text{(as implemented)} \qquad \dot{\epsilon_b} = -k_b \epsilon_h \quad \text{(the build note)}$$

That linear system's $2 \times 2$ matrix has first row $(-1/\tau,\ -1)$ and second row
$(\pm k_b,\ 0)$, so the two quantities that decide stability are

$$\mathrm{trace} = -1/\tau, \qquad \det = \pm k_b$$

As implemented the determinant is $+k_b > 0$ and the trace is $-1/\tau < 0$ — both
eigenvalues in the left half-plane, so the error decays: stable. With the brief's sign
the determinant is $-k_b < 0$, which forces one positive and one negative eigenvalue: a
saddle. The code records the bench measurement at $dt = 0.1$ s, noise-free, with a true
bias of $+2$ °/s: this rule reaches $b = +1.77$ by $t = 100$ s and $b = +2.0000$ by
$t = 300$ s with zero heading error; the brief's rule grows with an unstable eigenvalue
of $+0.0202$ /s and stalls at $b = -2.96$ °/s with a standing $\approx 9.9°$ error once
$|e|$ crosses the $10°$ gate and disables step 9.

`api/nav/filters.py — wrap180(), wrap360(), heading_delta(), thrust_level(), HeadingFilter.update()`

## 10. The speed Kalman filter — a weather forecast, argued out ten times a second

There is one Kalman filter in this codebase and this is it. It answers one question —
how fast is the sub going through the water — and the reason it needs a filter at all is
that neither instrument that could answer is trustworthy alone.

The **throttle-to-speed table** of
[§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure) is a model: someone once
ran the hull along a measured twenty metres at each throttle step with a stopwatch, and
now the software looks up "half throttle means about half a metre a second". It is
open-loop, which is a polite way of saying it has no idea what is actually happening. It
cannot know about a headwind, a fouled prop, half a metre of weed on the nose, or a
shopping trolley.

The **paddlewheel** can. It is a little printed wheel with magnets in the paddles, and
the water spins it. But it is coarse — a handful of pulses in each half-second window —
it cannot tell which way it is turning, and it stalls entirely below about a tenth of a
metre per second.

So the filter is where the two are made to argue, with the accelerometer holding the
estimate together between measurements. The argument has the same three beats as a
weather forecast, forever, ten times a second:

**Predict.** Take the speed we believed last tick, add whatever the accelerometer says
changed since, and — crucially — widen the error bars. Prediction always makes you less
certain. A forecast made from yesterday's forecast is worth less than yesterday's
forecast was.

**Measure.** Get one number from the outside world, with its own error bars attached.

**Argue.** Blend the two, weighted so that **whoever is less uncertain wins more**. That
sentence *is* the Kalman gain; the rest is bookkeeping. If the prediction is shaky and
the measurement is crisp, the estimate lurches toward the measurement. If the prediction
is solid and the measurement is mush, the measurement barely moves it. And then the
error bars *shrink*, because two half-decent opinions that broadly agree constrain the
truth better than either alone. That shrinking is the entire payoff of the exercise.

Around that core sit four supporting characters, and they are where the judgement lives.

**The accelerometer's built-in lie.** An accelerometer on a sub that is nose-down
slightly reads a sliver of gravity as forward thrust. Integrate that sliver and you
accelerate, smoothly and confidently, while sitting perfectly still. The filter
therefore estimates *two* things at once: how fast we are going, **and how much the
accelerometer lies**. They are linked in a way that makes the second learnable — the lie
converts into fake speed at a known rate per second, so a disagreement with the
paddlewheel that grows steadily over time is attributable to the lie rather than to the
speed. Identify it once and you can subtract it forever.

**The parked-sub rule.** When the wheel has gone quiet *and* nobody is asking for motion
— throttle below a tenth — the filter feeds itself a measurement it invented: "the speed
is zero, and I am sure." Firmly sure: this pseudo-measurement is believed harder than
anything the speed model ever gets to say. This is what stops the accelerometer's lie
from convincing us we are gliding. Without it, the lie wanders freely during every pause
at the bank, and the next burst of thrust starts from a speed that was never real. With
it, every pause is an opportunity: the sub sitting still is the one moment the filter
can lean hard enough on a measurement to strip the bias out of the accelerometer.

**The table demoted to gossip.** When the wheel has gone quiet *while the motors are
pushing*, the filter falls back to the throttle table — but as a rumour, never a fact.
It gets error bars that grow with its own claim: the faster it insists we are going, the
less it is believed. This is the deliberate part. Silent wheel plus hard thrust is
*exactly* the signature the snag detector of
[§11](#11-the-snag-detector--an-if-statement-with-the-instincts-of-a-lie-detector) is
watching for, and the table is exactly the instrument that cannot see a snag. So it is
allowed to lean the estimate, and never to overrule it. Give it twenty seconds of
silence and it does eventually win, because by then it is honestly all there is — but it
wins slowly, and by then the snag alarm has been shouting for eighteen of those seconds.

**The wheel's coarseness, admitted up front.** One pulse per window is the smallest
speed the wheel can express, so it is also the size of its staircase step — a tenth of a
metre per second at the standard settings, which at canal speeds is comparable to the
speed itself. A filter that did not know its measurement came in steps would chase the
staircase instead of the water. So the wheel's error bars are never smaller than that
step, whatever else the maths might suggest. And because the wheel is mechanically blind
to direction, the sign comes from the throttle: the wheel says *how fast*, the throttle
casts the only available vote on *which way*.

Then the sensor-loss rules, which mirror
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk)'s exactly,
because it is the same chip dying. **A missing accelerometer is not zero acceleration.**
Zero is the measurement "coasting", and it would let the filter claim it had watched the
speed hold steady. Silence says nothing about the speed at all. So the term is dropped
and the uncertainty is opened up to the hull's whole dynamic range instead of to a
sensor's noise figure — roughly a metre per second per second rather than a seventh of
that. Without that widening the filter would grow *more confident while blind*, and stop
listening to the paddlewheel, the one instrument still talking to it. And the route by
which a measurement teaches the filter about the accelerometer's lie is cut entirely:
with no chip answering there is no bias to attribute anything to, and doing it anyway
would quietly rewrite the calibration of an absent component out of paddlewheel noise —
a number the filter would then subtract from the first real reading after somebody
pushes the connector back on.

Two clamps finish it. The speed may not sit more than five centimetres per second on the
wrong side of the throttle's sign, because the wheel cannot contradict the throttle
about direction, so a speed that does came from integrated accelerometer noise. A small
tolerance is allowed so that a sub decelerating through zero is not snapped; and a
throttle of exactly zero asserts nothing at all, because a coasting sub genuinely is
still moving. The accelerometer's lie is clamped too: past half a metre per second per
second it is not a bias any more, it is a broken chip.

One boundary the file guards with real anxiety: acceleration is integrated **once**,
into speed, inside a loop that a measurement is continuously correcting. Position
integrates speed, once. Nothing here is ever allowed to integrate acceleration into a
position — that is the double integration that turns a small constant lie into a
kilometre of imaginary track, and it is the failure this whole project was designed
around.

A last honest note: unlike the heading filter, the speed filter has no gap rule. It only
refuses to run when no time has passed or time ran backwards — because re-applying a
measurement at an unchanged timestamp shrinks the error bars for free, and a filter that
has talked itself into certainty stops listening to the wheel, which is a very quiet way
to lose the only real speed measurement aboard. Across a long stall it will integrate
and its uncertainty will balloon, which means the very next real measurement almost
entirely overrules it. It recovers by construction rather than by rule.

### The formal bit

where:

- $dt$ — seconds since the previous tick, as the estimator hands it over
- $v$ — water-relative forward speed, m/s (state 1)
- $b_a$ — forward accelerometer bias, m/s² (state 2)
- $a$ — measured forward acceleration `accel_fwd_ms2`, m/s²; may be absent
- $P$ — $2 \times 2$ covariance, entries $p_{00}, p_{01}, p_{10}, p_{11}$
- $F$ — the state-transition matrix; $H$ — the measurement matrix
- $n_{00}, n_{01}, n_{10}, n_{11}$ — the predicted covariance $P^-$, entry by entry, as the code names them
- $z$ — the single measurement chosen this tick, m/s; $r$ — its variance
- $y$ — residual; $S$ — residual variance; $k_0, k_1$ — Kalman gains (gains here, not a scale factor or a slope)
- $\sigma_a$ — process acceleration noise, m/s²; $q_b = 10^{-4}$ — bias random walk per second
- $q$ — the paddlewheel's quantisation step, m/s (a step size here, not the simulator's steering command)
- $m_p$ — metres of travel per paddlewheel pulse, default $0.05$ (`NAV_M_PER_PULSE`)
- $W$ — the paddlewheel's averaging window, seconds, default $0.5$ (`NAV_PADDLE_WINDOW_S`)
- $u$ — commanded throttle, $-1..1$; $w$ — measured paddlewheel speed, m/s (unsigned); $\ell$ — speed-table answer for $u$ (signed)

Initial state: $v = 0$, $b_a = 0$, $P = \mathrm{diag}(0.25,\, 0.01)$. Quantisation step
$q = m_p / W$, defaulting to $0.05 / 0.5 = 0.1$ m/s, and taken as $0.03$ if $W$ is
non-positive.

If $dt \le 0$, return $v$ with nothing changed.

**Predict.** The state term runs only when $a$ is present:

$$v \leftarrow v + (a - b_a)\,dt$$

$$\sigma_a = 0.15 \;\text{with an accelerometer}, \qquad \sigma_a = 1.0 \;\text{without}$$

The state transition $F$ has first row $(1,\ -dt)$ and second row $(0,\ 1)$ — the speed
carries forward minus $dt$ of whatever the bias is, and the bias carries forward
unchanged. With $Q = \mathrm{diag}(\sigma_a^2 dt^2,\; q_b\, dt)$, the covariance
prediction $P^- = F P F^{\top} + Q$ is expanded in the code as:

$$n_{00} = p_{00} - dt\,(p_{01} + p_{10}) + dt^2 p_{11} + (\sigma_a dt)^2$$

$$n_{01} = p_{01} - dt\,p_{11} \qquad n_{10} = p_{10} - dt\,p_{11} \qquad n_{11} = p_{11} + q_b\, dt$$

**Exactly one measurement per tick**, first matching branch wins. The measurement matrix
is $H = [1,\ 0]$ throughout — every branch measures the speed and none of them measures
the bias directly:

| condition | $z$ | $r$ | `source` |
|---|---|---|---|
| $w$ present (fresh wheel) | $\mathrm{sign}(u) \cdot \lvert w \rvert$ | $\max(0.03,\, q)^2$ | `kf-paddle` |
| $w$ absent and $\lvert u \rvert < 0.1$ | $0$ | $0.05^2$ | `kf-lut` |
| $w$ absent and $\lvert u \rvert \ge 0.1$ | $\ell$ | $(0.3\,\lvert z \rvert + 0.1)^2$ | `kf-lut` |

with $\mathrm{sign}(x) = +1$ for $x \ge 0$ and $-1$ otherwise — the same tie-break the
speed table and the dead reckoner use, so a zero-throttle sample resolves identically in
all three.

**Update** (scalar):

$$y = z - v, \qquad S = n_{00} + r, \qquad k_0 = n_{00}/S$$

$$k_1 = n_{10}/S \text{ when } a \text{ is present}; \quad k_1 = 0 \text{ otherwise}$$

$$v \leftarrow v + k_0 y \qquad b_a \leftarrow b_a + k_1 y$$

$$p_{00} = (1 - k_0) n_{00} \qquad p_{01} = (1 - k_0) n_{01} \qquad p_{10} = n_{10} - k_1 n_{00} \qquad p_{11} = n_{11} - k_1 n_{01}$$

**Clamps:**

$$u > 0 \;\text{and}\; v < -0.05 \Rightarrow v = -0.05 \qquad u < 0 \;\text{and}\; v > +0.05 \Rightarrow v = +0.05$$

$$b_a \leftarrow \mathrm{clamp}(b_a,\, -0.5,\, +0.5)$$

$u = 0$ triggers neither sign clamp.

`api/nav/filters.py — SpeedKF.update()`; wiring and the $dt$ it is handed:
`api/nav/estimator.py — FilteredEstimator.update()`

## 11. The snag detector — an if-statement with the instincts of a lie detector

There is no filter in this one. No gain, no covariance, no blending. It is a comparison,
a stopwatch and a latch, and it is arguably the most valuable arithmetic in the
navigation stack.

The scenario: the sub noses into a submerged shopping trolley, or a rope, or a shelf of
sunken pallets. The motors keep pushing. The sub does not move. And on the map, the dot
sails serenely onward at whatever the throttle table of
[§6](#6-the-speed-lookup-table--four-dots-and-a-tape-measure) says half throttle is
worth, because that table is a function of the throttle and nothing else. The operator
drives the phantom for as long as their patience lasts, and every position computed
after the snag is fiction — not slightly wrong, *fiction*, and the error never comes
back out, because dead reckoning adds each tick to the last. Before the paddlewheel was
fitted, this failure was completely invisible. That is the entire reason there is a
paddlewheel on the bill of materials.

The rule is one sentence: **motors pushing above half power, sustained for more than two
seconds, while the wheel says we are not moving, means we are pinned on something.**

Every clause is doing work. *Above half power* uses the actual motor outputs rather than
the joystick, for the same reason the compass trust gate in
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk) does: a
disarmed sub has full-scale commands flying about and nothing in the windings. *Not
moving* means below five centimetres per second, which is slower than the wheel can
resolve anyway. And *sustained for more than two seconds* is the wait that stops a brief
wall-push from crying wolf. Nudge a lock gate, back off a weed bed, kick off the bank —
all of those are a second of thrust against something solid, and all of them are normal.
An alarm that fires on those is an alarm the operator learns to dismiss, at which point
the real one is worthless too.

**The throttle table is not allowed to testify.** This is a rule about who may be a
witness, not a rule about numbers, and it is expressed in the code as a source check
rather than as maths. In the default backend the detector is handed the raw paddlewheel
reading and nothing else — never the speed that was actually integrated, because on a
stale wheel that speed is the table's. In the filtered backend the detector is handed
the Kalman filter's speed *only while the filter's own label reads paddle-backed*; the
moment the wheel goes quiet and the filter starts leaning on the table, the detector is
handed nothing at all. That matters because the filtered speed does not collapse when
the wheel stops — it drifts toward the table's claim over the following seconds, and if
it were admitted as evidence the detector would go blind in exactly the mode it should
be strongest, on exactly the failure it exists for. A model may not testify to its own
correctness.

Which leaves the sharpest question in the whole detector: **what does silence mean?** A
quiet wheel under hard thrust *is* the snag signature — that is the signal, not the
absence of one. But a hull built without a paddlewheel is also silent, forever, and a
detector that fired on that would raise a snag alarm on every normal run of every
wheel-less hull until the operator learned to ignore snag alarms entirely. So the
detector carries a one-way latch: it remembers whether the wheel has *ever* reported.
Silence only counts as evidence once the wheel has proved it exists. Not knowing must
read as cannot-tell, even here, where the alarm would be the safe guess.

The code states the price of that choice out loud rather than hiding it: a sub that is
already snagged before the wheel has turned even once will not be caught until it moves
once. That is the honest trade, written down where the next person can re-open it.

Two smaller things. The stopwatch re-seeds if time runs backwards, so a replay harness
seeking through a log does not manufacture a snag out of a negative interval. And the
detector runs in **both** estimator backends — it is a safety signal, not a filter
feature, and which backend happens to be configured must never decide whether the
operator is told the sub is stuck. Given that the default backend is the unfiltered one,
a detector that only ran in the filtered path would, in practice, not run at all.

What it does when it fires is deliberately modest: it raises a flag on the frame and
pulls the confidence score down to four-tenths
([§12](#12-confidence--the-humility-score)). It does not stop the track, and it does not
correct the position. The code is blunt about why that is not an oversight — while the
flag is set, the position is still being integrated from a speed model that believes the
sub is moving, so the track is *actively wrong* and must not read as a healthy fix. The
detector's job is to say so. Acting on it is the operator's.

### The formal bit

where:

- $t$ — sample timestamp, seconds
- $T = \max(|left|, |right|)$ — thrust level from actual motor outputs, $0..1$
- $z$ — admissible measured speed, m/s, or absent
- $t_0$ — timestamp at which the current pinned condition began, or absent
- thresholds: thrust $> 0.5$, stopped $< 0.05$ m/s, sustain $> 2.0$ s

Evidence selection, per backend, before the detector is called:

$$z = \text{`speed\_ms\_measured`} \quad \text{(dr backend: the raw paddlewheel, or absent)}$$

$$z = v \text{ when source} = \texttt{kf-paddle}; \quad z = \text{absent otherwise} \qquad \text{(filtered backend)}$$

The latch and the stopped test:

$$\text{ever} \leftarrow \text{ever} \vee (z \text{ present})$$

$$\text{stopped} = (|z| < 0.05) \text{ when } z \text{ present}; \quad \text{stopped} = \text{ever when } z \text{ absent}$$

The timer:

$$\text{if } (T > 0.5) \wedge \text{stopped}: \quad t_0 \leftarrow t \text{ when } t_0 \text{ absent or } t < t_0; \quad \text{snagged} = (t - t_0) > 2.0$$

$$\text{else}: \quad t_0 \leftarrow \text{absent}, \quad \text{snagged} = \text{false}$$

Effect on the frame:

$$\text{confidence} \leftarrow \mathrm{round}(\min(\text{confidence},\, 0.4),\, 2) \quad \text{when snagged}$$

`api/nav/filters.py — SnagDetector.update()`; evidence selection and the confidence floor:
`api/nav/estimator.py — DeadReckonEstimator.update(), FilteredEstimator.update(), _finish()`

## 12. Confidence — the humility score

Every frame the sub sends carries one number between nought and one, and the question it
answers is not "how good is this equipment" or "how well is the software running". It is
narrower and more useful than that: **how much would the system bet on its own dot?**

It is built the same way every tick. Start at one — full confidence — and then let each
thing that has gone wrong knock it down. **Nothing in the codebase can push it back
up.** There is no branch anywhere that rewards agreement, no "everything checks out, so
let us go to one-point-two". It is a ratchet that only turns one way, rebuilt from
scratch each tick, so it is never a running score that could be talked upward over time
— it is this tick's verdict, and this tick's verdict alone. The rule is that a system
may not grade its own homework, and the ratchet is that rule made arithmetic.

Here is every knock that exists, what it means, and what to do about it.

**One — everything agreed.** No knock fired. Worth remembering what full confidence
actually claims, though: dead reckoning's error is roughly five to fifteen percent of
the distance travelled, so even at one the dot is trustworthy to the width of the canal,
not to the metre. Drive it. Do not survey off it.

**Nought point seven — the snapped dot and the raw dot have drifted between eight and
twenty-five metres apart.**
([§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie))
The estimate is being pulled onto the mapped centre line, and the amount of pulling
required has grown past a threshold. That gap is the drift indicator: the estimate and
the waterway are diverging, which means accumulated error, not a mystery. *Do:* finish
the pass, then surface and take a fresh fix. The dot is still useful; it is just getting
older.

Both ends of that range are load-bearing, and the upper one is a real oddity rather than
a tidy-up of the story. Past twenty-five metres the snap is abandoned altogether — and
because this knock lives *inside* the snapping branch, the knock is abandoned with it.
So an estimate that has wandered thirty metres from the water gets no snap and no
penalty, and the drift-o-meter falls silent exactly when the drift is worst.
[§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie) sets that gap out at
length; the thing to carry away here is that a *missing* nought-point-seven does not
mean "no drift".

**Nought point six — the compass has quietly gone bad.** Either the calibration score is
below two, or no instrument answered at all. Those two are genuinely different and they
send you to do different things, which is why the raw score also rides out on the frame:
a score of nought or one means *recalibrate* — the figure-of-eight dance — while nothing
at all means *go and find out why the motion chip is dead* — the one part that carries
the compass, the spin sensor and the accelerometer together, which is why they always go
quiet as a set. Worth knowing: the thruster-poisoned
compass is **not automatically** one of these. When the filter of
[§9](#9-the-heading-complementary-filter--two-witnesses-one-of-them-drunk) stops
believing a magnetically-drunk compass, it is the thrust gate that trips, and thrust is
not a calibration score. So a coast that begins because the motors came up while the
chip still rates itself two or better costs no confidence at all: the operator gets the
gyro-only badge and nothing else. Handled on purpose is not the same as broken.

But push harder and the two do arrive together, and that is correct rather than a
double-count. A compass being dragged about badly enough downgrades *its own* score, and
once that score falls below two this knock fires alongside the gyro-only badge — on the
simulated path, which runs legs at six and eight tenths of throttle, that overlap is the
common case and not the exotic one. The badge says "ignoring the compass on purpose";
the knock says "and the compass agrees it should be ignored". Two different witnesses
saying the same thing is worth hearing twice.

**Nought point five — the tether clamp fired.**
([§4](#4-the-tether-clamp--the-leash-is-a-fact)) The dead-reckoned range exceeded the
amount of cable that has actually been paid out, which is impossible, so the estimate
has already been pulled back onto the payout circle. The direction is still believed;
the distance was overstated. *Do:* expect the dot to be optimistic about how far out you
are, and treat the payout reading as the real bound.

**Nought point four — snagged.**
([§11](#11-the-snag-detector--an-if-statement-with-the-instincts-of-a-lie-detector))
Sustained thrust, no measured movement. *Do:* stop driving forward. The map is marching
and the sub is not, and every second of it makes the next position worse. Back off,
wiggle, and watch for the wheel to turn again.

**Nought point one — the track is held.**
([§2](#2-dead-reckoning--counting-strokes-with-your-eyes-shut)) No heading is available,
so there is no direction to put the speed on, and the position has stopped advancing
altogether. The coordinates on the screen are a *timestamp of the last fix*, not a
position, and the gap between the two grows with every second the sub keeps moving. It
is not nought, deliberately: the last fix is still the best place to start looking.
*Do:* do not navigate off the map at all. Recover the motion chip, or haul in on the tether,
which is the one measurement still working.

Notice the ordering of those last two. A snagged sub scores *higher* than a heading-less
one, and that is not an accident. A snagged sub is still being tracked — the estimator
knows where it thinks the sub is and is telling you that number is running away from
reality. With no heading, the estimator has stopped tracking at all. Being wrong in a
way you can describe beats not knowing.

The knocks stack by taking the worst, never by multiplying, so three problems at once
report the severity of the worst one rather than compounding into a number that means
nothing. And one honest caveat about today's code: the dashboard receives this number
and stores it, but does not currently draw it anywhere. What the operator actually sees
are the individual badges — snagged, gyro-only, heading-suspect, no-compass — plus a
tether-range readout the browser works out for itself, straight-line from the operator's
own dot to the sub against the cable length in the console's configuration. It never
touches the server's range or payout figures. Those two are sent, stored in a variable,
and read by nothing at all, which the API's frame audit records in as many words as dead
stores — the same suggests-but-is-never-read pattern
[§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie) describes for the
drift reading. So as it stands, confidence is mostly for the dive log and the replay
harness: it is the number a later analysis uses to decide which parts of a recorded
track to believe. The reasoning above still describes what each value means; it just
does not yet have a place on the screen to say it.

### The formal bit

where:

- $c$ — confidence, $0..1$, reported to two decimal places
- $r$ — dead-reckoned range from origin, metres; $L$ — tether payout `encoder_m`, metres
- $\text{cal}$ — magnetometer calibration status, integer $0..3$, or absent
- $d$ — snap offset (distance from the raw estimate to the snapped point), metres

Computed fresh every tick, in this order:

$$c = 1.0$$

$$\text{no heading (}h\text{ absent)} \Rightarrow c = 0.1$$

$$L > 0 \;\wedge\; r > L \Rightarrow c \leftarrow \min(c,\, 0.5) \quad \text{(applied with the clamp } x,y \leftarrow (L/r)\,x,y \text{)}$$

$$(\text{cal absent}) \vee (\text{cal} < 2) \Rightarrow c \leftarrow \min(c,\, 0.6)$$

$$\text{snapping on} \;\wedge\; d \le 25 \;\wedge\; d > 8 \Rightarrow c \leftarrow \min(c,\, 0.7)$$

$$\text{snagged} \Rightarrow c \leftarrow \min(c,\, 0.4)$$

$$\text{reported} = \mathrm{round}(c,\, 2)$$

The no-heading case is an assignment rather than a $\min$, but it is the smallest value
in the cascade, so every later $\min$ leaves it standing. $d > 25$ suppresses snapping
altogether and therefore cannot trigger the $0.7$ knock. The snag floor is applied after
the dead reckoner has finished, on the emitted frame, and is the only knock that lives
outside the dead reckoner.

In the filtered backend, "$h$ absent" means the heading filter produced nothing — no
compass *and* no gyro, or a filter never initialised. In the default backend it means
the raw compass reported nothing. Same floor, slightly different claim.

`api/nav/deadreckoning.py — DeadReckoner.update()` (the $0.1$, $0.5$, $0.6$ and $0.7$ knocks,
and `NO_HEADING_CONFIDENCE`); `api/nav/estimator.py — _finish()` (the $0.4$ snag floor,
`SNAGGED_CONFIDENCE`)

## 13. Depth from pressure — the one number nobody has to build

Water is heavy, and — this is the gift — it is boringly consistent about it. A metre of
canal water weighs the same as a metre of canal water everywhere in the canal. There is
no local variation, no weather down there, no fluky patch where the metres weigh less.
Stack up two metres and you get exactly twice the squeeze. So a pressure gauge is a
depth gauge. You do not have to compute depth. You measure it.

One honest asterisk, and it belongs here in the story rather than buried in the algebra:
consistent within *this* water. Salt water is about two and a half per cent heavier than
fresh, so it squeezes correspondingly harder, and the number the code divides by is a
fresh-water one. Take this sub to sea without changing it and every depth it reports
would read a couple of per cent deep — a few centimetres at canal depths, which is
nothing, and a real error nonetheless. It is a canal boat. The constant is right for the
water it is in, and wrong the moment it is not.

That makes depth the odd one out on this vehicle, and the good kind of odd. Every other
position number on the sub is *built* — that is the whole of
[§2](#2-dead-reckoning--counting-strokes-with-your-eyes-shut): a speed multiplied by a
bit of time, added to a running total, over and over, each step inheriting the errors of
every step before it. Leave the sub out for twenty minutes and the map's idea of where
it is has quietly gone soft. Depth does none of that. It is read fresh from the water
about ten times a second and it has no memory at all, so it cannot drift. Whatever the
map is doing, the depth is right. The code is explicit about protecting this: depth is
taken from the sensor and never, ever accumulated.

**The catch, and the day you zeroed it.** The gauge cannot tell water from air. Sitting
on top of the canal is an entire atmosphere, several miles of it, and it presses down
too — as it happens, about as hard as ten metres of water. The gauge feels the lot and
reports one number. Nobody has to tell it which part is sky.

So somebody has to tell it what "no water above me" feels like. That is the surface
zero: you float the sub with its sensor just under, in still water, read what the gauge
says, and write that down as the value that means nought metres. Every depth after that
is the difference between what the gauge says now and that written-down number.

And here is the thing the tired operator needs to know: **the atmosphere is not the same
every day.** A deep low over the Midlands presses noticeably less hard than a crisp
anticyclone does. A sub zeroed on a stormy Sunday and dived on a fine Wednesday will
read shallow by a hand's width or so, all day, quite confidently, with nothing on the
console looking wrong. It is not a big error. It is a completely invisible one, which is
a different problem.

Now the purposeful oddity, and it is a real one. **This sub does not zero itself.**
There is no tare button on the console. There is no automatic zeroing at boot. The
surface pressure is a setting that is read once when the software starts and is frozen
for the life of the process; changing it means an operator floats the sub, reads today's
pressure off the telemetry, types it into a configuration value and restarts the
software. The build notes then tell you to check it against a tape measure by holding
the sub at a marked two metres.

That is more faff than a button. What the faff buys is that the definition of "the
surface" cannot move on its own. A tare button is a button that can be pressed at three
metres down, and then every depth for the rest of the dive is measured from three metres
down, and nothing on the screen says so. Automatic zeroing at boot is worse, because the
Pi reboots when somebody kicks the tether, and tethers get kicked mid-dive. A number
typed by a human at a bank, that lives in a file and outlives the software, cannot
redefine the surface while your back is turned. The cost is that you have to remember to
do it, and if you never do, the sub uses a textbook sea-level figure that is nobody's
actual Tuesday.

**The one zero that is allowed.** If today's air is heavier than the day you zeroed, the
sub floating at the surface comes out very slightly *negative* — above the surface,
which is not a thing. The code clamps that to nought metres and reports it.

That nought is a genuine measurement. It says: the gauge answered, and the answer is "at
or above the surface". It is the one zero in this entire document that is allowed to
stand, and it is allowed precisely because a working instrument produced it.

**And when nothing answers** — which brings us to the reason this section exists at all.

When the depth sensor stops answering, the depth does not go to nought. It goes to
*nothing* — blank on the console, absent on the wire, no number at all. The pressure
goes with it, because the two are the same instrument and it would be strange to publish
a depth whose provenance had vanished.

The reason is the whole thesis of this document in one line: **nought metres is not a
neutral filler. It is the single most reassuring claim this vehicle can make.** It says
"I am at the surface", and a descending sub is at every depth except that one. Fill a
blank with it and you have not avoided making a claim; you have made the most comforting
claim available, at exactly the moment you had no right to make any.

This system learned that twice, expensively, and both scars are in the source.

The first was a sensor that died mid-dive at four and a third metres. It had come up
fine at boot, so every check anyone had written — all of which asked *did the chip
start?* — was happy. The last number it managed to read stayed in memory. The software
handed that number out fifteen times a second for the rest of the dive. The console
painted a confident depth, correctly colour-banded, arriving fresh in every frame, while
the sub went to eight metres. Nothing anywhere reported a fault. The cache was never the
problem; treating *I remember a number* as *I can measure it* was.

The second was nastier. An I2C line held low — a shorted wire, a connector with no
pull-ups, a chip with no power — reads as zero for everything you ask it. So the
sensor's factory calibration read back as all zeros. All-zero calibration data passes
its own built-in checksum, because the checksum of nothing is nothing, and the nothing
matched. The sensor therefore reported itself online. Zero calibration constants
multiply out to zero pressure. Zero pressure is below the surface zero, so the clamp
caught it and produced exactly nought point nought nought metres. A sub calmly reporting
"at the surface" all the way to the bottom of a canal, flagged as real hardware rather
than a simulation, with not one fault raised. That is the most dangerous reading this
system is capable of producing, and the shape of a dead bus is now rejected *before* the
checksum is allowed a vote.

So the sensor has to clear three separate hurdles before its number is believed at all.
Its calibration data must not look like a stuck wire — seven identical values is one
voltage on a wire, not seven constants from a part, and a rail value in any one of them
is a partially stuck bus. Then it must pass the factory checksum. Then, continuously, it
must have answered *recently* and not have failed repeatedly, since a bus can die by
throwing errors or by simply going quiet, and going quiet produces no error at all while
leaving the last number sitting there looking perfectly fresh.

There is a nice detail in that last hurdle. Reading this chip is a three-part ritual:
order a pressure measurement, come back and collect it while ordering a temperature
measurement, come back and collect that. Only the *collecting* counts as the chip having
answered. A chip that accepts the order and then never hands anything back is precisely
the silent freeze the whole check exists to catch, and congratulating it for having
accepted the order is exactly how it would hide.

**Three small honest details.** The three-part ritual is not efficiency, it is manners.
Each measurement takes about seventeen milliseconds to complete at the resolution this
sub uses. Sitting and waiting
through them would be a third of a second per second in which the same thread is not
reading the compass, and a compass sampled at a crawl is its own problem. So the
software places the order, goes away and does other work, and comes back on a later pass
to collect. One complete depth reading every tenth of a second, which is comfortably
faster than a canal sub can change depth.

The chip also measures the water's temperature, and it does that for its own sake: a
warm gauge and a cold gauge read differently by enough to shift the depth by
centimetres, and the shift appears exactly when the sub descends out of the sunlit top
layer into the cold. The correction is applied. The temperature itself is then thrown
away — it is computed, used, and not wired to anything you can see. It is sat there in
the code waiting for somebody to want it.

Finally, and it is a curiosity rather than a problem: there are two depths in this
system, computed the same way from the same reading, at different precision. The
console's depth is worked out from a pressure that has been rounded to a tenth of a
pound per square inch first, which makes it move in steps of about seven centimetres.
The navigation sample is worked out from the unrounded pressure and keeps millimetres —
though by the time the estimator has published it and the dive log has written it down
it has been rounded again, to centimetres. They can disagree with each other by a few
centimetres. Neither drifts, both are measured, and if you ever find yourself comparing
a screenshot against a dive log to the centimetre, that is why.

### The formal bit

where:

- $D_1$ — raw 24-bit pressure word from the chip
- $D_2$ — raw 24-bit temperature word from the chip
- $C_1 \ldots C_6$ — factory calibration coefficients read from the chip's PROM
- $\Delta T$ — raw temperature difference from the factory reference
- $T$ — chip temperature in hundredths of a degree Celsius (so 2000 is 20.00 °C)
- $\text{OFF}$, $\text{SENS}$ — the datasheet's pressure offset and sensitivity terms
- $T_i$, $\text{OFF}_i$, $\text{SENS}_i$ — the second-order temperature corrections
- $P_{mbar}$ — compensated absolute pressure, millibar
- $p$ — the same pressure in pounds per square inch, as published
- $p_0$ — configured surface pressure, psi (`surface_pressure_psi`, default 14.7)
- $k_p$ — pressure per metre of water, psi/m (`psi_per_meter`, default 1.42)
- $d$ — depth below the surface, metres

**Raw chip words to millibar** (MS5837-30BA datasheet compensation, second order
included):

$$\Delta T = D_2 - 256\,C_5$$

$$T = 2000 + \frac{\Delta T \cdot C_6}{8388608}$$

$$\text{OFF} = 65536\,C_2 + \frac{C_4 \cdot \Delta T}{128}$$

$$\text{SENS} = 32768\,C_1 + \frac{C_3 \cdot \Delta T}{256}$$

For $T \ge 2000$:

$$T_i = \frac{2\,\Delta T^2}{137438953472}, \qquad \text{OFF}_i = \frac{(T-2000)^2}{16}, \qquad \text{SENS}_i = 0$$

For $T < 2000$:

$$T_i = \frac{3\,\Delta T^2}{8589934592}, \qquad \text{OFF}_i = \frac{3(T-2000)^2}{2}, \qquad \text{SENS}_i = \frac{5(T-2000)^2}{8}$$

and additionally, for $T < -1500$, the terms $7(T+1500)^2$ and $4(T+1500)^2$ are added
to $\text{OFF}_i$ and $\text{SENS}_i$ respectively.

The second-order corrections are subtracted into the first-order terms, and the code
then computes the pressure as one flat left-to-right chain rather than a stacked
fraction:

$$\text{SENS} \leftarrow \text{SENS} - \text{SENS}_i, \qquad \text{OFF} \leftarrow \text{OFF} - \text{OFF}_i$$

$$P_{mbar} = (D_1 \cdot \text{SENS} / 2097152 - \text{OFF}) / 8192 / 10$$

$$p = 0.0145037738 \cdot P_{mbar}$$

The water temperature $(T - T_i)/100$ °C is computed and discarded — nothing consumes
it.

**Pressure to depth**, identical on both paths:

$$d = \max\left(0,\ \frac{p - p_0}{k_p}\right)$$

Telemetry rounds $p$ to 1 decimal place *before* this division and rounds $d$ to 2
(giving a depth quantised to $0.1/k_p \approx 0.070$ m); the navigation sample divides
the unrounded $p$ and rounds $d$ to 3. There is then a *second* rounding on the
navigation path, behind the two precisions the story mentions: the dead reckoner passes the sample's depth
straight through but rounds it to 2 on the way into `NavState`, and it is `NavState` the
dive log writes. So the millimetres live on the sample and the journal keeps
centimetres.

**Density.** $k_p = 1.42$ psi/m is fresh water (1 m of fresh water = 9.81 kPa = 1.42
psi) and is the only value shipped. Salt water is denser — roughly 1.46 psi/m — so
dividing a salt-water pressure by the fresh-water constant over-states the depth by
about 3 %; the calibration procedure requires a measurement before changing it.

**Absence.** $p$ absent implies $d$ absent. There is no substitution — in particular
never $d = 0$, and never the previous $d$.

$$p \ \text{absent} \implies d \ \text{absent}$$

**When is $p$ absent.** The cached pressure is offered only if a good read has ever
happened *and* the device is currently answering, where for this chip, with $t_{ok}$ the
time of the last successful collect and $F$ the count of consecutive failures:

$$\text{answering} \iff (t_{ok} \ \text{exists}) \ \wedge\ (F < 2) \ \wedge\ (t_{now} - t_{ok} \le 2.5\ \text{s})$$

Only the collect stage of the conversion cycle calls the success path.

**PROM acceptance**, in order, all four required:

1. exactly 7 words, else reject;
2. not all 7 words equal, else reject (a stuck bus repeating one value);
3. none of $C_1 \ldots C_6$ equal to `0x0000` or `0xFFFF`, else reject (a partially stuck bus);
4. $\text{CRC4}(\text{words}) = $ the top nibble of word 0.

The CRC is the datasheet's 4-bit remainder: word 0's top nibble cleared, the seven words
zero-padded to eight, fed as sixteen bytes through a shift register with polynomial
`0x3000`, result taken as the top nibble of the 16-bit remainder.

**Conversion timing.** Stage 0 issues the $D_1$ convert command and waits 20 ms; stage 1
reads $D_1$, issues the $D_2$ convert and waits 20 ms; stage 2 reads $D_2$, computes,
and waits 60 ms. One complete sample per 100 ms, i.e. 10 Hz. Both converts use OSR 8192
(commands `0x4A` / `0x5A`), whose conversion time is 17.2 ms — hence the 20 ms gaps. A
read that raises resets to stage 0 with a 1 s back-off.

`api/hardware.py — _ms5837_mbar(), _ms5837_prom_valid(), _ms5837_crc4(),
RealHardware._pressure_tick(), RealHardware.read_pressure(), DeviceHealth.faulted()`
`api/rov.py — RovState.telemetry()`
`api/nav/sensors.py — _hw_sample_fields()`
`api/nav/deadreckoning.py — DeadReckoner.update()`
`api/config.py — Settings.surface_pressure_psi, Settings.psi_per_meter`

## 14. The launch bank — measuring a bank against the water beside it

You are standing on a towpath with a sub in your arms, looking for somewhere to put it in.
What you want to know is simple and entirely local: **how far down is the water from here?**
Half a metre and you can kneel and lower it. Two metres of brick wall and you cannot, not
with a tethered vehicle and not without dropping it.

The Environment Agency has flown most of England with a laser and published the result: a
grid of ground heights, one number per metre square, measured from an aircraft. So the height
of the towpath is known. The height of the water is known. Subtract and you have your answer.

Except that "the height of the water" is where this gets interesting, and it is the whole
reason this section exists.

A canal is not a river. It does not slope. It is a **staircase of flat sheets**, each one
dead level for miles, separated by locks that step it down. In Camden the flight drops
29.0 → 27.6 → 25.2 → 22.6 metres above sea level in about four hundred metres of walking.
So there is no such thing as "the water level" for a canal. There are only the water levels,
one per pound, and the one that matters to you is **the one you are standing next to**.

Get this wrong in the obvious way — take one water height for the whole area — and the layer
does something worse than fail. Above the lock it measures banks against water that is too
low and calls safe ones dangerous. Below the lock it measures against water that is too high
and calls dangerous ones safe. It is confidently wrong at both ends and correct only in the
middle, which is the shape of error that gets somebody hurt: not noise, but a smooth,
plausible answer that is systematically wrong in a way that depends on where you are.

So the layer finds the sheets first. Water is the flattest thing in the landscape — a canal
surface varies by centimetres over hundreds of metres, while nothing else does — so the
elevations along the cut pile up into sharp spikes, one per pound, with the lock steps as
gaps between them. Find the spikes and you have found the water levels, without anybody
publishing them. Then every square metre of bank is measured against the sheet it actually
sits beside.

Two consequences worth stating plainly, because both are counter-intuitive on the ground.
A bank **higher above sea level** can be the safer one — the probes in the tests are exactly
this, 0.75 m higher in absolute terms and lower relative to its own pound. And **amber is a
statement about height and nothing else**. It knows nothing about fences, gates, private
land, nettles, or whether you can carry a sub to it. It is a geometric fact, not permission.

The last piece is what the layer refuses to draw. **Water is never painted**, at any zoom.
Not blue, not shaded, nothing — because the survey says nothing about what is under the
surface, and a tint over water would be read as a claim about depth by an operator glancing
at a screen in the rain. Ground with no paint has not been surveyed and found safe. It has
not been looked at.

### The formal bit

*Skippable. The story above is the whole idea; this is how the arithmetic is actually done.*

**Finding the water.** No list of locks appears anywhere in this code, and none is needed.
Water is detected by flatness: take the elevation gradient at every cell and call a cell flat
where $|\nabla z| <$ `lidar_flat_gradient` (0.04, i.e. a 4 % slope), then keep the flat cells
within `lidar_water_sample_m` (8 m) of the centreline. On a 1 m composite the canal surface is
the flattest thing in the scene by a wide margin — inside one sheet the 10th and 90th
percentiles agree to the centimetre. A bridge deck is the case this has to get right, and does:
a deck is cambered, fails the flatness test, and never becomes its own datum.

**The datum field, and the part that actually matters.** Every bank cell is measured against
the elevation of the **nearest flat-water cell**, found with a Euclidean distance transform
over the water mask. Not against a pound level, and not against any global figure — against
the water *beside it*. That single choice is what makes the amber/brown split walk down a lock
flight correctly with no knowledge that locks exist: cross a lock and the nearest water is the
next pound down, so the datum steps down with you, automatically.

**The pound levels are for the labels, not the classification.** Histogram the flat-water
elevations in `lidar_pound_bin_m` (0.2 m) bins and take the prominent, well-separated modes
via `scipy.signal.find_peaks`, requiring `lidar_pound_separation_m` (0.6 m) between them so
one pound cannot report as two. The reported level is the **median of the winning bin and its
immediate neighbours**, not the bin centre — a bin centre quantises every level to the nearest
10 cm and the phase of that quantisation is an accident of where the histogram happened to
start. `BANK_POUND_MIN_PIXELS` (500) is a guard on the whole sample: fewer flat-water cells
than that and no levels are reported at all, rather than a mode being fitted to a puddle.

**Checked against the real survey, two ways.** On the held Camden area the detector reports
seven levels — 29.67, 29.01, 27.64, 25.10, 23.08, 22.12, 20.89 m OD — every one a genuinely
populated sheet (13,905 to 53,498 flat cells within ±0.25 m of it), with the closest pair
0.66 m apart and so clearing the 0.6 m separation rule rather than being one pound reported
twice.

Five of those were measured **independently and by a different method**: taking the 10th
percentile of elevation within 7 m of the Trust's centreline either side of each lock in the
flight gives 29.01, 27.64, 25.10, 23.07, 20.89. The histogram agrees with all five, worst
case 1 cm. The two the lock-based sample never visited (29.67, 22.12) are pounds above and
below the stretch it walked. Two methods that share no code arriving at the same water levels
is the strongest evidence available that these are sheets of water and not artefacts of the
binning.

**Classifying.** With $h$ the cell's elevation and $w$ the elevation of its nearest water
cell, the height above local water is $\Delta = h - w$, and

$$
\text{class} =
\begin{cases}
\text{WATER} & \text{inside the channel buffer, \textbf{or} flat and } |\Delta| \le 0.25\ \text{m}\\
\text{LOW (amber)} & \Delta < \texttt{lidar\_launch\_max\_height\_m}\ (2\ \text{m})\\
\text{HIGH (brown)} & \text{otherwise}
\end{cases}
$$

The water test is a union of two, and the second half is why a wide basin or a winding hole
does not come out as a two-metre-deep hole in the paint: anything flat, within
`BANK_WATER_REACH_M` (40 m) of the centreline and level with the local datum to within
`BANK_WATER_TOLERANCE_M` (0.25 m) is water, whether or not the published centreline happens to
run through it.

The comparison is strictly `<`, so exactly 2.000 m is brown. That edge is asserted in the
tests with values chosen to be exact in float32 (1.9375 / 2.0000 / 2.0625), so the check
measures the comparison and not a rounding error.

**Downsampling without inventing.** At zoom levels where one screen pixel covers many grid
cells, the naive move is to average — and averaging class codes manufactures amber along
every water/wall seam, because the mean of WATER (1) and HIGH (3) is LOW (2). A constructed
sheer-wharf case produces 470 invented amber cells at z15 that way, from a scene containing
no amber at all. The tiler therefore **counts** rather than averages: each output cell takes
the majority class of the cells beneath it, so a class can only appear on screen if it exists
underneath. Verified at 0 invented cells across 1,123,868 aggregated cells. The output cell's
alpha is set to the *proportion* of its subpixels that carried paint, so the edge of a
surveyed corridor fades out honestly instead of claiming full coverage.

**Relief.** The shading is a standard hillshade with the light at azimuth 315°, altitude 45°,
and vertical exaggeration ×3 — a cartographic convention, not a measurement, and it modulates
brightness only within the band `BANK_HILLSHADE_MIN`–`MAX` (0.72–1.60) so it can never move a
cell across a class boundary. The relief is there to make a wall look like a wall; it is not
allowed to change what the colour says.

## 15. What a number is allowed to claim

*This section has no formal bit. It is the one topic in the document with nothing to
skip.*

Look back over everything you have just read and the arithmetic is embarrassing. A speed
multiplied by a slice of time. A running total. One sine, one cosine, and the schoolbook
fact that a right-angled triangle has a longest side. A lookup table with straight lines
drawn between the points. A single small matrix, two rows by two columns, in the only
Kalman filter anywhere in the system. There is not one step in this document that would
trouble a competent sixteen-year-old, and that is not an apology. Tuesday-level
arithmetic, thoroughly understood, is the correct amount of maths for a small submarine
in a canal.

The sophistication is somewhere else entirely. It is not in working out the answer. It
is in working out **what each number is entitled to say**, and refusing to let it say a
word more.

Five rules keep coming round, and once you have seen them you will spot them in every
section above.

**Payout bounds, but never positions.** ([§4](#4-the-tether-clamp--the-leash-is-a-fact))
The tether encoder counts the cable off the drum. That tells you, absolutely, that the
sub cannot be further away than that much cable. It tells you nothing whatsoever about
*where* — the cable could be looped, or dragged, or coiled on the bottom. So the payout
is allowed to pull an over-confident estimate *inwards* until it fits inside that
circle, and it is never, ever allowed to push an estimate outwards to reach the edge of
it. A payout of nothing means "no bound is known", which loosens the constraint rather
than yanking the sub home. And a real spool's count is allowed to go *down* when the
cable is wound back in, with no high-water mark latched anywhere, because a sub that has
been hauled halfway home should not still be claiming it might be at the far end of the
rope. That is the opposite of what a bound is for.

One footnote on today's build, in the same spirit. Hardly any hull has a spool encoder
fitted, and on the shipped default the bound is therefore not a cable count at all: it
is a deliberate over-estimate built out of throttle and time, which only ever grows.
That is the single place in this whole document where a model is permitted to stand in
for a measurement, and it is permitted for one reason only — it can loosen the leash and
it cannot tighten it. A one-sided error is a different object from an error.
[§4](#4-the-tether-clamp--the-leash-is-a-fact) sets out the terms.

**Snap suggests, but never overwrites.**
([§5](#5-centreline-snapping--the-magnet-that-is-not-allowed-to-lie)) A canal is a thin
line on a map, and sliding a wandering estimate sideways onto the middle of the water is
usually correct and looks wonderful. So it is done — but the un-snapped position travels
in the same frame, right beside the snapped one, and the *size* of the sideways
correction is published as a number. Because that number is not tidying-up, it is
evidence: a big correction means the estimate has drifted a long way, and the correct
response to a big correction is for confidence to go **down**. A system that let
snapping quietly raise its own confidence would be laundering an error into a
reassurance.

**A model may not grade its own homework.**
([§8](#8-calibration-forensics--the-tool-that-is-allowed-to-say-no),
[§11](#11-the-snag-detector--an-if-statement-with-the-instincts-of-a-lie-detector)) The
speed table maps throttle to speed and it does it well. It is also a function of
throttle and nothing else, which means that for a sub bolted immovably to a submerged
shopping trolley it will report a healthy speed with total serenity, for as long as the
operator's patience lasts. Therefore the model is inadmissible as evidence in the one
test that asks *is the sub actually moving*. Only a measurement counts there, or a
filtered estimate that a measurement is currently holding up. The same rule reaches
further than you would expect, and this is the one place in the document where it
reaches further than the code currently does: a simulator that derives its truth from
the model under test makes every error in that model cancel out invisibly, so the bench
simulator takes its own true speed table as an argument, separate from the estimators'.
Nothing in the repository passes it a different one today, so the largest error term in
the whole system is the single disturbance the default simulation cannot contain. The
door is cut; nobody has walked through it.
[§7](#7-the-simulators-dirty-tricks--a-liar-with-a-fixed-seed) says so at greater
length, and it is a one-argument fix.

**An estimate never dresses as a measurement.**
([§10](#10-the-speed-kalman-filter--a-weather-forecast-argued-out-ten-times-a-second))
Every speed that leaves this system carries a label saying which instrument produced it,
and the dashboard draws the two differently on purpose. When the paddlewheel goes stale
the number keeps flowing — the model is still the best guess available and withholding
it would help nobody — but the label changes, immediately and visibly. The label is the
honest part. The number was never the claim.

**And no measurement means no answer, not a default.**
([§2](#2-dead-reckoning--counting-strokes-with-your-eyes-shut),
[§13](#13-depth-from-pressure--the-one-number-nobody-has-to-build))

That last one has a sharp edge, and this codebase found it the way you find a sharp
edge. Here it is, and it is the sentence the whole document has been circling:

> **A "cannot tell" that is itself a measurement is not a "cannot tell".**

Nought degrees is not the absence of a compass. It is *due north*. And because this map
draws heading-up, a dead compass did not blank the map — it swung the entire map north
and carried on drawing, with a confident bearing sat next to a NO COMPASS badge on the
same screen.

Nought metres is not the absence of a depth sensor. It is *the surface*: the one depth a
descending sub is definitely not at.

A calibration score of nought is not the absence of a compass. It is "a compass answered,
and it is telling you not to trust it". Those two send an operator to do completely
different things — recalibrate the thing, or go and find out why the chip is dead — and
for a long time a hull with no IMU wired at all read as a fitted compass in need of a
wiggle.

Nought volts is not the absence of a battery monitor. It is the most alarming number
that gauge can draw, so a dead monitor painted a red critical warning over a perfectly
full pack. Being wrong in the safe direction is still being wrong, and an alarm that
cries wolf is an alarm the operator learns to dismiss without reading.

Nought metres per second is not the absence of a paddlewheel. It is "measured, and it is
not turning" — which is the exact evidence the snag detector was bought for, so
overloading it to also mean "no wheel fitted" would destroy the one signal that
justified the sensor.

A ballast level of nought is not "the syringe has never been calibrated". It is "the
syringe is empty", which is a specific claim about buoyancy, and an operator will dive
on it.

And NORMAL is not "the leak subsystem has nothing to report". NORMAL is a positive claim
about hull integrity — both probes were read, and both were dry — and it is the
strongest reassurance this vehicle ever gives. It was once being given at full telemetry
rate, for the rest of a dive, by debouncers that nobody had sampled since a completely
unrelated chip on a completely unrelated bus threw one error. Every other gauge on that
console correctly blanked and named its faulty part; the one that decides whether a dive
is recoverable stayed green on evidence no one was collecting. That is why there are
four leak states and not three.

So the working method, applied everywhere: for each channel, ask whether any value in
its range already means something real. Usually every value does, and then you have to
go and *spend a new one*. A null. A minus-one, on an integer field that has no null to
spend. A fourth word in a three-word vocabulary. A not-fresh flag travelling beside a
magnitude that is meaningless without it. It costs something every time — a protocol
change, a display case, a branch in every consumer — and it is paid every time.

Two corollaries, both learned rather than designed.

*Absence must arrive with a name.* A blank gauge nobody can explain is only half a fix.
So every null ships alongside the name of the part to go and look at, using the same
designation printed on the wiring diagram, so that the console names the object a human
will shortly be unplugging. And that vocabulary deliberately includes things that are
not chips — a pair of wires, a software loop — because a thread that has stopped freezes
its readings exactly as hard as a chip that has stopped, and the values it freezes are
always the comfortable ones: four bars, the last speed, NORMAL.

*Cannot-tell never cancels an alarm.* Water that has already reached a probe is an
established fact, and the sampler dying afterwards does not un-establish it. The probe
drying out later is not evidence the hull is sound, and neither is nobody watching it.
Only the *reassurance* needs to be alive. So the gate that turns a missing sampler into
"unknown" sits between the warning and the all-clear, never above the alarm. Nothing in
this system is permitted to talk the console down off a flood.

Which leaves the picture the whole thing is built on.

Every sensor on this vehicle is a **witness**, and not one of them is fully trusted. The
compass is pulled around by the sub's own thrusters, so it lies hardest at the precise
moment the sub is doing the thing that moves it. The paddlewheel cannot tell forwards
from backwards and goes blind below walking pace. The pressure gauge faithfully reports
this morning's weather along with the water. The tether knows how much rope went over
the side and nothing at all about which direction it went. The syringe has no eyes
whatever and is simply keeping count and hoping. The gyroscope is honest and slowly,
steadily wrong.

None of them is the truth. The maths in this document is not there to compute the truth
from them either — it is there to **cross-examine**: who saw what, how long ago, how
sure were they, does any of it contradict anything else, and is anybody still answering
at all. Most of the code that looks defensive is really that cross-examination written
out.

And the verdict it returns is quite often *not proven*. A blank where a number should
be. A held track that visibly stops following the sub. A hull state that says "unknown"
instead of "fine".

A system that can say **I do not know** out loud, in the middle of a dive, without
flinching and without filling the gap with something soothing — that is the entire
point. Everything above is machinery for making that possible.
