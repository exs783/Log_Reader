"""Builds the two JSON blobs the "Setup Explorer" artifact embeds: per-config
mission results (from mission_model_results.csv) and per-vehicle wing
geometry (from vechicle_configs/*.json + wing_geometry.json, if
wing_optimizer.py --geometry-out has recorded any -- kept OUTSIDE
vechicle_configs/ deliberately: Mission_Model.py's load_vehicle_types() globs
every *.json in that folder and expects each entry to be a full VehicleType,
so a geometry-record file living there breaks the vehicle-config load). Run:
    python build_artifact_data.py --setup-data-out /tmp/setup_data.json --wing-data-out /tmp/wing_data.json
Then splice both <script> tags into the artifact HTML and republish.

A vehicle with wing_area_m2 > 0 but no matching wing_geometry.json entry
shows up with geometry=null -- this script never invents an aspect ratio,
airfoil section, or planform shape for a wing whose design was never
actually recorded (the original TailSitter_OptWing.json entries predate
--geometry-out and are exactly this case: real wing_area_m2/wing_cl, no
record of what AR/section produced that area).
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Mission_Model import load_vehicle_types
from wing_optimizer import naca4_params, naca4_airfoil_point


def build_setup_data(csv_path: str) -> list[dict]:
    rows = list(csv.DictReader(open(csv_path)))
    out = []
    for r in rows:
        out.append({
            "vehicle": r["vehicle"],
            "motor": r["motor_model"],
            "prop_in": round(float(r["prop_diameter_in"]), 1),
            "battery": r["battery"],
            "batt_mah": int(float(r["battery_capacity_mah"])),
            "cells_s": int(float(r["cells_s"])),
            "mass_kg": round(float(r["total_mass_g"]) / 1000, 2),
            "airframe_kg": round(float(r["airframe_mass_g"]) / 1000, 2),
            "propulsion_kg": round(float(r["propulsion_mass_g"]) / 1000, 2),
            "cost": round(float(r["total_cost_usd"]), 2),
            "hover_min": round(float(r["hover_time_min"]), 2),
            "feasible": r["mission_feasible"] == "True",
            "time_min": round(float(r["mission_total_time_s"]) / 60, 2),
            "energy_mah": round(float(r["mission_total_energy_mah"]), 2),
            "margin_mah": round(float(r["mission_energy_remaining_mah"]), 2),
            "th_to": [round(float(r.get(f"throttle_takeoff{i}") or 0), 1) for i in (1, 2, 3)],
            "th_cr": [round(float(r.get(f"throttle_cruise{i}") or 0), 1) for i in (1, 2, 3)],
        })
    return out


def airfoil_side_cut_points(naca: str, root_chord_m: float, n: int = 48) -> list[list[float]]:
    """Closed-loop [x_mm, y_mm] outline of the root-chord profile: upper
    surface leading-to-trailing edge, then lower surface back to the
    leading edge. Cosine-spaced, matching the SolidWorks curve's spacing.
    """
    import math
    m, p, t = naca4_params(naca)
    chord_mm = root_chord_m * 1000
    xcs = [(1 - math.cos(math.pi * i / n)) / 2 for i in range(n + 1)]
    upper = [naca4_airfoil_point(xc, m, p, t) for xc in xcs]
    lower = list(reversed(upper))
    points = [[pt["xu"] * chord_mm, pt["yu"] * chord_mm] for pt in upper]
    points += [[pt["xl"] * chord_mm, pt["yl"] * chord_mm] for pt in lower]
    return [[round(x, 2), round(y, 2)] for x, y in points]


def build_wing_data(vehicle_configs_dir: str, geometry_path: str) -> dict:
    vehicles = load_vehicle_types(vehicle_configs_dir)
    geometry_by_name = {}
    if os.path.exists(geometry_path):
        with open(geometry_path) as f:
            for rec in json.load(f):
                geometry_by_name[rec["name"]] = rec

    wing_data = {}
    for v in vehicles:
        if not v.wing_area_m2:
            continue
        entry = {"wing_area_m2": v.wing_area_m2, "wing_cl": v.wing_cl, "geometry": None, "airfoil_points": None}
        rec = geometry_by_name.get(v.name)
        if rec:
            entry["geometry"] = {
                "aspect_ratio": rec["aspect_ratio"], "span_m": rec["span_m"], "chord_m": rec["chord_m"],
                "root_chord_m": rec["root_chord_m"], "tip_chord_m": rec["tip_chord_m"],
                "taper_ratio": rec["inputs"]["taper_ratio"], "sweep_deg": rec["inputs"]["sweep_deg"],
                "twist_deg": rec["inputs"]["twist_deg"], "naca": rec["inputs"]["naca"],
                "wing_mass_g": rec["wing_mass_g"], "wing_loading_kg_m2": rec["wing_loading_kg_m2"],
            }
            if rec["inputs"]["naca"]:
                entry["airfoil_points"] = airfoil_side_cut_points(rec["inputs"]["naca"], rec["root_chord_m"])
        wing_data[v.name] = entry
    return wing_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-csv", default="mission_model_results.csv")
    parser.add_argument("--vehicle-dir", default="vechicle_configs")
    parser.add_argument("--geometry-file", default="wing_geometry.json")
    parser.add_argument("--setup-data-out", required=True)
    parser.add_argument("--wing-data-out", required=True)
    args = parser.parse_args()

    geometry_path = args.geometry_file

    setup_data = build_setup_data(args.results_csv)
    with open(args.setup_data_out, "w") as f:
        json.dump(setup_data, f, separators=(",", ":"))
    print(f"wrote {len(setup_data)} config rows to {args.setup_data_out}")

    wing_data = build_wing_data(args.vehicle_dir, geometry_path)
    with open(args.wing_data_out, "w") as f:
        json.dump(wing_data, f, separators=(",", ":"))
    recorded = sum(1 for v in wing_data.values() if v["geometry"])
    print(f"wrote wing data for {len(wing_data)} winged vehicle(s) ({recorded} with recorded geometry) to {args.wing_data_out}")


if __name__ == "__main__":
    main()
