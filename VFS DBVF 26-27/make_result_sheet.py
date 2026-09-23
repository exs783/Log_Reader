"""Build the VFS DBVF 26-27 result sheet PDF from the last sweep.

Run the sweep first (it writes vfs_dbvf_26-27_results.csv), then:
    python make_result_sheet.py            -> VFS_DBVF_26-27_results.pdf
"""
import datetime
import glob
import importlib.util
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mm", os.path.join(HERE, "VFS_DBVF_26-27.py"))
mm = importlib.util.module_from_spec(spec)
sys.modules["mm"] = mm
spec.loader.exec_module(mm)

REF_PACK = "Tattu-RLine5-2200-6S-150C"
MIN_TW = 1.8  # loaded thrust-to-weight floor for a controllable multirotor
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e4e3df", "#fcfcfb"
BRANDS = {"TMOTOR": ("T-Motor", "#2a78d6", "o"), "KDE": ("KDE Direct", "#eb6834", "s"),
          "VERTIQ": ("Vertiq", "#1baf7a", "^")}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "text.color": INK,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "figure.facecolor": "white"})


def load():
    df = pd.read_csv(os.path.join(HERE, "vfs_dbvf_26-27_results.csv"))
    cfg = {e["motor_model"]: e for f in glob.glob(os.path.join(HERE, "Motors", "*.json"))
           for e in json.load(open(f))}
    bats = {b.name: b for b in mm.load_batteries(os.path.join(HERE, "Batteries"))}
    payload_g = mm.DEFAULT_PAYLOAD_LB * mm.LB_TO_G
    df["brand"] = df.motor_model.str.extract(r"^(TMOTOR|KDE|VERTIQ)")[0]
    df["loaded_tw"] = [4 * max(cfg[m]["thrust_g"]) / (t + payload_g) for m, t in zip(df.motor_model, df.total_mass_g)]
    df["peak_a"] = [4 * max(cfg[m]["current_a"]) for m in df.motor_model]
    df["pack_a"] = [bats[b].capacity_mah / 1000 * float(b.rsplit("-", 1)[1].rstrip("C")) for b in df.battery]
    df["used_pct"] = 100 * df.mission_total_energy_mah / df.battery_capacity_mah
    df["motor_usd"] = [4 * cfg[m].get("motor_cost", 0) for m in df.motor_model]
    df["ok"] = df.mission_feasible & (df.loaded_tw >= MIN_TW) & (df.pack_a >= df.peak_a)
    return df


def page(pdf, title, subtitle=None):
    fig = plt.figure(figsize=(8.5, 11))
    fig.text(0.06, 0.955, title, fontsize=15, weight="bold")
    if subtitle:
        fig.text(0.06, 0.935, subtitle, fontsize=8.5, color=INK2)
    fig.text(0.06, 0.025, f"VFS DBVF 26-27 mission model  |  generated {datetime.date.today()}",
             fontsize=7, color=MUTED)
    fig.text(0.94, 0.025, f"page {pdf.get_pagecount() + 1}", fontsize=7, color=MUTED, ha="right")
    return fig


def table(fig, rect, rows, cols, widths, fontsize=7.5, highlight=(), row_scale=1.35):
    ax = fig.add_axes(rect)
    ax.axis("off")
    t = ax.table(cellText=rows, colLabels=cols, colWidths=widths, loc="upper left", cellLoc="left")
    t.auto_set_font_size(False)
    t.set_fontsize(fontsize)
    t.scale(1, row_scale)
    for (r, _c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_linewidth(0.5)
        if r == 0:
            cell.set_text_props(weight="bold", color=INK)
            cell.set_facecolor("#f1f0ec")
        elif r - 1 in highlight:
            cell.set_facecolor("#eaf2fb")
    return ax


def short(m):
    return (m.replace("TMOTOR_MN6007II_KV320_", "T-Motor MN6007II ").replace("_ESTIMATED", " (est.)")
             .replace("VERTIQ_", "Vertiq ").replace("_", " "))


def build(out_path):
    df = load()
    ref = df[df.battery == REF_PACK].sort_values("mission_total_energy_mah")
    good = ref[ref.ok]
    best = good.iloc[0]
    best_us = good[good.brand.isin(["KDE", "VERTIQ"])]
    legs = mm.MISSION_LEGS

    with PdfPages(out_path) as pdf:
        # ---- page 1: summary --------------------------------------------------
        fig = page(pdf, "VFS DBVF 26-27 - Mission Model Results",
                   "Full-success mission (FM-1 + FM-2 x3 payloads + FM-3 x3 retrievals, end at Home) on one 6S pack")
        y = 0.90
        facts = [
            ("Airframe", f"Quadcopter, {mm.VehicleType(**json.load(open(os.path.join(HERE, 'vechicle_configs', 'DBVF_Quad.json')))[0]).unloaded_mass_g / mm.LB_TO_G:.0f} lb unloaded "
                         f"incl. motors, props, battery (R = {df.payload_fraction_R.iloc[0]:g}); "
                         f"{mm.DEFAULT_PAYLOAD_LB:.0f} lb water payload"),
            ("Drag / speed", "Cd 1.0 (quadcopter frontal area), 15 m/s governor"),
            ("Mission", f"{sum(k == 'takeoff' for k, _ in legs)} takeoffs, {sum(k == 'land' for k, _ in legs)} landings, "
                        f"{sum(k == 'cruise' for k, _ in legs)} transits, 3 hover drops; all transits at 30 ft AGL"),
            ("Time limit", f"{mm.MISSION_TIME_LIMIT_S:.0f} s; throttle chosen per phase for minimum energy inside it"),
            ("Voltage", "6S - every motor curve measured at ~24 V, no pack stacking"),
            ("Sweep", f"{df.motor_model.nunique()} motor/prop combos x {df.battery.nunique()} batteries = {len(df)} runs"),
            ("'Usable' means", f"flies the whole mission, loaded thrust/weight >= {MIN_TW}, "
                               f"pack capacity x C covers 4-motor peak current"),
        ]
        for k, v in facts:
            fig.text(0.06, y, k, fontsize=8.5, weight="bold")
            fig.text(0.24, y, v, fontsize=8.5, color=INK2)
            y -= 0.022

        y -= 0.01
        fig.text(0.06, y, "Recommendation", fontsize=12, weight="bold")
        y -= 0.028
        rec = [
            f"Battery: {REF_PACK.replace('-', ' ')} ({bat_g(REF_PACK)} g). 1300 mAh finishes too, but with no real reserve.",
            f"Most efficient: {short(best.motor_model)} - {best.used_pct:.0f}% of the pack, "
            f"thrust/weight {best.loaded_tw:.1f}, {best.mission_total_time_s:.0f} s.",
            f"Best US-brand, published price: {short(best_us[best_us.brand == 'KDE'].iloc[0].motor_model)} - "
            f"{best_us[best_us.brand == 'KDE'].iloc[0].used_pct:.0f}% of the pack, "
            f"${best_us[best_us.brand == 'KDE'].iloc[0].motor_usd:.0f} for 4 motors.",
            f"Every usable combo finishes in ~{good.mission_total_time_s.min():.0f}-{good.mission_total_time_s.max():.0f} s: "
            f"~{mm.MISSION_TIME_LIMIT_S - good.mission_total_time_s.max():.0f} s of margin on the {mm.MISSION_TIME_LIMIT_S:.0f} s limit.",
        ]
        for line in rec:
            fig.text(0.07, y, "•  " + line, fontsize=8.5, wrap=True)
            y -= 0.024

        y -= 0.015
        fig.text(0.06, y, f"Top 15 motor/prop combos on the {REF_PACK.split('-')[1]} {REF_PACK.split('-')[2]} mAh pack",
                 fontsize=11, weight="bold")
        rows = [[short(r.motor_model), f"{r.prop_diameter_in:g}", f"{r.total_mass_g / 1000:.2f}",
                 f"{r.loaded_tw:.2f}", f"{r.mission_total_time_s:.0f}", f"{r.mission_total_energy_mah:.0f}",
                 f"{r.used_pct:.0f}%", f"{r.peak_a:.0f}", f"${r.motor_usd:.0f}" if r.motor_usd else "quote"]
                for r in good.head(15).itertuples()]
        table(fig, [0.06, 0.06, 0.88, y - 0.075],
              rows, ["Motor + prop", "Prop in", "Mass kg*", "T/W loaded", "Time s", "Energy mAh",
                     "Pack used", "Peak A", "4 motors"],
              [0.34, 0.07, 0.08, 0.09, 0.07, 0.1, 0.08, 0.07, 0.1], highlight=(0,))
        fig.text(0.06, 0.05, "*takeoff mass without payload. Vertiq mass includes its built-in ESC; others exclude the ESC.",
                 fontsize=7, color=MUTED)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- page 2: trade-space chart ---------------------------------------
        fig = page(pdf, "Trade space: efficiency vs. thrust margin",
                   f"Every combo that flies the mission on the {REF_PACK.split('-')[2]} mAh pack. "
                   "Lower = less battery; right = more thrust headroom.")
        ax = fig.add_axes([0.1, 0.52, 0.84, 0.38], facecolor=SURFACE)
        feas = ref[ref.mission_feasible]
        for key, (label, color, marker) in BRANDS.items():
            s = feas[feas.brand == key]
            ax.scatter(s.loaded_tw, s.used_pct, s=60, c=color, marker=marker, edgecolors=SURFACE,
                       linewidths=1, label=f"{label} ({len(s)})", zorder=3)
        ax.axvline(MIN_TW, color=MUTED, lw=1, ls="--", zorder=1)
        ax.text(MIN_TW + 0.04, feas.used_pct.max(), f"T/W {MIN_TW} floor", color=INK2, fontsize=7, va="top")
        ax.annotate(short(best.motor_model), (best.loaded_tw, best.used_pct), xytext=(16, 10),
                    textcoords="offset points", fontsize=7, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7))
        ax.set_xlabel("Loaded thrust-to-weight (4 motors at full throttle / takeoff mass with payload)")
        ax.set_ylabel(f"Pack used (% of {REF_PACK.split('-')[2]} mAh)")
        ax.grid(color=GRID, lw=0.6, zorder=0)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.legend(frameon=False, loc="upper right", fontsize=7.5)

        fig.text(0.06, 0.45, "Best usable combo at each prop size", fontsize=11, weight="bold")
        per = good.loc[good.groupby(good.prop_diameter_in)["mission_total_energy_mah"].idxmin()]
        rows = [[f"{r.prop_diameter_in:g}", short(r.motor_model), f"{r.loaded_tw:.2f}", f"{r.used_pct:.0f}%",
                 f"{r.peak_a:.0f}"] for r in per.itertuples()]
        table(fig, [0.06, 0.08, 0.88, 0.35], rows, ["Prop in", "Motor + prop", "T/W loaded", "Pack used", "Peak A"],
              [0.1, 0.5, 0.13, 0.13, 0.12])
        pdf.savefig(fig)
        plt.close(fig)

        # ---- page 3: battery sizing for the top picks -----------------------
        picks = list(dict.fromkeys([best.motor_model] + [good[good.brand == b].iloc[0].motor_model
                                                          for b in BRANDS if (good.brand == b).any()]))
        fig = page(pdf, "Battery sizing for the top picks",
                   "Energy left after the full mission. 'no' = can't finish, loaded T/W < 1.8, or C rating too low.")
        bat_order = df.drop_duplicates("battery").sort_values("battery_capacity_mah").battery.tolist()
        rows = []
        for b in bat_order:
            row = [b.replace("-6S", "").replace("-", " "), f"{bat_g(b)}"]
            for m in picks:
                hit = df[(df.motor_model == m) & (df.battery == b)]
                if hit.empty:  # motors + pack alone are over the unloaded weight target
                    row.append("> 10 lb")
                    continue
                r = hit.iloc[0]
                row.append(f"{r.mission_energy_remaining_mah:.0f} ({100 - r.used_pct:.0f}%)" if r.ok else "no")
            rows.append(row)
        table(fig, [0.06, 0.45, 0.88, 0.46], rows, ["Pack", "Pack g"] + [short(m) for m in picks],
              [0.24, 0.08] + [0.68 / len(picks)] * len(picks), fontsize=6.8)
        fig.text(0.06, 0.62, "Reading it: land with >= 20-30% left in practice - the model drains to 0% and does not "
                 "model voltage sag.", fontsize=8, color=INK2)
        fig.text(0.06, 0.60, "Aircraft is held at 10 lb unloaded: a bigger pack doesn't cut T/W, it eats frame budget.",
                 fontsize=8, color=INK2)
        fig.text(0.06, 0.58, "'> 10 lb' = motors + pack alone exceed 10 lb.", fontsize=8, color=INK2)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- page 4: course --------------------------------------------------
        fig = page(pdf, "Course as modeled", "Aim points from the 26-27 field drawing, feet from Home (H). "
                   "Pickup spot, A/B/C inset and D center scaled from the picture.")
        rows = [[k, f"{x:.0f}", f"{y_:.0f}"] for k, (x, y_) in mm.FIELD_FT.items()]
        table(fig, [0.06, 0.72, 0.4, 0.19], rows, ["Point", "x ft", "y ft"], [0.3, 0.35, 0.35])
        seq, n = [], 0
        for k, v in legs:
            n += 1
            seq.append([str(n), k, f"{v / mm.FT_TO_M:.0f} ft" if k == "cruise" else (f"{v:.0f} s" if v else "")])
        half = (len(seq) + 1) // 2
        table(fig, [0.06, 0.06, 0.42, 0.6], seq[:half], ["#", "Leg", ""], [0.15, 0.5, 0.35], fontsize=6.8, row_scale=1.1)
        table(fig, [0.52, 0.06, 0.42, 0.6], seq[half:], ["#", "Leg", ""], [0.15, 0.5, 0.35], fontsize=6.8, row_scale=1.1)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- page 5+: every combo on the reference pack ----------------------
        rows = [[short(r.motor_model), f"{r.prop_diameter_in:g}", f"{r.loaded_tw:.2f}",
                 f"{r.mission_total_time_s:.0f}" if r.mission_feasible else "-",
                 f"{r.used_pct:.0f}%" if r.mission_feasible else "-", f"{r.peak_a:.0f}",
                 "yes" if r.ok else ("fails T/W" if r.mission_feasible and r.loaded_tw < MIN_TW else
                                     "no current" if r.mission_feasible else "can't fly")]
                for r in ref.itertuples()]
        per_page = 40
        for i in range(0, len(rows), per_page):
            fig = page(pdf, f"All combos on the {REF_PACK.split('-')[2]} mAh pack ({i // per_page + 1})",
                       "Sorted by energy used. Full per-battery results: vfs_dbvf_26-27_results.csv")
            chunk = rows[i:i + per_page]
            table(fig, [0.06, 0.06, 0.88, 0.86], chunk,
                  ["Motor + prop", "Prop in", "T/W loaded", "Time s", "Pack used", "Peak A", "Usable"],
                  [0.42, 0.08, 0.1, 0.08, 0.1, 0.08, 0.14], fontsize=6.8)
            pdf.savefig(fig)
            plt.close(fig)

        # ---- last page: sources & caveats -----------------------------------
        fig = page(pdf, "Data sources & caveats")
        notes = [
            "Motor curves (all measured at ~24 V / 6S):",
            "  T-Motor MN6007II KV320 - T-Motor bench sheet, P21x6.3 and P22x6.6.",
            "  T-Motor MN6007II + P18x6.1 - ESTIMATED by estimate_prop_curve.py from the 21/22 in data",
            "    (method checked on KDE data: thrust within 6% at 50-100% throttle; current padded +11%).",
            "  KDE Direct - KDE performance sheets (tested at 3,500 ft; sea-level thrust ~10% higher).",
            "  Vertiq 40-06 / 40-14 / 60-08 - vertiq.co/thrust-data, 24 V, continuous-rated rows only.",
            "Batteries - manufacturer net weight and genstattu.com list price (Sept 2026).",
            "Props - KDE blade weights; T-Motor P-series weights/prices from ligpower.com.",
            "",
            "Caveats:",
            "  Energy is drained to 0%; real packs sag under load - keep a 20-30% reserve.",
            "  Throttle is never modeled below each sheet's lowest point (40% for T-Motor), so light-load",
            "    hover current is over- rather than under-estimated.",
            "  Landing is a prescribed descent profile, not optimized.",
            "  Coaxial losses, wind, and control margin beyond thrust/weight are not modeled.",
            "  Vertiq prices are quote-only (shown as 'quote').",
            "",
            "Reproduce:",
            "python VFS_DBVF_26-27.py --data-dir Motors --battery-file Batteries \\",
            "    --vehicle-file vechicle_configs --no-plot --grid-step 1",
            "python make_result_sheet.py",
        ]
        for i, line in enumerate(notes):
            code = i >= len(notes) - 3
            fig.text(0.06, 0.9 - i * 0.022, line, fontsize=8 if code else 8.5,
                     family="DejaVu Sans Mono" if code else None,
                     weight="bold" if line.endswith(":") and not line.startswith(" ") else None)
        pdf.savefig(fig)
        plt.close(fig)


def bat_g(name):
    return int(next(b.mass_g for b in mm.load_batteries(os.path.join(HERE, "Batteries")) if b.name == name))


if __name__ == "__main__":
    out = os.path.join(HERE, "VFS_DBVF_26-27_results.pdf")
    build(out)
    print(f"Wrote {out}")
