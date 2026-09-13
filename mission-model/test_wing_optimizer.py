"""Self-check for wing_optimizer.py. Run: python test_wing_optimizer.py"""

import math

from wing_optimizer import (
    required_wing_area_m2, wing_mass_g, optimize_wing, AR_REF,
    naca4_params, naca4_airfoil_point, solidworks_naca4_equations, rotate_about_pivot,
    bending_relief_factor, elliptical_chord_m,
    parse_selig_dat, place_station_points, wing_stations,
)
from Mission_Model import G, RHO_AIR

# required_wing_area_m2: invert the lift equation by hand and check round-trip.
area = required_wing_area_m2(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6)
lift_n = 0.5 * RHO_AIR * 0.6 * area * 25.0 * 25.0
assert math.isclose(lift_n, 12.0 * G, rel_tol=1e-9), "area doesn't satisfy the lift equation it was derived from"

# wing_mass_g: increasing AR above the reference must increase mass (bending-moment penalty).
low = wing_mass_g(area_m2=2.0, aspect_ratio=AR_REF, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
high = wing_mass_g(area_m2=2.0, aspect_ratio=AR_REF * 4, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
assert high > low, "wing mass should grow with aspect ratio"
assert math.isclose(low, 1500.0 * 2.0, rel_tol=1e-9), "mass at the reference AR should equal areal_density * area"

# optimize_wing: with no span cap, the optimizer should just take the lowest allowed AR.
result = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                        max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
assert math.isclose(result["aspect_ratio"], 4.0, abs_tol=1e-6), "unconstrained optimum should sit at min_ar"
assert not result["span_capped"]

# optimize_wing: a tight span cap should force AR down below max_ar and still satisfy the cap.
capped = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                        max_span_m=math.sqrt(6.0 * result["wing_area_m2"]),
                        areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
assert capped["span_m"] <= math.sqrt(6.0 * result["wing_area_m2"]) + 1e-6
assert capped["aspect_ratio"] <= 6.0 + 1e-6


# naca4_params: digit parsing, and the p=0.5 symmetric-section guard.
assert naca4_params("2412") == (0.02, 0.4, 0.12)
assert naca4_params("0012") == (0.0, 0.5, 0.12), "symmetric section (m=0) should report p=0.5, not 0.0"

# naca4_airfoil_point, symmetric section (NACA 0012): camber line is zero
# everywhere, so upper/lower surfaces must mirror exactly around y=0. Check
# the thickness distribution against an independently-written copy of the
# standard formula (not the one naca4_airfoil_point itself calls) rather
# than a memorized "published" constant.
m, p, t = naca4_params("0012")
xc = 0.3
yt_reference = 5 * t * (0.2969 * math.sqrt(xc) - 0.1260 * xc - 0.3516 * xc**2 + 0.2843 * xc**3 - 0.1015 * xc**4)
pt = naca4_airfoil_point(xc, m, p, t, closed_te=False)
assert math.isclose(pt["yu"], -pt["yl"], abs_tol=1e-12), "symmetric section must be mirror-symmetric about the chord line"
assert math.isclose(pt["yu"] - pt["yl"], 2 * yt_reference, rel_tol=1e-9), "thickness distribution doesn't match the standard NACA formula"
assert math.isclose(pt["xu"], xc, abs_tol=1e-12) and math.isclose(pt["xl"], xc, abs_tol=1e-12), \
    "a symmetric section has zero camber-line slope, so x should be untouched by the thickness offset"

# Closed vs. open trailing edge: at x=1 (the trailing edge), the closed-TE
# variant must land exactly on the chord line; the open-TE (published)
# coefficient leaves the well-known small gap instead.
closed = naca4_airfoil_point(1.0, m, p, t, closed_te=True)
open_te = naca4_airfoil_point(1.0, m, p, t, closed_te=False)
assert math.isclose(closed["yu"], 0.0, abs_tol=1e-9), "closed-TE trailing edge should sit exactly on the chord line"
assert open_te["yu"] > 1e-4, "open-TE trailing edge should leave the standard small gap"

# naca4_airfoil_point, cambered section (NACA 2412): the camber line's peak
# (at x=p=0.4) should equal m=0.02, and upper/lower surfaces must no longer
# be mirror-symmetric about y=0.
m, p, t = naca4_params("2412")
at_p = naca4_airfoil_point(p, m, p, t)
camber_at_p = (at_p["yu"] + at_p["yl"]) / 2  # midline between surfaces ~= camber line, away from high-curvature LE/TE
assert math.isclose(camber_at_p, m, abs_tol=1e-3), "camber line should peak at m at x/c=p"
assert not math.isclose(at_p["yu"], -at_p["yl"], abs_tol=1e-6), "a cambered section must not be mirror-symmetric"

# solidworks_naca4_equations: sanity-check the emitted strings rather than
# their numeric output (that's what the checks above already cover) --
# just confirm the chord unit conversion is right and the SW-specific
# function names (sqr/atn/iif, not sqrt/atan/if) actually appear.
eqs = solidworks_naca4_equations("2412", chord_value=0.25, units="m")
assert '250.0000' in eqs["global_variables"]["chord"], "0.25 m should convert to 250 mm"
for expr in (eqs["upper_x_of_t"], eqs["upper_y_of_t"], eqs["lower_x_of_t"], eqs["lower_y_of_t"]):
    assert "sqrt(" not in expr and "atan(" not in expr, "SolidWorks equations use sqr()/atn(), not sqrt()/atan()"
    assert "sqr(" in expr and "atn(" in expr and "iif(" in expr

# optimize_wing: taper. Area = span * mean_chord always; a linear taper just
# splits that mean between root/tip (area = span * (root+tip)/2), and taper
# shouldn't change area, span, or AR (this model doesn't feed taper back
# into the mass estimate).
untapered = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                           max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
tapered = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                         max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5, taper_ratio=0.5)
assert math.isclose(tapered["wing_area_m2"], untapered["wing_area_m2"], rel_tol=1e-9)
assert math.isclose(tapered["span_m"], untapered["span_m"], rel_tol=1e-9)
assert math.isclose(tapered["tip_chord_m"], 0.5 * tapered["root_chord_m"], rel_tol=1e-9), "taper_ratio=0.5 means tip=half of root"
assert math.isclose(tapered["span_m"] * (tapered["root_chord_m"] + tapered["tip_chord_m"]) / 2,
                     tapered["wing_area_m2"], rel_tol=1e-9), "trapezoid area from root/tip chord must match wing_area_m2"
assert math.isclose(untapered["root_chord_m"], untapered["chord_m"], rel_tol=1e-9), "taper_ratio=1 (default) should give root=tip=mean chord"

try:
    optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                   max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5, taper_ratio=0.0)
    raise AssertionError("taper_ratio=0 (zero tip chord) should be rejected, not silently accepted")
except ValueError:
    pass

# rotate_about_pivot: a point on the pivot doesn't move; a 90-degree
# rotation about the origin sends (1, 0) to (0, 1) exactly.
assert rotate_about_pivot(5.0, 0.0, pivot_x=5.0, twist_deg=37.0) == (5.0, 0.0)
rx, ry = rotate_about_pivot(1.0, 0.0, pivot_x=0.0, twist_deg=90.0)
assert math.isclose(rx, 0.0, abs_tol=1e-9) and math.isclose(ry, 1.0, abs_tol=1e-9)

# solidworks_naca4_equations: twist must rotate the emitted curve by the
# same transform rotate_about_pivot() computes, at a sample t (checked via
# naca4_airfoil_point's untwisted point run through the standalone rotation
# -- the two code paths agreeing is the point of the test).
chord_m = 0.3
twist_deg = -5.0
m, p, t = naca4_params("2412")
pt = naca4_airfoil_point(0.5, m, p, t, closed_te=True)
expected_x, expected_y = rotate_about_pivot(pt["xu"] * chord_m, pt["yu"] * chord_m,
                                             pivot_x=0.25 * chord_m, twist_deg=twist_deg)
twisted_eqs = solidworks_naca4_equations("2412", chord_m, units="m", closed_te=True, twist_deg=twist_deg)
# Evaluate the emitted SW-flavored expression in Python by swapping in its
# equivalents (sqr->math.sqrt, atn->math.atan, iif->a ternary) rather than
# retyping the formula -- this is checking the *emitted string*, not a
# reimplementation of it.
env = {
    # SolidWorks' sin()/cos() auto-convert a (deg)-tagged global variable's
    # value to radians at the point of use (same as its "45deg" literal
    # syntax) -- Python's math.cos/sin don't, so "twist" is pre-converted
    # here to model that, not because the emitted equation does the
    # conversion itself.
    "cos": math.cos, "sin": math.sin, "sqr": math.sqrt, "atn": math.atan, "pi": math.pi,
    "chord": chord_m * 1000, "naca_m": m, "naca_p": p, "naca_t": t, "twist": math.radians(twist_deg),
    "iif": lambda cond, a, b: a if cond else b, "t": 0.5,
}
def sw_eval(expr: str):
    # SolidWorks uses "^" for exponentiation, which Python parses as XOR --
    # swap it for "**" so eval() can run the emitted expression as-is.
    return eval(expr.replace("^", "**"), {"__builtins__": {}}, env)

got_x = sw_eval(twisted_eqs["upper_x_of_t"]) / 1000  # mm -> m
got_y = sw_eval(twisted_eqs["upper_y_of_t"]) / 1000
assert math.isclose(got_x, expected_x, rel_tol=1e-6), "emitted twisted x(t) doesn't match rotate_about_pivot"
assert math.isclose(got_y, expected_y, rel_tol=1e-6), "emitted twisted y(t) doesn't match rotate_about_pivot"

# var_prefix must namespace every declared global variable, and the curve
# equations must reference those same prefixed names (not the bare ones) --
# otherwise a root+tip pair would collide or silently reference each other's
# variables in the real SolidWorks Equations manager.
tip_eqs = solidworks_naca4_equations("2412", 0.2, units="m", twist_deg=-3.0, var_prefix="tip_")
assert set(tip_eqs["global_variables"]) == {"tip_chord", "tip_naca_m", "tip_naca_p", "tip_naca_t", "tip_twist"}
for expr in (tip_eqs["upper_x_of_t"], tip_eqs["upper_y_of_t"], tip_eqs["lower_x_of_t"], tip_eqs["lower_y_of_t"]):
    assert "tip_chord" in expr and "tip_twist" in expr
    assert "\"chord\"" not in expr

# bending_relief_factor: a rectangular wing (taper_ratio=1) must get exactly
# 1.0 -- today's mass model, unchanged -- derived from the root bending-
# moment integral of a chord-proportional load: BM(lambda) ~ 1/2 - (1-lambda)/3,
# normalized so BM(1) = 1.0.
assert math.isclose(bending_relief_factor("linear", 1.0), 1.0, rel_tol=1e-12)
assert math.isclose(bending_relief_factor("linear", 0.5), 2.0 / 3.0, rel_tol=1e-9)
assert math.isclose(bending_relief_factor("linear", 0.0), 1.0 / 3.0, rel_tol=1e-9)
# Elliptical loading's own bending-moment integral (BM ~ 1/3 vs rectangle's
# 1/2) gives exactly 2/3 -- independent of any taper_ratio argument, and not
# coincidentally close to the linear taper_ratio~0.5 industry rule of thumb
# for approximating an elliptical load distribution.
assert math.isclose(bending_relief_factor("elliptical", taper_ratio=1.0), 2.0 / 3.0, rel_tol=1e-12)
assert math.isclose(bending_relief_factor("elliptical", taper_ratio=0.2), 2.0 / 3.0, rel_tol=1e-12)
try:
    bending_relief_factor("triangular", 1.0)
    raise AssertionError("unknown planform should be rejected")
except ValueError:
    pass

# wing_mass_g: bending_relief_factor multiplies straight through, default 1.0
# (today's behavior, unaffected by this new parameter).
base_mass = wing_mass_g(area_m2=2.0, aspect_ratio=AR_REF, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
relieved_mass = wing_mass_g(area_m2=2.0, aspect_ratio=AR_REF, areal_density_g_m2=1500.0, ar_mass_exponent=0.5,
                             bending_relief_factor=0.5)
assert math.isclose(relieved_mass, 0.5 * base_mass, rel_tol=1e-12)

# optimize_wing: taper_ratio=1.0 (the default) must give the exact same
# wing_mass_g as before this feature existed -- a rectangular wing's bending
# relief factor is 1.0, so nothing here should move for anyone's existing config.
rect = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                      max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
assert math.isclose(rect["wing_mass_g"], 1500.0 * rect["wing_area_m2"] * (4.0 / AR_REF) ** 0.5, rel_tol=1e-9)

# optimize_wing: taper_ratio=0.5 should now reduce wing_mass_g by exactly its
# bending_relief_factor relative to the untapered wing at the same AR/area
# (both hit min_ar=4 unconstrained, so AR/area are identical between the two).
tapered_relief = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                                max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5, taper_ratio=0.5)
assert math.isclose(tapered_relief["wing_mass_g"], rect["wing_mass_g"] * bending_relief_factor("linear", 0.5),
                     rel_tol=1e-9)

# elliptical_chord_m: root (y_frac=0) is the max chord, tip (y_frac=1) is
# zero, and the chord distribution integrates back to the original area
# (area = (pi/4) * root_chord * span for a true ellipse).
root_chord = elliptical_chord_m(0.0, root_chord_m=4.0)
tip_chord = elliptical_chord_m(1.0, root_chord_m=4.0)
assert math.isclose(root_chord, 4.0, rel_tol=1e-12)
assert math.isclose(tip_chord, 0.0, abs_tol=1e-9)
mid_chord = elliptical_chord_m(0.6, root_chord_m=4.0)
assert math.isclose(mid_chord, 4.0 * math.sqrt(1 - 0.6**2), rel_tol=1e-12)

# optimize_wing: elliptical planform. root_chord_m/span_m must satisfy the
# ellipse-area identity, tip_chord_m must be 0, and the mass must carry the
# elliptical bending_relief_factor (2/3) rather than the linear-taper one.
ell = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                     max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5, planform="elliptical")
assert math.isclose(ell["wing_area_m2"], rect["wing_area_m2"], rel_tol=1e-9), "planform must not change required area"
assert math.isclose(ell["tip_chord_m"], 0.0, abs_tol=1e-9)
assert math.isclose(math.pi / 4 * ell["root_chord_m"] * ell["span_m"], ell["wing_area_m2"], rel_tol=1e-9)
assert math.isclose(ell["wing_mass_g"], rect["wing_mass_g"] * bending_relief_factor("elliptical", 1.0), rel_tol=1e-9)

# elliptical planform + an explicit taper_ratio is a contradiction (a true
# ellipse's tip/root ratio is fixed at 0, not user-chosen) -- must be rejected.
try:
    optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                   max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5,
                   planform="elliptical", taper_ratio=0.5)
    raise AssertionError("elliptical planform with a non-default taper_ratio should be rejected")
except ValueError:
    pass

# parse_selig_dat: a minimal Selig-format (UIUC) airfoil .dat -- title line,
# then whitespace-separated chord-fraction "x y" pairs, real digitized
# geometry rather than an analytic NACA family. Uses a synthetic symmetric
# diamond shape (not a real airfoil) since only the parsing is under test.
sample_dat = """Test Diamond Airfoil
1.0 0.0
0.5 0.05
0.0 0.0
0.5 -0.05
1.0 0.0
"""
pts = parse_selig_dat(sample_dat)
assert pts == [(1.0, 0.0), (0.5, 0.05), (0.0, 0.0), (0.5, -0.05), (1.0, 0.0)]
assert len(pts) == 5, "title line must not be parsed as a coordinate"

try:
    parse_selig_dat("Just A Title\n1.0 0.0\n")
    raise AssertionError("a file with only one usable point should be rejected")
except ValueError:
    pass

# place_station_points: no twist/sweep should just scale by chord (m -> mm).
plain = place_station_points([(1.0, 0.0), (0.0, 0.0)], chord_value=0.5, units="m")
assert plain == [(500.0, 0.0, 0.0), (0.0, 0.0, 0.0)]

# Twist must match rotate_about_pivot() exactly -- same transform NACA's
# SolidWorks export already uses, just applied to raw coordinates instead of
# an equation string.
twisted = place_station_points([(1.0, 0.0)], chord_value=1.0, units="m", twist_deg=30.0)
expected_x, expected_y = rotate_about_pivot(1000.0, 0.0, pivot_x=250.0, twist_deg=30.0)
assert math.isclose(twisted[0][0], expected_x, rel_tol=1e-9)
assert math.isclose(twisted[0][1], expected_y, rel_tol=1e-9)
assert twisted[0][2] == 0.0

# Sweep offset must land purely in x, after any twist.
swept = place_station_points([(0.0, 0.0)], chord_value=1.0, units="m", sweep_offset_mm=42.0)
assert swept == [(42.0, 0.0, 0.0)]

# wing_stations: rectangular (untapered/unswept/untwisted linear planform)
# collapses to a single Root station at the mean chord.
rect_result = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                             max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5)
rect_stations = wing_stations(rect_result, planform="linear", elliptical_stations=6,
                               twist_deg=0.0, sweep_deg=0.0, tapered=False, swept_or_twisted=False)
assert len(rect_stations) == 1
assert rect_stations[0]["label"] == "Root"
assert math.isclose(rect_stations[0]["chord_m"], rect_result["chord_m"], rel_tol=1e-9)

# wing_stations: a linear taper gets exactly Root+Tip, at the root/tip chords
# optimize_wing() itself computed.
tap_result = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                            max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5, taper_ratio=0.5)
tap_stations = wing_stations(tap_result, planform="linear", elliptical_stations=6,
                              twist_deg=-4.0, sweep_deg=10.0, tapered=True, swept_or_twisted=True)
assert [s["label"] for s in tap_stations] == ["Root", "Tip"]
assert math.isclose(tap_stations[0]["chord_m"], tap_result["root_chord_m"], rel_tol=1e-9)
assert math.isclose(tap_stations[1]["chord_m"], tap_result["tip_chord_m"], rel_tol=1e-9)
assert tap_stations[0]["twist_deg"] == 0.0
assert tap_stations[1]["twist_deg"] == -4.0
assert tap_stations[0]["sweep_offset_mm"] == 0.0
assert tap_stations[1]["sweep_offset_mm"] > 0.0

# wing_stations: elliptical gets N stations, decreasing chord root->tip, with
# twist/sweep interpolated linearly by span fraction (0 at the root).
ell_result = optimize_wing(lift_mass_kg=12.0, cruise_mps=25.0, cl=0.6, min_ar=4.0, max_ar=10.0,
                            max_span_m=None, areal_density_g_m2=1500.0, ar_mass_exponent=0.5,
                            planform="elliptical")
ell_stations = wing_stations(ell_result, planform="elliptical", elliptical_stations=5,
                              twist_deg=-6.0, sweep_deg=15.0, tapered=False, swept_or_twisted=False)
assert len(ell_stations) == 5
assert math.isclose(ell_stations[0]["chord_m"], ell_result["root_chord_m"], rel_tol=1e-9)
assert ell_stations[0]["twist_deg"] == 0.0 and ell_stations[0]["sweep_offset_mm"] == 0.0
for a, b in zip(ell_stations, ell_stations[1:]):
    assert b["chord_m"] < a["chord_m"], "chord must strictly decrease root to tip on an ellipse"
    assert b["twist_deg"] < a["twist_deg"], "twist should ramp toward the (negative) tip value"
    assert b["sweep_offset_mm"] > a["sweep_offset_mm"]

print("wing_optimizer self-check: all assertions passed")
