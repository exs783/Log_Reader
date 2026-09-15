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
