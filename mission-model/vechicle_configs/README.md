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
