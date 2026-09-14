"""Wing sizing/optimization for a winged VTOL, in the vechicle_configs convention
(wing_area_m2 / wing_cl). Standalone -- no import dependency on Mission_Model.py.
Run: python wing_optimizer.py --lift-mass-kg 12 --cruise-mps 25 --cl 0.6

Two objectives (--objective):

  mass (default): the wing area is not a free variable -- it's fixed by the
  lift equation given the required lift, cruise speed, and lift coefficient
  (all inputs, matching how Mission_Model.py's VehicleType.wing_cl is a fixed
  per-aircraft constant, not something solved for). The only free variable is
  aspect ratio: higher AR means a longer, thinner wing for the same area,
  which is more structurally demanding (longer bending moment arm). This
  grid-searches AR in a user-given range and picks the one that minimizes
  estimated structural mass, subject to an optional max-span cap (e.g. a
  trailer/hangar width limit). Without a binding span cap, the answer is
  always the lowest allowed AR -- there's no other force pushing AR up in
  this simple mass model, and the script says so rather than dressing up a
  constant as an optimum.

  drag: cl is *also* free (search --cl-min/--cl-max instead of a fixed
  --cl), searched jointly with aspect ratio to minimize total cruise drag
  (parasite + induced, via a Raymer-style Oswald-efficiency estimate) --
  higher AR now has a real reason to win (it lowers induced drag), not just
  a structural cost. --taper-ratio is supported (same bending-relief effect
  as the mass objective); --planform elliptical and --naca/--airfoil-dat
  export aren't wired into this objective yet.

Pass --naca (a 4-digit code, e.g. 2412) to also print SolidWorks Equation
Driven Curve equations for that airfoil at the optimized chord -- paste-ready
global variables plus parametric x(t)/y(t) for the upper and lower surfaces.
--out writes a complete, directly loadable VehicleType entry (see
--num-rotors/--base-mass-g/etc.) to a vehicle_configs-style JSON file.
"""

import argparse
import json
import math
import os

# Duplicated (not imported) from Mission_Model.py so this script has zero
# dependency on it -- keep these two in sync by hand if Mission_Model.py's
# ever change (test_wing_optimizer.py cross-checks them against it).
G = 9.807  # m/s^2
RHO_AIR = 1.225  # kg/m^3

# Same sanity ceiling vechicle_configs/README.md checks existing wings against:
# <=20 psf, i.e. wing loading (weight/area) shouldn't exceed ~97.65 kg/m^2.
MAX_WING_LOADING_KG_M2 = 97.65

AR_REF = 8.0  # aspect ratio the areal-density input is quoted at

UNIT_TO_MM = {"mm": 1.0, "m": 1000.0, "in": 25.4}


def naca4_params(code: str) -> tuple[float, float, float]:
    """Parse a 4-digit NACA code (e.g. "2412") into (max_camber, camber_pos,
    thickness) as chord fractions: m=0.02, p=0.4, t=0.12 for "2412". p is
    reported as 0.5 for a symmetric section (m=0, digit2="0") since the
    camber-line formula divides by p^2 -- irrelevant once m=0 zeroes it out,
    but a literal p=0 would be a division by zero in the emitted equations.
    """
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"NACA code must be 4 digits, got {code!r}")
    m = int(code[0]) / 100.0
    p = int(code[1]) / 10.0
    t = int(code[2:4]) / 100.0
    if m == 0.0:
        p = 0.5
    return m, p, t


def naca4_airfoil_point(xc: float, m: float, p: float, t: float, closed_te: bool = True) -> dict:
    """Upper/lower surface coordinates (chord fractions) at chord fraction xc,
    from the standard NACA 4-digit closed-form equations. Mirrors exactly the
    equations emitted for SolidWorks in solidworks_naca4_equations() below, so
    this is both the source of the CLI's sample-point table and the reference
    test_wing_optimizer.py checks it against.
    """
    a4 = -0.1036 if closed_te else -0.1015
    yt = 5 * t * (0.2969 * math.sqrt(xc) - 0.1260 * xc - 0.3516 * xc**2 + 0.2843 * xc**3 + a4 * xc**4)
    if xc < p:
        yc = (m / p**2) * (2 * p * xc - xc**2)
        dyc_dx = (2 * m / p**2) * (p - xc)
    else:
        yc = (m / (1 - p) ** 2) * ((1 - 2 * p) + 2 * p * xc - xc**2)
        dyc_dx = (2 * m / (1 - p) ** 2) * (p - xc)
    theta = math.atan(dyc_dx)
    return {
        "xu": xc - yt * math.sin(theta), "yu": yc + yt * math.cos(theta),
        "xl": xc + yt * math.sin(theta), "yl": yc - yt * math.cos(theta),
    }


def solidworks_naca4_equations(naca_code: str, chord_value: float, units: str = "mm",
                                closed_te: bool = True, twist_deg: float = 0.0,
                                twist_pivot_frac: float = 0.25, sweep_offset_mm: float = 0.0,
                                var_prefix: str = "") -> dict:
    """SolidWorks-ready global variables and Equation Driven Curve strings for
    a NACA 4-digit airfoil scaled to chord_value (in `units`, one of mm/m/in
    -- SolidWorks equations carry no unit tags, so the chord global variable
    is pre-converted to millimeters, SolidWorks' usual default document unit).

    Paste the global variables into Tools > Equations, then create two
    Equation Driven Curves (Sketch > Spline > Equation Driven Curve,
    parametric, t from 0 to 1) using the upper/lower x(t)/y(t) pairs. Cosine
    spacing (xc below) packs points near the leading edge, where curvature is
    highest, instead of spreading them evenly by t.

    SolidWorks equation syntax is VBA-flavored, not standard math: sqr() not
    sqrt(), atn() not atan(), and iif(cond, a, b) for the piecewise camber
    line -- all used literally below, so these paste in without edits.

    twist_deg rotates the whole profile about the chord-line point at
    twist_pivot_frac * chord (0.25 = quarter-chord, the usual twist axis) --
    positive twist_deg pitches the leading edge up, so a tip washout is
    negative. sweep_offset_mm is added directly to x(t) so a tip sketch
    placed on a plane offset only in the spanwise direction (not also
    shifted in X) still lands with its leading edge swept back correctly --
    see the CLI's printed placement note for the offset to use.

    var_prefix namespaces the global variable names (e.g. "tip_" ->
    "tip_chord", "tip_naca_m", ...) so a root and a tip station can both
    have their own global variables live in the same SolidWorks Equations
    list at once without colliding -- the x(t)/y(t) strings reference
    whichever prefixed names were actually declared, so this isn't just a
    display label, it's load-bearing for a two-station wing.
    """
    m, p, t = naca4_params(naca_code)
    chord_mm = chord_value * UNIT_TO_MM[units]
    a4 = "-0.1036" if closed_te else "-0.1015"

    chord_v, m_v, p_v, t_v, twist_v = (
        f"{var_prefix}chord", f"{var_prefix}naca_m", f"{var_prefix}naca_p", f"{var_prefix}naca_t", f"{var_prefix}twist",
    )

    global_vars = {
        chord_v: f'{chord_mm:.4f} (mm) "chord length"',
        m_v: f'{m:.4f} "max camber, fraction of chord"',
        p_v: f'{p:.4f} "camber position, fraction of chord"',
        t_v: f'{t:.4f} "max thickness, fraction of chord"',
    }
    if twist_deg != 0.0:
        global_vars[twist_v] = f'{twist_deg:.4f} (deg) "section twist, +LE up"'

    xc = "((1 - cos(pi * t)) / 2)"
    yt = f"(5 * {t_v} * (0.2969 * sqr({xc}) - 0.1260 * {xc} - 0.3516 * {xc}^2 + 0.2843 * {xc}^3 + {a4} * {xc}^4))"
    yc = f"iif({xc} < {p_v}, ({m_v} / {p_v}^2) * (2 * {p_v} * {xc} - {xc}^2), ({m_v} / (1 - {p_v})^2) * ((1 - 2 * {p_v}) + 2 * {p_v} * {xc} - {xc}^2))"
    dyc_dx = f"iif({xc} < {p_v}, (2 * {m_v} / {p_v}^2) * ({p_v} - {xc}), (2 * {m_v} / (1 - {p_v})^2) * ({p_v} - {xc}))"
    theta = f"atn({dyc_dx})"

    surfaces = {
        "upper": (f"{chord_v} * ({xc} - {yt} * sin({theta}))", f"{chord_v} * ({yc} + {yt} * cos({theta}))"),
        "lower": (f"{chord_v} * ({xc} + {yt} * sin({theta}))", f"{chord_v} * ({yc} - {yt} * cos({theta}))"),
    }

    result = {"global_variables": global_vars, "t_range": (0.0, 1.0)}
    for name, (x_expr, y_expr) in surfaces.items():
        if twist_deg != 0.0:
            pivot_x = f"({twist_pivot_frac:g} * {chord_v})"
            x_rot = f"({pivot_x} + ({x_expr} - {pivot_x}) * cos({twist_v}) - ({y_expr}) * sin({twist_v}))"
            y_rot = f"(({x_expr} - {pivot_x}) * sin({twist_v}) + ({y_expr}) * cos({twist_v}))"
            x_expr, y_expr = x_rot, y_rot
        if sweep_offset_mm != 0.0:
            x_expr = f"({sweep_offset_mm:.4f} + {x_expr})"
        result[f"{name}_x_of_t"] = x_expr
        result[f"{name}_y_of_t"] = y_expr
    return result


def parse_selig_dat(text: str) -> list[tuple[float, float]]:
    """Parse a Selig-format UIUC airfoil .dat file's content: a title line,
    then whitespace-separated chord-fraction "x y" pairs (upper surface
    trailing edge to leading edge, then lower surface leading edge to
    trailing edge, in the usual UIUC convention -- this just reads points in
    file order, it doesn't resort or validate topology). Real digitized
    coordinates are used exactly as given, not run through NACA's closed/
    open-TE coefficient logic -- that's specific to the analytic 4-digit
    family and has no meaning for arbitrary scanned/digitized geometry.
    """
    points = []
    for line in text.splitlines()[1:]:  # first line is the airfoil name/title
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(points) < 3:
        raise ValueError(
            f"expected a Selig-format airfoil .dat (title line + coordinate pairs), got {len(points)} usable point(s)"
        )
    return points


def place_station_points(coords_frac: list[tuple[float, float]], chord_value: float, units: str = "mm",
                          twist_deg: float = 0.0, twist_pivot_frac: float = 0.25,
                          sweep_offset_mm: float = 0.0) -> list[tuple[float, float, float]]:
    """Scale unit-chord (x, y) airfoil coordinates to chord_value (in
    `units`, converted to mm to match solidworks_naca4_equations()'s
    convention), apply twist about twist_pivot_frac * chord (the same
    rotate_about_pivot() transform that function uses for NACA), then add
    sweep_offset_mm to x. Returns (x, y, 0.0) triples ready for a SolidWorks
    "Curve Through XYZ Points" (.sldcrv) import -- Z=0 matches the NACA
    export's convention of putting all spanwise offset on the station's own
    sketch plane, not in a Z coordinate.
    """
    chord_mm = chord_value * UNIT_TO_MM[units]
    pivot_x = twist_pivot_frac * chord_mm
    out = []
    for xf, yf in coords_frac:
        x, y = xf * chord_mm, yf * chord_mm
        if twist_deg != 0.0:
            x, y = rotate_about_pivot(x, y, pivot_x, twist_deg)
        out.append((x + sweep_offset_mm, y, 0.0))
    return out


def write_sldcrv(points: list[tuple[float, float, float]], path: str) -> None:
    """Write a SolidWorks "Curve Through XYZ Points" file: one point per
    line, tab-separated X/Y/Z, in the same units the points were already
    scaled to (mm, matching this script's other SolidWorks output)."""
    with open(path, "w") as f:
        for x, y, z in points:
            f.write(f"{x:.6f}\t{y:.6f}\t{z:.6f}\n")


def wing_stations(result: dict, planform: str, elliptical_stations: int, twist_deg: float, sweep_deg: float,
                   tapered: bool, swept_or_twisted: bool) -> list[dict]:
    """The root/tip/N-station planform layout shared by both the NACA
    equation export and the airfoil-.dat point-cloud export: each entry is
    {label, chord_m, twist_deg, sweep_offset_mm}. A rectangular (untapered/
    unswept/untwisted linear) wing needs one station; a linear taper needs
    Root+Tip; an elliptical planform needs elliptical_stations of them
    (twist/sweep interpolated linearly by span fraction, 0 at the root) --
    see optimize_wing()'s planform handling for why an ellipse has no single
    taper_ratio to key off of instead.
    """
    half_span_mm = result["span_m"] / 2 * 1000
    if planform == "elliptical":
        n = max(elliptical_stations, 2)
        stations = []
        for i in range(n):
            y_frac = i / (n - 1)
            if i == n - 1:
                # A literal y_frac=1 station has zero chord -- nudge in from
                # the true tip so every station still has a real profile (a
                # practical build blends to a small square-cut tip here
                # rather than the mathematical point, same as real
                # elliptical wings do).
                y_frac = 1.0 - 1e-3
            stations.append({
                "label": f"S{i}",
                "chord_m": elliptical_chord_m(y_frac, result["root_chord_m"]),
                "twist_deg": twist_deg * y_frac,
                "sweep_offset_mm": y_frac * half_span_mm * math.tan(math.radians(sweep_deg)),
            })
        return stations
    if not tapered and not swept_or_twisted:
        return [{"label": "Root", "chord_m": result["chord_m"], "twist_deg": 0.0, "sweep_offset_mm": 0.0}]
    sweep_offset_mm = half_span_mm * math.tan(math.radians(sweep_deg))
    return [
        {"label": "Root", "chord_m": result["root_chord_m"], "twist_deg": 0.0, "sweep_offset_mm": 0.0},
        {"label": "Tip", "chord_m": result["tip_chord_m"], "twist_deg": twist_deg, "sweep_offset_mm": sweep_offset_mm},
    ]


def rotate_about_pivot(x: float, y: float, pivot_x: float, twist_deg: float) -> tuple[float, float]:
    """Rotate (x, y) by twist_deg (degrees, +LE up) about (pivot_x, 0) --
    the real-units twist transform solidworks_naca4_equations() emits as SW
    equations, kept here standalone so tests can check the two agree.
    """
    theta = math.radians(twist_deg)
    dx, dy = x - pivot_x, y
    return (
        pivot_x + dx * math.cos(theta) - dy * math.sin(theta),
        dx * math.sin(theta) + dy * math.cos(theta),
    )


def required_wing_area_m2(lift_mass_kg: float, cruise_mps: float, cl: float) -> float:
    lift_n = lift_mass_kg * G
    return lift_n / (0.5 * RHO_AIR * cl * cruise_mps * cruise_mps)


def wing_mass_g(area_m2: float, aspect_ratio: float, areal_density_g_m2: float, ar_mass_exponent: float,
                 bending_relief_factor: float = 1.0) -> float:
    return areal_density_g_m2 * area_m2 * (aspect_ratio / AR_REF) ** ar_mass_exponent * bending_relief_factor


def bending_relief_factor(planform: str, taper_ratio: float) -> float:
    """How much lighter a tapered/elliptical wing's structure can be than a
    rectangular one of the same area/AR, from the root bending moment a
    chord-proportional spanwise load produces (load ~ chord is the standard
    first-order assumption at a fixed cl). Integrating y*load(y) over the
    semi-span and normalizing to the rectangular case (taper_ratio=1) gives
    (1 + 2*taper_ratio) / 3 for a linear taper -- 1.0 at taper_ratio=1 (no
    change from today's model), down to 1/3 at taper_ratio=0 (a triangular
    planform). An elliptical load distribution's own integral comes out to
    2/3 exactly, independent of any taper_ratio -- which lands near the
    linear formula's taper_ratio~0.5 value, the well-known rule of thumb for
    approximating elliptical loading with a linear taper.
    """
    if planform == "linear":
        return (1.0 + 2.0 * taper_ratio) / 3.0
    if planform == "elliptical":
        return 2.0 / 3.0
    raise ValueError(f"planform must be 'linear' or 'elliptical', got {planform!r}")


def elliptical_chord_m(y_frac: float, root_chord_m: float) -> float:
    """Local chord at a fractional semi-span position (0=root, 1=tip) of a
    true elliptical planform: chord(y) = root_chord * sqrt(1 - y_frac^2).
    Integrating this over the semi-span and doubling gives the classic
    area = (pi/4) * root_chord * span identity optimize_wing() relies on."""
    return root_chord_m * math.sqrt(max(1.0 - y_frac * y_frac, 0.0))


def optimize_wing(lift_mass_kg: float, cruise_mps: float, cl: float, min_ar: float, max_ar: float,
                   max_span_m: float | None, areal_density_g_m2: float, ar_mass_exponent: float,
                   steps: int = 200, taper_ratio: float = 1.0, planform: str = "linear") -> dict:
    if taper_ratio <= 0.0:
        raise ValueError(f"taper_ratio must be > 0 (tip_chord/root_chord), got {taper_ratio}")
    if planform == "elliptical" and taper_ratio != 1.0:
        raise ValueError(
            "taper_ratio doesn't apply to an elliptical planform (its tip/root ratio is fixed at 0 "
            "by the ellipse itself, not user-chosen) -- leave taper_ratio at its default of 1.0"
        )
    relief = bending_relief_factor(planform, taper_ratio)
    area_m2 = required_wing_area_m2(lift_mass_kg, cruise_mps, cl)

    best = None
    for i in range(steps + 1):
        ar = min_ar + (max_ar - min_ar) * i / steps
        span_m = math.sqrt(ar * area_m2)
        if max_span_m is not None and span_m > max_span_m:
            continue
        mass_g = wing_mass_g(area_m2, ar, areal_density_g_m2, ar_mass_exponent, bending_relief_factor=relief)
        if best is None or mass_g < best["wing_mass_g"]:
            best = {"aspect_ratio": ar, "span_m": span_m, "wing_mass_g": mass_g}

    if best is None:
        raise ValueError(
            f"no aspect ratio in [{min_ar}, {max_ar}] keeps span <= {max_span_m} m "
            f"for the required area of {area_m2:.3f} m^2 -- raise --max-span-m or --max-ar bound won't help, "
            f"lower --min-ar or shrink the required area (higher --cl/--cruise-mps)."
        )

    best["wing_area_m2"] = area_m2
    best["planform"] = planform
    best["bending_relief_factor"] = relief
    # Mean chord from area/span holds regardless of taper (area = span *
    # mean_chord always); taper only changes how that mean splits between
    # root and tip (area = span * (root+tip)/2 for a linear taper; area =
    # span * (pi/4) * root_chord for a true ellipse, root_chord = 4*area/
    # (pi*span) -- see elliptical_chord_m()). bending_relief_factor above is
    # this model's only feedback from planform into the mass estimate; the
    # rest (e.g. induced-drag differences between planforms) still isn't
    # modeled here, only the resulting geometry is.
    best["chord_m"] = area_m2 / best["span_m"]
    if planform == "elliptical":
        best["taper_ratio"] = 0.0
        best["root_chord_m"] = 4.0 * area_m2 / (math.pi * best["span_m"])
        best["tip_chord_m"] = 0.0
    else:
        best["taper_ratio"] = taper_ratio
        best["root_chord_m"] = 2 * best["chord_m"] / (1 + taper_ratio)
        best["tip_chord_m"] = taper_ratio * best["root_chord_m"]
    best["wing_loading_kg_m2"] = lift_mass_kg / area_m2
    best["span_capped"] = max_span_m is not None and best["aspect_ratio"] < max_ar - 1e-9
    return best


def oswald_efficiency(aspect_ratio: float, sweep_deg: float = 0.0) -> float:
    """Raymer's empirical Oswald efficiency estimate for a straight or
    moderately swept subsonic wing -- the same formula
    MyProjectsMK/Wing_optimization uses for its drag-minimizing sizing:
    e0 = 4.61*(1 - 0.045*AR^0.68)*cos(sweep)^0.15 - 3.1. Not meant for
    delta/highly-swept planforms.
    """
    return 4.61 * (1 - 0.045 * aspect_ratio**0.68) * math.cos(math.radians(sweep_deg)) ** 0.15 - 3.1


def induced_drag_coefficient(cl: float, aspect_ratio: float, oswald_e: float) -> float:
    return cl * cl / (math.pi * aspect_ratio * oswald_e)


def optimize_wing_drag(lift_mass_kg: float, cruise_mps: float, cl_min: float, cl_max: float,
                        min_ar: float, max_ar: float, max_span_m: float | None,
                        parasite_cd0: float, sweep_deg: float = 0.0,
                        cl_steps: int = 100, ar_steps: int = 100) -> dict:
    """Grid-search (cl, aspect_ratio) jointly to minimize total cruise drag
    (parasite + induced, via oswald_efficiency()) -- the aerodynamic-depth
    counterpart to optimize_wing()'s structural-mass objective. Unlike
    optimize_wing(), cl isn't a fixed input here: it trades off against area
    through the lift equation (higher cl -> smaller area -> less parasite
    drag) and against induced drag directly (cdi ~ cl^2/AR), so both are
    free variables. cl_max both bounds the search and acts as the
    stall-margin ceiling -- keep it below the airfoil's real Cl_max with
    whatever margin you want; this does no separate stall check.
    """
    if cl_min <= 0.0 or cl_max <= cl_min:
        raise ValueError(f"need 0 < cl_min < cl_max, got cl_min={cl_min}, cl_max={cl_max}")

    best = None
    for ci in range(cl_steps + 1):
        cl = cl_min + (cl_max - cl_min) * ci / cl_steps
        area_m2 = required_wing_area_m2(lift_mass_kg, cruise_mps, cl)
        for ai in range(ar_steps + 1):
            ar = min_ar + (max_ar - min_ar) * ai / ar_steps
            span_m = math.sqrt(ar * area_m2)
            if max_span_m is not None and span_m > max_span_m:
                continue
            e0 = oswald_efficiency(ar, sweep_deg)
            cdi = induced_drag_coefficient(cl, ar, e0)
            drag_n = 0.5 * RHO_AIR * cruise_mps * cruise_mps * area_m2 * (parasite_cd0 + cdi)
            if best is None or drag_n < best["drag_n"]:
                best = {
                    "cl": cl, "aspect_ratio": ar, "span_m": span_m, "wing_area_m2": area_m2,
                    "oswald_efficiency": e0, "induced_cd": cdi, "parasite_cd0": parasite_cd0, "drag_n": drag_n,
                }

    if best is None:
        raise ValueError(
            f"no (cl, aspect_ratio) combination in cl=[{cl_min}, {cl_max}] x AR=[{min_ar}, {max_ar}] "
            f"keeps span <= {max_span_m} m for lift_mass_kg={lift_mass_kg} -- raise --max-span-m, "
            f"raise --cl-max (smaller required area), or lower --min-ar."
        )

    best["chord_m"] = best["wing_area_m2"] / best["span_m"]
    best["wing_loading_kg_m2"] = lift_mass_kg / best["wing_area_m2"]
    best["span_capped"] = max_span_m is not None and best["aspect_ratio"] < max_ar - 1e-9
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lift-mass-kg", type=float, required=True, help="weight the wing must offload from the rotors, kg")
    parser.add_argument("--cruise-mps", type=float, required=True, help="cruise airspeed the wing is sized for, m/s")
    parser.add_argument("--cl", type=float, default=None,
                         help="lift coefficient at cruise attitude (matches vehicle_configs wing_cl); "
                              "required for --objective mass, not accepted for --objective drag (there cl "
                              "is a search variable -- use --cl-min/--cl-max instead)")
    parser.add_argument("--objective", choices=["mass", "drag"], default="mass",
                         help="what to optimize (default mass): 'mass' fixes --cl and searches aspect ratio "
                              "for minimum structural mass (today's behavior); 'drag' searches --cl-min.."
                              "--cl-max and aspect ratio jointly for minimum total cruise drag (parasite + "
                              "induced, via an Oswald-efficiency estimate); --taper-ratio is supported, "
                              "not combinable with --planform elliptical, --naca, or --airfoil-dat")
    parser.add_argument("--cl-min", type=float, default=None, help="lowest lift coefficient to search (--objective drag only)")
    parser.add_argument("--cl-max", type=float, default=None,
                         help="highest lift coefficient to search (--objective drag only) -- also doubles as "
                              "the stall-margin ceiling, so keep it below the airfoil's real Cl_max with "
                              "whatever margin you want")
    parser.add_argument("--parasite-cd0", type=float, default=0.045,
                         help="zero-lift drag coefficient referenced to wing area, matching Mission_Model.py's "
                              "VehicleType.parasite_cd0 default (default 0.045); drives --objective drag's "
                              "drag estimate and is written into --out's vehicle entry when non-default")
    parser.add_argument("--min-ar", type=float, default=4.0, help="lowest aspect ratio to consider (default 4)")
    parser.add_argument("--max-ar", type=float, default=10.0, help="highest aspect ratio to consider (default 10)")
    parser.add_argument("--max-span-m", type=float, default=None, help="optional wingspan cap, e.g. a transport/storage limit")
    parser.add_argument("--areal-density-g-m2", type=float, default=1500.0,
                         help=f"structural mass per m^2 of wing area at AR={AR_REF:g} (default 1500 g/m^2)")
    parser.add_argument("--ar-mass-exponent", type=float, default=0.5,
                         help="how fast structural mass grows with aspect ratio above the reference (default 0.5, an estimate)")
    parser.add_argument("--name", default="OptimizedWing", help="name field for the emitted JSON entry")
    parser.add_argument("--out", default=None, help="optional path to a vehicle_configs-style JSON file to append the result to")
    parser.add_argument("--num-rotors", type=int, default=None, help="VehicleType.num_rotors for the --out entry (required if --out is given)")
    parser.add_argument("--base-mass-g", type=float, default=None, help="VehicleType.base_mass_g for the --out entry (required if --out is given)")
    parser.add_argument("--arm-mass-g", type=float, default=0.0, help="VehicleType.arm_mass_g for the --out entry (default 0.0, matching VehicleType's own default)")
    parser.add_argument("--drag-cd", type=float, default=1.28, help="VehicleType.drag_cd for the --out entry (default 1.28, matching VehicleType's own default)")
    parser.add_argument("--max-speed-mps", type=float, default=18.0, help="VehicleType.max_speed_mps for the --out entry (default 18.0, matching Mission_Model.py's DEFAULT_MAX_SPEED_MPS)")
    parser.add_argument("--naca", default=None,
                         help="4-digit NACA code (e.g. 2412) -- if given, also emits SolidWorks "
                              "Equation Driven Curve equations for the airfoil at the optimized chord. "
                              "Not accepted together with --airfoil-dat")
    parser.add_argument("--airfoil-dat", default=None,
                         help="path to a Selig-format (UIUC airfoil database) .dat coordinate file -- "
                              "an alternative to --naca for any real airfoil outside the analytic NACA "
                              "4-digit family (Clark-Y, Eppler, Selig/SD, etc.). Since digitized "
                              "coordinates have no closed-form equation, this emits SolidWorks "
                              "\"Curve Through XYZ Points\" (.sldcrv) files instead of Equation Driven "
                              "Curve equations -- one per station, written to --curve-points-out-dir. "
                              "Not accepted together with --naca")
    parser.add_argument("--curve-points-out-dir", default=None,
                         help="directory to write one .sldcrv point file per station into, for "
                              "--airfoil-dat (ignored otherwise); required if --airfoil-dat is given")
    parser.add_argument("--open-te", action="store_true",
                         help="use the standard open-trailing-edge NACA coefficient instead of the closed-TE one "
                              "(default closed, since an open TE leaves a ~0.002-chord gap a real part can't have)")
    parser.add_argument("--sw-eq-out", default=None,
                         help="optional path to write the SolidWorks equations to as plain text, ready to paste")
    parser.add_argument("--taper-ratio", type=float, default=1.0,
                         help="tip_chord/root_chord (default 1.0 = untapered/rectangular); "
                              "reduces the estimated wing mass via bending_relief_factor(), "
                              "not accepted together with --planform elliptical")
    parser.add_argument("--planform", choices=["linear", "elliptical"], default="linear",
                         help="wing planform shape (default linear = today's rectangular/tapered "
                              "geometry via --taper-ratio); elliptical fixes tip_chord_m to 0 and "
                              "gets its own bending_relief_factor, independent of --taper-ratio")
    parser.add_argument("--elliptical-stations", type=int, default=6,
                         help="number of NACA/SolidWorks stations along the semi-span for an "
                              "elliptical planform's loft (default 6); ignored for --planform linear")
    parser.add_argument("--sweep-deg", type=float, default=0.0,
                         help="leading-edge sweep angle, degrees (default 0); geometry-only, "
                              "only affects the --naca tip-plane placement note, not the mass estimate")
    parser.add_argument("--twist-deg", type=float, default=0.0,
                         help="tip section twist relative to the root, degrees, +LE up (default 0); "
                              "washout is negative. Geometry-only, applied about the quarter-chord")
    parser.add_argument("--geometry-out", default="wing_geometry.json",
                         help="path to a JSON file recording this run's full wing geometry (span/chord/AR/"
                              "taper/sweep/twist/NACA/inputs), keyed by --name -- reruns with the same --name "
                              "overwrite that entry rather than piling up duplicates (default wing_geometry.json "
                              "in the current directory); --out's vehicle_configs entry is a complete VehicleType "
                              "on its own (see --num-rotors etc.) but doesn't carry aspect_ratio/span/chord/NACA/"
                              "bending-relief, so this is the only record of those -- keep it OUT of a "
                              "vehicle_configs folder, since Mission_Model.py's load_vehicle_types() globs every "
                              "*.json there and expects each entry to be a full VehicleType. Pass an empty string "
                              "to skip writing it")
    args = parser.parse_args()
    if args.naca and args.airfoil_dat:
        parser.error("--naca and --airfoil-dat are alternatives -- pass one, not both")
    if args.airfoil_dat and not args.curve_points_out_dir:
        parser.error("--airfoil-dat needs --curve-points-out-dir to write its station point files to")
    if args.objective == "mass":
        if args.cl is None:
            parser.error("--cl is required for --objective mass")
    else:
        if args.cl_min is None or args.cl_max is None:
            parser.error("--cl-min and --cl-max are required for --objective drag (cl is a search variable there, not fixed)")
        if args.planform == "elliptical" or args.naca or args.airfoil_dat:
            parser.error("--objective drag only supports a linear (rectangular/tapered) planform today -- "
                          "drop --planform elliptical / --naca / --airfoil-dat")
    if args.taper_ratio <= 0.0:
        parser.error(f"--taper-ratio must be > 0 (tip_chord/root_chord), got {args.taper_ratio}")
    if args.out and (args.num_rotors is None or args.base_mass_g is None):
        parser.error("--num-rotors and --base-mass-g are required with --out -- VehicleType can't load an entry without them")

    if args.objective == "mass":
        result = optimize_wing(
            args.lift_mass_kg, args.cruise_mps, args.cl, args.min_ar, args.max_ar,
            args.max_span_m, args.areal_density_g_m2, args.ar_mass_exponent,
            taper_ratio=args.taper_ratio, planform=args.planform,
        )
        cl_used = args.cl
    else:
        result = optimize_wing_drag(
            args.lift_mass_kg, args.cruise_mps, args.cl_min, args.cl_max, args.min_ar, args.max_ar,
            args.max_span_m, args.parasite_cd0, sweep_deg=args.sweep_deg,
        )
        cl_used = result["cl"]
        # Taper isn't fed into the drag search itself (this model's induced-drag
        # estimate doesn't depend on taper, same as optimize_wing()'s mass search
        # doesn't feed taper into AR) -- it's applied after the fact to split the
        # winning mean chord into root/tip and to relieve the mass estimate, same
        # math optimize_wing() uses for its own taper_ratio handling.
        relief = bending_relief_factor("linear", args.taper_ratio)
        result["wing_mass_g"] = wing_mass_g(result["wing_area_m2"], result["aspect_ratio"],
                                             args.areal_density_g_m2, args.ar_mass_exponent, bending_relief_factor=relief)
        result["planform"] = "linear"
        result["taper_ratio"] = args.taper_ratio
        result["bending_relief_factor"] = relief
        result["root_chord_m"] = 2 * result["chord_m"] / (1 + args.taper_ratio)
        result["tip_chord_m"] = args.taper_ratio * result["root_chord_m"]

    elliptical = args.planform == "elliptical"
    tapered = args.taper_ratio != 1.0
    swept_or_twisted = args.sweep_deg != 0.0 or args.twist_deg != 0.0

    if args.objective == "drag":
        print(f"cl (chosen):        {result['cl']:.4f}   (searched {args.cl_min:g} to {args.cl_max:g})")
    print(f"wing_area_m2:       {result['wing_area_m2']:.4f}")
    print(f"aspect_ratio:       {result['aspect_ratio']:.3f}"
          + ("" if result["span_capped"] or args.objective == "drag"
             else "  (unconstrained -- equals --min-ar; nothing in this model favors higher AR without a span cap)"))
    print(f"span_m:             {result['span_m']:.3f}")
    print(f"chord_m:            {result['chord_m']:.3f}" + ("  (mean chord)" if tapered or elliptical else ""))
    if tapered or elliptical:
        print(f"root_chord_m:       {result['root_chord_m']:.3f}")
        print(f"tip_chord_m:        {result['tip_chord_m']:.3f}")
    if result["bending_relief_factor"] != 1.0:
        print(f"bending_relief:     {result['bending_relief_factor']:.3f}x wing_mass_g vs. an untapered/rectangular wing")
    print(f"wing_mass_g:        {result['wing_mass_g']:.1f}")
    print(f"wing_loading_kg_m2: {result['wing_loading_kg_m2']:.2f}"
          + (f"  WARNING: exceeds {MAX_WING_LOADING_KG_M2:g} kg/m^2 sanity limit" if result["wing_loading_kg_m2"] > MAX_WING_LOADING_KG_M2 else ""))
    if args.objective == "drag":
        print(f"oswald_efficiency:  {result['oswald_efficiency']:.4f}")
        print(f"induced_cd:         {result['induced_cd']:.5f}")
        print(f"parasite_cd0:       {result['parasite_cd0']:.4f}")
        print(f"drag_n:             {result['drag_n']:.4f}   (at cruise, parasite + induced)")
    if args.sweep_deg:
        print(f"sweep_deg (LE):     {args.sweep_deg:.2f}")
    if args.twist_deg:
        print(f"twist_deg (tip):    {args.twist_deg:.2f}")

    if args.out:
        entry = {
            "name": args.name, "num_rotors": args.num_rotors, "base_mass_g": args.base_mass_g,
            "arm_mass_g": args.arm_mass_g, "drag_cd": args.drag_cd,
            "wing_area_m2": round(result["wing_area_m2"], 4), "wing_cl": round(cl_used, 4),
            "max_speed_mps": args.max_speed_mps,
        }
        if args.parasite_cd0 != 0.045:
            entry["parasite_cd0"] = args.parasite_cd0
        entries = []
        if os.path.exists(args.out):
            with open(args.out) as f:
                entries = json.load(f)
        entries.append(entry)
        with open(args.out, "w") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
        print(f"appended entry to {args.out}")

    if args.geometry_out:
        record = {
            "name": args.name,
            "inputs": {
                "lift_mass_kg": args.lift_mass_kg, "cruise_mps": args.cruise_mps, "cl": round(cl_used, 4),
                "objective": args.objective, "cl_min": args.cl_min, "cl_max": args.cl_max,
                "parasite_cd0": args.parasite_cd0,
                "min_ar": args.min_ar, "max_ar": args.max_ar, "max_span_m": args.max_span_m,
                "areal_density_g_m2": args.areal_density_g_m2, "ar_mass_exponent": args.ar_mass_exponent,
                "taper_ratio": args.taper_ratio, "planform": args.planform,
                "sweep_deg": args.sweep_deg, "twist_deg": args.twist_deg,
                "naca": args.naca, "closed_te": not args.open_te, "airfoil_dat": args.airfoil_dat,
            },
            "wing_area_m2": round(result["wing_area_m2"], 4),
            "aspect_ratio": round(result["aspect_ratio"], 4),
            "span_m": round(result["span_m"], 4),
            "chord_m": round(result["chord_m"], 4),
            "root_chord_m": round(result["root_chord_m"], 4),
            "tip_chord_m": round(result["tip_chord_m"], 4),
            "wing_mass_g": round(result["wing_mass_g"], 2),
            "bending_relief_factor": round(result["bending_relief_factor"], 4),
            "wing_loading_kg_m2": round(result["wing_loading_kg_m2"], 3),
        }
        records = []
        if os.path.exists(args.geometry_out):
            with open(args.geometry_out) as f:
                records = json.load(f)
        records = [r for r in records if r["name"] != args.name]  # upsert by name, not append-forever
        records.append(record)
        with open(args.geometry_out, "w") as f:
            json.dump(records, f, indent=2)
            f.write("\n")
        print(f"recorded full wing geometry for {args.name!r} to {args.geometry_out}")

    stations = wing_stations(result, args.planform, args.elliptical_stations, args.twist_deg, args.sweep_deg,
                              tapered, swept_or_twisted) if (args.naca or args.airfoil_dat) else []

    def loft_note(n: int) -> str:
        half_span_mm = result["span_m"] / 2 * 1000
        if n == 1:
            return "# Rectangular planform, same profile at root and tip -- one station needed."
        if elliptical:
            return (
                f"# Elliptical planform: {n} stations, lofted through all of them in order (a 2-station\n"
                f"# loft can't approximate a continuously-curving edge). Place station i's sketch on a\n"
                f"# plane offset i/{n - 1} * {half_span_mm:.2f} mm from the root plane along the span axis\n"
                f"# only -- sweep is baked into each station's own placement below instead."
            )
        return (
            f"# Tapered/swept/twisted wing: two stations, lofted between them. Place the tip\n"
            f"# sketch on a plane offset {half_span_mm:.2f} mm from the root plane along the span\n"
            f"# axis only (no X shift on the plane itself -- sweep is baked into the tip's\n"
            f"# placement below instead)."
        )

    def loft_close_note(n: int) -> str:
        target = "the elliptical panel" if elliptical else "the tapered/swept/twisted panel"
        return (
            f"# Loft (Insert > Boss/Base > Loft) through all {n} station profiles in span order\n"
            f"# (upper+lower curves knitted into one contour each) to get {target};\n"
            "# mirror it about the root plane for the other half-span."
        )

    if args.naca:
        closed_te = not args.open_te
        m, p, t = naca4_params(args.naca)
        lines = [loft_note(len(stations)), ""]

        for station in stations:
            eqs = solidworks_naca4_equations(args.naca, station["chord_m"], units="m", closed_te=closed_te,
                                              twist_deg=station["twist_deg"], sweep_offset_mm=station["sweep_offset_mm"],
                                              var_prefix=f"{station['label'].lower()}_")
            lines.append(
                f"## {station['label']}: NACA {args.naca} at chord = {station['chord_m']:.4f} m "
                f"({station['chord_m'] * 1000:.2f} mm)"
                + (f", twist {station['twist_deg']:+.2f} deg about quarter-chord" if station["twist_deg"] else "")
                + (f", sweep offset {station['sweep_offset_mm']:+.2f} mm" if station["sweep_offset_mm"] else "")
            )
            lines.append("# SolidWorks Tools > Equations (global variables):")
            for name, expr in eqs["global_variables"].items():
                lines.append(f'"{name}" = {expr}')
            lines += [
                "# Sketch > Spline > Equation Driven Curve, parametric, t from 0 to 1:",
                "# Upper surface:",
                f"x(t) = {eqs['upper_x_of_t']}",
                f"y(t) = {eqs['upper_y_of_t']}",
                "# Lower surface:",
                f"x(t) = {eqs['lower_x_of_t']}",
                f"y(t) = {eqs['lower_y_of_t']}",
                "",
            ]
        if len(stations) > 1:
            lines.append(loft_close_note(len(stations)))

        print()
        print("\n".join(lines))

        print("\n# sample points (chord fractions, untwisted/unswept -- root and tip only differ by chord scale"
              " unless twist/sweep is set):")
        print("# xc      xu       yu       xl       yl")
        for xc in (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0):
            pt = naca4_airfoil_point(xc, m, p, t, closed_te=closed_te)
            print(f"# {xc:.3f}   {pt['xu']:+.4f}  {pt['yu']:+.4f}  {pt['xl']:+.4f}  {pt['yl']:+.4f}")

        if args.sw_eq_out:
            with open(args.sw_eq_out, "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"\nwrote SolidWorks equations to {args.sw_eq_out}")

    elif args.airfoil_dat:
        with open(args.airfoil_dat) as f:
            coords_frac = parse_selig_dat(f.read())
        airfoil_name = os.path.splitext(os.path.basename(args.airfoil_dat))[0]

        print()
        print(loft_note(len(stations)))
        print(f"# Airfoil: {airfoil_name!r} ({len(coords_frac)} digitized points from {args.airfoil_dat}) --")
        print("# not a NACA analytic family, so each station below is a point cloud for SolidWorks'")
        print("# \"Curve Through XYZ Points\" import (Insert > Curve > Curve Through XYZ Points),")
        print("# not an Equation Driven Curve.")
        print()
        os.makedirs(args.curve_points_out_dir, exist_ok=True)
        for station in stations:
            points = place_station_points(coords_frac, station["chord_m"], units="m",
                                           twist_deg=station["twist_deg"], sweep_offset_mm=station["sweep_offset_mm"])
            out_path = os.path.join(args.curve_points_out_dir, f"{args.name}_{station['label']}.sldcrv")
            write_sldcrv(points, out_path)
            print(
                f"## {station['label']}: chord = {station['chord_m']:.4f} m ({station['chord_m'] * 1000:.2f} mm)"
                + (f", twist {station['twist_deg']:+.2f} deg" if station["twist_deg"] else "")
                + (f", sweep offset {station['sweep_offset_mm']:+.2f} mm" if station["sweep_offset_mm"] else "")
                + f" -> {out_path} ({len(points)} points)"
            )
        if len(stations) > 1:
            print()
            print(loft_close_note(len(stations)))


if __name__ == "__main__":
    main()
