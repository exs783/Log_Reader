"""VTOL mission-performance model. Python port of VTOLMissionModel1.m.

Loads per-motor/prop thrust & current curves from JSON files, then for every
vehicle type x motor/prop x battery combination computes hover time and
simulates a fixed mission course, optimizing throttle at each takeoff and
cruise leg.

Changes vs. the MATLAB original (see inline notes at each site):
  - hoverTime compared thrust against a hard-coded 11363 g constant instead
    of the total_mass it had just computed -- fixed to use the real weight.
  - takeoff/segment/landing never included the extra arm mass added for
    props > 18in (hoverTime used 22.5 g/in, the plotting code used a
    different 33.5 g/in) -- unified into one constant applied everywhere.
  - segment()'s drag-area correction was applied *inside* the integration
    loop and multiplied into itself every step, compounding without bound.
    Computed once instead (throttle, and thus the tilt angle, is constant
    over a phase for wingless vehicles -- see the wing note below for why
    it can vary for winged ones).
  - Every fixed-step Euler integration (takeoff/cruise accel/decel) for a
    wingless vehicle is replaced with the closed-form solution of
    dv/dt = a0 - k*v^2 (thrust vs. quadratic drag). Same physics, exact
    instead of step-size-dependent, and O(1) per call instead of thousands
    of loop iterations.
  - landing() prescribes a constant descent speed and never used its
    computed acceleration, so its stepping loop was replaced with direct
    distance/speed kinematics (same answer, no discretization bias).
  - Motor/prop performance curves are interpolated once directly from the
    raw data points (PCHIP) and reused for every lookup, instead of being
    resampled onto a 61-point grid and re-interpolated from *that* at
    runtime (interpolating an interpolation adds avoidable error).
  - hoverTime's required-thrust throttle is solved with root-finding
    (brentq) against the continuous curve instead of taking the first
    point on a discrete 1%-step grid.
  - Rotor count, frame mass, drag, and (new) wings are pulled out of global
    constants into a VehicleType so multiple aircraft configs can be swept
    in one run, not just one hard-coded multirotor.
  - Wing lift (0.5*rho*CL*S*v^2) is subtracted from the weight the rotors
    must support during cruise. For a wingless vehicle this is always zero
    and the fast closed-form path above still applies. For a winged one,
    thrust_horizontal grows (and required tilt shrinks) as airspeed builds,
    which makes the accel-phase ODE velocity-coupled and no longer
    closed-form -- that one phase, for winged vehicles only, is solved
    numerically (scipy.solve_ivp) instead.
  - The MATLAB course (timed laps, maximize lap count, patternsearch
    MultiStart) is replaced by a fixed one-pass course: takeoff/100ft,
    cruise 1nm, land, pick up cargo, takeoff, cruise 8nm, land, drop cargo,
    takeoff, cruise 1nm, land. There's no lap-count or time-limit objective
    for a one-pass course, so throttle is instead chosen to maximize
    leftover battery energy. Because energy is additive across phases and
    nothing here couples one phase's time budget to another's, maximizing
    total leftover energy is equivalent to independently minimizing energy
    on each of the 6 powered phases (3 takeoffs + 3 cruise legs) -- so
    instead of one expensive joint throttle search, this does 6 cheap 1-D
    throttle sweeps (landing throttle stays prescribed/unoptimized, as in
    the original).
  - VehicleType gained arm_mass_g (a flat extra structural mass, independent
    of prop size -- distinct from arm_extra_mass_per_in, which is the
    *additional* mass for oversized props) and cruise_velocity_mps (an
    airframe can target a forward cruise speed other than the inherited
    10 m/s default -- a fixed-wing-ish hybrid is typically designed around
    a much faster cruise point than a hovering multirotor).
  - The motor-config folder, vehicle types, and batteries are all run-time
    inputs now rather than hard-coded: pass --data-dir/--vehicle-file/
    --battery-file, or leave them off and the script prompts for each
    (blank vehicle/battery prompts fall back to a single built-in default
    vehicle and battery lineup). See load_vehicle_types(),
    load_motor_configs(), and load_batteries() for the JSON schemas.
  - Motor configs moved from CSV (metadata packed into the filename, e.g.
    MotorModel_PropD_MotorMass_PropMass_MotorCost_PropCost.csv) to JSON
    (all fields named explicitly inside the object) -- same folder-of-files
    convention as vehicle types, and no more fragile filename parsing. Its
    loader also stopped requiring a literal >=40%-throttle data point
    before fitting the curve: a sparse spec sheet (e.g. samples at
    30/50/60/.../100%, no exact 40) now still interpolates 40% correctly
    from its real neighbors, instead of erroring or ignoring that data.
  - Both batteries and motor configs now carry cells_s (battery cell count:
    6S/12S/24S/...). A motor's thrust/current curve is only valid at the
    voltage it was actually measured on -- there's no validated way here to
    extrapolate it to a different voltage -- so run() only pairs a motor
    with a battery of the *same* cells_s, and reports how many
    motor/battery combinations it skipped for a voltage mismatch.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import math
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq

# ---------------------------------------------------------------------------
# Assumptions and parameters
# ---------------------------------------------------------------------------

G = 9.807  # m/s^2
RHO_AIR = 1.225  # kg/m^3

THROTTLE_MIN, THROTTLE_MAX = 40.0, 100.0

TARGET_CRUISE_VELOCITY_MPS = 10.0  # inherited from the original model, unchanged
TARGET_TAKEOFF_ALTITUDE_M = 30.48  # 100 ft

LANDING_START_ALT_M = 30.48  # 100 ft, matches the new takeoff altitude
LANDING_PHASE1_ALT_M = 3.048  # 10 ft
LANDING_PHASE1_SPEED_MPS = 5.0
LANDING_PHASE2_SPEED_MPS = 1.0

NAUTICAL_MILE_M = 1852.0
DEFAULT_PAYLOAD_LB = 220.0
LB_TO_G = 453.59237

# take off, 1nm out, land, pick up cargo, take off, 8nm, land, drop cargo,
# take off, 1nm back, land
MISSION_LEGS = [
    ("takeoff", None),
    ("cruise", NAUTICAL_MILE_M),
    ("land", None),
    ("add_payload", None),
    ("takeoff", None),
    ("cruise", 8 * NAUTICAL_MILE_M),
    ("land", None),
    ("remove_payload", None),
    ("takeoff", None),
    ("cruise", NAUTICAL_MILE_M),
    ("land", None),
]

# A configuration that physically cannot take off / cruise forward is
# penalized with a large time/energy sentinel, so it naturally comes out
# infeasible rather than crashing the optimizer.
INFEASIBLE_TIME_S = 9999.0
INFEASIBLE_ENERGY_MAH = 1_000_000.0  # mission legs are now nautical miles, not meters -- old 1000 sentinel could look like a real number

@dataclass
class Battery:
    name: str
    mass_g: float
    capacity_mah: float
    cost_usd: float
    cells_s: int  # cell count (6S/12S/24S/...) -- a motor's thrust/current curve is only
    # valid at the voltage it was measured on, so a motor is only paired with a battery
    # of the *same* cells_s (see run()); this model doesn't extrapolate performance
    # across voltages, since doing that accurately needs a validated KV/voltage scaling
    # law this project doesn't have data to back.


# Reproduces the original MATLAB model's battery lineup. cells_s wasn't part
# of the original data (which had no voltage-compatibility concept at all);
# 6S is assumed here as typical for hobby-scale packs in this capacity range
# -- replace via --battery-file / load_batteries() with real packs (and
# their real cell counts) as you get them.
BATTERIES = [
    Battery(name="12Ah-6S", mass_g=1532.0, capacity_mah=12000.0, cost_usd=273.0, cells_s=6),
    Battery(name="16Ah-6S", mass_g=1988.0, capacity_mah=16000.0, cost_usd=336.0, cells_s=6),
    Battery(name="18Ah-6S", mass_g=2030.0, capacity_mah=18000.0, cost_usd=500.0, cells_s=6),
    Battery(name="22Ah-6S", mass_g=2530.0, capacity_mah=22000.0, cost_usd=476.0, cells_s=6),
    Battery(name="30Ah-6S", mass_g=3500.0, capacity_mah=30000.0, cost_usd=532.0, cells_s=6),
]

BATTERY_FIELDS = {f.name for f in dataclasses.fields(Battery)}
BATTERY_REQUIRED_FIELDS = {"name", "mass_g", "capacity_mah", "cost_usd", "cells_s"}


def load_batteries(path: str) -> list[Battery]:
    """Load batteries from a JSON file, or a folder of them (each holding a
    list of battery objects) -- same convention as load_vehicle_types() and
    load_motor_configs(). All fields are required, e.g.:
        [{"name": "22Ah-12S", "mass_g": 3100, "capacity_mah": 22000,
          "cost_usd": 610, "cells_s": 12}]
    """
    if os.path.isdir(path):
        file_paths = sorted(glob.glob(os.path.join(path, "*.json")))
        if not file_paths:
            raise FileNotFoundError(f"No .json files found in {path}")
    else:
        file_paths = [path]

    batteries = []
    for file_path in file_paths:
        with open(file_path) as f:
            raw = json.load(f)
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{file_path}: expected a non-empty JSON list of battery objects")
        for i, entry in enumerate(raw):
            missing = BATTERY_REQUIRED_FIELDS - entry.keys()
            if missing:
                raise ValueError(f"{file_path}: entry {i} is missing required field(s) {sorted(missing)}")
            unknown = entry.keys() - BATTERY_FIELDS
            if unknown:
                raise ValueError(f"{file_path}: entry {i} has unknown field(s) {sorted(unknown)}")
            batteries.append(Battery(**entry))
    return batteries


# ---------------------------------------------------------------------------
# Vehicle types
# ---------------------------------------------------------------------------

@dataclass
class VehicleType:
    name: str
    num_rotors: int
    base_mass_g: float  # frame + electronics + payload mounting; no battery/motors/cargo
    arm_mass_g: float = 0.0  # flat extra structural mass for the arms, independent of prop size
    arm_diameter_threshold_in: float = 18.0
    arm_extra_mass_per_in: float = 22.5  # *additional* arm mass per inch of prop diameter beyond the threshold
    drag_cd: float = 1.28
    wing_area_m2: float = 0.0  # 0 = no wings
    wing_cl: float = 0.0  # lift coefficient at cruise attitude; ignored if wing_area_m2 is 0
    cruise_velocity_mps: float = TARGET_CRUISE_VELOCITY_MPS  # target forward cruise speed for this airframe


# Reproduces the original MATLAB model's single hard-coded airframe.
MULTIROTOR = VehicleType(
    name="Multirotor-6",
    num_rotors=6,
    base_mass_g=3000.0 + 700.0 + 800.0,  # frame+arms, electronics, payload mounting
)

# Add more VehicleType(...) instances here to sweep additional airframes
# (e.g. a winged hybrid: VehicleType(..., wing_area_m2=1.2, wing_cl=0.9)).
VEHICLE_TYPES = [MULTIROTOR]

VEHICLE_TYPE_FIELDS = {f.name for f in dataclasses.fields(VehicleType)}
VEHICLE_TYPE_REQUIRED_FIELDS = {"name", "num_rotors", "base_mass_g"}


def load_vehicle_types(path: str) -> list[VehicleType]:
    """Load vehicle types from a JSON file, or a folder of them (each file
    holding a list of vehicle objects) -- matching the motor-CSV folder
    convention. Each vehicle object has VehicleType's fields, e.g.
        [
          {"name": "Multirotor-6", "num_rotors": 6, "base_mass_g": 4500},
          {"name": "Hybrid-VTOL", "num_rotors": 4, "base_mass_g": 5200,
           "wing_area_m2": 1.2, "wing_cl": 0.9, "cruise_velocity_mps": 26}
        ]
    Only name/num_rotors/base_mass_g are required; everything else falls
    back to VehicleType's defaults (wingless, standard drag/arm constants).
    """
    if os.path.isdir(path):
        file_paths = sorted(glob.glob(os.path.join(path, "*.json")))
        if not file_paths:
            raise FileNotFoundError(f"No .json files found in {path}")
    else:
        file_paths = [path]

    vehicles = []
    for file_path in file_paths:
        with open(file_path) as f:
            raw = json.load(f)
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{file_path}: expected a non-empty JSON list of vehicle type objects")
        for i, entry in enumerate(raw):
            missing = VEHICLE_TYPE_REQUIRED_FIELDS - entry.keys()
            if missing:
                raise ValueError(f"{file_path}: entry {i} is missing required field(s) {sorted(missing)}")
            unknown = entry.keys() - VEHICLE_TYPE_FIELDS
            if unknown:
                raise ValueError(f"{file_path}: entry {i} has unknown field(s) {sorted(unknown)}")
            vehicles.append(VehicleType(**entry))
    return vehicles


# ---------------------------------------------------------------------------
# Motor/prop performance data
# ---------------------------------------------------------------------------

@dataclass
class MotorPropConfig:
    motor_model: str
    prop_diameter_in: float
    motor_mass_g: float
    prop_mass_g: float
    motor_cost: float
    prop_cost: float
    cells_s: int  # battery cell count (6S/12S/24S/...) the thrust/current curve below was measured at
    thrust_of: PchipInterpolator  # throttle% -> single-motor thrust, g
    amps_of: PchipInterpolator  # throttle% -> single-motor current, A

    def thrust_g(self, throttle_pct: float) -> float:
        t = min(max(throttle_pct, THROTTLE_MIN), THROTTLE_MAX)
        return float(self.thrust_of(t))

    def amps_a(self, throttle_pct: float) -> float:
        t = min(max(throttle_pct, THROTTLE_MIN), THROTTLE_MAX)
        return float(self.amps_of(t))


MOTOR_CONFIG_PHYSICS_FIELDS = {
    "prop_diameter_in", "motor_mass_g", "prop_mass_g", "cells_s",
    "throttle_pct", "thrust_g", "current_a",
}
MOTOR_CONFIG_COST_FIELDS = {"motor_cost", "prop_cost"}  # informational only, don't affect physics -- optional
MOTOR_CONFIG_FIELDS = MOTOR_CONFIG_PHYSICS_FIELDS | MOTOR_CONFIG_COST_FIELDS | {"motor_model"}


def load_motor_configs(path: str) -> list[MotorPropConfig]:
    """Load motor/prop configs from a JSON file (a single object or a list
    of objects), or a folder of such files -- same convention as
    load_vehicle_types(). All metadata lives in the object itself rather
    than being packed into the filename, e.g.:
        [
          {
            "motor_model": "M50C35_PRO_EEE_34kv",   # optional, defaults to the filename
            "prop_diameter_in": 20, "motor_mass_g": 450, "prop_mass_g": 60,
            "motor_cost": 120, "prop_cost": 25, "cells_s": 12,
            "throttle_pct": [30, 50, 60, 70, 80, 90, 100],
            "thrust_g": [13360, 35262, 49381, 63961, 80002, 96733, 113654],
            "current_a": [10.63, 42, 68.22, 102.19, 147.52, 203.99, 251.48]
          }
        ]
    throttle_pct/thrust_g/current_a must be equal-length parallel arrays
    with at least 2 points spanning [40, 100]% throttle; motor_cost/
    prop_cost are optional (default 0, since they're informational only --
    they don't feed the physics). cells_s is the battery cell count
    (6S/12S/24S/...) the curve was measured at -- run() only pairs this
    motor with a battery of that same cells_s, since thrust and current at
    a given throttle % genuinely depend on supply voltage and this model
    doesn't extrapolate a curve across voltages.

    An entry missing a physics-required field (or with it null), with a
    non-numeric throttle/thrust/current value, or with too few throttle
    points to span [40, 100]% is skipped rather than failing the whole
    load -- real-world spec sheets are often partial (e.g. "peak thrust
    only, no full sweep published"). Skipped entries and unrecognized
    fields are printed so you know what's still missing.
    """
    if os.path.isdir(path):
        file_paths = sorted(glob.glob(os.path.join(path, "*.json")))
        if not file_paths:
            raise FileNotFoundError(f"No .json files found in {path}")
    else:
        file_paths = [path]

    configs = []
    skipped = []
    for file_path in file_paths:
        with open(file_path) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{file_path}: expected a JSON object, or a non-empty JSON list of them")
        default_name = os.path.splitext(os.path.basename(file_path))[0]

        for i, entry in enumerate(raw):
            label = f"{file_path}#{i} ({entry.get('motor_model', default_name)})"

            missing = sorted(f for f in MOTOR_CONFIG_PHYSICS_FIELDS if entry.get(f) is None)
            if missing:
                skipped.append((label, f"missing/null required field(s): {missing}"))
                continue
            unknown = entry.keys() - MOTOR_CONFIG_FIELDS
            if unknown:
                print(f"Note: {label}: ignoring unrecognized field(s) {sorted(unknown)}")

            try:
                throttle = np.array(entry["throttle_pct"], dtype=float)
                current = np.array(entry["current_a"], dtype=float)
                thrust = np.array(entry["thrust_g"], dtype=float)
            except (TypeError, ValueError):
                skipped.append((label, "throttle_pct/thrust_g/current_a contains a non-numeric value"))
                continue
            if np.isnan(throttle).any() or np.isnan(current).any() or np.isnan(thrust).any():
                skipped.append((label, "throttle_pct/thrust_g/current_a contains a null value"))
                continue
            if not (len(throttle) == len(current) == len(thrust)):
                skipped.append((label, "throttle_pct/thrust_g/current_a must be the same length"))
                continue
            if throttle.size < 2:
                skipped.append((label, "need at least 2 throttle/thrust/current points (only an endpoint given)"))
                continue
            if throttle.min() > THROTTLE_MIN or throttle.max() < THROTTLE_MAX:
                skipped.append((label, f"throttle data covers [{throttle.min():.1f}, {throttle.max():.1f}]%, "
                                        f"needs to span [{THROTTLE_MIN}, {THROTTLE_MAX}]%"))
                continue
            # Fit on the *full* raw range (not just points >= 40) so a sparse
            # spec sheet with no sample exactly at 40 -- e.g. 30/50/60/...100
            # -- still interpolates 40 correctly from its real neighbors,
            # rather than requiring an exact boundary sample or extrapolating.
            # thrust_g()/amps_a() clamp queries to [40, 100] at lookup time,
            # so points outside that band only ever inform the curve's shape.

            throttle, unique_idx = np.unique(throttle, return_index=True)
            current, thrust = current[unique_idx], thrust[unique_idx]

            configs.append(
                MotorPropConfig(
                    motor_model=entry.get("motor_model", default_name),
                    prop_diameter_in=float(entry["prop_diameter_in"]),
                    motor_mass_g=float(entry["motor_mass_g"]),
                    prop_mass_g=float(entry["prop_mass_g"]),
                    motor_cost=float(entry.get("motor_cost") or 0.0),
                    prop_cost=float(entry.get("prop_cost") or 0.0),
                    cells_s=int(entry["cells_s"]),
                    thrust_of=PchipInterpolator(throttle, thrust),
                    amps_of=PchipInterpolator(throttle, current),
                )
            )

    if skipped:
        plural = "y" if len(skipped) == 1 else "ies"
        print(f"Skipped {len(skipped)} motor config entr{plural} (incomplete data):")
        for label, reason in skipped:
            print(f"  {label}: {reason}")
    if not configs:
        raise FileNotFoundError(f"No usable motor configs found in {path} (see skip reasons above)")
    return configs


# ---------------------------------------------------------------------------
# Shared physics helpers
# ---------------------------------------------------------------------------

def airframe_mass_g(vehicle: VehicleType, cfg: MotorPropConfig) -> float:
    """Structure only: frame/arms/electronics/payload mounting -- everything
    in total_mass_g() except the propulsion system and payload."""
    mass = vehicle.base_mass_g + vehicle.arm_mass_g
    if cfg.prop_diameter_in > vehicle.arm_diameter_threshold_in:
        mass += (cfg.prop_diameter_in - vehicle.arm_diameter_threshold_in) * vehicle.arm_extra_mass_per_in
    return mass


def propulsion_mass_g(vehicle: VehicleType, cfg: MotorPropConfig, battery_mass_g: float) -> float:
    """Motors + props (one set per rotor) + battery."""
    return battery_mass_g + vehicle.num_rotors * (cfg.motor_mass_g + cfg.prop_mass_g)


def total_mass_g(vehicle: VehicleType, cfg: MotorPropConfig, battery_mass_g: float,
                  extra_payload_g: float = 0.0) -> float:
    return (airframe_mass_g(vehicle, cfg) + propulsion_mass_g(vehicle, cfg, battery_mass_g)
            + extra_payload_g)


def frontal_area_m2(vehicle: VehicleType, cfg: MotorPropConfig) -> float:
    return (cfg.prop_diameter_in * vehicle.num_rotors + 12 * 12) / 1550.0  # in^2 -> m^2


def rotor_set_thrust_n(vehicle: VehicleType, cfg: MotorPropConfig, throttle_pct: float) -> float:
    return cfg.thrust_g(throttle_pct) / 1000.0 * G * vehicle.num_rotors


def rotor_set_amps_ma(vehicle: VehicleType, cfg: MotorPropConfig, throttle_pct: float) -> float:
    return cfg.amps_a(throttle_pct) * vehicle.num_rotors * 1000.0


def wing_lift_n(vehicle: VehicleType, velocity_mps: float) -> float:
    if vehicle.wing_area_m2 <= 0:
        return 0.0
    return 0.5 * RHO_AIR * vehicle.wing_cl * vehicle.wing_area_m2 * velocity_mps * velocity_mps


def _effective_area_at(thrust_n: float, weight_n: float, base_area_m2: float, lift_fn, v: float) -> float:
    """Frontal area inflated by the forward tilt needed to support
    (weight - wing lift) at this velocity, i.e. how much of total thrust
    magnitude is left over to push horizontally."""
    effective_weight_n = max(weight_n - lift_fn(v), 0.0)
    thrust_horizontal_n = math.sqrt(max(thrust_n * thrust_n - effective_weight_n * effective_weight_n, 0.0))
    if thrust_horizontal_n <= 0:
        return base_area_m2
    return base_area_m2 * (thrust_n / thrust_horizontal_n)


def _log_cosh(x: float) -> float:
    """log(cosh(x)) without the overflow cosh(x) itself hits once |x| passes
    ~710 -- log1p(exp(-2|x|)) stays in [0, log 2] for every x, so this is
    exact and safe everywhere cosh(x) is finite or not."""
    ax = abs(x)
    return ax - math.log(2.0) + math.log1p(math.exp(-2.0 * ax))


def _accel_phase_closed_form(a0: float, kk: float, v0: float, v_target: float, distance_cap: float):
    """Closed-form solve of dv/dt = a0 - kk*v^2 (constant thrust vs.
    quadratic drag), stopping at whichever comes first: v reaching
    v_target, or distance reaching distance_cap. Returns (time_s,
    distance_m, v_final)."""
    if a0 <= 0:
        return None
    if kk <= 0:
        t = min((v_target - v0) / a0 if v_target > v0 else math.inf,
                 (-v0 + math.sqrt(v0 * v0 + 2 * a0 * distance_cap)) / a0)
        v = v0 + a0 * t
        x = v0 * t + 0.5 * a0 * t * t
        return t, x, v

    v_t = math.sqrt(a0 / kk)
    c0 = math.atanh(min(v0 / v_t, 0.999999))

    candidates = []
    if v_target < v_t:
        u_target = math.atanh(v_target / v_t)
        candidates.append((u_target - c0) / (kk * v_t))
    # acosh(cosh(c0) * exp(kk*distance_cap)) overflows exp() long before u_d
    # itself would be too big to represent (u_d grows only linearly in
    # kk*distance_cap, since acosh compresses it back down) -- e.g. an
    # overweight/underpowered vehicle whose terminal velocity never reaches
    # v_target still needs a finite, ordinary-sized u_d here. Do the
    # acosh(cosh(c0)*exp(y)) step in log-space instead so the intermediate
    # never overflows: for y large, acosh(exp(y)) -> y + ln(2).
    ln_y = _log_cosh(c0) + kk * distance_cap
    u_d = ln_y + math.log(2.0) if ln_y > 300 else math.acosh(math.exp(ln_y))
    candidates.append((u_d - c0) / (kk * v_t))
    t = min(candidates)

    u = kk * v_t * t + c0
    v = v_t * math.tanh(u)
    x = (_log_cosh(u) - _log_cosh(c0)) / kk
    return t, x, v


def _accel_phase_numeric(thrust_n: float, weight_n: float, base_area_m2: float, cd: float,
                          mass_kg: float, lift_fn, v0: float, v_target: float, distance_cap: float):
    """Numeric fallback for winged vehicles: wing lift makes thrust_horizontal
    (and the tilt-inflated drag area) a function of v, so dv/dt is no longer
    the simple Riccati form the closed-form solver handles."""
    effective_weight0 = max(weight_n - lift_fn(v0), 0.0)
    if math.sqrt(max(thrust_n * thrust_n - effective_weight0 * effective_weight0, 0.0)) <= 0:
        return None

    def rhs(_t, state):
        _x, v = state
        effective_weight_n = max(weight_n - lift_fn(v), 0.0)
        thrust_horizontal_n = math.sqrt(max(thrust_n * thrust_n - effective_weight_n * effective_weight_n, 0.0))
        area = base_area_m2 * (thrust_n / thrust_horizontal_n) if thrust_horizontal_n > 0 else base_area_m2
        drag_n = 0.5 * RHO_AIR * cd * area * v * v
        return [v, (thrust_horizontal_n - drag_n) / mass_kg]

    def hit_target_v(_t, state):
        return state[1] - v_target

    hit_target_v.terminal = True
    hit_target_v.direction = 1

    def hit_distance(_t, state):
        return state[0] - distance_cap

    hit_distance.terminal = True
    hit_distance.direction = 1

    sol = solve_ivp(rhs, [0.0, 300.0], [0.0, v0], events=[hit_target_v, hit_distance],
                     max_step=0.5, rtol=1e-7, atol=1e-9)
    if sol.t_events[0].size == 0 and sol.t_events[1].size == 0:
        return None  # stalled: never reached cruise speed or the distance cap
    return sol.t[-1], sol.y[0, -1], sol.y[1, -1]


def _accel_phase(thrust_n: float, weight_n: float, base_area_m2: float, cd: float, mass_kg: float,
                  lift_fn, has_wing: bool, v0: float, v_target: float, distance_cap: float):
    if not has_wing:
        thrust_horizontal_n = math.sqrt(max(thrust_n * thrust_n - weight_n * weight_n, 0.0))
        if thrust_horizontal_n <= 0:
            return None
        effective_area = base_area_m2 * (thrust_n / thrust_horizontal_n)
        kk = 0.5 * RHO_AIR * cd * effective_area / mass_kg
        a0 = thrust_horizontal_n / mass_kg
        return _accel_phase_closed_form(a0, kk, v0, v_target, distance_cap)
    return _accel_phase_numeric(thrust_n, weight_n, base_area_m2, cd, mass_kg, lift_fn, v0, v_target, distance_cap)


def _decel_phase(kk: float, v0: float, v_floor: float, distance_cap: float):
    """Closed-form solve of dv/dt = -kk*v^2 (drag only, thrust off), stopping
    at v reaching v_floor or distance reaching distance_cap. Unaffected by
    wings: this model only ever treats decel as an unpowered coast, so
    weight-support and thus tilt isn't in play (see segment() for how the
    coast-phase drag area is picked)."""
    if kk <= 0 or v0 <= v_floor:
        return 0.0, 0.0, v0
    t_v = (v0 / v_floor - 1) / (kk * v0)
    # Unlike the accel case above, t_d itself (not just an intermediate) is
    # genuinely astronomically large when kk*distance_cap is big -- coasting
    # drag decay is hyperbolic, so covering a "large" kk*distance_cap this
    # way takes truly exponential time. That's beyond float64 range; math.inf
    # is the correct closest representation, and min() below still picks the
    # (ordinary-sized) t_v whenever it's the real constraint.
    try:
        t_d = (math.exp(kk * distance_cap) - 1) / (kk * v0)
    except OverflowError:
        t_d = math.inf
    t = min(t_v, t_d)
    v = v0 / (1 + kk * v0 * t)
    x = math.log(1 + kk * v0 * t) / kk
    return t, x, v


# ---------------------------------------------------------------------------
# Hover time
# ---------------------------------------------------------------------------

def hover_time_min(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery) -> float:
    thrust_needed_per_motor_g = total_mass_g(vehicle, cfg, battery.mass_g) / vehicle.num_rotors

    if thrust_needed_per_motor_g > cfg.thrust_g(THROTTLE_MAX):
        return 0.0  # can't hover even at full throttle

    f = lambda th: cfg.thrust_g(th) - thrust_needed_per_motor_g
    if f(THROTTLE_MIN) >= 0:
        throttle_hover = THROTTLE_MIN
    else:
        throttle_hover = brentq(f, THROTTLE_MIN, THROTTLE_MAX)

    current_a = cfg.amps_a(throttle_hover)
    battery_capacity_ah = battery.capacity_mah / 1000.0
    return 60.0 * battery_capacity_ah / (current_a * vehicle.num_rotors)


# ---------------------------------------------------------------------------
# Flight phases
# ---------------------------------------------------------------------------

def takeoff(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery,
            throttle_pct: float, extra_payload_g: float = 0.0):
    """Vertical climb only -- wings don't generate lift at zero airspeed, so
    they're irrelevant here regardless of vehicle type."""
    mass_g = total_mass_g(vehicle, cfg, battery.mass_g, extra_payload_g)
    mass_kg = mass_g / 1000.0
    weight_n = mass_kg * G
    thrust_n = rotor_set_thrust_n(vehicle, cfg, throttle_pct)
    if thrust_n <= weight_n:
        return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH

    amps_ma = rotor_set_amps_ma(vehicle, cfg, throttle_pct)
    kk = 0.5 * RHO_AIR * vehicle.drag_cd * frontal_area_m2(vehicle, cfg) / mass_kg
    a0 = (thrust_n - weight_n) / mass_kg

    t, _x, _v = _accel_phase_closed_form(a0, kk, v0=1.0, v_target=math.inf, distance_cap=TARGET_TAKEOFF_ALTITUDE_M)
    return t, amps_ma * (t / 3600.0)


def segment(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery,
            throttle_pct: float, distance_m: float, extra_payload_g: float = 0.0):
    mass_g = total_mass_g(vehicle, cfg, battery.mass_g, extra_payload_g)
    mass_kg = mass_g / 1000.0
    weight_n = mass_kg * G
    thrust_n = rotor_set_thrust_n(vehicle, cfg, throttle_pct)
    amps_ma = rotor_set_amps_ma(vehicle, cfg, throttle_pct)
    base_area = frontal_area_m2(vehicle, cfg)
    has_wing = vehicle.wing_area_m2 > 0
    lift_fn = (lambda v: wing_lift_n(vehicle, v)) if has_wing else (lambda _v: 0.0)

    accel = _accel_phase(thrust_n, weight_n, base_area, vehicle.drag_cd, mass_kg, lift_fn, has_wing,
                          v0=1.0, v_target=vehicle.cruise_velocity_mps, distance_cap=distance_m / 2)
    if accel is None:
        return INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
    t_acc, x_acc, v_cruise = accel

    # Coast/decel is unpowered drag only; use the tilt-area as of hand-off
    # speed (equals the constant accel-phase area when there's no wing).
    # Note: this decel phase's average speed while decaying from v_cruise
    # down to the 1 m/s floor is independent of drag by construction (both
    # its distance and duration scale as 1/kk, so their ratio doesn't), so
    # less drag here doesn't cover more ground -- it just stretches the
    # slow tail. That can make a lower-drag (e.g. winged) vehicle spend
    # more of the leg at this phase's low average speed, not less.
    decel_area = _effective_area_at(thrust_n, weight_n, base_area, lift_fn, v_cruise)
    kk_decel = 0.5 * RHO_AIR * vehicle.drag_cd * decel_area / mass_kg
    t_dec, x_dec, _v_end = _decel_phase(kk_decel, v_cruise, v_floor=1.0, distance_cap=distance_m / 2)

    # Energy uses the cruise-throttle current for both phases, matching the
    # original's assumption that throttle (and so current draw) is held
    # constant rather than reduced while coasting down.
    energy_mah = amps_ma * ((t_acc + t_dec) / 3600.0)

    if x_acc + x_dec >= distance_m:
        return t_acc + t_dec, energy_mah

    t_cruise = (distance_m - (x_acc + x_dec)) / v_cruise
    energy_mah += amps_ma * (t_cruise / 3600.0)
    return t_acc + t_dec + t_cruise, energy_mah


def landing(vehicle: VehicleType, cfg: MotorPropConfig):
    """Both phases hold a prescribed constant descent speed, so time/energy
    are direct kinematics -- no integration loop needed. Mass/payload isn't
    a parameter here: the original's descent was likewise prescribed at a
    fixed speed regardless of mass (its computed acceleration was dead
    code, never fed back into the descent rate)."""
    amps40_ma = rotor_set_amps_ma(vehicle, cfg, THROTTLE_MIN)
    linear_amps_ma = lambda throttle_pct: amps40_ma * (throttle_pct / THROTTLE_MIN)

    t1 = (LANDING_START_ALT_M - LANDING_PHASE1_ALT_M) / LANDING_PHASE1_SPEED_MPS
    energy1_mah = linear_amps_ma(30.0) * (t1 / 3600.0)

    t2 = LANDING_PHASE1_ALT_M / LANDING_PHASE2_SPEED_MPS
    energy2_mah = linear_amps_ma(20.0) * (t2 / 3600.0)

    return t1 + t2, energy1_mah + energy2_mah


# ---------------------------------------------------------------------------
# Mission: fixed one-pass course
# ---------------------------------------------------------------------------

def mission(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery,
            takeoff_throttles: list[float], cruise_throttles: list[float], payload_mass_g: float):
    """Simulate the fixed course given a throttle for each takeoff (in
    order) and each cruise leg (in order). Returns (total_time_s,
    total_energy_mah, feasible)."""
    payload_g = 0.0
    total_time = total_energy = 0.0
    infeasible = False
    to_i = cr_i = 0
    for kind, distance_m in MISSION_LEGS:
        if kind == "takeoff":
            t, e = takeoff(vehicle, cfg, battery, takeoff_throttles[to_i], extra_payload_g=payload_g)
            to_i += 1
        elif kind == "land":
            t, e = landing(vehicle, cfg)
        elif kind == "cruise":
            t, e = segment(vehicle, cfg, battery, cruise_throttles[cr_i], distance_m, extra_payload_g=payload_g)
            cr_i += 1
        elif kind == "add_payload":
            payload_g += payload_mass_g
            continue
        else:  # remove_payload
            payload_g -= payload_mass_g
            continue
        if e >= INFEASIBLE_ENERGY_MAH:
            infeasible = True
        total_time += t
        total_energy += e

    feasible = (not infeasible) and total_energy <= battery.capacity_mah
    return total_time, total_energy, feasible


def _min_energy_throttle(phase_fn, grid_step: float):
    """Grid-search throttle in [40, 100] for the value minimizing this
    single phase's energy."""
    best = None
    for th in np.arange(THROTTLE_MIN, THROTTLE_MAX + 1e-9, grid_step):
        t, e = phase_fn(th)
        if e >= INFEASIBLE_ENERGY_MAH:
            continue
        if best is None or e < best[2]:
            best = (th, t, e)
    if best is None:
        return THROTTLE_MAX, INFEASIBLE_TIME_S, INFEASIBLE_ENERGY_MAH
    return best


def mission_best(vehicle: VehicleType, cfg: MotorPropConfig, battery: Battery,
                  payload_mass_g: float, grid_step: float = 0.5) -> dict:
    """Independently optimize each takeoff's and each cruise leg's throttle
    for minimum energy. The mission has no time limit and energy is
    additive across phases, so this is equivalent to (and much cheaper
    than) a joint search maximizing total leftover battery energy. Landing
    throttle stays prescribed/unoptimized, as in the original."""
    payload_g = 0.0
    total_time = total_energy = 0.0
    infeasible = False
    throttles = []
    for kind, distance_m in MISSION_LEGS:
        if kind == "takeoff":
            th, t, e = _min_energy_throttle(
                lambda x, p=payload_g: takeoff(vehicle, cfg, battery, x, extra_payload_g=p), grid_step
            )
            throttles.append(("takeoff", th))
        elif kind == "land":
            t, e = landing(vehicle, cfg)
        elif kind == "cruise":
            th, t, e = _min_energy_throttle(
                lambda x, p=payload_g, d=distance_m: segment(vehicle, cfg, battery, x, d, extra_payload_g=p),
                grid_step,
            )
            throttles.append(("cruise", th))
        elif kind == "add_payload":
            payload_g += payload_mass_g
            continue
        else:  # remove_payload
            payload_g -= payload_mass_g
            continue
        if e >= INFEASIBLE_ENERGY_MAH:
            infeasible = True
        total_time += t
        total_energy += e

    feasible = (not infeasible) and total_energy <= battery.capacity_mah
    return {
        "feasible": feasible,
        "total_time_s": total_time,
        "total_energy_mah": total_energy,
        "energy_remaining_mah": battery.capacity_mah - total_energy,
        "throttles": throttles,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(data_dir: str, payload_lb: float = DEFAULT_PAYLOAD_LB, grid_step: float = 0.5,
        vehicle_types: list[VehicleType] | None = None,
        batteries: list[Battery] | None = None) -> pd.DataFrame:
    vehicle_types = vehicle_types or VEHICLE_TYPES
    batteries = batteries or BATTERIES
    payload_mass_g = payload_lb * LB_TO_G
    configs = load_motor_configs(data_dir)

    rows = []
    skipped_voltage_mismatch = 0
    for vehicle in vehicle_types:
        for cfg in configs:
            # A motor's thrust/current curve was measured at one specific
            # voltage; pair it only with a battery of that same cell count
            # rather than extrapolating performance across voltages.
            compatible_batteries = [b for b in batteries if b.cells_s == cfg.cells_s]
            skipped_voltage_mismatch += len(batteries) - len(compatible_batteries)
            for battery in compatible_batteries:
                airframe_g = airframe_mass_g(vehicle, cfg)
                propulsion_g = propulsion_mass_g(vehicle, cfg, battery.mass_g)
                mass_g = airframe_g + propulsion_g
                result = mission_best(vehicle, cfg, battery, payload_mass_g, grid_step)

                row = {
                    "vehicle": vehicle.name,
                    "motor_model": cfg.motor_model,
                    "prop_diameter_in": cfg.prop_diameter_in,
                    "battery": battery.name,
                    "battery_capacity_mah": battery.capacity_mah,
                    "cells_s": cfg.cells_s,
                    "total_mass_g": mass_g,
                    "airframe_mass_g": airframe_g,
                    "propulsion_mass_g": propulsion_g,
                    "total_cost_usd": cfg.motor_cost * 7 + cfg.prop_cost * 6 + battery.cost_usd * 2,
                    "hover_time_min": hover_time_min(vehicle, cfg, battery),
                    "mission_feasible": result["feasible"],
                    "mission_total_time_s": result["total_time_s"],
                    "mission_total_energy_mah": result["total_energy_mah"],
                    "mission_energy_remaining_mah": result["energy_remaining_mah"],
                }
                takeoff_n = cruise_n = 0
                for kind, th in result["throttles"]:
                    if kind == "takeoff":
                        takeoff_n += 1
                        row[f"throttle_takeoff{takeoff_n}"] = th
                    else:
                        cruise_n += 1
                        row[f"throttle_cruise{cruise_n}"] = th
                rows.append(row)

    if skipped_voltage_mismatch:
        print(f"Skipped {skipped_voltage_mismatch} motor/battery pairing(s) with mismatched cells_s "
              f"(a motor's curve is only valid at the voltage it was measured on).")
    return pd.DataFrame(rows)


GOOD_COLOR, CRITICAL_COLOR, SERIES_COLOR = "#0ca30c", "#d03b3b", "#2a78d6"


def plot_results(df: pd.DataFrame, out_prefix: str | None):
    """One dot/bar per config would need a 250+-entry legend to identify --
    unreadable. Instead: rank vehicle types by their best setup (bar charts,
    one series, no legend needed) and show the full trade space colored only
    by feasible/infeasible (2 categories, not one per config)."""
    import matplotlib.pyplot as plt

    def _bar_by_vehicle(ax, values, title, ylabel, colors=SERIES_COLOR):
        ax.bar(values.index, values.values, color=colors)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.tick_params(axis="x", rotation=45)
        for label in ax.get_xticklabels():
            label.set_ha("right")

    fig1, ax1 = plt.subplots(figsize=(9, 5))
    best_hover = df.loc[df.groupby("vehicle")["hover_time_min"].idxmax()] \
        .set_index("vehicle")["hover_time_min"].sort_values(ascending=False)
    _bar_by_vehicle(ax1, best_hover, "Best Hover Time by Vehicle Type", "Hover Time (minutes)")
    fig1.tight_layout()

    fig2, ax2 = plt.subplots(figsize=(9, 5))
    best_idx = df[df.mission_feasible].groupby("vehicle")["mission_energy_remaining_mah"].idxmax()
    infeasible_vehicles = set(df.vehicle) - set(df.loc[best_idx, "vehicle"])
    if infeasible_vehicles:
        fallback_idx = df[df.vehicle.isin(infeasible_vehicles)].groupby("vehicle")["mission_energy_remaining_mah"].idxmax()
        best_idx = pd.concat([best_idx, fallback_idx])
    best_energy = df.loc[best_idx].set_index("vehicle")["mission_energy_remaining_mah"].sort_values(ascending=False)
    colors = [GOOD_COLOR if v not in infeasible_vehicles else CRITICAL_COLOR for v in best_energy.index]
    _bar_by_vehicle(ax2, best_energy,
                     "Best Mission Energy Margin by Vehicle Type\n(green = feasible, red = no feasible setup found)",
                     "Energy Remaining (mAh)", colors=colors)
    ax2.axhline(0, color="#898781", linewidth=0.8)
    fig2.tight_layout()

    fig3, ax3 = plt.subplots(figsize=(7, 6))
    for feasible, color, label in [(True, GOOD_COLOR, "Feasible"), (False, CRITICAL_COLOR, "Infeasible")]:
        sub = df[df.mission_feasible == feasible]
        ax3.scatter(sub.total_mass_g / 1000.0, sub.hover_time_min, color=color, label=label, s=25, alpha=0.7)
    ax3.set_title(f"Hover Time vs Total Mass (all {len(df)} configurations)")
    ax3.set_xlabel("Total Mass (kg)")
    ax3.set_ylabel("Hover Time (minutes)")
    ax3.legend()
    ax3.grid(True, color="#e1e0d9")
    fig3.tight_layout()

    if out_prefix:
        fig1.savefig(f"{out_prefix}_best_hover_by_vehicle.png", bbox_inches="tight", dpi=150)
        fig2.savefig(f"{out_prefix}_best_energy_margin_by_vehicle.png", bbox_inches="tight", dpi=150)
        fig3.savefig(f"{out_prefix}_hover_vs_mass_tradespace.png", bbox_inches="tight", dpi=150)
    else:
        plt.show()


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    entered = input(f"{message}{suffix}: ").strip()
    return entered or default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=None,
                         help="JSON file, or folder of JSON files, of motor/prop configs "
                              "(see load_motor_configs()). If omitted, you'll be prompted for it.")
    parser.add_argument("--vehicle-file", default=None,
                         help="JSON file, or folder of JSON files, of vehicle type configs "
                              "(see load_vehicle_types()). If omitted, you'll be prompted for it; "
                              "leave that prompt blank to use the single built-in default vehicle.")
    parser.add_argument("--battery-file", default=None,
                         help="JSON file, or folder of JSON files, of battery configs "
                              "(see load_batteries()). If omitted, you'll be prompted for it; "
                              "leave that prompt blank to use the built-in default battery lineup.")
    parser.add_argument("--payload-lb", type=float, default=DEFAULT_PAYLOAD_LB,
                         help="Cargo mass picked up after leg 1 and dropped after leg 2, in lb")
    parser.add_argument("--grid-step", type=float, default=0.5,
                         help="Throttle grid resolution in %% for per-phase optimization")
    parser.add_argument("--out-csv", default="mission_model_results.csv")
    parser.add_argument("--plot-prefix", default=None,
                         help="Save plots as <prefix>_*.png instead of showing them interactively")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    data_dir = args.data_dir or _prompt("Path to a motor config JSON file or folder")
    if not data_dir:
        parser.error("--data-dir (or a non-blank answer at the prompt) is required")

    vehicle_file = args.vehicle_file
    if vehicle_file is None:
        vehicle_file = _prompt("Path to a vehicle types JSON file or folder (blank = built-in default vehicle)")
    vehicle_types = load_vehicle_types(vehicle_file) if vehicle_file else VEHICLE_TYPES

    battery_file = args.battery_file
    if battery_file is None:
        battery_file = _prompt("Path to a battery configs JSON file or folder (blank = built-in default batteries)")
    batteries = load_batteries(battery_file) if battery_file else BATTERIES

    df = run(data_dir, args.payload_lb, args.grid_step, vehicle_types, batteries)
    df.to_csv(args.out_csv, index=False)
    print(df.to_string(index=True))
    print(f"\nWrote {args.out_csv}")

    if not args.no_plot:
        plot_results(df, args.plot_prefix)


if __name__ == "__main__":
    main()
