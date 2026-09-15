# Quaternion 6DOF Transition Dynamics for Mission_Model.py

Status: proposed
Date: 2026-09-15

## Problem

`mission-model/Mission_Model.py` is a 1D point-mass energy/range budgeting
model: every flight phase (takeoff, cruise accel/cruise/decel, landing) is
scalar kinematics (`x`, `v` only). Winged (tailsitter) vehicles already get a
numeric fallback for the accel phase (`_accel_phase_numeric`, in
`segment()`) because wing lift makes `thrust_horizontal` velocity-dependent —
but the model still has no real attitude state. The forward tilt needed to
support weight is represented as an "effective frontal area" fudge
(`_effective_area_at()`), not an actual body orientation, and pitch-axis
inertia/control-authority don't exist anywhere in the model.

This matters specifically for the hover↔cruise transition leg of a
tailsitter: that's the one regime where attitude genuinely varies over the
phase (nose-up in hover, nose-forward in cruise), and where three papers the
user is drawing on (quaternion attitude representation to avoid Euler-angle
gimbal lock; a tailsitter-specific quaternion full-attitude control law,
arXiv:1903.06393; and an energy-optimal hover-cruise transition formulation,
arXiv:2411.08661) point at the same fix: replace the tilt-area geometric
trick with a real quaternion-based rigid-body integration through the
transition.

## Goals

- Represent tailsitter transition attitude with a quaternion (not Euler
  angles), integrating the standard kinematics `q̇ = ½ q ⊗ ω`, avoiding
  gimbal lock at 90° pitch — the specific property the source papers call
  out.
- Full unconstrained 3D 6DOF state (position, velocity, quaternion, angular
  velocity — 13 states), not a planar/pitch-only simplification, per
  explicit direction: build the general case now even though today's model
  and vehicle data are 1D/symmetric, so off-axis disturbance/crosswind work
  later doesn't need a rewrite.
- Runs *inside* the existing fleet sweep (`mission_best()` → `segment()`),
  for every throttle grid point of every winged vehicle/motor/battery combo
  — not as a separate post-hoc detailed-analysis pass.
- Scoped to the transition legs only (the accel/decel phases inside
  `segment()`, winged vehicles only). Steady hover (`hover_time_min()`) and
  steady cruise/landing keep their existing scalar treatment — attitude is
  ~constant there, so 6DOF adds cost with no accuracy gain.
- Wingless multirotor path (`MULTIROTOR`, the only vehicle in
  `VEHICLE_TYPES` today) is completely unaffected — no change to its
  closed-form results.
- Physical data (inertia tensor, control-torque authority) that doesn't
  exist until CAD is done gets a geometry-based estimate now, with an
  explicit per-vehicle override field so real CAD-derived numbers can drop
  in later without any code change.

## Non-goals

- Not building a flight controller. The attitude "control law" here exists
  only to produce a physically bounded pitch-over trajectory for the energy
  calculation — it is not meant to be flown on real hardware.
- Not adding wind/gust disturbance modeling, multi-vehicle formation, or any
  planning/trajectory-optimization layer. Those are exactly what several of
  the surveyed papers (arXiv:2411.08661, arXiv:2412.06197) cover but are out
  of scope for this change — future work if the energy-optimal-trajectory
  angle is worth pursuing.
- Not modeling roll/yaw actuation mechanisms (differential thrust
  allocation across specific rotors, control surfaces). Roll/yaw torque
  authority is a coarse scalar estimate (see below), not a real allocation
  matrix.

## Architecture

### New module: `mission-model/dynamics_6dof.py`

Self-contained quaternion/rigid-body layer with no dependency on
`Mission_Model.py` (the reverse dependency is fine — `Mission_Model.py`
already imports `scipy.integrate.solve_ivp` the same way).

**Quaternion utilities** (Hamilton convention, scalar-first `[w, x, y, z]` —
matches `scipy.spatial.transform.Rotation`'s internal math and the Solà
2017 reference, arXiv:1711.02508, the paper the subagent's survey flagged as
the best source for picking a convention):
- `quat_mult(q1, q2)`
- `quat_normalize(q)`
- `quat_rotate_vector(q, v)` — rotate a body-frame vector into world frame
- `quat_derivative(q, omega_body)` — returns `q̇ = ½ q ⊗ [0, ω]`
- `quat_from_hover_to_cruise(...)` — the two reference attitudes (thrust
  axis vertical vs. horizontal) transition interpolates between

**`RigidBodyState`**: a 13-vector `[r(3), v(3), q(4), omega(3)]`, packed/
unpacked for `solve_ivp`.

**Inertia estimate** — `estimate_inertia_kg_m2(vehicle, cfg, mass_kg) ->
(Ixx, Iyy, Izz)`: a coarse geometric approximation, diagonal-only (no
products of inertia — a real asymmetric mass distribution needs CAD, which
is exactly what the override field is for):
- Fuselage/frame treated as a slender rod of length `vehicle.body_length_m`
  (falls back to an estimate from prop diameter × num_rotors layout when
  `0.0`) contributing to pitch/yaw inertia.
- Wing treated as a flat plate of span `vehicle.wing_span_m` (falls back to
  `sqrt(wing_area_m2 * assumed_aspect_ratio)`, AR=6 default) contributing to
  roll/yaw inertia.
- Standard thin-rod/flat-plate `I = (1/12) m L²` forms, mass-split
  proportionally between fuselage and wing by their share of `total_mass_g`.
- Marked with a `ponytail:` comment: coarse estimate, ceiling is "wrong by
  some constant factor for any real airframe," upgrade path is
  `VehicleType.inertia_kg_m2` override once CAD gives real numbers.

**Control-torque estimate** — `estimate_max_torque_n_m(vehicle, cfg,
throttle_pct) -> (roll, pitch, yaw)`: derived from the rotor set's thrust
range (`cfg.thrust_g(THROTTLE_MAX) - cfg.thrust_g(THROTTLE_MIN)`) times a
characteristic moment arm (half of `body_length_m`/`wing_span_m` per axis).
Same `ponytail:` treatment — placeholder pending
`VehicleType.max_control_torque_n_m` override.

**Attitude control law** — a simple PD in quaternion error space:
`torque = clip(I ⊙ (Kp * quat_log_error(q, q_target) - Kd * omega),
-max_torque, +max_torque)`, with `quat_log_error` the standard
quaternion-error-to-rotation-vector map (also from Solà 2017 — this avoids
the shortest-path sign ambiguity that a naive `q1 - q2` has). Not a tuned
flight controller; just enough to produce a bounded, monotonic pitch-over
that the energy integral can be taken over.

**Forces**: thrust along body `+z`, magnitude from
`rotor_set_thrust_n(vehicle, cfg, throttle_pct)` (unchanged, reused from
`Mission_Model.py`), rotated to world frame via `quat_rotate_vector`; wing
lift/parasite drag reuse `wing_lift_n()`/`parasite_drag_area_m2()` but now
driven by world-frame airspeed and its angle relative to the body axis
(true angle of attack) instead of the old effective-tilt-area trick;
gravity `[0, 0, -m g]` in world frame.

**`transition_accel(vehicle, cfg, battery, throttle_pct, v0, v_target,
distance_cap, extra_payload_g) -> (time_s, distance_m, v_final) | None`** and
a mirrored `transition_decel(...)`: build initial `RigidBodyState`, integrate
via `solve_ivp` with the same terminal-event pattern
`_accel_phase_numeric` already uses (`hit_target_v`, `hit_distance`),
renormalizing `q` each RHS evaluation to counter integration drift, and
return the same 3-tuple contract those existing functions return (or `None`
on infeasibility — torque authority insufficient to complete the pitch-over
before the event conditions trigger). This return-contract match is what
keeps the `Mission_Model.py` change small (see below).

### `Mission_Model.py` changes

- `VehicleType` gains five optional fields, all defaulted to preserve
  today's behavior exactly for `MULTIROTOR` (which has `wing_area_m2=0`, so
  none of this new code path executes for it at all):
  - `wing_span_m: float = 0.0`
  - `body_length_m: float = 0.0`
  - `inertia_kg_m2: tuple[float, float, float] | None = None`
  - `max_control_torque_n_m: tuple[float, float, float] | None = None`
  - `attitude_kp: float`, `attitude_kd: float` with defaults — exposed so a
    per-vehicle transition trajectory can be re-tuned without editing code
- `load_vehicle_types()` / `VEHICLE_TYPE_FIELDS` pick the new fields up
  automatically (already generic over `dataclasses.fields(VehicleType)`).
- `segment()`: for `has_wing` vehicles, the accel-phase call to
  `_accel_phase_numeric(...)` is replaced with
  `dynamics_6dof.transition_accel(...)`, and the decel-phase call
  (currently `_decel_phase_powered`, closed-form even for winged vehicles
  today) is replaced with `dynamics_6dof.transition_decel(...)` — both
  behind the existing `has_wing` branch, so the wingless path is byte-for-
  byte unchanged.
- `_accel_phase_numeric` and the winged branch of `_decel_phase_powered`
  become dead code once this lands and can be deleted (kept during
  implementation for the regression comparison in Testing, below).

## Data flow

```
mission_best() / mission()
  -> segment(vehicle, cfg, battery, throttle, distance, payload)
       has_wing?
         no  -> _accel_phase_closed_form / _decel_phase_powered   (unchanged)
         yes -> dynamics_6dof.transition_accel(...)
                dynamics_6dof.transition_decel(...)
                  -> estimate_inertia_kg_m2() / vehicle.inertia_kg_m2 override
                  -> estimate_max_torque_n_m() / vehicle.max_control_torque_n_m override
                  -> solve_ivp over 13-state RigidBodyState
                  -> (time_s, distance_m, v_final) | None
```

Energy accounting in `segment()` doesn't change: it already just multiplies
`amps_ma` by phase time, and that formula is untouched.

## Error handling

- Torque-insufficient pitch-over (events never fire within `solve_ivp`'s
  time/step budget): `transition_accel`/`transition_decel` return `None`,
  exactly like `_accel_phase_numeric` does today — `segment()` already
  treats a `None` accel result as `INFEASIBLE_TIME_S`/`INFEASIBLE_ENERGY_MAH`,
  no new handling needed there.
- Quaternion norm drift from numerical integration: renormalized after
  every RHS evaluation (standard practice — `solve_ivp`'s adaptive step
  doesn't otherwise guarantee `|q|=1`).
- Degenerate geometry (e.g. `wing_area_m2 > 0` but `body_length_m` and
  `wing_span_m` both left at `0.0`, so even the fallback estimates have
  nothing to work from): raise `ValueError` at `transition_accel`/
  `transition_decel` entry rather than silently producing a zero/garbage
  inertia — surfaces the missing-data problem instead of masking it as
  "infeasible."

## Testing

- New `mission-model/test_dynamics_6dof.py`:
  - Quaternion kinematics: `ω=0 → q̇=0`; rotating `[0,0,1]` by the identity
    quaternion is unchanged; a known 90° rotation matches
    `scipy.spatial.transform.Rotation` on the same axis/angle (cross-check
    against an independent, trusted implementation).
  - Gimbal-lock check: integrate through a 90°+ pitch maneuver and confirm
    no singularity/NaN — this is the concrete behavior the source papers
    motivate replacing Euler angles for.
  - `transition_accel`/`transition_decel` on a synthetic test vehicle:
    result is a similar order of magnitude (not exact — the physics
    genuinely changed) to today's `_accel_phase_numeric`/
    `_decel_phase_powered` output on the same inputs, as a regression
    sanity bound.
  - Infeasible case: a vehicle with near-zero `max_control_torque_n_m`
    returns `None`.
- `dynamics_6dof.py` gets a `demo()`/`__main__` self-check (ponytail
  convention already used elsewhere in this session) integrating one
  representative transition and printing final state — smallest possible
  smoke test for the non-trivial ODE/quaternion logic.
- Existing `test_mission_model.py` / `test_wing_optimizer.py` are expected
  to pass unchanged (wingless `MULTIROTOR` path untouched); re-run as part
  of verification.

## Open questions for the implementation plan

- Exact `Kp`/`Kd` defaults and the `solve_ivp` tolerance/`max_step` budget
  (today's `_accel_phase_numeric` uses `rtol=1e-7, atol=1e-9, max_step=0.5`
  — 13-state quaternion integration may need different tolerances to stay
  fast across a full fleet sweep; this is a tuning detail for the plan/
  implementation phase, not a design fork).
- Whether the two new tuning fields (`attitude_kp`/`attitude_kd`) belong on
  `VehicleType` (per-airframe) or as module-level constants next to
  `CRUISE_V_FRACTION` (global) — leaning per-vehicle since transition
  authority is airframe-specific, but worth confirming against how the CAD
  data eventually gets structured.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
