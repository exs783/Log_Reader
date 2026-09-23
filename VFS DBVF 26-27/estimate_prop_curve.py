"""Estimate a motor's thrust/current curve on a prop size it was never tested
with, from two props it was.

At a fixed throttle a BLDC motor is close to a straight torque-speed line
(Q = a - b*w), and its current is close to linear in torque. Two measured
props at the same throttle give both lines exactly. The new prop's thrust and
torque coefficients (Ct, Cq) are taken as the two measured props' average, and
its operating point is where its torque curve Cq*rho*n^2*D^5 crosses the motor
line.

Checked against KDE5215XF-330 at 6S (__main__): predicting its measured
15.5" curve from its 18.5" and 21.5" curves gives thrust within 6% from 50% to 100%
throttle (~-19% at 25-37.5%, i.e. conservative), and current 4-17% LOW.
CURRENT_MARGIN adds the mean current miss back so estimates err heavy.

Run: python estimate_prop_curve.py   (self-check + prints MN6007II 18" curve)
"""
import numpy as np
from scipy.optimize import brentq

RHO = 1.225
CURRENT_MARGIN = 1.11  # mean current under-prediction in the KDE check below


def extrapolate(d1_in, rows1, d2_in, rows2, d_new_in):
    """rows: [(throttle_pct, thrust_g, torque_Nm, current_A, rpm)], same throttles
    in both. Returns [(throttle_pct, thrust_g, current_A, rpm)] for d_new_in,
    current NOT yet multiplied by CURRENT_MARGIN."""
    def coeffs(d_in, rows):
        D = d_in * 0.0254
        n = np.array([r[4] for r in rows]) / 60
        ct = np.array([r[1] for r in rows]) / 1000 * 9.807 / (RHO * n**2 * D**4)
        cq = np.array([r[2] for r in rows]) / (RHO * n**2 * D**5)
        return ct, cq
    (ct1, cq1), (ct2, cq2) = coeffs(d1_in, rows1), coeffs(d2_in, rows2)
    ct, cq = (ct1 + ct2) / 2, (cq1 + cq2) / 2
    D = d_new_in * 0.0254
    out = []
    for i, (r1, r2) in enumerate(zip(rows1, rows2)):
        assert r1[0] == r2[0], "both props must be tested at the same throttles"
        w1, w2 = r1[4] * 2 * np.pi / 60, r2[4] * 2 * np.pi / 60
        b = (r2[2] - r1[2]) / (w1 - w2)
        a = r1[2] + b * w1
        k_i = (r2[3] - r1[3]) / (r2[2] - r1[2])
        w = brentq(lambda w: cq[i] * RHO * (w / 2 / np.pi) ** 2 * D**5 - (a - b * w), 1.0, a / b)
        n = w / 2 / np.pi
        q = a - b * w
        out.append((r1[0], ct[i] * RHO * n**2 * D**4 / 9.807 * 1000, r1[3] + k_i * (q - r1[2]), n * 60))
    return out


# KDE5215XF-330, 6S, dual-blade, from KDE's own test sheet (KDE5215-330kV_rev2.xlsx)
KDE_155 = [(25, 554, .12, 2.5, 3130), (37.5, 873, .18, 4.1, 3910), (50, 1166, .23, 6.0, 4520),
           (62.5, 1582, .31, 9.0, 5280), (75, 2162, .42, 13.4, 6140), (87.5, 2794, .54, 19.3, 6970),
           (100, 3451, .66, 25.5, 7630)]
KDE_185 = [(25, 697, .14, 2.7, 2560), (37.5, 1205, .24, 5.1, 3330), (50, 1850, .38, 9.0, 4130),
           (62.5, 2669, .55, 14.8, 4940), (75, 3494, .74, 22.2, 5710), (87.5, 4453, .93, 31.7, 6420),
           (100, 5269, 1.11, 40.9, 7030)]
KDE_215 = [(25, 1066, .25, 3.6, 2140), (37.5, 1961, .46, 8.2, 2960), (50, 3088, .73, 15.6, 3690),
           (62.5, 4187, 1.03, 26.3, 4340), (75, 5365, 1.31, 38.7, 4930), (87.5, 6040, 1.51, 50.6, 5250),
           (100, 7267, 1.84, 68.5, 5830)]

# T-Motor MN6007II KV320, 6S, from T-Motor's sheet (tmotor_mn6007II_data.pdf)
MN6007_P21 = [(40, 1496, .35, 5.55, 2988), (50, 2111, .48, 9.10, 3525), (60, 2723, .62, 13.18, 3981),
              (70, 3269, .74, 17.56, 4343), (80, 3918, .88, 23.16, 4744), (90, 4585, 1.03, 29.77, 5122),
              (100, 5397, 1.21, 38.70, 5538)]
MN6007_P22 = [(40, 1737, .41, 6.51, 2904), (50, 2431, .57, 10.71, 3421), (60, 3059, .72, 15.25, 3820),
              (70, 3704, .86, 20.64, 4203), (80, 4347, 1.01, 26.71, 4547), (90, 5084, 1.19, 34.32, 4885),
              (100, 5882, 1.37, 44.19, 5262)]

if __name__ == "__main__":
    for (th, t, _i, _r), m in zip(extrapolate(18.5, KDE_185, 21.5, KDE_215, 15.5), KDE_155):
        if th >= 50:
            assert abs(t / m[1] - 1) < 0.07, (th, t, m[1])
    print("KDE check ok (thrust within 7% at >=50% throttle)")
    for th, t, i, rpm in extrapolate(21, MN6007_P21, 22, MN6007_P22, 18):
        print(f"{th:5.1f}%  {t:6.0f} g  {i * CURRENT_MARGIN:6.2f} A  {rpm:5.0f} rpm")
