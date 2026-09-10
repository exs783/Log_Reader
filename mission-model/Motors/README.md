# Heavy-lift motor JSON files

One JSON per motor, in your schema. Kept your exact keys; added a few `_`-prefixed
fields (`_verified`, `_data_source`, `_notes`) for provenance so nothing is ambiguous.

## Important: what is real vs. what is missing

You asked for **real bench data**. Here is the honest situation after reading the
actual manufacturer pages:

- **T-Motor (U15 series)** and **MAD (AM / M series)** do **NOT** publish a full
  throttle-vs-thrust-vs-current sweep in any readable form — only endpoints
  (max thrust, peak current, rated power). I confirmed this by opening the store
  pages directly. So those files carry `throttle_pct: [100]` with the real
  endpoint values only. **I did not invent the 30–90% points** — fabricated
  performance data would corrupt any thrust model you build.
  `"_data_source": "endpoints_only"`.

- **Hobbywing (H-series coaxial)** DOES publish full measured tables (datasheet
  PDFs). `HOBBYWING_H15MD_coaxial.json` has the **complete real curve** at both
  24S and 28S, row for row. `"_data_source": "measured_full_curve"`.

## Coaxial files

`HOBBYWING_*` are coaxial (two counter-rotating motors on one vertical axis).
`thrust_g` and `current_a` are **per-axis totals** (both rotors together), not
per motor. See earlier discussion for plate/vertical mounting.

## Fields left null

`prop_mass_g`, `motor_cost`, `prop_cost`, and some `cells_s` are `null` where the
maker doesn't publish them. MAD AM160 and the M-series are **750–800 V HV** motors,
not 24S battery motors — `cells_s` is null and `voltage_v` is given instead.

## To finish the curves (pick one, I can do it next)

1. **Model the missing 30–90% points** from momentum theory, anchored to each real
   endpoint, flagged `"modeled"`. Fast, usable placeholders you replace later.
2. **Independent test databases** (e.g. Tyto Robotics / thrust-stand datasets) may
   have measured sweeps for some of these — I can dig there.
3. **Send me any datasheet/Excel you already have** (your M50C35 example clearly
   came from one) and I'll transcribe it exactly.
