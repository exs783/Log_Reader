"""Self-check for dynamics_6dof.py. Run: python test_dynamics_6dof.py"""

import math

import numpy as np

from dynamics_6dof import (
    quat_mult, quat_normalize, quat_conjugate, quat_rotate_vector,
    quat_derivative, quat_from_axis_angle, quat_log, quat_log_error,
    HOVER_QUAT, CRUISE_QUAT, G as DYN_G, RHO_AIR as DYN_RHO_AIR,
    estimate_inertia_kg_m2, estimate_max_torque_n_m,
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


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
