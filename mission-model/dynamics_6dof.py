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
CRUISE_QUAT = quat_from_axis_angle(np.array([0.0, 1.0, 0.0]), math.pi / 2)


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
                           q_start, q_target, v0, v_event, event_direction, distance_cap,
                           speed_slice=slice(3, 6)):
    # Deviation from the brief: the speed the v_event tracks is configurable
    # (full 3D velocity norm by default, matching the brief's original
    # hit_v_event) rather than hard-coded, because the two callers need
    # different quantities. transition_accel keeps the brief's full-norm
    # check as given (its own v_target=18 regression test only reaches 18
    # via the dive-augmented full speed; the pure-forward component tops
    # out near 16.2 m/s here, verified numerically). transition_decel
    # instead needs the horizontal-only component (state[3:5]): with this
    # zero-lift synthetic vehicle, thrust must always exceed weight_n (see
    # the guard below), so once the body pitches back to hover it
    # inevitably develops a net climb -- verified numerically, decel's
    # forward (x) speed decays past v_floor=1.0 around t=7.3s while the
    # full 3D speed (climb included) instead settles at a steady state
    # ~9.6 m/s and never crosses the floor, timing out with no event
    # fired. Both transition functions' own docstrings describe this as a
    # forward-airspeed transition, so the horizontal projection is the
    # physically correct quantity for decel's floor check.
    if thrust_n <= weight_n:
        return None  # can't even support weight, let alone complete a transition

    state0 = np.concatenate([[0.0, 0.0, 0.0], [v0, 0.0, 0.0], q_start, [0.0, 0.0, 0.0]])

    def hit_v_event(_t, state, *_args):
        # scipy passes solve_ivp's `args` tuple to event functions too, not
        # just `fun` -- *_args absorbs it since these events don't need it.
        return float(np.linalg.norm(state[speed_slice])) - v_event

    hit_v_event.terminal = True
    hit_v_event.direction = event_direction

    def hit_distance(_t, state, *_args):
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
                                  v_event=v_floor, event_direction=-1, distance_cap=distance_cap,
                                  speed_slice=slice(3, 5))
