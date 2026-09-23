# Vehicle config notes (DBVF 26-27)

One airframe: **DBVF_Quad**, a medium-size, wingless X quadcopter.

- **unloaded_mass_g = 4535.924 (10.00 lb):** the whole aircraft without water --
  frame, arms, electronics, grabber, empty payload container, motors, props,
  and battery -- is held at 10 lb, the weight RFP Table 4 scores (R = 1.4 at
  10.00-11.99 lb; R = 1.5 needs < 10 lb). For each motor/battery combo the
  model sets `base_mass_g` to whatever is left after motors, props, and
  battery, so the JSON's `base_mass_g` (0) is overwritten. Combos whose
  motors + battery alone exceed 10 lb are skipped. The 3 lb water payload is
  added only on legs where one is carried (13 lb loaded, under the 25 lb cap).
  `arm_mass_g` and `arm_extra_mass_per_in` are 0 (the arms are inside the 10 lb).
- **drag_cd = 1.0:** flight-test and wind-tunnel studies of quadcopter
  frontal drag put Cd at 0.8-1.2 (Hattenberger et al. 2023, "Evaluation of
  drag coefficient for a quadrotor model"). 1.0 is the middle of that range.
  Frontal area comes from the model's own estimate: (prop_in * rotors + 144 in^2) / 1550.
- **max_speed_mps = 15 (~34 mph):** a typical top cruise speed for a
  medium photo/utility quad. Legs on this field are only tens of metres, so
  the aircraft rarely gets near this speed anyway.
- Wing / tailsitter fields are left at their defaults (no wing).

The old heavy-lift airframes (Hexacopter, Quadcopter, x_8, tailsitters)
were removed. They're still in `mission-model/vechicle_configs/`.
