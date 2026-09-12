# Propeller sourcing notes

## Not wired in yet

Unlike `Motors/`, `Batteries/`, and `vechicle_configs/`, this folder is **not**
glob-loaded by `Mission_Model.py` -- there's no propeller loader in the sim
yet. Treat everything here as reference/future-use data, not active inputs.

## Existing files fixed, not touched otherwise

`MSC_54x20.json` and `FLUXER_PRO_60x20_in.json` both had a JSON syntax bug --
missing the closing `]`, with a stray trailing `}` instead. Fixed that one
character in each (`}` -> `]`) so the files parse. No field or value was
changed.

## Four new files added 2026-09-11

All four are real published SKUs from manufacturer product pages, matching
motors already in `Motors/` (T-Motor U15/NS series, MAD AM-series). Only
`name`, `weight`, `max_rpm_rec`, `Thrust_Limit` came from sourcing --
`type`/`material` are constant per the schema. `null` means the manufacturer
doesn't publish that field, not that it's zero.

- **XOAR_40x10** (Xoar PJP-T-L 40x10): weight 380g (+-15g), max RPM <4000,
  thrust 40.2kg measured at 3864 RPM on Xoar's published performance table --
  used as `Thrust_Limit` (40200g) since it's essentially at the rated max RPM.
  Source: https://www.xoarintl.com/multicopter-propellers/precision-pair/PJP-T-L-Precision-Pair-Multicopter-Carbon-Fiber-Propeller-Low-Kv-Motor/

- **XOAR_50x10** (Xoar PJP-T-L 50x10): weight 830g (+-20g), max RPM <3500.
  No thrust table published for this size on the same page, so
  `Thrust_Limit` is `null`.
  Source: same page as above.

- **TMOTOR_VZ38x15** (T-Motor VZ38x15, "large VTOL logistics" propeller):
  page lists "average single blade weight" as 188g for this one-piece
  2-blade prop -- `weight` here is 376g (2x188), a straightforward doubling
  of the published per-blade figure, not an independently published total.
  Recommended max RPM 3950 (limit RPM 4400), recommended max thrust 44kg
  (limit thrust 55kg) -- used the recommended (not limit) values.
  Source: https://store.tmotor.com/product/vz38-15-carbon-fiber-vtol-propeller.html

- **TMOTOR_NS62x24** (T-Motor NS62x24, manned-aircraft/heavy-lift series --
  same family as the NS47x18 that ships with the U15L kit already in
  `Motors/`): page publishes only "weight (single blade)" = 920g (+-40g) for
  this 2-blade-integrated prop, no RPM or thrust spec at all. `weight` is
  1840g (2x920, same doubling caveat as VZ38x15 above); `max_rpm_rec` and
  `Thrust_Limit` are `null` because T-Motor genuinely doesn't publish them.
  Source: https://store.tmotor.com/product/ns62x24-manned-aircraft-carbon-fiber.html

## Leads that didn't pan out (no real numbers found)

- MAD FLUXER PRO 78x30 (the companion prop to the AM160 motor already in
  `Motors/`) -- confirmed as a real product (mad-motor.com and Alibaba both
  list it, ~$1,225), but neither page publishes weight, RPM, or thrust.
  Not added rather than guess.
- MAD CB2 PROP 47.5x18, MAD FLUXER PRO 47.5x17.4 -- same story, real SKUs,
  no published performance numbers.
- T-Motor NS47x18 / NS52x20 -- same "single blade" weight-only spec as
  NS62x24 above, but without even a companion RPM/thrust figure elsewhere;
  skipped in favor of NS62x24 which at least matches the larger motors.
- Hobbywing X11 Max 48x17.5 coaxial prop -- real product (Arris Hobby
  listing), no weight/RPM/thrust published on the page.
