"""Self-check for Mission Model.py. Run: python test_mission_model.py"""

import dataclasses
import importlib.util
import math
import os
import sys

spec = importlib.util.spec_from_file_location("mission_model", os.path.join(os.path.dirname(__file__),
                                                                            "Mission_Model.py"))
mm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mm
spec.loader.exec_module(mm)

from scipy.interpolate import PchipInterpolator

THROTTLE_PTS = [40.0, 60.0, 80.0, 100.0]


def make_cfg(thrust_at_100, current_at_100, prop_diameter_in=16.0, cells_s=6):
    thrust = [thrust_at_100 * f for f in (0.33, 0.55, 0.78, 1.0)]
    current = [current_at_100 * f for f in (0.33, 0.55, 0.78, 1.0)]
    return mm.MotorPropConfig(
        motor_model="test", prop_diameter_in=prop_diameter_in, motor_mass_g=100.0, prop_mass_g=20.0,
        motor_cost=50.0, prop_cost=10.0, cells_s=cells_s,
        thrust_of=PchipInterpolator(THROTTLE_PTS, thrust),
        amps_of=PchipInterpolator(THROTTLE_PTS, current),
    )


BATTERY = mm.Battery(name="test-6S", mass_g=2000.0, capacity_mah=16000.0, cost_usd=300.0, cells_s=6)


def numeric_accel_phase(a0, kk, v0, v_target, distance_cap, dt=1e-4):
    v, x, t = v0, 0.0, 0.0
    while v < v_target and x < distance_cap and t < 60:
        a = a0 - kk * v * v
        v += a * dt
        x += v * dt
        t += dt
    return t, x, v


def test_accel_phase_matches_numeric_integration():
    a0, kk, v0 = 8.0, 0.02, 1.0
    t_a, x_a, v_a = mm._accel_phase_closed_form(a0, kk, v0, v_target=10.0, distance_cap=1000.0)
    t_n, x_n, v_n = numeric_accel_phase(a0, kk, v0, 10.0, 1000.0)
    assert math.isclose(t_a, t_n, rel_tol=1e-2), (t_a, t_n)
    assert math.isclose(v_a, v_n, rel_tol=1e-2), (v_a, v_n)


def test_accel_phase_distance_capped():
    a0, kk, v0 = 8.0, 0.02, 1.0
    t_a, x_a, v_a = mm._accel_phase_closed_form(a0, kk, v0, v_target=100.0, distance_cap=5.0)
    assert math.isclose(x_a, 5.0, rel_tol=1e-6), x_a


def test_decel_phase_matches_numeric_integration():
    kk, v0 = 0.02, 8.0

    def numeric_decel(kk, v0, v_floor, distance_cap, dt=1e-4):
        v, x, t = v0, 0.0, 0.0
        while v > v_floor and x < distance_cap and t < 60:
            a = -kk * v * v
            v += a * dt
            x += v * dt
            t += dt
        return t, x, v

    t_a, x_a, v_a = mm._decel_phase(kk, v0, v_floor=1.0, distance_cap=1000.0)
    t_n, x_n, v_n = numeric_decel(kk, v0, 1.0, 1000.0)
    assert math.isclose(t_a, t_n, rel_tol=1e-2), (t_a, t_n)
    assert math.isclose(x_a, x_n, rel_tol=1e-2), (x_a, x_n)


def test_landing_is_pure_kinematics():
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    t, e = mm.landing(mm.MULTIROTOR, cfg)
    expected_t = (mm.LANDING_START_ALT_M - mm.LANDING_PHASE1_ALT_M) / mm.LANDING_PHASE1_SPEED_MPS \
        + mm.LANDING_PHASE1_ALT_M / mm.LANDING_PHASE2_SPEED_MPS
    assert math.isclose(t, expected_t, rel_tol=1e-9)
    assert e > 0


def test_hover_time_positive_for_capable_config():
    cfg = make_cfg(thrust_at_100=2800.0, current_at_100=25.0)
    ht = mm.hover_time_min(mm.MULTIROTOR, cfg, BATTERY)
    assert ht > 0


def test_hover_time_zero_when_underpowered():
    cfg = make_cfg(thrust_at_100=20.0, current_at_100=25.0)
    assert mm.hover_time_min(mm.MULTIROTOR, cfg, BATTERY) == 0.0


def test_airframe_and_propulsion_mass_sum_to_total():
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0, prop_diameter_in=20.0)
    airframe = mm.airframe_mass_g(mm.MULTIROTOR, cfg)
    propulsion = mm.propulsion_mass_g(mm.MULTIROTOR, cfg, BATTERY.mass_g)
    total = mm.total_mass_g(mm.MULTIROTOR, cfg, BATTERY.mass_g)
    assert math.isclose(airframe + propulsion, total, rel_tol=1e-9)
    assert propulsion == BATTERY.mass_g + mm.MULTIROTOR.num_rotors * (cfg.motor_mass_g + cfg.prop_mass_g)


def test_extra_payload_increases_segment_energy():
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    t_light, e_light = mm.segment(mm.MULTIROTOR, cfg, BATTERY, throttle_pct=70.0,
                                   distance_m=1000.0, extra_payload_g=0.0)
    t_heavy, e_heavy = mm.segment(mm.MULTIROTOR, cfg, BATTERY, throttle_pct=70.0,
                                   distance_m=1000.0, extra_payload_g=50_000.0)
    assert e_heavy > e_light
    assert e_light < mm.INFEASIBLE_ENERGY_MAH


def test_wings_reduce_required_tilt_and_speed_up_accel():
    """Wing lift should offload weight-support so the same throttle produces
    more usable horizontal thrust (smaller tilt-inflated drag area, faster
    acceleration) than an otherwise-identical wingless vehicle. Checked
    directly against the accel-phase helpers rather than via segment()'s
    total time/energy, which also folds in decel and cruise-speed effects
    that aren't this test's concern."""
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    winged = mm.VehicleType(
        name="winged", num_rotors=mm.MULTIROTOR.num_rotors, base_mass_g=mm.MULTIROTOR.base_mass_g,
        drag_cd=mm.MULTIROTOR.drag_cd, wing_area_m2=1.0, wing_cl=1.2,
    )
    mass_g = mm.total_mass_g(mm.MULTIROTOR, cfg, BATTERY.mass_g)
    mass_kg = mass_g / 1000.0
    weight_n = mass_kg * mm.G
    thrust_n = mm.rotor_set_thrust_n(mm.MULTIROTOR, cfg, 70.0)
    base_area = mm.frontal_area_m2(mm.MULTIROTOR, cfg)
    lift_fn = lambda v: mm.wing_lift_n(winged, v)
    no_lift_fn = lambda _v: 0.0

    for v in (2.0, 5.0, 8.0, 10.0):
        area_wing = mm._effective_area_at(thrust_n, weight_n, base_area, lift_fn, v)
        area_plain = mm._effective_area_at(thrust_n, weight_n, base_area, no_lift_fn, v)
        assert area_wing <= area_plain + 1e-9, (v, area_wing, area_plain)

    t_wing, x_wing, _ = mm._accel_phase(thrust_n, weight_n, base_area, mm.MULTIROTOR.drag_cd, mass_kg,
                                         lift_fn, True, v0=1.0, v_target=10.0, distance_cap=10_000.0)
    t_plain, x_plain, _ = mm._accel_phase(thrust_n, weight_n, base_area, mm.MULTIROTOR.drag_cd, mass_kg,
                                           no_lift_fn, False, v0=1.0, v_target=10.0, distance_cap=10_000.0)
    assert t_wing < t_plain, (t_wing, t_plain)
    assert x_wing < x_plain, (x_wing, x_plain)


def test_mission_runs_and_reports_consistent_energy():
    cfg = make_cfg(thrust_at_100=3400.0, current_at_100=28.0)
    payload_g = 220.0 * mm.LB_TO_G
    to_th = [80.0, 80.0, 80.0]
    cr_th = [60.0, 60.0, 60.0]
    total_time, total_energy, feasible = mm.mission(mm.MULTIROTOR, cfg, BATTERY, to_th, cr_th, payload_g)
    assert total_time > 0
    assert total_energy > 0
    assert isinstance(feasible, bool)
    assert feasible == (total_energy <= BATTERY.capacity_mah)


def test_mission_best_matches_manual_mission_at_its_chosen_throttles():
    cfg = make_cfg(thrust_at_100=3400.0, current_at_100=28.0)
    payload_g = 220.0 * mm.LB_TO_G
    result = mm.mission_best(mm.MULTIROTOR, cfg, BATTERY, payload_g, grid_step=2.0)
    assert set(k for k, _ in result["throttles"]) <= {"takeoff", "cruise"}
    assert len([k for k, _ in result["throttles"] if k == "takeoff"]) == 3
    assert len([k for k, _ in result["throttles"] if k == "cruise"]) == 3

    to_th = [th for k, th in result["throttles"] if k == "takeoff"]
    cr_th = [th for k, th in result["throttles"] if k == "cruise"]
    total_time, total_energy, feasible = mm.mission(mm.MULTIROTOR, cfg, BATTERY, to_th, cr_th, payload_g)
    assert math.isclose(total_time, result["total_time_s"], rel_tol=1e-9)
    assert math.isclose(total_energy, result["total_energy_mah"], rel_tol=1e-9)
    assert feasible == result["feasible"]


def test_load_vehicle_types_from_json(tmp_path_str=None):
    import json
    import tempfile

    data = [
        {"name": "Multirotor-6", "num_rotors": 6, "base_mass_g": 4500.0},
        {"name": "Hybrid", "num_rotors": 4, "base_mass_g": 5200.0, "wing_area_m2": 1.2, "wing_cl": 0.9},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        vehicles = mm.load_vehicle_types(path)
        assert len(vehicles) == 2
        assert vehicles[0].wing_area_m2 == 0.0
        assert vehicles[1].wing_area_m2 == 1.2
        assert vehicles[1].wing_cl == 0.9
    finally:
        os.remove(path)


def test_load_vehicle_types_rejects_missing_required_field():
    import json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"name": "NoMass", "num_rotors": 4}], f)
        path = f.name
    try:
        try:
            mm.load_vehicle_types(path)
            assert False, "expected ValueError for missing base_mass_g"
        except ValueError:
            pass
    finally:
        os.remove(path)


def test_load_motor_configs_from_json():
    import json
    import tempfile

    data = [
        {
            "motor_model": "TestMotor",
            "prop_diameter_in": 20, "motor_mass_g": 450, "prop_mass_g": 60,
            "motor_cost": 120, "prop_cost": 25, "cells_s": 12,
            "throttle_pct": [30, 50, 60, 70, 80, 90, 100],
            "thrust_g": [13360, 35262, 49381, 63961, 80002, 96733, 113654],
            "current_a": [10.63, 42, 68.22, 102.19, 147.52, 203.99, 251.48],
        }
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        configs = mm.load_motor_configs(path)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.motor_model == "TestMotor"
        assert cfg.prop_diameter_in == 20
        assert cfg.cells_s == 12
        assert math.isclose(cfg.thrust_g(100.0), 113654, rel_tol=1e-6)
        assert math.isclose(cfg.amps_a(50.0), 42, rel_tol=1e-6)
    finally:
        os.remove(path)


def test_load_motor_configs_defaults_model_name_to_filename():
    import json
    import tempfile

    data = [
        {
            "prop_diameter_in": 20, "motor_mass_g": 450, "prop_mass_g": 60,
            "motor_cost": 120, "prop_cost": 25, "cells_s": 6,
            "throttle_pct": [40, 100], "thrust_g": [30000, 100000], "current_a": [40, 200],
        }
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        configs = mm.load_motor_configs(path)
        assert configs[0].motor_model == os.path.splitext(os.path.basename(path))[0]
    finally:
        os.remove(path)


def test_load_motor_configs_skips_incomplete_entry_but_keeps_good_ones():
    import json
    import tempfile

    data = [
        {"motor_model": "Incomplete", "prop_diameter_in": 20},  # missing everything else
        {
            "motor_model": "Complete", "prop_diameter_in": 20, "motor_mass_g": 450, "prop_mass_g": 60,
            "cells_s": 6, "throttle_pct": [40, 100], "thrust_g": [30000, 100000], "current_a": [40, 200],
        },
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        configs = mm.load_motor_configs(path)
        assert len(configs) == 1
        assert configs[0].motor_model == "Complete"
        assert configs[0].motor_cost == 0.0  # optional field, defaulted
    finally:
        os.remove(path)


def test_load_motor_configs_raises_when_nothing_usable():
    import json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"motor_model": "Incomplete", "prop_diameter_in": 20}], f)
        path = f.name
    try:
        try:
            mm.load_motor_configs(path)
            assert False, "expected FileNotFoundError when no entry has enough data"
        except FileNotFoundError:
            pass
    finally:
        os.remove(path)


def test_load_motor_configs_accepts_bare_object_not_just_list():
    import json
    import tempfile

    data = {
        "motor_model": "BareObject", "prop_diameter_in": 20, "motor_mass_g": 450, "prop_mass_g": 60,
        "cells_s": 6, "throttle_pct": [40, 100], "thrust_g": [30000, 100000], "current_a": [40, 200],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        configs = mm.load_motor_configs(path)
        assert len(configs) == 1
        assert configs[0].motor_model == "BareObject"
    finally:
        os.remove(path)


def test_load_batteries_from_json():
    import json
    import tempfile

    data = [
        {"name": "22Ah-12S", "mass_g": 3100, "capacity_mah": 22000, "cost_usd": 610, "cells_s": 12},
        {"name": "12Ah-6S", "mass_g": 1500, "capacity_mah": 12000, "cost_usd": 270, "cells_s": 6},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        batteries = mm.load_batteries(path)
        assert len(batteries) == 2
        assert batteries[0].cells_s == 12
        assert batteries[1].cells_s == 6
    finally:
        os.remove(path)


def test_load_batteries_rejects_missing_required_field():
    import json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"name": "NoCellCount", "mass_g": 1500, "capacity_mah": 12000, "cost_usd": 270}], f)
        path = f.name
    try:
        try:
            mm.load_batteries(path)
            assert False, "expected ValueError for missing cells_s"
        except ValueError:
            pass
    finally:
        os.remove(path)


def test_run_skips_voltage_mismatched_battery_pairs():
    import csv
    import json
    import tempfile

    motor_data = [{
        "motor_model": "TestMotor6S",
        "prop_diameter_in": 16, "motor_mass_g": 100, "prop_mass_g": 20,
        "motor_cost": 50, "prop_cost": 10, "cells_s": 6,
        "throttle_pct": [40, 60, 80, 100], "thrust_g": [1000, 1500, 2100, 2800],
        "current_a": [8, 12, 18, 25],
    }]
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(os.path.join(tmp_dir, "motor.json"), "w") as f:
            json.dump(motor_data, f)

        only_12s = [mm.Battery(name="12S-pack", mass_g=2000.0, capacity_mah=16000.0, cost_usd=300.0, cells_s=12)]
        df = mm.run(tmp_dir, grid_step=5.0, batteries=only_12s)
        assert len(df) == 0  # the motor is 6S-rated; no compatible battery was given

        mixed = only_12s + [mm.Battery(name="6S-pack", mass_g=2000.0, capacity_mah=16000.0, cost_usd=300.0, cells_s=6)]
        df2 = mm.run(tmp_dir, grid_step=5.0, batteries=mixed)
        assert len(df2) == 1
        assert df2.iloc[0]["battery"] == "6S-pack"


def test_run_auto_series_stacks_a_lower_voltage_battery_to_reach_the_motor():
    import json
    import tempfile

    motor_data = [{
        "motor_model": "TestMotor24S",
        "prop_diameter_in": 16, "motor_mass_g": 100, "prop_mass_g": 20,
        "motor_cost": 50, "prop_cost": 10, "cells_s": 24,
        "throttle_pct": [40, 60, 80, 100], "thrust_g": [1000, 1500, 2100, 2800],
        "current_a": [8, 12, 18, 25],
    }]
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(os.path.join(tmp_dir, "motor.json"), "w") as f:
            json.dump(motor_data, f)

        # 24 is a multiple of 12 (x2) but not of 8 (24/8 = 3 -- that one
        # *is* an integer multiple too, so both should synthesize a stack).
        base_12s = mm.Battery(name="12S-pack", mass_g=2000.0, capacity_mah=16000.0, cost_usd=300.0, cells_s=12)
        odd_9s = mm.Battery(name="9S-pack", mass_g=500.0, capacity_mah=5000.0, cost_usd=80.0, cells_s=9)
        df = mm.run(tmp_dir, grid_step=5.0, batteries=[base_12s, odd_9s])

        assert len(df) == 1  # 9S has no integer stack to 24S -- still skipped
        row = df.iloc[0]
        assert row["battery"] == "12S-pack_x2S"
        assert row["cells_s"] == 24
        assert row["battery_capacity_mah"] == 16000.0  # series stacking doesn't add capacity

        stacked = mm.series_stack_battery(base_12s, 2)
        assert stacked.mass_g == 4000.0  # mass doubles with 2 packs in series
        assert stacked.cost_usd == 600.0
        assert stacked.capacity_mah == 16000.0
        assert stacked.cells_s == 24


def test_parasite_drag_area_zero_for_wingless():
    assert mm.parasite_drag_area_m2(mm.MULTIROTOR) == 0.0  # wing_area_m2=0 zeroes it regardless of parasite_cd0


def test_parasite_drag_area_matches_formula_for_winged():
    winged = dataclasses.replace(mm.MULTIROTOR, wing_area_m2=2.0, wing_cl=0.6, drag_cd=0.4, parasite_cd0=0.05)
    assert math.isclose(mm.parasite_drag_area_m2(winged), 0.05 * 2.0 / 0.4, rel_tol=1e-12)


def test_effective_area_at_adds_parasite_area():
    lift_fn = lambda _v: 0.0
    base = mm._effective_area_at(thrust_n=100.0, weight_n=50.0, base_area_m2=0.2, lift_fn=lift_fn, v=5.0)
    with_parasite = mm._effective_area_at(thrust_n=100.0, weight_n=50.0, base_area_m2=0.2, lift_fn=lift_fn, v=5.0,
                                           parasite_area_m2=0.3)
    assert math.isclose(with_parasite, base + 0.3, rel_tol=1e-12)


def test_parasite_drag_lowers_equilibrium_velocity_for_a_winged_vehicle():
    """Regression for the validation spike's finding: a winged vehicle's
    equilibrium cruise speed came out at 150-200 m/s (10x too fast for any
    real aircraft this size) because the only drag area modeled was the
    prop/frame frontal area -- the wing's own parasite drag wasn't in the
    equation at all. Adding it (parasite_cd0 > 0) must pull the equilibrium
    speed down, not leave it unchanged."""
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    thrust_n = mm.rotor_set_thrust_n(mm.MULTIROTOR, cfg, 100.0)
    base_area = mm.frontal_area_m2(mm.MULTIROTOR, cfg)
    winged_no_parasite = dataclasses.replace(mm.MULTIROTOR, wing_area_m2=2.0, wing_cl=0.6, parasite_cd0=0.0)
    winged_with_parasite = dataclasses.replace(mm.MULTIROTOR, wing_area_m2=2.0, wing_cl=0.6, parasite_cd0=0.045)
    weight_n = 100.0  # well under this cfg's ~177 N max thrust so both cases can actually reach equilibrium
    lift_fn_a = lambda v: mm.wing_lift_n(winged_no_parasite, v)
    lift_fn_b = lambda v: mm.wing_lift_n(winged_with_parasite, v)
    v_eq_no_parasite = mm._equilibrium_velocity(thrust_n, weight_n, base_area, winged_no_parasite.drag_cd, lift_fn_a)
    v_eq_with_parasite = mm._equilibrium_velocity(
        thrust_n, weight_n, base_area, winged_with_parasite.drag_cd, lift_fn_b,
        parasite_area_m2=mm.parasite_drag_area_m2(winged_with_parasite),
    )
    assert v_eq_with_parasite < v_eq_no_parasite, (v_eq_with_parasite, v_eq_no_parasite)


def test_equilibrium_velocity_is_where_drag_balances_horizontal_thrust():
    """The cruise speed is no longer prescribed -- it's the root of
    thrust_horizontal(v) = drag(v). Check both the wingless closed-form case
    and the winged one, by evaluating the balance at the returned speed."""
    for lift_fn, thrust_n in (((lambda _v: 0.0), 4000.0), ((lambda v: 0.5 * mm.RHO_AIR * 0.9 * 1.2 * v * v), 2000.0)):
        weight_n = 1500.0
        area, cd = 0.4, 1.0
        v_eq = mm._equilibrium_velocity(thrust_n, weight_n, area, cd, lift_fn)
        assert v_eq > 0
        effective_weight_n = max(weight_n - lift_fn(v_eq), 0.0)
        thrust_h = math.sqrt(thrust_n ** 2 - effective_weight_n ** 2)
        drag = 0.5 * mm.RHO_AIR * cd * area * (thrust_n / thrust_h) * v_eq ** 2
        assert math.isclose(thrust_h, drag, rel_tol=1e-6)

    # Can't even lift itself: no forward speed, and the leg is infeasible.
    assert mm._equilibrium_velocity(1000.0, 1500.0, 0.4, 1.0, lambda _v: 0.0) == 0.0


def test_cruise_speed_is_capped_at_max_speed_mps():
    """A vehicle with plenty of thrust to spare (equilibrium speed well
    above the governor) should cruise at max_speed_mps, not its bare
    thrust/drag equilibrium."""
    cfg = make_cfg(thrust_at_100=6000.0, current_at_100=25.0)  # overpowered -> high v_eq
    fast_governed = dataclasses.replace(mm.MULTIROTOR, max_speed_mps=15.0)
    t, _e = mm.segment(fast_governed, cfg, BATTERY, throttle_pct=100.0, distance_m=3000.0)
    # Below governor, hover mass ~120 kg dominates the leg at 15 m/s:
    # 3000 m at (up to) 15 m/s takes at least 200 s.
    assert t >= 200.0

    uncapped = dataclasses.replace(mm.MULTIROTOR, max_speed_mps=1000.0)
    t_uncapped, _e2 = mm.segment(uncapped, cfg, BATTERY, throttle_pct=100.0, distance_m=3000.0)
    assert t_uncapped < t  # same vehicle, no governor -> faster


def test_decel_is_powered_so_less_drag_never_makes_a_leg_slower():
    """Regression: with the old unpowered-coast decel, both the distance and
    time to decay from cruise speed down to the 1 m/s floor scaled as 1/kk,
    so a LOWER-drag vehicle took LONGER to finish a leg -- backwards for a
    model whose whole point is flying it as efficiently as possible. Braking
    with the rotors instead of coasting on drag should make leg time
    monotonically non-increasing as drag drops."""
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    times = []
    for cd in (1.28, 0.5, 0.1):
        v = dataclasses.replace(mm.MULTIROTOR, drag_cd=cd, max_speed_mps=1000.0)  # ungoverned
        t, _e = mm.segment(v, cfg, BATTERY, throttle_pct=80.0, distance_m=2000.0)
        times.append(t)
    assert times[0] > times[1] > times[2], times


def test_leg_is_no_longer_flown_at_the_old_fixed_10_mps_target():
    """A vehicle with thrust to spare now cruises at its own equilibrium
    speed, so it covers the leg faster than the retired 10 m/s target
    would have (2000 m / 10 m/s = 200 s, before takeoff/landing)."""
    cfg = make_cfg(thrust_at_100=3000.0, current_at_100=25.0)
    t, _e = mm.segment(mm.MULTIROTOR, cfg, BATTERY, throttle_pct=80.0, distance_m=2000.0)
    assert t < 200.0
    assert not hasattr(mm.MULTIROTOR, "cruise_velocity_mps")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
