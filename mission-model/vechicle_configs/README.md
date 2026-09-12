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
