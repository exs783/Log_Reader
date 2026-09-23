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
import sys

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


# Roskam non-dimensional radii-of-gyration (Rx=kx/b, Ry=ky/l) for light
# single-engine aircraft (Roskam, "Airplane Flight Dynamics and Automatic
# Flight Controls" -- values found for both low-wing, Rx~0.248/Ry~0.338, and
# high-wing/C182-type, Rx~0.242/Ry~0.397, configurations; averaged/rounded
# here since this project has no CAD to pick a specific configuration from).
# Izz uses the same Ry-scale coefficient applied to the mean of span/length,
# not a separately-tabulated Rz, to keep izz = ixx + iyy (both axes'
# distributions resist yaw) -- a simplification vs. Roskam's own distinct Rz
# row, kept for consistency with this function's existing izz convention.
ROSKAM_RX = 0.25  # roll, applied to wing_span_m
ROSKAM_RY = 0.35  # pitch, applied to body_length_m


def estimate_inertia_kg_m2(mass_kg: float, body_length_m: float, wing_span_m: float) -> tuple[float, float, float]:
    """ponytail: Roskam radius-of-gyration estimate (Ix=m*(Rx*b/2)^2, Iy=
    m*(Ry*l/2)^2, see ROSKAM_RX/ROSKAM_RY above) -- light-aircraft-class
    coefficients, not specific to this tailsitter class, so still an
    estimate, but tracks real light-aircraft inertia far better than a
    uniform-rod model: a rod-along-span estimate over-predicts roll inertia
    by roughly 5x and pitch by roughly 2-3x relative to these coefficients,
    since real airframe mass (fuselage, battery, payload) concentrates near
    the CG rather than spreading uniformly to the tips like a rod. Missing
    span or length zeroes that axis's own contribution (ixx needs
    wing_span_m, iyy needs body_length_m) rather than substituting the other
    dimension. Upgrade path: set VehicleType.inertia_kg_m2 once CAD gives
    real principal moments."""
    if body_length_m <= 0.0 and wing_span_m <= 0.0:
        raise ValueError("estimate_inertia_kg_m2: need a positive body_length_m or wing_span_m")
    ixx = mass_kg * (ROSKAM_RX * wing_span_m / 2.0) ** 2  # roll: resisted by wing-span mass distribution
    iyy = mass_kg * (ROSKAM_RY * body_length_m / 2.0) ** 2  # pitch: fuselage-length mass distribution -- the transition axis
    izz = ixx + iyy  # yaw: resisted by both
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

    # Lift acts along a body-fixed axis, so its world direction follows the
    # vehicle's attitude (the design spec's "true angle of attack" wing
    # model). Body -x is the axis that lands in the right place at both
    # reference attitudes -- verified against the definitions above:
    # CRUISE_QUAT (body +z -> world +x) sends body -x -> world +z, i.e.
    # lift straight up with the thrust axis horizontal; HOVER_QUAT
    # (identity) sends body -x -> world -x, i.e. edge-on, no useful lift.
    # The magnitude uses only the velocity component *perpendicular* to
    # that axis: a velocity component along the lift axis must not feed
    # back into more lift along the same axis (that loop is unbounded and
    # diverged solve_ivp for every real winged vehicle).
    lift_axis_world = quat_rotate_vector(q, np.array([-1.0, 0.0, 0.0]))
    lift_axis_world = lift_axis_world / np.linalg.norm(lift_axis_world)
    v_perp = v - np.dot(v, lift_axis_world) * lift_axis_world
    lift_world = lift_axis_world * lift_fn(float(np.linalg.norm(v_perp)))
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
                           return_final_state=False):
    # Deviation from the brief: v_event tracks the *signed forward* ground
    # speed (state[3]) rather than the full 3D velocity norm, and the
    # returned v_final reports that same quantity. Both transition
    # functions' docstrings describe these as forward-airspeed
    # transitions, and segment() uses transition_accel's returned speed as
    # the cruise-phase ground speed for the remaining-distance/energy
    # calculation -- so a velocity that is mostly climb, or is pointing
    # backwards, is not "reached v_cruise" in any sense segment() can use.
    # The sign matters, not just the horizontal projection: with a
    # magnitude (norm) test, a vehicle blown backwards reads as fast, which
    # let a hover-attitude vehicle with no pitch authority "reach v_target"
    # while flying tail-first.
    if thrust_n <= weight_n:
        return None  # can't even support weight, let alone complete a transition

    state0 = np.concatenate([[0.0, 0.0, 0.0], [v0, 0.0, 0.0], q_start, [0.0, 0.0, 0.0]])

    def hit_v_event(_t, state, *_args):
        # scipy passes solve_ivp's `args` tuple to event functions too, not
        # just `fun` -- *_args absorbs it since these events don't need it.
        return float(state[3]) - v_event

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
    if sol.status == -1:
        # Solver breakdown (step size collapsed), not a physical verdict --
        # scipy's 0 = reached t_span, 1 = termination event. Caller still
        # gets None (a diverged run is not a usable result), but say so out
        # loud instead of letting it look like genuine infeasibility.
        print(f"Warning: 6DOF transition integration diverged at t={float(sol.t[-1]):.3f}s "
              f"({sol.message}) -- reporting as infeasible.", file=sys.stderr)
        return None
    if sol.t_events[0].size == 0 and sol.t_events[1].size == 0:
        return None  # neither the target speed nor the distance cap was reached
    final = sol.y[:, -1]
    result = (float(sol.t[-1]), float(final[0]), float(final[3]))
    # return_final_state appends the raw 13-state vector, so a test can check
    # the attitude actually reached; transition_accel / transition_decel keep
    # their 3-tuple contract, which segment() depends on.
    return (*result, final) if return_final_state else result


def transition_accel(mass_kg, weight_n, thrust_n, drag_cd, base_area_m2, lift_fn, parasite_area_m2,
                      inertia_kg_m2, max_torque_n_m, kp, kd, v0, v_target, distance_cap):
    """Hover -> cruise attitude transition, powered by thrust redirected via
    quaternion attitude integration instead of the tilt-area trick. Same
    (time_s, distance_m, v_final) | None contract as the retired
    Mission_Model.py _accel_phase_numeric -- v_final is forward ground
    speed, which is what segment() flies the rest of the leg at."""
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
