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

## 2026-09-11: four more real heavy-lift/agri motors

Added `TMOTOR_P80III_KV100.json`, `TMOTOR_P80III_KV120.json`,
`HOBBYWING_X9_PLUS.json`, `HOBBYWING_X11_PLUS.json`.

- **T-Motor P80III (KV100 / KV120)** -- agricultural UAV motor, 30in G30x10.5
  prop. Same situation as the existing T-Motor/MAD files: T-Motor's store
  pages (store.tmotor.com/product/P80-v3-pin-kv100/kv120-p-type.html) only
  publish a single 100%-throttle endpoint (thrust + current at that one
  point), not a sweep. `throttle_pct: [100]` only -- **no invented
  intermediate points**. `"_data_source": "endpoints_only"`. Motor mass
  (649g) and price ($199.90) are real, from the same pages. Prop mass/price
  (97g/blade, $167.95 each) are from T-Motor's separate G30x10.5 propeller
  listing. Because this is a single-point curve, `load_motor_configs()`
  currently skips these two entries (needs >=2 throttle points spanning
  [40,100]%) -- they're included for completeness/future use if T-Motor ever
  publishes a full sweep, same rationale as the existing endpoints-only
  T-Motor/MAD files.

- **Hobbywing X9 Plus / X11 Plus** -- agricultural power systems (motor +
  ESC + prop as one SKU). Both **DO** publish a full measured throttle
  sweep (22 points, 33-100%) directly on their product pages
  (hobbywing.com/en/products/xrotor-x9-plus112 and .../xrotor-x11-plus270).
  Transcribed row-for-row, nothing interpolated or invented.
  `"_data_source": "measured_full_curve"`. These two are real, usable
  additions to the model (cells_s=14, real masses) -- verified they load and
  interpolate correctly via `load_motor_configs()`.

  Honesty caveat on mass: Hobbywing only publishes **total system weight**
  (motor+ESC+cable+prop together), not motor-only mass. Where the prop mass
  was separately published (X9 Plus: 242g stated on the spec page; X11 Plus:
  427g from Hobbywing's standalone 4314-propeller listing), `motor_mass_g`
  here is **derived** as (published total system weight) minus (published
  prop mass) -- a plain subtraction of two real numbers, not a fabricated
  or modeled value, but flagged here since it's not itself a number
  Hobbywing prints directly. `motor_cost` is left `null` for both: these
  only sell as a bundled combo and street price varies a lot by
  retailer/region ($180-350 for X9 Plus, $240-410 for X11 Plus); X9 Plus's
  `prop_cost` is left `null` for the same reason, X11 Plus's prop_cost
  ($90.00) came from Hobbywing's separate propeller SKU.
