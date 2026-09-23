# Vehicle config sourcing notes

Values updated from initial placeholders using public reference data (search
performed 2026-09-09). No public source gives an exact frame-only (motors/
props/battery excluded) mass breakdown for a bespoke 100+ kg-payload VTOL, so
the numbers below are engineering estimates anchored to real comparable
aircraft, not a single citable spec.

- **drag_cd, multirotor (1.0):** wind-tunnel/flight-test studies of
  quadcopter frontal drag put Cd at 0.8-1.2, with up to ~40% of total drag
  from exposed rotors/struts (Hattenberger et al., "Evaluation of drag
  coefficient for a quadrotor model", 2023; GlobalSpec "Aerodynamics of
  multirotor drones"). Previous value (0.42) was below this range.
- **drag_cd, tailsitter (0.35):** no source gives Cd for this model's
  frontal-area convention on a winged airframe; 0.35 is an estimate between
  a faired fixed-wing fuselage (~0.2-0.4 frontal-area Cd for fixed-gear
  light aircraft) and the multirotor value above, since a tailsitter still
  has exposed rotors/motor mounts. Previous value (0.04) is an airfoil
  profile-drag number, not a frontal-area whole-airframe number, so it was
  replaced.
- **wing_area_m2 / wing_cl:** unchanged. Checked against wing loading
  (preferred <=20 psf / ~98 kg/m^2 per US20050178879A1) and already
  comfortably inside that range. `cruise_velocity_mps` is gone: cruise speed
  is no longer prescribed per airframe -- each leg is flown at the steady
  speed its throttle settles at, and the throttle is picked for minimum
  energy. (Surveyed tailsitter cruise speeds, 20-35 m/s, are now a sanity
  check on the model's output rather than an input.)
- **base_mass_g / arm_mass_g:** the previous JSON used a flat 15000 g base
  and near-flat arm mass for every vehicle regardless of rotor count or wing
  size. Real heavy-lift cargo drones in this payload class (DJI FlyCart 30:
  42.5 kg empty w/o battery, 30-40 kg payload; an 8-rotor 100 kg-payload
  octocopter: 52 kg empty) put airframe-only mass at roughly 15-25 kg for
  this class -- consistent with the old flat value as a *rough* midpoint,
  but not with using the same number for a 2-rotor bicopter and an 8-rotor
  octocopter. Rescaled base_mass_g with rotor count (multirotor) or wing
  area (tailsitter, ~1.5 kg of structure per m^2 of wing -- a typical
  composite UAV wing areal density), and arm_mass_g with rotor count /
  mounting layout (a coaxial "Contra" pair shares one central mast; a
  "Bicopter" mounts one motor per wingtip, so it carries a bit more arm
  structure for the same rotor count).

Motor/prop/battery masses (Motors/*.json, Batteries/*.json) are taken from
real manufacturer spec sheets already and weren't touched here.

## 2026-09-12: backfilled wing_geometry.json (assumed, not derived)

All 18 winged vehicles above only ever specified wing_area_m2/wing_cl --
nothing recorded what aspect ratio, airfoil, or span/chord actually produced
that area (wing_optimizer.py's --geometry-out didn't exist yet when these
were written). To get real, trackable geometry and an airfoil visualization
in the Setup Explorer artifact, wing_geometry.json was backfilled by running
wing_optimizer.py once per vehicle with:

- **cruise_mps = 25**: not previously recorded anywhere (per the note above,
  cruise speed stopped being a per-vehicle input and became a model output).
  25 m/s is the midpoint of this file's own surveyed tailsitter range
  (20-35 m/s) -- a labeled assumption, not a lookup.
- **lift_mass_kg**: back-solved so `required_wing_area_m2(lift_mass_kg, 25,
  wing_cl)` reproduces each vehicle's *existing* wing_area_m2 exactly
  (60.97 / 92.51 / 129.15 kg for the Compact/Balanced/Big wing_cl values)
  -- this isn't a new assumption, it's the one lift_mass_kg value consistent
  with the area/cl this file already committed to.
- **AR = 4, untapered, unswept, untwisted**: wing_optimizer's own default
  behavior with no span cap (see wing_optimizer.py's docstring: absent a
  span cap, the AR search always lands on the lowest allowed value) -- not
  a new choice on top of the tool's defaults.
- **NACA 2412**: a generic, widely-used general-aviation/UAV section with no
  particular basis in this project -- picked only so the artifact has a real
  airfoil shape to draw, not asserted as the intended section.

Every one of these is a labeled assumption for visualization purposes, not
a claim about original design intent. Re-run wing_optimizer.py yourself with
real inputs (and --geometry-out vechicle_configs/wing_geometry.json) to
replace any of these 18 records with the actual answer.

## 2026-09-12: per-size max_speed_mps (cruise-speed validation follow-up)

Every TailSitter variant previously shared Mission_Model.py's flat
`DEFAULT_MAX_SPEED_MPS` (18.0), despite spanning a real wing-area range
(2.84-5.09 m^2, wing_loading_kg_m2 21.5-25.4 per wing_geometry.json). A
validation pass against two real production tailsitters -- Wingtra
WingtraOne (16 m/s cruise) and Quantum Systems Trinity F90+ (17 m/s cruise)
-- found 18 m/s a good match for *that* weight class, but those are ~5kg
mapping drones, 8-40x lighter than this lineup's 39-193kg gross TailSitter
configs; no commercial tailsitter exists at this project's weight/payload
class to check against directly.

Rather than invent per-size numbers with no source, each variant's cap is
now derived from the same physics the 18 m/s reference itself rests on --
cruise speed scales as sqrt(wing_loading) at a fixed lift coefficient (from
L = 0.5*rho*Cl*S*V^2) -- anchored to the Compact tier's wing loading
(21.469 kg/m^2, closest to the two validated real reference aircraft):

    max_speed_mps = 18.0 * sqrt(wing_loading_kg_m2 / 21.469)

  - **Compact** (21.469 kg/m^2): 18.0 m/s (unchanged -- this is the
    reference point)
  - **Balanced** (23.421 kg/m^2): 18.8 m/s
  - **Big** (25.373 kg/m^2): 19.6 m/s

The spread is modest (~9%) because this lineup's own wing-loading range is
modest -- this isn't claiming these are the *real* rated speeds for a
100+kg-payload tailsitter (no such aircraft exists to check against), only
that varying the cap by the same relationship that validated the baseline
is more honest than one flat constant across a real size range.

## 2026-09-14: re-sized all 3 wing tiers with wing_optimizer.py's new --objective combined

wing_optimizer.py gained a third objective (`combined`): instead of fixing
cl and searching aspect ratio for minimum structural mass (`mass`, today's
prior default -- always bottoms out at `--min-ar` without a span cap) or
searching cl+AR for minimum drag alone (`drag`), it searches cl+AR jointly
for minimum *total mission energy* (hover induced-power + cruise drag
energy), so the optimum lands on a real interior tradeoff instead of a
search-range boundary. Full method in wing_optimizer.py's docstring and
`optimize_wing_combined()`.

Re-ran it for all 3 tiers (Compact/Balanced/Big -- Contra/Quad/Bicopter
share identical wing geometry within a tier, unchanged from before) with:

- `--cruise-mps` = each tier's existing `max_speed_mps` (18.0/18.8/19.6),
  not the old 25 m/s placeholder -- sizes the wing for the speed the
  vehicle is actually governed to fly at in Mission_Model.py's sim, not a
  faster speed it never reaches. This is the single biggest driver of the
  area increase below (required area scales as 1/v^2).
- `--hover-s 0`: Mission_Model.py's mission has no sustained hover/loiter
  phase (just 3 quick vertical takeoff/landing transitions at fixed
  climb/descent speeds -- see MISSION_LEGS), so there's no real number to
  put here; 0 means this re-sizing is really pure drag-minimization for
  these vehicles specifically, with `--objective combined`'s machinery
  wired in for a future mission profile that does have a real hover budget.
  `--disk-loading-kg-m2` was left at its default (irrelevant with
  hover_s=0).
- `--cl-min 0.3 --cl-max 1.0 --min-ar 4 --max-ar 12`: `min_ar`/`drag_cd`/
  `max_speed_mps`/`arm_mass_g` unchanged from the existing lineup;
  `max_ar` kept <=12, comfortably below where oswald_efficiency() goes
  unphysical (~AR 18.5, see its docstring) -- not re-derived from anything
  vehicle-specific.

Because the optimal cl/AR for this energy model turn out independent of
lift_mass_kg and cruise speed (drag_n = lift_n*(parasite_cd0+cdi)/cl, which
has no v dependence once area is substituted out), all 3 tiers landed on
the identical cl=0.867, AR=8.64 -- only the resulting area scales with
each tier's own lift_mass_kg and cruise speed:

| tier     | wing_area_m2  | wing_cl     | wing_loading_kg_m2 | base_mass_g      |
|----------|---------------|-------------|---------------------|------------------|
| Compact  | 2.84 -> 3.4753  | 0.55 -> 0.867 | 21.47 -> 17.54    | 14752.3 -> 15326.9 |
| Balanced | 3.95 -> 4.8339  | 0.6 -> 0.867  | 23.42 -> 19.14    | 15764.6 -> 16541.4 |
| Big      | 5.09 -> 6.2084  | 0.65 -> 0.867 | 25.37 -> 20.80    | 16763.8 -> 17770.1 |

`max_speed_mps` was deliberately **not** re-derived from the new wing
loading via the sqrt-scaling formula two sections up: that formula's anchor
(21.469 kg/m^2 -> 18.0 m/s, from real Wingtra/Trinity F90+ cruise speeds)
is a real-aircraft calibration point, but plugging the new cl back into it
turns the relationship into an unstable iteration (each pass shrinks
toward v=0 instead of converging) once cl no longer matches the value the
anchor was implicitly taken at. `max_speed_mps` stays pinned to the
already-validated 18.0/18.8/19.6 m/s.

`base_mass_g` was rescaled using a linear fit (893.9 g per m^2 of wing
area + 12220.3 g fixed) through the *existing* 3 tiers' own
base_mass_g-vs-wing_area_m2 relationship (the ~1.5 kg/m^2 figure quoted
above for that relationship was a rounded description, not the literal
fitted slope) -- reusing this project's already-committed mass-scaling
relationship at the new area, not new research into frame mass. This is
the same kind of ~linear approximation the original relationship already
was; it hasn't been re-validated against any new comparable aircraft data.
`arm_mass_g` (rotor-layout-specific, not wing-area-dependent) is unchanged.

`mission_model_results.csv` and the tradespace PNGs/HTML were generated
against the *old* wing sizing and are now stale for the TailSitter*
configs -- re-run Mission_Model.py's sweep to refresh them.

## 2026-09-12: parasite_cd0 (wing/fuselage drag, separate from drag_cd)

The same validation pass found the cruise-speed model's *unconstrained*
equilibrium velocity (before the max_speed_mps governor) came out at
150-200 m/s for a real feasible TailSitter+motor pairing -- 10x too fast
for any real aircraft this size. Root cause: Mission_Model.py's drag area
was only ever the prop/frame frontal area (frontal_area_m2(), inflated by
thrust tilt) -- a winged vehicle near level cruise, with wings carrying
most of the weight, got essentially zero drag contribution from the wing
itself. Added `parasite_cd0` (default 0.045, VehicleType's field
docstring has the mid-range light-UAV justification) as a wing-area-
referenced zero-lift drag coefficient, combined with drag_cd's frontal-area
term in Mission_Model.py's parasite_drag_area_m2(). No vehicle_configs
entry overrides the default -- 0.045 is a reasonable placeholder pending a
real source, not a per-airframe measurement. This roughly halves the
unconstrained equilibrium velocity for this lineup's configs (150-200 m/s
-> 70-90 m/s) -- still above max_speed_mps, which is expected: these
motors are sized for hover, not cruise, so real autopilots throttle back
for cruise too. The governor was never wrong to bind; the bug was that the
number it was masking was 10x too high rather than ~4-5x.
