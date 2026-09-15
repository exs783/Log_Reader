"""Self-check for dynamics_6dof.py. Run: python test_dynamics_6dof.py"""

import math

import numpy as np

from dynamics_6dof import (
    quat_mult, quat_normalize, quat_conjugate, quat_rotate_vector,
    quat_derivative, quat_from_axis_angle, quat_log, quat_log_error,
    HOVER_QUAT, CRUISE_QUAT, G as DYN_G, RHO_AIR as DYN_RHO_AIR,
    estimate_inertia_kg_m2, estimate_max_torque_n_m,
    transition_accel, transition_decel,
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
    assert np.allclose(err, [0.0, math.pi / 2, 0.0], atol=1e-9)


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


def test_estimate_max_torque_fallback_pitch_arm_when_body_length_zero():
    # body_length_m=0, wing_span_m>0: pitch_arm should fall back to wing_span_m/2
    roll, pitch, yaw = estimate_max_torque_n_m(thrust_min_n_per_motor=10.0, thrust_max_n_per_motor=30.0,
                                                body_length_m=0.0, wing_span_m=2.0)
    thrust_range_n = 20.0
    expected_pitch = thrust_range_n * (2.0 / 2.0)  # fallback arm
    expected_roll = thrust_range_n * (2.0 / 2.0)  # normal arm
    assert math.isclose(pitch, expected_pitch)
    assert math.isclose(roll, expected_roll)
    assert math.isclose(yaw, 0.2 * pitch)


def test_estimate_max_torque_fallback_roll_arm_when_wing_span_zero():
    # wing_span_m=0, body_length_m>0: roll_arm should fall back to body_length_m/2
    roll, pitch, yaw = estimate_max_torque_n_m(thrust_min_n_per_motor=10.0, thrust_max_n_per_motor=30.0,
                                                body_length_m=1.0, wing_span_m=0.0)
    thrust_range_n = 20.0
    expected_pitch = thrust_range_n * (1.0 / 2.0)  # normal arm
    expected_roll = thrust_range_n * (1.0 / 2.0)  # fallback arm
    assert math.isclose(pitch, expected_pitch)
    assert math.isclose(roll, expected_roll)
    assert math.isclose(yaw, 0.2 * pitch)


def test_estimate_inertia_succeeds_with_asymmetric_zero():
    # body_length_m=0, wing_span_m>0: should not raise, i_fuselage=0, ixx and izz from wing only
    ixx, iyy, izz = estimate_inertia_kg_m2(mass_kg=2.0, body_length_m=0.0, wing_span_m=2.0)
    expected_i_wing = (1.0 * 2.0 ** 2) / 12.0
    assert math.isclose(ixx, expected_i_wing)  # roll: wing only
    assert math.isclose(iyy, 0.0)  # pitch: fuselage zero
    assert math.isclose(izz, expected_i_wing)  # yaw: wing only

    # wing_span_m=0, body_length_m>0: should not raise, i_wing=0, iyy and izz from fuselage only
    ixx, iyy, izz = estimate_inertia_kg_m2(mass_kg=2.0, body_length_m=1.0, wing_span_m=0.0)
    expected_i_fuselage = (1.0 * 1.0 ** 2) / 12.0
    assert math.isclose(ixx, 0.0)  # roll: wing zero
    assert math.isclose(iyy, expected_i_fuselage)  # pitch: fuselage only
    assert math.isclose(izz, expected_i_fuselage)  # yaw: fuselage only


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


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
