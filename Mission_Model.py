
import os, sys, glob, warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.integrate import solve_ivp
import matplotlib.pyplot as plt
import matplotlib.cm as cm

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ══════════════════════════════════════════════════════════════════
#  ✏️  EDIT THESE TO MATCH YOUR SETUP
# ══════════════════════════════════════════════════════════════════

MOTORS_FOLDER  = '/Users/ethansimon/Desktop/VTOL Coding files/Raw_Motor_Files'
RESULTS_FOLDER = r'/Users/ethansimon/Desktop/VTOL Coding files/Processed_Motor_FIles'

# Vehicle base masses (grams)
MASS_FRAME_G    = 3000
MASS_ELECTRONICS_G  = 700
MASS_PAYLOAD_G  = 800

# Battery — pick one row:  [mass_g, capacity_mAh, cost_$]
BATTERY = [1532, 12000, 273]   # 12 Ah
# BATTERY = [1988, 16000, 336] # 16 Ah
# BATTERY = [2030, 18000, 500] # 18 Ah
# BATTERY = [2530, 22000, 476] # 22 Ah
# BATTERY = [3500, 30000, 532] # 30 Ah

# Mission profile
TAKEOFF_ALT_M   = 15.24   # metres (50 ft)
CRUISE_1_DIST_M = 100.0   # metres
HOVER_TIME_S    =  20.0   # seconds
CRUISE_2_DIST_M = 200.0   # metres
# landing returns from TAKEOFF_ALT_M to ground

# Throttle settings
TAKEOFF_THROTTLE_PCT = 75
CRUISE_THROTTLE_PCT  = 80

# Physics
NUM_MOTORS = 6
DRAG_COEFF = 1.28
AIR_DENSITY = 1.225   # kg/m³

# Plots — set False to skip the popup windows
SHOW_PLOTS = True

# ══════════════════════════════════════════════════════════════════

G = 9.807
THROTTLE_RANGE = np.linspace(30, 100, 71)


# ──────────────────────────────────────────────────────────────────
# Motor data container
# ──────────────────────────────────────────────────────────────────
@dataclass
class MotorData:
    motor_model:   str
    prop_diameter: float
    motor_mass:    float
    prop_mass:     float
    motor_cost:    float
    prop_cost:     float
    throttle:      np.ndarray = field(default_factory=lambda: np.array([]))
    amps_interp:   np.ndarray = field(default_factory=lambda: np.array([]))
    thrust_interp: np.ndarray = field(default_factory=lambda: np.array([]))
    _amps_fn:      Optional[PchipInterpolator] = field(default=None, repr=False)
    _thrust_fn:    Optional[PchipInterpolator] = field(default=None, repr=False)

    def build_interpolators(self):
        self._amps_fn   = PchipInterpolator(self.throttle, self.amps_interp)
        self._thrust_fn = PchipInterpolator(self.throttle, self.thrust_interp)

    def get_thrust_and_amps(self, throttle_pct: float):
        """Returns (total thrust gf, total amps mA) across all NUM_MOTORS motors."""
        throttle_pct = float(np.clip(throttle_pct, 40, 100))
        thrust = float(self._thrust_fn(throttle_pct)) * NUM_MOTORS
        amps   = float(self._amps_fn(throttle_pct))   * NUM_MOTORS * 1000
        return thrust, amps

    @property
    def label(self):
        return f"{self.motor_model} Ø{self.prop_diameter}in"


# ──────────────────────────────────────────────────────────────────
# Load all CSVs from folder
# ──────────────────────────────────────────────────────────────────
def load_motor_files(folder: str) -> list[MotorData]:
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        sys.exit(f"[ERROR] No CSV files found in: {folder}")

    dataset, skipped = [], []

    for fpath in files:
        basename = os.path.splitext(os.path.basename(fpath))[0]
        tokens   = basename.split("_")

        try:
            if len(tokens) == 8:
                # Model_PropDiam_PropPitch_MotorMass_MotorCost_NumProps_PropMass_PropCost
                motor_model   = tokens[0]
                prop_diameter = float(tokens[1])
                motor_mass    = float(tokens[3])
                motor_cost    = float(tokens[4])
                prop_mass     = float(tokens[6])
                prop_cost     = float(tokens[7])
            elif len(tokens) == 6:
                # Model_PropDiam_MotorMass_PropMass_MotorCost_PropCost
                motor_model   = tokens[0]
                prop_diameter = float(tokens[1])
                motor_mass    = float(tokens[2])
                prop_mass     = float(tokens[3])
                motor_cost    = float(tokens[4])
                prop_cost     = float(tokens[5])
            else:
                raise ValueError(f"expected 6 or 8 '_'-separated tokens, got {len(tokens)}")
        except ValueError as e:
            skipped.append((basename, str(e))); continue

        # Auto-detect header row
        try:
            peek = pd.read_csv(fpath, header=None, nrows=1).iloc[0, 0]
            try:
                float(str(peek).replace("\ufeff", "").strip())
                raw = pd.read_csv(fpath, header=None)
            except ValueError:
                raw = pd.read_csv(fpath, header=0)
        except Exception as e:
            skipped.append((basename, str(e))); continue

        throttle = raw.iloc[:, 0].values.astype(float)
        current  = raw.iloc[:, 1].values.astype(float)
        thrust   = raw.iloc[:, 2].values.astype(float)

        mask = (throttle >= 30) & (throttle <= 100)
        if mask.sum() < 2:
            skipped.append((basename, "fewer than 2 points in 30-100% throttle range")); continue

        amps_interp   = PchipInterpolator(throttle[mask], current[mask])(THROTTLE_RANGE)
        thrust_interp = PchipInterpolator(throttle[mask], thrust[mask])(THROTTLE_RANGE)

        md = MotorData(
            motor_model=motor_model, prop_diameter=prop_diameter,
            motor_mass=motor_mass,   prop_mass=prop_mass,
            motor_cost=motor_cost,   prop_cost=prop_cost,
            throttle=THROTTLE_RANGE.copy(),
            amps_interp=amps_interp,
            thrust_interp=thrust_interp,
        )
        md.build_interpolators()
        dataset.append(md)
        print(f"  ✓  {basename}")

    if skipped:
        print()
        for name, reason in skipped:
            print(f"  ✗  {name}  ({reason})")

    print(f"\n  {len(dataset)} motor(s) loaded, {len(skipped)} skipped.\n")
    return dataset


# ──────────────────────────────────────────────────────────────────
# Physics helpers
# ──────────────────────────────────────────────────────────────────
def total_mass_kg(md: MotorData) -> float:
    base = MASS_FRAME_G + MASS_ELECTRONICS_G + MASS_PAYLOAD_G
    return (base + BATTERY[0] + NUM_MOTORS * (md.motor_mass + md.prop_mass)) / 1000.0

def drag_area(md: MotorData) -> float:
    return (np.pi * (md.prop_diameter / 2) ** 2 * NUM_MOTORS + 144) / 1550.0

def to_mah(amps_ma: float, seconds: float) -> float:
    return amps_ma * seconds / 3600.0


# ──────────────────────────────────────────────────────────────────
# ODE dynamics
# ──────────────────────────────────────────────────────────────────
def _rhs_vertical(t, y, alpha, lam):
    return [y[1], alpha - lam * y[1] ** 2]

def _rhs_horizontal(t, x, sigma, gamma):
    return [x[1], sigma - gamma * x[1] ** 2]

def _rhs_landing(t, y, a, b):
    return [y[1], a + b * y[1] ** 2]


# ──────────────────────────────────────────────────────────────────
# Mission phases
# ──────────────────────────────────────────────────────────────────
def simulate_takeoff(md: MotorData):
    tm = total_mass_kg(md)
    W  = tm * G
    A  = drag_area(md)

    thrust_gf, amps_ma = md.get_thrust_and_amps(TAKEOFF_THROTTLE_PCT)
    thrust_N = thrust_gf * 0.009807

    if thrust_N <= W:
        print(f"    [!] Cannot lift at {TAKEOFF_THROTTLE_PCT}% throttle "
              f"(thrust {thrust_N:.1f} N ≤ weight {W:.1f} N)")
        return 9999.0, np.array([0.0, 0.001]), np.zeros((2, 2))

    alpha = (thrust_N - W) / tm
    lam   = (AIR_DENSITY * DRAG_COEFF * A) / (2 * tm)

    def ev(t, y, *_): return y[0] - TAKEOFF_ALT_M
    ev.terminal = True; ev.direction = +1

    sol = solve_ivp(_rhs_vertical, [0, 300], [0.0, 0.0],
                    args=(alpha, lam), events=ev,
                    max_step=0.05, rtol=1e-7, atol=1e-10)
    return to_mah(amps_ma, sol.t[-1]), sol.t, sol.y.T


def simulate_cruise(md: MotorData, distance_m: float):
    tm = total_mass_kg(md)
    W  = tm * G
    A  = drag_area(md)

    thrust_gf, amps_ma = md.get_thrust_and_amps(CRUISE_THROTTLE_PCT)
    thrust_N = thrust_gf * 0.009807

    AoA = 30.0
    if np.sin(np.radians(AoA)) * thrust_N < W * 1.05:
        AoA += AoA * 1.2
    elif np.sin(np.radians(AoA)) * thrust_N > W * 1.15:
        AoA -= AoA * 0.2
    AoA = float(np.clip(AoA, 5, 85))

    sigma = np.cos(np.radians(AoA)) * thrust_N / tm
    gamma = (DRAG_COEFF * A * 1.3 * AIR_DENSITY) / (2 * tm)

    def ev(t, x, *_): return x[0] - distance_m
    ev.terminal = True; ev.direction = +1

    sol = solve_ivp(_rhs_horizontal, [0, 900], [0.0, 0.0],
                    args=(sigma, gamma), events=ev,
                    max_step=0.05, rtol=1e-7, atol=1e-10)
    return to_mah(amps_ma, sol.t[-1]), sol.t, sol.y.T


def simulate_hover(md: MotorData):
    tm = total_mass_kg(md)
    W  = tm * G
    target_gf = W / 0.009807 / NUM_MOTORS
    tol  = 0.50
    mask = ((md.thrust_interp >= target_gf * (1 - tol)) &
            (md.thrust_interp <= target_gf * (1 + tol)))
    inds = np.where(mask)[0]
    tpct = float(md.throttle[inds[0]]) if len(inds) else 50.0
    _, amps_ma = md.get_thrust_and_amps(tpct)
    return to_mah(amps_ma, HOVER_TIME_S)


def simulate_landing(md: MotorData):
    tm = total_mass_kg(md)
    W  = tm * G
    A  = drag_area(md)
    b  = (DRAG_COEFF * AIR_DENSITY * A) / (tm * 2)

    def find_tpct(fraction):
        target_gf = W * fraction / 0.009807 / NUM_MOTORS
        tol  = 0.05
        mask = ((md.thrust_interp >= target_gf * (1 - tol)) &
                (md.thrust_interp <= target_gf * (1 + tol)))
        inds = np.where(mask)[0]
        return float(md.throttle[inds[0]]) if len(inds) else 30.0

    def run_phase(y0, end_alt, a):
        def ev(t, y, *_): return y[0] - end_alt
        ev.terminal = True; ev.direction = -1
        sol = solve_ivp(_rhs_landing, [0, 90], y0,
                        args=(a, b), events=ev,
                        max_step=0.05, rtol=1e-7, atol=1e-10)
        return sol.t, sol.y.T

    # Phase 1: fast descent (TAKEOFF_ALT → 2 m)
    tpct1 = find_tpct(0.93)
    a1 = ((W * 0.93 / 0.009807 / NUM_MOTORS) * 0.009807 * NUM_MOTORS - W) / tm
    t1, y1 = run_phase([TAKEOFF_ALT_M, 0.0], 2.0, a1)
    _, amp1 = md.get_thrust_and_amps(tpct1)

    # Phase 2: slow descent (2 m → 0)
    tpct2 = find_tpct(0.98)
    a2 = ((W * 0.98 / 0.009807 / NUM_MOTORS) * 0.009807 * NUM_MOTORS - W) / tm
    t2, y2 = run_phase([2.0, 0.0], 0.0, a2)
    _, amp2 = md.get_thrust_and_amps(tpct2)

    mah_land = to_mah(amp1, t1[-1]) + to_mah(amp2, t2[-1])
    t_combined = np.concatenate([t1, t2 + t1[-1]])
    y_combined = np.concatenate([y1, y2])
    return mah_land, t_combined, y_combined, t1[-1] + t2[-1]


# ──────────────────────────────────────────────────────────────────
# Plotting
# ──────────────────────────────────────────────────────────────────
def _colors(n):
    return [cm.tab20(i / max(n, 1)) for i in range(n)]

def plot_trajectories(results, title, ylabel):
    fig, axes = plt.subplots(2, 1, figsize=(11, 7))
    colors = _colors(len(results))
    for i, r in enumerate(results):
        lbl = f"{i+1}. {r['label']}"
        axes[0].plot(r["t"], r["y"][:, 0], color=colors[i], lw=1.5, label=lbl)
        axes[1].plot(r["t"], r["y"][:, 1], color=colors[i], lw=1.5, label=lbl)
    for ax, yl in zip(axes, [ylabel, "Velocity (m/s)"]):
        ax.set_xlabel("Time (s)"); ax.set_ylabel(yl)
        ax.legend(fontsize=7, loc="best"); ax.grid(True)
    axes[0].set_title(title)
    fig.tight_layout()
    return fig

def plot_phase_bar(df):
    phases = ["mAh_takeoff", "mAh_cruise1", "mAh_hover", "mAh_cruise2", "mAh_landing"]
    labels = ["Takeoff", "Cruise 1", "Hover", "Cruise 2", "Landing"]
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2"]
    motors = [f"{i+1}. {r['Motor']} {r['Prop (in)']}in" for i, r in df.iterrows()]

    fig, ax = plt.subplots(figsize=(max(10, len(df) * 1.1 + 3), 6))
    bottoms = np.zeros(len(df))
    x = np.arange(len(df))
    for phase, label, color in zip(phases, labels, colors):
        vals = df[phase].values
        ax.bar(x, vals, bottom=bottoms, label=label, color=color, width=0.6)
        bottoms += vals

    ax.set_xticks(x)
    ax.set_xticklabels(motors, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Energy (mAh)")
    ax.set_title("Mission Energy by Phase — All Motors  (ranked #1 = most efficient)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.4)
    fig.tight_layout()
    return fig

def plot_summary_scatter(df):
    fig, ax = plt.subplots(figsize=(11, 6))
    colors = _colors(len(df))
    for i, (_, row) in enumerate(df.iterrows()):
        ax.scatter(row["Total time (s)"], row["Total energy (mAh)"],
                   color=colors[i], s=120, zorder=5)
        ax.annotate(f"#{i+1} {row['Motor']} {row['Prop (in)']}in",
                    (row["Total time (s)"], row["Total energy (mAh)"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax.set_xlabel("Total mission time (s)")
    ax.set_ylabel("Total energy (mAh)")
    ax.set_title("Full-Mission Energy vs. Time — All Motors")
    ax.grid(True)
    fig.tight_layout()
    return fig


# ──────────────────────────────────────────────────────────────────
# Excel output
# ──────────────────────────────────────────────────────────────────
def save_excel(df: pd.DataFrame, timestamp: str) -> str:
    os.makedirs(RESULTS_FOLDER, exist_ok=True)
    fname = os.path.join(RESULTS_FOLDER, f"mission_results_{timestamp}.xlsx")

    params = pd.DataFrame({
        "Parameter": [
            "Run timestamp",
            "Motors folder",
            "Battery mass (g)", "Battery capacity (mAh)", "Battery cost ($)",
            "Takeoff altitude (m)", "Cruise 1 distance (m)",
            "Hover time (s)", "Cruise 2 distance (m)",
            "Num motors", "Frame+arms (g)", "Electronics (g)", "Payload mounting (g)",
            "Takeoff throttle (%)", "Cruise throttle (%)",
        ],
        "Value": [
            timestamp,
            MOTORS_FOLDER,
            BATTERY[0], BATTERY[1], BATTERY[2],
            TAKEOFF_ALT_M, CRUISE_1_DIST_M,
            HOVER_TIME_S, CRUISE_2_DIST_M,
            NUM_MOTORS, MASS_FRAME_G, MASS_ELECTRONICS_G, MASS_PAYLOAD_G,
            TAKEOFF_THROTTLE_PCT, CRUISE_THROTTLE_PCT,
        ],
    })

    with pd.ExcelWriter(fname, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Ranked Results")
        params.to_excel(writer, sheet_name="Mission Parameters", index=False)

        # Auto-size columns
        for sheet in writer.sheets.values():
            for col in sheet.columns:
                width = max((len(str(c.value)) for c in col if c.value), default=10)
                sheet.column_dimensions[col[0].column_letter].width = min(width + 4, 55)

    return fname


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 65)
    print("  VTOL Mission Model")
    print("=" * 65)
    print(f"  Folder  : {MOTORS_FOLDER}")
    print(f"  Battery : {BATTERY[0]} g  |  {BATTERY[1]} mAh")
    print(f"  Mission : ↑{TAKEOFF_ALT_M}m  →{CRUISE_1_DIST_M}m"
          f"  ⊙{HOVER_TIME_S}s  →{CRUISE_2_DIST_M}m  ↓land")
    print("-" * 65)

    dataset = load_motor_files(MOTORS_FOLDER)

    to_res, cr1_res, cr2_res, land_res = [], [], [], []
    summary_rows = []

    for md in dataset:
        print(f"\n  [{md.label}]")
        tm = total_mass_kg(md)
        print(f"    Total mass : {tm:.3f} kg")

        mah_to,   t_to,   y_to   = simulate_takeoff(md)
        mah_cr1,  t_cr1,  y_cr1  = simulate_cruise(md, CRUISE_1_DIST_M)
        mah_hov                   = simulate_hover(md)
        mah_cr2,  t_cr2,  y_cr2  = simulate_cruise(md, CRUISE_2_DIST_M)
        mah_land, t_land, y_land, t_land_total = simulate_landing(md)

        total_mah  = mah_to + mah_cr1 + mah_hov + mah_cr2 + mah_land
        total_time = t_to[-1] + t_cr1[-1] + HOVER_TIME_S + t_cr2[-1] + t_land_total
        pct        = 100.0 * total_mah / BATTERY[1]

        print(f"    Takeoff    : {t_to[-1]:6.1f} s   {mah_to:8.2f} mAh")
        print(f"    Cruise 1   : {t_cr1[-1]:6.1f} s   {mah_cr1:8.2f} mAh")
        print(f"    Hover      : {HOVER_TIME_S:6.1f} s   {mah_hov:8.2f} mAh")
        print(f"    Cruise 2   : {t_cr2[-1]:6.1f} s   {mah_cr2:8.2f} mAh")
        print(f"    Landing    : {t_land_total:6.1f} s   {mah_land:8.2f} mAh")
        print(f"    ── TOTAL   : {total_time:6.1f} s   {total_mah:8.2f} mAh  ({pct:.1f}% of battery)")

        r = {"label": md.label, "motor_model": md.motor_model, "prop_diameter": md.prop_diameter}
        to_res.append({**r,   "t": t_to,   "y": y_to,   "mah": mah_to,   "time": t_to[-1]})
        cr1_res.append({**r,  "t": t_cr1,  "y": y_cr1,  "mah": mah_cr1,  "time": t_cr1[-1]})
        cr2_res.append({**r,  "t": t_cr2,  "y": y_cr2,  "mah": mah_cr2,  "time": t_cr2[-1]})
        land_res.append({**r, "t": t_land, "y": y_land, "mah": mah_land, "time": t_land_total})

        summary_rows.append({
            "Motor":                 md.motor_model,
            "Prop (in)":             md.prop_diameter,
            "Motor mass (g)":        md.motor_mass,
            "Prop mass (g)":         md.prop_mass,
            "Motor cost ($)":        md.motor_cost,
            "Prop cost ($)":         md.prop_cost,
            "Total cost ($)":        md.motor_cost + md.prop_cost,
            "Total mass (kg)":       round(tm, 3),
            "Total time (s)":        round(total_time, 1),
            "Total energy (mAh)":    round(total_mah, 2),
            "Battery used (%)":      round(pct, 1),
            "mAh_takeoff":           round(mah_to, 2),
            "mAh_cruise1":           round(mah_cr1, 2),
            "mAh_hover":             round(mah_hov, 2),
            "mAh_cruise2":           round(mah_cr2, 2),
            "mAh_landing":           round(mah_land, 2),
        })

    # Rank by total energy
    df = pd.DataFrame(summary_rows)
    df.sort_values("Total energy (mAh)", inplace=True, ignore_index=True)
    df.index += 1  # 1-based rank

    print("\n" + "=" * 65)
    print("  RANKING  (1 = most efficient)")
    print("=" * 65)
    print(df[["Motor", "Prop (in)", "Total mass (kg)",
              "Total time (s)", "Total energy (mAh)",
              "Battery used (%)", "Total cost ($)"]].to_string())
    print()

    # Save Excel
    try:
        path = save_excel(df, timestamp)
        print(f"  Results saved → {path}\n")
    except ImportError:
        print("  [!] openpyxl not installed — saving as CSV instead.")
        os.makedirs(RESULTS_FOLDER, exist_ok=True)
        csv_path = os.path.join(RESULTS_FOLDER, f"mission_results_{timestamp}.csv")
        df.to_csv(csv_path)
        print(f"  Results saved → {csv_path}\n")

    # Plots
    if SHOW_PLOTS:
        plot_trajectories(to_res,   "Takeoff",  "Altitude (m)")
        plot_trajectories(cr1_res,  "Cruise 1", "Distance (m)")
        plot_trajectories(cr2_res,  "Cruise 2", "Distance (m)")
        plot_trajectories(land_res, "Landing",  "Altitude (m)")
        plot_phase_bar(df)
        plot_summary_scatter(df)
        plt.show()


if __name__ == "__main__":
    main()