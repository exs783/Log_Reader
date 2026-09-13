"""Wing sizing/optimization for a winged VTOL, in the vechicle_configs convention
(wing_area_m2 / wing_cl). Run: python wing_optimizer.py --lift-mass-kg 12 --cruise-mps 25 --cl 0.6

The wing area is not a free variable -- it's fixed by the lift equation given
the required lift, cruise speed, and lift coefficient (all inputs, matching
how Mission_Model.py's VehicleType.wing_cl is a fixed per-aircraft constant,
not something solved for). The only free variable is aspect ratio: higher AR
means a longer, thinner wing for the same area, which is more structurally
demanding (longer bending moment arm) but not modeled elsewhere in this repo.
This script grid-searches AR in a user-given range and picks the one that
minimizes estimated structural mass, subject to an optional max-span cap
(e.g. a trailer/hangar width limit). Without a binding span cap, the answer
is always the lowest allowed AR -- there's no other force pushing AR up in
this simple mass model, and the script says so rather than dressing up a
constant as an optimum.

Pass --naca (a 4-digit code, e.g. 2412) to also print SolidWorks Equation
Driven Curve equations for that airfoil at the optimized chord -- paste-ready
global variables plus parametric x(t)/y(t) for the upper and lower surfaces.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Mission_Model import G, RHO_AIR

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lift-mass-kg", type=float, required=True, help="weight the wing must offload from the rotors, kg")
    parser.add_argument("--cruise-mps", type=float, required=True, help="cruise airspeed the wing is sized for, m/s")
    parser.add_argument("--cl", type=float, required=True, help="lift coefficient at cruise attitude (matches vehicle_configs wing_cl)")
    parser.add_argument("--min-ar", type=float, default=4.0, help="lowest aspect ratio to consider (default 4)")
    parser.add_argument("--max-ar", type=float, default=10.0, help="highest aspect ratio to consider (default 10)")
    parser.add_argument("--max-span-m", type=float, default=None, help="optional wingspan cap, e.g. a transport/storage limit")
    parser.add_argument("--areal-density-g-m2", type=float, default=1500.0,
                         help=f"structural mass per m^2 of wing area at AR={AR_REF:g} (default 1500 g/m^2)")
    parser.add_argument("--ar-mass-exponent", type=float, default=0.5,
                         help="how fast structural mass grows with aspect ratio above the reference (default 0.5, an estimate)")
    parser.add_argument("--name", default="OptimizedWing", help="name field for the emitted JSON entry")
    parser.add_argument("--out", default=None, help="optional path to a vehicle_configs-style JSON file to append the result to")
    parser.add_argument("--naca", default=None,
                         help="4-digit NACA code (e.g. 2412) -- if given, also emits SolidWorks "
                              "Equation Driven Curve equations for the airfoil at the optimized chord")
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
                              "in the current directory). --out's vehicle_configs entry only ever carries "
                              "wing_area_m2/wing_cl (VehicleType's loader rejects any other field), so this is "
                              "the only record of the rest -- keep it OUT of a vehicle_configs folder, since "
                              "Mission_Model.py's load_vehicle_types() globs every *.json there and expects each "
                              "entry to be a full VehicleType. Pass an empty string to skip writing it")
    args = parser.parse_args()

    result = optimize_wing(
        args.lift_mass_kg, args.cruise_mps, args.cl, args.min_ar, args.max_ar,
        args.max_span_m, args.areal_density_g_m2, args.ar_mass_exponent,
        taper_ratio=args.taper_ratio, planform=args.planform,
    )
    elliptical = args.planform == "elliptical"
    tapered = args.taper_ratio != 1.0
    swept_or_twisted = args.sweep_deg != 0.0 or args.twist_deg != 0.0

    print(f"wing_area_m2:       {result['wing_area_m2']:.4f}")
    print(f"aspect_ratio:       {result['aspect_ratio']:.3f}"
          + ("" if result["span_capped"] else "  (unconstrained -- equals --min-ar; nothing in this model favors higher AR without a span cap)"))
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
    if args.sweep_deg:
        print(f"sweep_deg (LE):     {args.sweep_deg:.2f}")
    if args.twist_deg:
        print(f"twist_deg (tip):    {args.twist_deg:.2f}")

    if args.out:
        entry = {"name": args.name, "wing_area_m2": round(result["wing_area_m2"], 4), "wing_cl": args.cl}
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
                "lift_mass_kg": args.lift_mass_kg, "cruise_mps": args.cruise_mps, "cl": args.cl,
                "min_ar": args.min_ar, "max_ar": args.max_ar, "max_span_m": args.max_span_m,
                "areal_density_g_m2": args.areal_density_g_m2, "ar_mass_exponent": args.ar_mass_exponent,
                "taper_ratio": args.taper_ratio, "planform": args.planform,
                "sweep_deg": args.sweep_deg, "twist_deg": args.twist_deg,
                "naca": args.naca, "closed_te": not args.open_te,
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

    if args.naca:
        closed_te = not args.open_te
        m, p, t = naca4_params(args.naca)
        lines = []

        def station_block(label: str, chord_m: float, twist_deg: float, sweep_offset_mm: float) -> list[str]:
            eqs = solidworks_naca4_equations(args.naca, chord_m, units="m", closed_te=closed_te,
                                              twist_deg=twist_deg, sweep_offset_mm=sweep_offset_mm,
                                              var_prefix=f"{label.lower()}_")
            out = [
                f"## {label}: NACA {args.naca} at chord = {chord_m:.4f} m ({chord_m * 1000:.2f} mm)"
                + (f", twist {twist_deg:+.2f} deg about quarter-chord" if twist_deg else "")
                + (f", sweep offset {sweep_offset_mm:+.2f} mm" if sweep_offset_mm else ""),
                "# SolidWorks Tools > Equations (global variables):",
            ]
            for name, expr in eqs["global_variables"].items():
                out.append(f'"{name}" = {expr}')
            out += [
                "# Sketch > Spline > Equation Driven Curve, parametric, t from 0 to 1:",
                "# Upper surface:",
                f"x(t) = {eqs['upper_x_of_t']}",
                f"y(t) = {eqs['upper_y_of_t']}",
                "# Lower surface:",
                f"x(t) = {eqs['lower_x_of_t']}",
                f"y(t) = {eqs['lower_y_of_t']}",
                "",
            ]
            return out

        if elliptical:
            n = max(args.elliptical_stations, 2)
            half_span_mm = result["span_m"] / 2 * 1000
            lines.append(
                f"# Elliptical planform: {n} stations, lofted through all of them in order (a 2-station\n"
                f"# loft can't approximate a continuously-curving edge). Place station i's sketch on a\n"
                f"# plane offset i/{n - 1} * {half_span_mm:.2f} mm from the root plane along the span axis\n"
                f"# only -- sweep is baked into each station's x(t) below instead."
            )
            lines.append("")
            for i in range(n):
                y_frac = i / (n - 1)
                chord_m = elliptical_chord_m(y_frac, result["root_chord_m"])
                if i == n - 1:
                    # A literal y_frac=1 station has zero chord -- no airfoil curve exists there.
                    # Nudge the last station in from the true tip so it still lofts to a real
                    # (very small) profile; a practical build blends to a small square-cut tip
                    # here rather than the mathematical point, same as real elliptical wings do.
                    y_frac = 1.0 - 1e-3
                    chord_m = elliptical_chord_m(y_frac, result["root_chord_m"])
                twist_deg = args.twist_deg * y_frac
                sweep_offset_mm = y_frac * half_span_mm * math.tan(math.radians(args.sweep_deg))
                lines += station_block(f"S{i}", chord_m, twist_deg, sweep_offset_mm)
            lines.append(
                f"# Loft (Insert > Boss/Base > Loft) through all {n} station profiles in span order\n"
                "# (upper+lower curves knitted into one contour each) to get the elliptical panel;\n"
                "# mirror it about the root plane for the other half-span."
            )
        elif not tapered and not swept_or_twisted:
            lines.append(f"# Rectangular planform, same profile at root and tip -- one station needed.")
            lines += station_block("Root", result["chord_m"], 0.0, 0.0)
        else:
            half_span_mm = result["span_m"] / 2 * 1000
            sweep_offset_mm = half_span_mm * math.tan(math.radians(args.sweep_deg))
            lines.append(
                f"# Tapered/swept/twisted wing: two stations, lofted between them. Place the tip\n"
                f"# sketch on a plane offset {half_span_mm:.2f} mm from the root plane along the span\n"
                f"# axis only (no X shift on the plane itself -- sweep is baked into the tip's x(t)\n"
                f"# below instead)."
            )
            lines.append("")
            lines += station_block("Root", result["root_chord_m"], 0.0, 0.0)
            lines += station_block("Tip", result["tip_chord_m"], args.twist_deg, sweep_offset_mm)
            lines.append(
                "# Loft (Insert > Boss/Base > Loft) between the Root and Tip closed profiles\n"
                "# (upper+lower curves knitted into one contour each) to get the tapered/swept/\n"
                "# twisted panel; mirror it about the root plane for the other half-span."
            )

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


if __name__ == "__main__":
    main()
