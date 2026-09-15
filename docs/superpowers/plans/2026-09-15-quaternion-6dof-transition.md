# Quaternion 6DOF Transition Dynamics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the tilt-area geometric trick used for tailsitter (winged) hover↔cruise transition legs in `Mission_Model.py` with a real quaternion-based 6DOF rigid-body integration, so attitude is an actual simulated state (no Euler-angle gimbal lock) instead of an algebraic fudge.

**Architecture:** A new standalone module `dynamics_6dof.py` (no import of `Mission_Model.py`, avoiding a circular import — it duplicates `G`/`RHO_AIR` the same way `wing_optimizer.py` already does, with a drift-guard test) provides quaternion math, coarse geometric inertia/control-torque estimates (overridable per vehicle once CAD data exists), and `transition_accel`/`transition_decel` functions that integrate a 13-state rigid body (position, velocity, quaternion, angular velocity) via `scipy.solve_ivp`. `Mission_Model.py`'s `segment()` calls these for winged vehicles only; the wingless `MULTIROTOR` path is untouched.

**Tech Stack:** Python, NumPy, SciPy (`solve_ivp`, already a dependency).

**Spec:** `docs/superpowers/specs/2026-09-15-quaternion-6dof-transition-design.md`

## Global Constraints

- Quaternion convention: Hamilton, scalar-first `[w, x, y, z]` (per arXiv:1711.02508).
- `dynamics_6dof.py` must not import `Mission_Model.py` — it duplicates `G = 9.807` and `RHO_AIR = 1.225`, exactly like `wing_optimizer.py` already does, and every test file that imports both asserts the constants haven't drifted apart (see `test_wing_optimizer.py:14-19` for the existing pattern to copy).
- The wingless `MULTIROTOR` code path (`_accel_phase_closed_form`, `_decel_phase_powered`, and everything `segment()` does when `has_wing` is `False`) must remain behaviorally unchanged — every existing test in `test_mission_model.py` must keep passing without modification.
- New `VehicleType` fields all default to values that mean "estimate it" (`0.0` or `None`) so every existing vehicle JSON file and the built-in `MULTIROTOR` keep working unchanged.
- Self-check test files follow this project's existing convention (see `test_mission_model.py` / `test_wing_optimizer.py`): plain `assert` statements in `test_*` functions, `if __name__ == "__main__":` collects and runs them — **not** pytest, **not** a test framework.
- `transition_accel`/`transition_decel` return the same `(time_s, distance_m, v_final) | None` contract the functions they replace (`_accel_phase_numeric`, `_decel_phase_powered`) already used, so `segment()`'s energy accounting doesn't change.

---

### Task 1: Quaternion math core

**Files:**
- Create: `mission-model/dynamics_6dof.py`
- Test: `mission-model/test_dynamics_6dof.py`

**Interfaces:**
- Produces: `quat_mult(q1, q2) -> np.ndarray`, `quat_normalize(q) -> np.ndarray`, `quat_conjugate(q) -> np.ndarray`, `quat_rotate_vector(q, v) -> np.ndarray`, `quat_derivative(q, omega_body) -> np.ndarray`, `quat_from_axis_angle(axis, angle_rad) -> np.ndarray`, `quat_log(q) -> np.ndarray`, `quat_log_error(q, q_target) -> np.ndarray`, module constants `HOVER_QUAT`, `CRUISE_QUAT`, `G`, `RHO_AIR`.

- [ ] **Step 1: Write the failing tests**

Create `mission-model/test_dynamics_6dof.py`:

```python
"""Self-check for dynamics_6dof.py. Run: python test_dynamics_6dof.py"""

import math

import numpy as np

from dynamics_6dof import (
    quat_mult, quat_normalize, quat_conjugate, quat_rotate_vector,
    quat_derivative, quat_from_axis_angle, quat_log, quat_log_error,
    HOVER_QUAT, CRUISE_QUAT, G as DYN_G, RHO_AIR as DYN_RHO_AIR,
)
from Mission_Model import G, RHO_AIR

# dynamics_6dof.py duplicates G/RHO_AIR instead of importing Mission_Model.py
# (avoids a circular import, same pattern wing_optimizer.py already uses) --
# guard against the copies drifting apart.
assert DYN_G == G and DYN_RHO_AIR == RHO_AIR, \
    "dynamics_6dof.py's duplicated physics constants have drifted from Mission_Model.py's"


def test_identity_quaternion_rotates_vector_unchanged():
    v = np.array([1.0, 2.0, 3.0])
    assert np.allclose(quat_rotate_vector(HOVER_QUAT, v), v)


def test_zero_angular_velocity_gives_zero_quaternion_derivative():
    q = quat_normalize(np.array([0.7, 0.1, 0.2, 0.3]))
    qdot = quat_derivative(q, np.zeros(3))
    assert np.allclose(qdot, np.zeros(4))


def test_quat_from_axis_angle_matches_known_90_degree_rotation():
    # +90 deg about world z: [1,0,0] -> [0,1,0]
    q = quat_from_axis_angle(np.array([0.0, 0.0, 1.0]), math.pi / 2)
    v = quat_rotate_vector(q, np.array([1.0, 0.0, 0.0]))
    assert np.allclose(v, [0.0, 1.0, 0.0], atol=1e-9)


def test_cruise_quat_points_body_thrust_axis_forward():
    # CRUISE_QUAT rotates the body thrust axis (+z) to world +x -- the
    # gimbal-lock-prone 90 degree pitch case the source papers motivate
    # using quaternions (not Euler angles) for.
    v = quat_rotate_vector(CRUISE_QUAT, np.array([0.0, 0.0, 1.0]))
    assert np.allclose(v, [1.0, 0.0, 0.0], atol=1e-9)
    assert not np.isnan(v).any()


def test_quat_log_error_zero_when_aligned():
    err = quat_log_error(HOVER_QUAT, HOVER_QUAT)
    assert np.allclose(err, np.zeros(3), atol=1e-9)


def test_quat_log_error_matches_hand_derived_hover_to_cruise_vector():
    err = quat_log_error(HOVER_QUAT, CRUISE_QUAT)
    assert np.allclose(err, [0.0, -math.pi / 2, 0.0], atol=1e-9)


def test_quat_mult_is_associative():
    a = quat_normalize(np.array([0.7, 0.1, 0.2, 0.3]))
    b = quat_normalize(np.array([0.2, 0.6, 0.1, 0.4]))
    c = quat_normalize(np.array([0.4, 0.2, 0.5, 0.1]))
    left = quat_mult(quat_mult(a, b), c)
    right = quat_mult(a, quat_mult(b, c))
    assert np.allclose(left, right, atol=1e-9)


def test_quat_conjugate_of_unit_quaternion_is_its_inverse():
    q = quat_normalize(np.array([0.7, 0.1, 0.2, 0.3]))
    identity = quat_mult(q, quat_conjugate(q))
    assert np.allclose(identity, [1.0, 0.0, 0.0, 0.0], atol=1e-9)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `ModuleNotFoundError: No module named 'dynamics_6dof'`

- [ ] **Step 3: Write the implementation**

Create `mission-model/dynamics_6dof.py`:

```python
"""Quaternion math and rigid-body transition dynamics for tailsitter
hover<->cruise legs. Standalone module (no Mission_Model import, to avoid
a circular import since Mission_Model.py calls into this one) -- G/RHO_AIR
below are intentionally duplicated from Mission_Model.py, same convention
wing_optimizer.py already uses; test_dynamics_6dof.py asserts they stay in
sync.

Quaternion convention: Hamilton, scalar-first [w, x, y, z] -- matches the
convention in Sola, "Quaternion kinematics for the error-state Kalman
filter" (arXiv:1711.02508), which is what this module's kinematics/log-map
formulas are taken from.

Body frame: x forward, y right, z thrust axis -- thrust is always along
body +z. HOVER_QUAT (identity) has body +z aligned with world +z (thrust
vertical, supporting weight). CRUISE_QUAT rotates body +z to world +x
(thrust horizontal, providing forward push). The transition legs in
Mission_Model.segment() integrate attitude between these two references.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.integrate import solve_ivp

G = 9.807  # m/s^2 -- kept in sync with Mission_Model.G, see test_dynamics_6dof.py's drift-guard
RHO_AIR = 1.225  # kg/m^3 -- kept in sync with Mission_Model.RHO_AIR


def quat_mult(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_normalize(q: np.ndarray) -> np.ndarray:
    return q / np.linalg.norm(q)


def quat_conjugate(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def quat_rotate_vector(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate body-frame vector v into world frame via the q * v * q^-1 sandwich."""
    vq = np.array([0.0, v[0], v[1], v[2]])
    return quat_mult(quat_mult(q, vq), quat_conjugate(q))[1:]


def quat_derivative(q: np.ndarray, omega_body: np.ndarray) -> np.ndarray:
    """q_dot = 0.5 * q (x) [0, omega] -- the standard quaternion kinematics ODE."""
    omega_quat = np.array([0.0, omega_body[0], omega_body[1], omega_body[2]])
    return 0.5 * quat_mult(q, omega_quat)


def quat_from_axis_angle(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    half = angle_rad / 2.0
    return np.array([math.cos(half), *(axis * math.sin(half))])


def quat_log(q: np.ndarray) -> np.ndarray:
    """Rotation-vector (axis * angle) log map of a unit quaternion, shortest path."""
    w = q[0]
    v = q[1:]
    v_norm = np.linalg.norm(v)
    if v_norm < 1e-12:
        return np.zeros(3)
    angle = 2.0 * math.atan2(v_norm, w)
    if angle > math.pi:
        angle -= 2.0 * math.pi
    return angle * v / v_norm


def quat_log_error(q: np.ndarray, q_target: np.ndarray) -> np.ndarray:
    """Body-frame rotation vector needed to bring q to q_target."""
    return quat_log(quat_mult(quat_conjugate(q), q_target))


HOVER_QUAT = np.array([1.0, 0.0, 0.0, 0.0])
CRUISE_QUAT = quat_from_axis_angle(np.array([0.0, 1.0, 0.0]), -math.pi / 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
cd mission-model
git add dynamics_6dof.py test_dynamics_6dof.py
git commit -m "$(cat <<'EOF'
Add quaternion math core (dynamics_6dof.py)

Hamilton scalar-first quaternion kinematics/log-map, standalone module
per docs/superpowers/specs/2026-09-15-quaternion-6dof-transition-design.md.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Coarse inertia and control-torque estimates

**Files:**
- Modify: `mission-model/dynamics_6dof.py`
- Test: `mission-model/test_dynamics_6dof.py`

**Interfaces:**
- Consumes: nothing new from Task 1 beyond the module itself existing.
- Produces: `estimate_inertia_kg_m2(mass_kg, body_length_m, wing_span_m) -> tuple[float, float, float]` (Ixx, Iyy, Izz), `estimate_max_torque_n_m(thrust_min_n_per_motor, thrust_max_n_per_motor, body_length_m, wing_span_m) -> tuple[float, float, float]` (roll, pitch, yaw N·m). Both raise `ValueError` if `body_length_m <= 0` and `wing_span_m <= 0`.

- [ ] **Step 1: Write the failing tests**

Append to `mission-model/test_dynamics_6dof.py` (add the import names to the existing `from dynamics_6dof import (...)` block at the top):

```python
# add to the existing dynamics_6dof import list:
#   estimate_inertia_kg_m2, estimate_max_torque_n_m,


def test_estimate_inertia_matches_hand_computed_rod_formula():
    ixx, iyy, izz = estimate_inertia_kg_m2(mass_kg=2.0, body_length_m=1.0, wing_span_m=2.0)
    expected_i_fuselage = (1.0 * 1.0 ** 2) / 12.0  # half the mass, rod along body length
    expected_i_wing = (1.0 * 2.0 ** 2) / 12.0  # half the mass, rod along wingspan
    assert math.isclose(iyy, expected_i_fuselage)  # pitch: fuselage-length mass distribution
    assert math.isclose(ixx, expected_i_wing)  # roll: wingspan mass distribution
    assert math.isclose(izz, expected_i_fuselage + expected_i_wing)  # yaw: both


def test_estimate_inertia_rejects_zero_geometry():
    try:
        estimate_inertia_kg_m2(mass_kg=2.0, body_length_m=0.0, wing_span_m=0.0)
        raise AssertionError("should have rejected all-zero geometry")
    except ValueError:
        pass


def test_estimate_max_torque_scales_with_thrust_range_and_arm():
    roll, pitch, yaw = estimate_max_torque_n_m(thrust_min_n_per_motor=10.0, thrust_max_n_per_motor=30.0,
                                                body_length_m=1.0, wing_span_m=2.0)
    assert math.isclose(pitch, 20.0 * 0.5)  # thrust range 20N * half body length
    assert math.isclose(roll, 20.0 * 1.0)  # thrust range 20N * half wingspan
    assert math.isclose(yaw, 0.2 * pitch)


def test_estimate_max_torque_rejects_zero_geometry():
    try:
        estimate_max_torque_n_m(thrust_min_n_per_motor=10.0, thrust_max_n_per_motor=30.0,
                                 body_length_m=0.0, wing_span_m=0.0)
        raise AssertionError("should have rejected all-zero geometry")
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `ImportError: cannot import name 'estimate_inertia_kg_m2'`

- [ ] **Step 3: Write the implementation**

Append to `mission-model/dynamics_6dof.py` (after `CRUISE_QUAT`):

```python
def estimate_inertia_kg_m2(mass_kg: float, body_length_m: float, wing_span_m: float) -> tuple[float, float, float]:
    """ponytail: coarse geometric placeholder -- mass split 50/50 between a
    fuselage rod along body x (length body_length_m) and a wing rod along
    body y (length wing_span_m), each I=(1/12)*m*L^2. Wrong by some constant
    factor for any real airframe. Upgrade path: set
    VehicleType.inertia_kg_m2 once CAD gives real principal moments."""
    if body_length_m <= 0.0 and wing_span_m <= 0.0:
        raise ValueError("estimate_inertia_kg_m2: need a positive body_length_m or wing_span_m")
    mass_fuselage_kg = mass_kg * 0.5
    mass_wing_kg = mass_kg * 0.5
    i_fuselage = (mass_fuselage_kg * body_length_m ** 2) / 12.0
    i_wing = (mass_wing_kg * wing_span_m ** 2) / 12.0
    ixx = i_wing  # roll: resisted by wing-span mass distribution
    iyy = i_fuselage  # pitch: resisted by fuselage-length mass distribution -- the transition axis
    izz = i_fuselage + i_wing  # yaw: resisted by both
    return ixx, iyy, izz


def estimate_max_torque_n_m(thrust_min_n_per_motor: float, thrust_max_n_per_motor: float,
                             body_length_m: float, wing_span_m: float) -> tuple[float, float, float]:
    """ponytail: coarse placeholder -- (max-min single-motor thrust) times a
    half-length moment arm per axis, no real per-rotor allocation matrix.
    Upgrade path: VehicleType.max_control_torque_n_m once CAD/actuator
    layout is known. Yaw authority (prop reaction torque / differential
    drag, not thrust) is much weaker than roll/pitch here -- approximated
    as 20% of the pitch figure."""
    if body_length_m <= 0.0 and wing_span_m <= 0.0:
        raise ValueError("estimate_max_torque_n_m: need a positive body_length_m or wing_span_m")
    thrust_range_n = max(thrust_max_n_per_motor - thrust_min_n_per_motor, 0.0)
    pitch_arm_m = body_length_m / 2.0 if body_length_m > 0.0 else wing_span_m / 2.0
    roll_arm_m = wing_span_m / 2.0 if wing_span_m > 0.0 else body_length_m / 2.0
    pitch_torque_n_m = thrust_range_n * pitch_arm_m
    roll_torque_n_m = thrust_range_n * roll_arm_m
    yaw_torque_n_m = 0.2 * pitch_torque_n_m
    return roll_torque_n_m, pitch_torque_n_m, yaw_torque_n_m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
cd mission-model
git add dynamics_6dof.py test_dynamics_6dof.py
git commit -m "$(cat <<'EOF'
Add coarse inertia/control-torque estimates to dynamics_6dof

Geometric placeholders (rod-inertia, thrust-range x moment-arm) marked
ponytail: for the upgrade path once CAD-derived VehicleType overrides
exist, per the design spec.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Rigid-body integration (`transition_accel` / `transition_decel`)

**Files:**
- Modify: `mission-model/dynamics_6dof.py`
- Test: `mission-model/test_dynamics_6dof.py`

**Interfaces:**
- Consumes: `quat_normalize`, `quat_rotate_vector`, `quat_derivative`, `quat_log_error`, `HOVER_QUAT`, `CRUISE_QUAT`, `G`, `RHO_AIR` from Task 1.
- Produces: `transition_accel(mass_kg, weight_n, thrust_n, drag_cd, base_area_m2, lift_fn, parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd, v0, v_target, distance_cap) -> tuple[float, float, float] | None` and `transition_decel(mass_kg, weight_n, thrust_n, drag_cd, base_area_m2, lift_fn, parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd, v0, v_floor, distance_cap) -> tuple[float, float, float] | None`, both returning `(time_s, distance_m, v_final)`.

- [ ] **Step 1: Write the failing tests**

Append to `mission-model/test_dynamics_6dof.py` (add `transition_accel, transition_decel,` to the import list):

```python
def _synthetic_params():
    return dict(
        mass_kg=5.0, weight_n=5.0 * DYN_G, thrust_n=5.0 * DYN_G * 1.4, drag_cd=1.0,
        base_area_m2=0.3, lift_fn=lambda v: 0.0, parasite_area_m2=0.05,
        inertia_kg_m2=(0.3, 0.5, 0.6), max_torque_n_m=(2.0, 3.0, 1.0),
        kp=4.0, kd=2.0,
    )


def test_transition_accel_reaches_target_speed():
    p = _synthetic_params()
    result = transition_accel(v0=1.0, v_target=15.0, distance_cap=2000.0, **p)
    assert result is not None
    t, x, v = result
    assert t > 0.0 and x > 0.0
    assert not math.isnan(t) and not math.isnan(x) and not math.isnan(v)


def test_transition_accel_returns_none_when_thrust_cant_support_weight():
    p = _synthetic_params()
    p["thrust_n"] = p["weight_n"] * 0.9
    result = transition_accel(v0=1.0, v_target=15.0, distance_cap=2000.0, **p)
    assert result is None


def test_transition_accel_returns_none_when_torque_authority_is_negligible():
    """With no pitch authority, thrust stays vertical the whole flight, so
    forward speed only ever decays under drag from its 1 m/s start -- it
    never reaches v_target and (being bounded, not runaway) never reaches
    distance_cap either, so solve_ivp times out with no event fired."""
    p = _synthetic_params()
    p["max_torque_n_m"] = (1e-6, 1e-6, 1e-6)
    result = transition_accel(v0=1.0, v_target=15.0, distance_cap=2000.0, **p)
    assert result is None


def test_transition_decel_reaches_floor_speed():
    p = _synthetic_params()
    result = transition_decel(v0=15.0, v_floor=1.0, distance_cap=2000.0, **p)
    assert result is not None
    t, x, v = result
    assert t > 0.0
    assert not math.isnan(v)


def test_transition_accel_pitches_through_90_degrees_without_nan():
    """The specific failure mode quaternions (vs. Euler angles) avoid --
    confirm the full hover-to-cruise 90 degree pitch integrates cleanly."""
    p = _synthetic_params()
    result = transition_accel(v0=1.0, v_target=18.0, distance_cap=5000.0, **p)
    assert result is not None
    assert all(not math.isnan(x) for x in result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `ImportError: cannot import name 'transition_accel'`

- [ ] **Step 3: Write the implementation**

Append to `mission-model/dynamics_6dof.py` (after `estimate_max_torque_n_m`):

```python
def _rigid_body_rhs(_t, state, thrust_n, mass_kg, drag_cd, base_area_m2, lift_fn,
                     parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd, q_target):
    v = state[3:6]
    q = quat_normalize(state[6:10])
    omega = state[10:13]

    speed = float(np.linalg.norm(v))
    v_dir = v / speed if speed > 1e-9 else np.array([1.0, 0.0, 0.0])

    thrust_world = quat_rotate_vector(q, np.array([0.0, 0.0, thrust_n]))
    lift_world = np.array([0.0, 0.0, lift_fn(speed)])
    drag_n = 0.5 * RHO_AIR * drag_cd * (base_area_m2 + parasite_area_m2) * speed * speed
    drag_world = -drag_n * v_dir
    gravity_world = np.array([0.0, 0.0, -mass_kg * G])
    accel = (thrust_world + lift_world + drag_world + gravity_world) / mass_kg

    inertia = np.array(inertia_kg_m2, dtype=float)
    q_err = quat_log_error(q, q_target)
    torque = np.clip(kp * inertia * q_err - kd * inertia * omega,
                      -np.array(max_torque_n_m, dtype=float), np.array(max_torque_n_m, dtype=float))
    omega_dot = (torque - np.cross(omega, inertia * omega)) / inertia

    q_dot = quat_derivative(q, omega)
    return np.concatenate([v, accel, q_dot, omega_dot])


def _integrate_transition(thrust_n, weight_n, mass_kg, drag_cd, base_area_m2, lift_fn,
                           parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd,
                           q_start, q_target, v0, v_event, event_direction, distance_cap):
    if thrust_n <= weight_n:
        return None  # can't even support weight, let alone complete a transition

    state0 = np.concatenate([[0.0, 0.0, 0.0], [v0, 0.0, 0.0], q_start, [0.0, 0.0, 0.0]])

    def hit_v_event(_t, state):
        return float(np.linalg.norm(state[3:6])) - v_event

    hit_v_event.terminal = True
    hit_v_event.direction = event_direction

    def hit_distance(_t, state):
        return state[0] - distance_cap

    hit_distance.terminal = True
    hit_distance.direction = 1

    sol = solve_ivp(
        _rigid_body_rhs, [0.0, 300.0], state0,
        args=(thrust_n, mass_kg, drag_cd, base_area_m2, lift_fn, parasite_area_m2,
              inertia_kg_m2, max_torque_n_m, kp, kd, q_target),
        events=[hit_v_event, hit_distance], max_step=0.5, rtol=1e-6, atol=1e-8,
    )
    if sol.t_events[0].size == 0 and sol.t_events[1].size == 0:
        return None  # neither the target speed nor the distance cap was reached
    final = sol.y[:, -1]
    return sol.t[-1], float(final[0]), float(np.linalg.norm(final[3:6]))


def transition_accel(mass_kg, weight_n, thrust_n, drag_cd, base_area_m2, lift_fn, parasite_area_m2,
                      inertia_kg_m2, max_torque_n_m, kp, kd, v0, v_target, distance_cap):
    """Hover -> cruise attitude transition, powered by thrust redirected via
    quaternion attitude integration instead of the tilt-area trick. Same
    (time_s, distance_m, v_final) | None contract as the retired
    Mission_Model.py _accel_phase_numeric."""
    return _integrate_transition(thrust_n, weight_n, mass_kg, drag_cd, base_area_m2, lift_fn,
                                  parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd,
                                  q_start=HOVER_QUAT, q_target=CRUISE_QUAT, v0=v0,
                                  v_event=v_target, event_direction=1, distance_cap=distance_cap)


def transition_decel(mass_kg, weight_n, thrust_n, drag_cd, base_area_m2, lift_fn, parasite_area_m2,
                      inertia_kg_m2, max_torque_n_m, kp, kd, v0, v_floor, distance_cap):
    """Cruise -> hover attitude transition: as the body pitches back toward
    vertical, forward thrust drops off and drag decelerates the vehicle --
    a simplified 'return to hover attitude' braking model, not an
    aggressive reverse-thrust maneuver (see the design doc's non-goals)."""
    return _integrate_transition(thrust_n, weight_n, mass_kg, drag_cd, base_area_m2, lift_fn,
                                  parasite_area_m2, inertia_kg_m2, max_torque_n_m, kp, kd,
                                  q_start=CRUISE_QUAT, q_target=HOVER_QUAT, v0=v0,
                                  v_event=v_floor, event_direction=-1, distance_cap=distance_cap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
cd mission-model
git add dynamics_6dof.py test_dynamics_6dof.py
git commit -m "$(cat <<'EOF'
Add transition_accel/transition_decel 6DOF integrators

13-state rigid body (position, velocity, quaternion, angular velocity)
integrated via solve_ivp with the same terminal-event pattern the numeric
accel-phase fallback already used, returning the same (time_s, distance_m,
v_final) | None contract so Mission_Model.py's wiring stays a small patch.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire into `Mission_Model.py`

**Files:**
- Modify: `mission-model/Mission_Model.py:103-107` (imports), `Mission_Model.py:249-272` (`VehicleType`), `Mission_Model.py:515-523` (new resolver helpers, after `parasite_drag_area_m2`), `Mission_Model.py:648-663` (delete `_accel_phase`), `Mission_Model.py:611-645` (delete `_accel_phase_numeric`), `Mission_Model.py:772-819` (`segment()`)
- Test: `mission-model/test_mission_model.py`

**Interfaces:**
- Consumes: `dynamics_6dof.transition_accel`, `dynamics_6dof.transition_decel`, `dynamics_6dof.estimate_inertia_kg_m2`, `dynamics_6dof.estimate_max_torque_n_m` from Tasks 1-3.
- Produces: `VehicleType` fields `wing_span_m`, `body_length_m`, `inertia_kg_m2`, `max_control_torque_n_m`, `attitude_kp`, `attitude_kd`; module-private `_resolved_body_length_m(vehicle, cfg) -> float`, `_resolved_wing_span_m(vehicle) -> float`. `segment()`'s public signature/return type is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `mission-model/test_mission_model.py`:

```python
def test_segment_winged_vehicle_uses_6dof_transition_and_is_feasible():
    """A reasonably powered tailsitter completes a cruise leg via the
    quaternion 6DOF transition path (has_wing branch) with finite, sane
    time/energy. Every other test in this file exercises the wingless
    MULTIROTOR path and must keep passing unchanged -- that's this
    change's main safety net."""
    cfg = make_cfg(thrust_at_100=4000.0, current_at_100=30.0)
    vehicle = dataclasses.replace(mm.MULTIROTOR, wing_area_m2=1.0, wing_cl=0.9, max_speed_mps=20.0)
    t, e = mm.segment(vehicle, cfg, BATTERY, throttle_pct=80.0, distance_m=2000.0)
    assert t < mm.INFEASIBLE_TIME_S
    assert e < mm.INFEASIBLE_ENERGY_MAH
    assert t > 0.0 and e > 0.0


def test_segment_winged_vehicle_infeasible_with_negligible_torque_authority():
    """Forcing max_control_torque_n_m to near-zero means the tailsitter can
    never pitch over into cruise attitude -- segment() should report the
    leg as infeasible, not raise or hang."""
    cfg = make_cfg(thrust_at_100=4000.0, current_at_100=30.0)
    vehicle = dataclasses.replace(mm.MULTIROTOR, wing_area_m2=1.0, wing_cl=0.9, max_speed_mps=20.0,
                                   max_control_torque_n_m=(1e-6, 1e-6, 1e-6))
    t, e = mm.segment(vehicle, cfg, BATTERY, throttle_pct=80.0, distance_m=2000.0)
    assert t == mm.INFEASIBLE_TIME_S
    assert e == mm.INFEASIBLE_ENERGY_MAH


def test_vehicle_type_fields_load_and_ignore_still_work():
    """New optional fields are picked up by VEHICLE_TYPE_FIELDS the same
    way every other optional VehicleType field already is."""
    assert "wing_span_m" in mm.VEHICLE_TYPE_FIELDS
    assert "body_length_m" in mm.VEHICLE_TYPE_FIELDS
    assert "inertia_kg_m2" in mm.VEHICLE_TYPE_FIELDS
    assert "max_control_torque_n_m" in mm.VEHICLE_TYPE_FIELDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mission-model && python test_mission_model.py`
Expected: `AttributeError` (no `wing_span_m` field / `dynamics_6dof` not imported) or similar failure on the new tests, all prior tests still passing.

- [ ] **Step 3: Add the import**

In `Mission_Model.py`, in the import block (currently lines 103-107):

```python
import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq

import dynamics_6dof
```

- [ ] **Step 4: Add the new `VehicleType` fields**

In `Mission_Model.py`, immediately after the `parasite_cd0: float = 0.045` line inside the `VehicleType` dataclass (currently line 272), add:

```python
    # -- Tailsitter transition dynamics (dynamics_6dof.py) -- all optional;
    # 0.0/None means "estimate from what's already here," matching this
    # model's other geometry defaults. Only read when wing_area_m2 > 0
    # (has_wing) -- a wingless vehicle never touches these.
    wing_span_m: float = 0.0  # 0 -> estimate as sqrt(6 * wing_area_m2), AR=6 assumed
    body_length_m: float = 0.0  # 0 -> estimate as 2 prop diameters
    inertia_kg_m2: tuple[float, float, float] | None = None  # (Ixx, Iyy, Izz); None -> geometric estimate
    max_control_torque_n_m: tuple[float, float, float] | None = None  # (roll, pitch, yaw) N*m; None -> geometric estimate
    attitude_kp: float = 4.0  # transition attitude-control proportional gain
    attitude_kd: float = 2.0  # transition attitude-control derivative gain
```

- [ ] **Step 5: Add the geometry resolver helpers**

In `Mission_Model.py`, immediately after `parasite_drag_area_m2()` (currently ending at line 523), add:

```python
def _resolved_body_length_m(vehicle: VehicleType, cfg: MotorPropConfig) -> float:
    """vehicle.body_length_m if set, else a crude nose-to-tail estimate (2
    prop diameters) -- good enough for dynamics_6dof.py's coarse
    inertia/torque estimates. See VehicleType.body_length_m."""
    if vehicle.body_length_m > 0.0:
        return vehicle.body_length_m
    return 2.0 * cfg.prop_diameter_in * 0.0254


def _resolved_wing_span_m(vehicle: VehicleType) -> float:
    """vehicle.wing_span_m if set, else sqrt(6 * wing_area_m2) (AR=6
    assumed). See VehicleType.wing_span_m."""
    if vehicle.wing_span_m > 0.0:
        return vehicle.wing_span_m
    if vehicle.wing_area_m2 <= 0.0:
        return 0.0
    return math.sqrt(6.0 * vehicle.wing_area_m2)
```

- [ ] **Step 6: Delete `_accel_phase_numeric` and `_accel_phase`**

Delete the entire `_accel_phase_numeric` function (currently lines 611-645) and the entire `_accel_phase` function (currently lines 648-663) — both become dead code once `segment()` is rewritten in the next step to dispatch on `has_wing` directly instead of delegating to `_accel_phase`.

- [ ] **Step 7: Rewrite `segment()`**

Replace the whole `segment()` function (currently lines 772-819) with:

```python
def segment(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery,
            throttle_pct: float, distance_m: float, extra_payload_g: float = 0.0):
    mass_g = total_mass_g(vehicle, cfg, battery.mass_g, extra_payload_g)
    mass_kg = mass_g / 1000.0
    weight_n = mass_kg * G
    thrust_n = rotor_set_thrust_n(vehicle, cfg, throttle_pct)
    amps_ma = rotor_set_amps_ma(vehicle, cfg, throttle_pct)
    base_area = frontal_area_m2(vehicle, cfg)
    has_wing = vehicle.wing_area_m2 > 0
    lift_fn = (lambda v: wing_lift_n(vehicle, v)) if has_wing else (lambda _v: 0.0)
    parasite_area = parasite_drag_area_m2(vehicle)  # 0.0 for a wingless vehicle

    v_eq = _equilibrium_velocity(thrust_n, weight_n, base_area, vehicle.drag_cd, lift_fn, parasite_area)
    if v_eq <= 1.0:  # can't out-accelerate its own drag past the 1 m/s start
        return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
    v_target = min(CRUISE_V_FRACTION * v_eq, vehicle.max_speed_mps)

    if has_wing:
        # Tailsitter transition: real quaternion 6DOF attitude integration
        # instead of the tilt-area trick (see dynamics_6dof.py and
        # docs/superpowers/specs/2026-09-15-quaternion-6dof-transition-design.md).
        body_length_m = _resolved_body_length_m(vehicle, cfg)
        wing_span_m = _resolved_wing_span_m(vehicle)
        inertia_kg_m2 = vehicle.inertia_kg_m2 or dynamics_6dof.estimate_inertia_kg_m2(
            mass_kg, body_length_m, wing_span_m)
        thrust_min_n = cfg.thrust_g(THROTTLE_MIN) / 1000.0 * G
        thrust_max_n = cfg.thrust_g(THROTTLE_MAX) / 1000.0 * G
        max_torque_n_m = vehicle.max_control_torque_n_m or dynamics_6dof.estimate_max_torque_n_m(
            thrust_min_n, thrust_max_n, body_length_m, wing_span_m)

        accel = dynamics_6dof.transition_accel(
            mass_kg=mass_kg, weight_n=weight_n, thrust_n=thrust_n, drag_cd=vehicle.drag_cd,
            base_area_m2=base_area, lift_fn=lift_fn, parasite_area_m2=parasite_area,
            inertia_kg_m2=inertia_kg_m2, max_torque_n_m=max_torque_n_m,
            kp=vehicle.attitude_kp, kd=vehicle.attitude_kd,
            v0=1.0, v_target=v_target, distance_cap=distance_m / 2)
        if accel is None:
            return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
        t_acc, x_acc, v_cruise = accel

        decel = dynamics_6dof.transition_decel(
            mass_kg=mass_kg, weight_n=weight_n, thrust_n=thrust_n, drag_cd=vehicle.drag_cd,
            base_area_m2=base_area, lift_fn=lift_fn, parasite_area_m2=parasite_area,
            inertia_kg_m2=inertia_kg_m2, max_torque_n_m=max_torque_n_m,
            kp=vehicle.attitude_kp, kd=vehicle.attitude_kd,
            v0=v_cruise, v_floor=1.0, distance_cap=distance_m / 2)
        if decel is None:
            return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
        t_dec, x_dec, _v_end = decel
    else:
        # Wingless multirotor: unchanged closed-form path.
        thrust_horizontal_n = math.sqrt(max(thrust_n * thrust_n - weight_n * weight_n, 0.0))
        if thrust_horizontal_n <= 0:
            return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
        effective_area = base_area * (thrust_n / thrust_horizontal_n)
        kk = 0.5 * RHO_AIR * vehicle.drag_cd * effective_area / mass_kg
        a0 = thrust_horizontal_n / mass_kg
        accel = _accel_phase_closed_form(a0, kk, v0=1.0, v_target=v_target, distance_cap=distance_m / 2)
        if accel is None:
            return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
        t_acc, x_acc, v_cruise = accel

        decel_area = _effective_area_at(thrust_n, weight_n, base_area, lift_fn, v_cruise, parasite_area)
        kk_decel = 0.5 * RHO_AIR * vehicle.drag_cd * decel_area / mass_kg
        effective_weight_cruise_n = max(weight_n - lift_fn(v_cruise), 0.0)
        thrust_horizontal_decel_n = math.sqrt(
            max(thrust_n * thrust_n - effective_weight_cruise_n * effective_weight_cruise_n, 0.0))
        a0_decel = thrust_horizontal_decel_n / mass_kg
        t_dec, x_dec, _v_end = _decel_phase_powered(a0_decel, kk_decel, v_cruise, v_floor=1.0,
                                                      distance_cap=distance_m / 2)

    # Energy uses the cruise-throttle current for both phases, matching the
    # original's assumption that throttle (and so current draw) is held
    # constant rather than reduced while braking down.
    energy_mah = amps_ma * ((t_acc + t_dec) / 3600.0)

    if x_acc + x_dec >= distance_m:
        return t_acc + t_dec, energy_mah

    t_cruise = (distance_m - (x_acc + x_dec)) / v_cruise
    energy_mah += amps_ma * (t_cruise / 3600.0)
    return t_acc + t_dec + t_cruise, energy_mah
```

- [ ] **Step 8: Run test to verify everything passes**

Run: `cd mission-model && python test_mission_model.py`
Expected: every existing test passes unchanged, plus the 3 new ones from Step 1.

Also re-run Task 1-3's suite to confirm nothing there regressed:

Run: `cd mission-model && python test_dynamics_6dof.py`
Expected: `18 passed`

- [ ] **Step 9: Commit**

```bash
cd mission-model
git add Mission_Model.py test_mission_model.py
git commit -m "$(cat <<'EOF'
Wire quaternion 6DOF transition into segment() for winged vehicles

Tailsitter accel/decel legs now integrate real quaternion attitude via
dynamics_6dof.transition_accel/transition_decel instead of the tilt-area
trick; wingless MULTIROTOR path is untouched (has_wing branches at the
top of segment() instead of inside the retired _accel_phase). New
optional VehicleType fields (wing_span_m, body_length_m, inertia_kg_m2,
max_control_torque_n_m, attitude_kp/kd) default to "estimate it," so
existing vehicle JSON files keep working unchanged.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** quaternion kinematics (Task 1) → inertia/torque estimates with CAD-override upgrade path (Task 2) → PD control law + force model + solve_ivp integration + `None`/`ValueError` error handling (Task 3) → `VehicleType` fields + `segment()` dispatch, wingless path untouched (Task 4). Testing section of the spec (gimbal-lock check, quaternion kinematics identities, regression sanity bound, self-check convention) is covered across Tasks 1, 3, and 4.
- **Circular import risk** (not explicit in the spec, caught while planning): the spec's phrasing implied `dynamics_6dof.py` might reuse `Mission_Model.py` helpers like `wing_lift_n`/`rotor_set_thrust_n` directly. Resolved by keeping `dynamics_6dof.py` fully float/callable-based (no `VehicleType`/`MotorPropConfig` params at all) — `Mission_Model.py`'s `segment()` extracts every physical quantity itself before calling in, exactly like it already does for `_accel_phase_numeric` today. No behavior change from the spec's intent, just a cleaner module boundary.
- **Type consistency:** `transition_accel`/`transition_decel` parameter names and order match between their Task 3 definitions and every Task 4 call site; `estimate_inertia_kg_m2`/`estimate_max_torque_n_m` likewise match between Task 2 and Task 4.
- **Placeholder scan:** no TBD/TODO; every code step is complete, runnable code.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
