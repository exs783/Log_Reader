# Motor sourcing notes

**T-Motor MN6007II KV320** -- the only motor in the DBVF 26-27 lineup.

- Product: https://rcdrone.top/products/t-motor-mn6007ii-mn6007-ii-kv1?variant=44004186161376
  (KV320 variant, $181.65). Motor weight 155 g (180 g is the package weight).
- Thrust/current curves: T-Motor bench sheet (`~/Desktop/tmotor_mn6007II_data.pdf`),
  measured at ~24.6 V, so `cells_s` = 6. One entry per tested prop (P21x6.3, P22x6.6).
- Prop mass/cost are per rotor: one prop, half the price of a CW/CCW pair.
  - P22x6.6: 45 g (T-Motor, 45+/-2 g), $134.90/pair (shop.tmotor.com).
  - P21x6.3: 58 g (ligpower.com listing), $155.47/pair (rcdrone.top). The
    rcdrone page lists 14.5 g with a 4 kg thrust limit instead, which doesn't
    fit a prop the sheet runs at 5.4 kg. That listing looks like it's copied
    from another prop. Weigh a real blade to settle it.
- Coaxial use: T-Motor says to expect ~25% thrust loss. That isn't applied here.

**MN6007II KV320 + P18x6.1 (ESTIMATED).** T-Motor never tested this motor on an 18" prop,
so this curve is extrapolated from the 21" and 22" data by `estimate_prop_curve.py` (see
its docstring; checked against KDE's measured 15.5" data, thrust within 6% at 50-100%
throttle, current padded +11%). Prop: T-Motor P18x6.1, 31.5 g, $82.90/pair (ligpower.com).

# KDE Direct (Bend, Oregon -- "Designed in the USA, manufactured in China")

Every curve is copied straight from KDE's own performance sheet (`KDE<model>_rev2.xlsx`,
linked on each product page at kdedirect.com/products/<model>), 6S (25.2 V) blocks only.
- Thrust uses KDE's *measured* column, taken at 3,500 ft elevation. KDE's sea-level
  column would read about 10% higher, so these numbers err low for a sea-level field.
- Motor mass is KDE's listed "Motor Weight"; price is kdedirect.com list (Sept 2026).
- Props: KDE CF blades, per-blade weight 10.1 g (12.5"), 14.7 g (15.5"), 18.8 g (18.5").
  prop_mass_g is 2 or 3 blades (dual/triple); prop_cost is half a CW/CCW set.
- 21.5" blocks skipped (no blade weight, and bigger than this airframe needs).

| Motor | Weight | Price | 6S props tested |
|---|---|---|---|
| KDE2814XF-515 | 95 g | $105 | 12.5 |
| KDE3520XF-400 | 190 g | $145 | 12.5, 15.5, 18.5 |
| KDE4014XF-380 | 160 g | $155 | 12.5, 15.5, 18.5 |
| KDE4213XF-360 | 175 g | $170 | 12.5, 15.5, 18.5 |
| KDE4215XF-465 | 195 g | $195 | 12.5, 15.5, 18.5 |
| KDE5215XF-330 | 305 g | $260 | 15.5, 18.5 |
| KDE5215XF-435 | 305 g | $260 | 15.5, 18.5 |

# Vertiq (Philadelphia, PA -- 81-08 G2 is on DIU's Blue UAS Framework)

Integrated motor + ESC modules. Curves are Vertiq's published bench tests at 24 V (6S),
one page per module/prop at https://www.vertiq.co/thrust-data (e.g.
/vert-60-08-300kv-p18x6-1r-24v).
- Vertiq tests by *commanded voltage*, not throttle %. throttle_pct here is commanded
  voltage / the highest commanded voltage still rated "Continuous", x 100. The
  "Intermittent" rows are dropped, and so is any row after thrust stops rising.
  Throttle is only an index into the curve, so this rescaling doesn't change the physics.
- motor_mass_g is Vertiq's "Base Module Weight", **which includes the ESC** (every
  other motor here leaves the ESC out, so Vertiq carries a small weight penalty in the
  comparison): 40-06 91.3 g, 40-14 154.4 g, 60-08 218 g.
- Price: not published (quote on request), so motor_cost is left at 0.
- Props: T-Motor P-series polish CF (the props Vertiq tested with). Weight and price per
  prop from ligpower.com: P12x4 14.5 g, P13x4.4 14.2 g, P14x4.8 15 g, P15x5 21 g,
  P16x5.4 25 g, P17x5.8 26.5 g, P18x6.1 31.5 g, P20x6 47 g, P22x6.6 45 g.
- Skipped: props under 12" (not enough thrust for this airframe), P24/G27/P28/MEJ24
  (no published blade weight), and every 81-08 test except one 24" prop (not 6S).

# MAD Components (mad-motor.com -- Chinese maker, sold via rcdrone.top / OZ Robotics)

Every curve is MAD's own published test table (6S/8S/12S, AMPX ESC), copied from the
table images on each product page. The images were OCR'd, every row was checked
(V x I = input power, thrust/W matches the printed column, thrust and current rise with
throttle), and the rows OCR couldn't read were typed in by eye. A few rows are inconsistent
in MAD's own table (e.g. 5008 300KV Havoc 30%: 22.0 W vs 24.28 V x 0.94 A); copied as published.
Curves start at 30% throttle.

- **V62 IPE 280KV** (`MAD_V62_IPE_280KV.json`): 12S, Fluxer VTOL 18.1x7.2. 270 g, $128.
  Prop mass 32 g is a **placeholder** (T-Motor V18x7.2; MAD doesn't publish it), $39.50 is
  half the $79 pair price (ozrobotics.com).
- **7005 EEE 115KV (12S) / 230KV (6S)** (`MAD_7005_EEE.json`): Fluxer Pro 24x7.2. 186 g
  (page also lists 196 g), $199. Prop 60 g (rcdrone.top), $84.95 = half the $169.90 pair
  price (ozrobotics.com). Most efficient motors in the sweep, but needs 24" of rotor clearance.
- **5008 EEE, all KVs** (`MAD_5008_EEE.json`): $76. Weight from each KV's data sheet:
  170KV 130 g, 240KV 131 g, 300KV 127 g, 340KV 127 g, 400KV 138 g.

| KV | Cells | Props tested |
|---|---|---|
| 170 | 12S | Fluxer Pro 17x5.8, 18x6.1; Havoc 18x5.7 |
| 240 | 6S | Fluxer Pro 20x6.0, 21x6.3, 22x6.6; Havoc 22x7.0 |
| 240 | 8S | Fluxer Pro 17x5.8, 18x6.1; Havoc 18x5.7 (no 8S pack, so unused) |
| 300 | 6S | Fluxer Pro 18x6.1, 19x5.7, 20x6.0; Havoc 18x5.7 |
| 340 | 6S | Fluxer Pro 17x5.8, 18x6.1, 19x5.7; Havoc 18x5.7 |
| 400 | 6S | Fluxer Pro 16x5.4, 17x5.8 |

  Prop mass is the single-prop weight on the rcdrone.top listing (Fluxer 16x5.4 28 g,
  17x5.8 32.5 g, 18x6.1 31.5 g, 19x5.7 41 g, 20x6.0 47 g, 21x6.3 58 g, 22x6.6 56 g;
  Havoc 18x5.7 47 g, 22x7.0 65 g). Fluxer prices are half the listed price (listed as
  pairs, though not every listing says so); Havoc is the listed price (sold push/pull, single).

# Checked, no public thrust data (not added)

- Ewing Motors 3115 860KV (Ashland, OH, made in USA): no thrust/current sheet published.
- Dronesmith USA, USA Drone Motors (Los Angeles): custom-wound, quote-only.
- Unusual Machines (Orlando): 2207/2807/3220 FPV sizes, too small for this airframe.
- ThinGap, Moog, Allied Motion, LaunchPoint, ePropelled, NeuMotors, Oransi: no
  off-the-shelf 6S multirotor motor with a published thrust sheet.
