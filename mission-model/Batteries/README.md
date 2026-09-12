# Battery sourcing notes

The original three packs (YANGDA XTurbo, Tattu NEO, MAD 55Ah) are real
manufacturer SKUs and were left unchanged -- their implied energy density
(157-305 Wh/kg depending on chemistry) matches published Li-ion/solid-state
UAV pack density (~180-250 Wh/kg typical, up to ~400 Wh/kg for newer
solid-state cells) and their masses were already credible.

Three bigger options added 2026-09-09 to cover "run a bigger pack" and "run
12S packs in series/parallel to reach 24S":

- **MAD_24S_100Ah_solid_state**: same manufacturer, same Wh/kg and $/Ah as
  MAD's real 24S 55Ah pack (already in this lineup), scaled up to 100Ah. Not
  a real listed SKU -- MAD's largest published 24S product at search time
  was the 55Ah pack -- but consistent with their own product line's density.

- **12Sx2_series_24S_66Ah**: two 12S packs stacked in series (voltage adds,
  capacity doesn't) to reach 24S -- the standard way to build a
  higher-voltage pack out of smaller ones when a native 24S SKU isn't
  available. Mass is 2x a real 12S 66Ah/10.7kg pack (Lipower spec: a 12S
  30Ah pack weighs 5.2kg, a 12S 66Ah pack weighs 10.7kg -- ~270 Wh/kg).
  Cost uses MAD's published 12S 20Ah price ($898, ~$45/Ah) applied to 66Ah
  and doubled for two packs, since the generic 12S source didn't list price.

- **12Sx4_2P2S_24S_132Ah**: four of the same 12S packs, wired as two
  parallel pairs then those pairs in series (2P2S) -- doubles the capacity
  of the series-only pack above at double the mass/cost. This is the
  "parallel" 12S setup: parallel packs add capacity, series packs add
  voltage, and a 24S-with-more-capacity pack needs both.

cells_s is recorded as 24 for all three (that's the pack's usable voltage
class after series-stacking), since Mission_Model.py only pairs a battery
with a motor of matching cells_s.

Two more added 2026-09-09 from a user-supplied cell datasheet: **Farasis
3.7V 53Ah pouch, 0.822 kg, 237 Wh/kg, 8C, ~$18/cell** (bulk raw-cell price,
made-in-china.com listing for the same Farasis 53Ah NMC pouch part).
24S requires 24 of these in series regardless of parallel count -- that part
isn't a choice. Two builds:

- **Farasis_24S1P_53Ah**: 24 cells in series only, no parallel. 53Ah,
  21.7kg (24 x 0.822kg x 1.10 for estimated BMS/wiring/enclosure
  overhead), $432 in raw cells (no assembly/BMS/labor cost included).
- **Farasis_24S2P_106Ah**: two of those series strings in parallel. 106Ah,
  43.4kg, $864 raw.

Both work out to **~217 Wh/kg assembled** -- below MAD's 55/100Ah packs
(293 Wh/kg) and YANGDA's 44Ah pack (305 Wh/kg), though above Tattu NEO's
30Ah pack (157 Wh/kg). Wh/kg is a per-cell property: no series/parallel
wiring changes it, so this cell can't beat the denser packs already in the
lineup on weight at any pack size -- only on price, since $432-864 in raw
cells is far below the ~$85-90/Ah retail price of the finished UAV packs
above (which include BMS, connectors, smart monitoring, and safety
certification that a raw-cell build would still need to add).

## 2026-09-11 additions

Four more packs, all real manufacturer/cell specs (no fabricated numbers):

- **ZYE_UltraHV_24S_66Ah**: a real listed SKU (ZYE "Ultra HV" semi-solid-state
  24S, 66000mAh). Manufacturer spec page
  ([zyebattery.com](https://www.zyebattery.com/66000mah-hv-solid-state-battery.html))
  states 21kg and 297 Wh/kg for the 24S/66Ah variant (implies ~94.5V pack
  voltage, i.e. ~3.94V/cell nominal -- consistent with the 4.45V-charge HV
  chemistry the "HV" name signals). Price $1,249 confirmed both on
  [motionew.com](https://www.motionew.com/shop/power-solution/zye-power-ultra-hv-semi-solid-state-battery-24s-66000mah/)'s
  product listing and independently on the
  [rcdrone.top 24S collection page](https://rcdrone.top/collections/24s-lipo-battery).
  297 Wh/kg sits between Tattu NEO (157) and MAD/YANGDA (293-305), a
  believable mid-pack number for this chemistry class.

- **Grepow_TARBG_24S_30Ah**: real pack (model "TARBG3030K24S10X") from
  Grepow's own spec table in their heavy-lift battery guide
  ([grepow.com blog](https://www.grepow.com/blog/how-to-choose-batteries-for-100kg-200kg-payload-heavy-lift-drones.html)):
  24S, 88.8V, 30Ah, 2664Wh, 9345g, 285.00 Wh/kg -- all four numbers are
  internally consistent (2664Wh / 9.345kg = 285.03 Wh/kg; 2664Wh / 30Ah =
  88.8V = 24 x 3.7V). This is Grepow's real NMC811 semi-solid-state
  product line (their 280-350 Wh/kg series), and at 285 Wh/kg it's a
  believable step above Tattu NEO's 157 Wh/kg 30Ah pack at less than a
  third of the weight (9.3kg vs 16.7kg) for the *same* 24S/30Ah spec --
  the two are directly comparable since they're the same voltage and
  capacity class. **Price is not published anywhere on Grepow's site**, so
  cost_usd here is estimated the same way the 2026-09-09 12S-series entries
  were: applying this lineup's average $/Ah across the other four priced,
  finished 24S packs (MAD 55Ah $85.20/Ah, MAD 100Ah $85.20/Ah, YANGDA 44Ah
  $88.64/Ah, Tattu NEO 30Ah $94.48/Ah -- average $88.38/Ah) to Grepow's
  30Ah capacity: 30 x $88.38 = **$2,651.39**. Flagged as an estimate, not
  a quoted price.

- **Amprius_SA08_24S1P_11Ah** and **Amprius_SA08_24S2P_22Ah**: derived
  builds from a real silicon-anode pouch cell datasheet, same pattern as
  the Farasis entries above. The Amprius SA08 cell datasheet
  ([Tenergy Power listing](https://power.tenergy.com/amprius-sa08-3-4v-11-05ah-li-ion-polymer-rechargeable-pouch-cell-battery/),
  cross-checked against
  [About:Energy's SA08 library page](https://www.aboutenergy.io/voltt-battery-library/amprius-sa08)):
  3.4V nominal, 11.05Ah, 106.5g +/-2g, 37.57Wh (~353 Wh/kg bare cell).
  24S requires 24 in series regardless of parallel count. Rather than
  guessing a BMS/wiring overhead factor, both mass and price were scaled
  from a **real finished Amprius-SA08 pack**: Upgrade Energy's "Gold V1
  6S2P 22.2Ah" pack
  ([upgradeenergytech.com](https://www.upgradeenergytech.com/products/gold-v1-6s2p-22ah-amprius-sa08)) --
  12 cells (6S2P), 1.35kg assembled, $1,160 -- giving real per-cell ratios
  of 112.5 g/cell (a ~5.6% assembly overhead over the bare 106.5g cell)
  and $96.67/cell.
  - **Amprius_SA08_24S1P_11Ah**: 24 cells in series only. 11.05Ah (11050
    mAh), 24 x 112.5g = 2700g, 24 x $96.67 = $2,320.00.
  - **Amprius_SA08_24S2P_22Ah**: two series strings in parallel. 22.1Ah
    (22100 mAh), 5400g, $4,640.00.

  Sanity check: pack voltage 24 x 3.4V = 81.6V, so the 24S1P build carries
  81.6V x 11.05Ah = 901.7Wh in 2.7kg = **~334 Wh/kg assembled** -- just
  under the 338-351 Wh/kg Amprius publishes for the bare SA08 cell, which
  is exactly the direction assembly overhead should push it (an assembled
  pack can only be less dense than its own cells, never more). This makes
  Amprius_SA08_24S1P the densest pack in the whole lineup, consistent with
  silicon-anode chemistry being marketed specifically on energy density;
  it's also the most expensive per Ah (~$210/Ah) of any pack here, which
  tracks with silicon-anode cells being a premium/early-availability
  product rather than a commodity LiPo.

## 2026-09-11 additions -- pushing past 305 Wh/kg

Four more packs, all derived from real, currently-sourceable cell
datasheets (same "24 cells in series, real per-cell mass/price, labeled
derived" pattern as the Farasis and Amprius SA08 entries above). Goal was
to beat this lineup's previous best (YANGDA XTurbo / MAD, ~293-305 Wh/kg)
and to add smaller ~15-30Ah options alongside bigger ones, since a pack
only needs to carry the energy a mission actually needs.

Two candidates were investigated and **rejected** for lack of a real
per-cell datasheet: Amprius's newer **SiCore 450/500** press releases
(450 Wh/kg shipping to Airbus AALTO; 500 Wh/kg announced for Q4 2026,
[amprius.com](https://amprius.com/amprius-launches-sicore-450-wh-kg-high-energy-cell-with-near-term-mass-production-capability-to-scale/),
[ir.amprius.com SiCore500 release](https://ir.amprius.com/news-events/press-releases/detail/174/amprius-introduces-sicore500-high-energy-density-cells-for-unmanned-aviation))
publish only a headline Wh/kg figure -- no per-cell capacity or mass, so
building a pack from them would mean fabricating one of the two required
numbers. Skipped rather than guessed, per this file's sourcing rule.

What *does* have a full datasheet is Amprius's newer high-power pouch
cell, the **SA88** (successor to the SA08 already in this lineup):

- **SA88 cell**: 3.45V nominal, 10.5Ah, 96.5g, 36.23Wh -> **375 Wh/kg bare
  cell** (vs. SA08's 338-351 Wh/kg), 9.7C continuous discharge. Datasheet
  via [About:Energy's SA88 library page](https://www.aboutenergy.io/voltt-battery-library/amprius-sa88),
  cross-listed at [amprius.com/products/sa88](https://amprius.com/products/sa88/).
  Price: **$40.50/cell** at the 100-999 unit tier on
  [Alibaba's Amprius SA08/SA88 listing](https://www.alibaba.com/product-detail/Highest-Quality-Rechargeable-Lithium-Pouch-Cell_1601764602607.html).
  No finished SA88 pack was found, so mass overhead uses the *same real
  ratio* the SA08 entries above derived from an actual finished
  Amprius pack (Upgrade Energy's Gold V1 6S2P: 112.5g assembled per
  106.5g bare cell, ~5.6% overhead) -- an analogy across the same
  manufacturer's cell family, not a fabricated number, and flagged as such.
  - **Amprius_SA88_24S1P_10Ah**: 24 cells in series only. 10500mAh,
    24 x 96.5g x 1.0563 = 2447g, 24 x $40.50 = $972.00.
    82.8V x 10.5Ah = 869.4Wh / 2.447kg = **~355 Wh/kg assembled** -- the
    densest pack in the whole lineup, and (at 2.4kg) the lightest-mass
    way to carry any meaningful energy here.
  - **Amprius_SA88_24S2P_21Ah**: two series strings in parallel. 21000mAh,
    4893g, $1,944.00. Same **~355 Wh/kg** (Wh/kg doesn't change with
    parallel count) at a capacity that lands in the 15-30Ah "small pack"
    range this update specifically targeted.

For a bigger pack at similarly high density, Grepow's real NMC811
semi-solid-state product line (same family as the Grepow_TARBG entry
already in this lineup) publishes a full per-model spec table on its
[380Wh/kg product page](https://www.grepow.com/nmc811-battery/380wh-kg-semi-solid-state-high-energy-density-battery.html):
six cell models from 8Ah to 87Ah, 352-372 Wh/kg bare, 3.7V nominal, no
price published. Two were used, at opposite ends of that size range:

- **GRPA1F0270** (87000mAh, 865g, 372.1 Wh/kg bare) for a big pack, and
- **GRP5370175** (12000mAh, 126g, 352.4 Wh/kg bare) 2P'd to 24Ah for a
  second small-pack option in the 15-30Ah range.

  Mass overhead uses 1.08x (mid-point of this file's stated 1.05-1.10x
  BMS/enclosure range, since Grepow's page doesn't publish a finished-pack
  weight to check against). Price isn't published for either model, so
  cost_usd reuses this lineup's established estimate methodology (average
  $/Ah of this file's priced, finished packs, $88.38/Ah -- see the
  Grepow_TARBG entry above) -- flagged as an estimate, not a quote.
  - **Grepow_NMC811_24S1P_87Ah**: 24 cells in series. 87000mAh,
    24 x 865g x 1.08 = 22421g, 87 x $88.38 = $7,689.06.
    88.8V x 87Ah = 7725.6Wh / 22.421kg = **~345 Wh/kg assembled** -- still
    well above the previous lineup best (305 Wh/kg) despite being the
    biggest pack in the file by capacity after the 12Sx4 132Ah entry.
  - **Grepow_NMC811_24S2P_24Ah**: 48 cells (24S2P). 24000mAh, 6532g,
    24 x $88.38 = $2,121.12. Same math -> **~326 Wh/kg assembled**.

Ranked by assembled Wh/kg, these four all beat every existing entry:

| Pack | Wh/kg (assembled) | Capacity |
|---|---|---|
| Amprius_SA88_24S1P_10Ah | ~355 | 10.5Ah |
| Amprius_SA88_24S2P_21Ah | ~355 | 21Ah |
| Grepow_NMC811_24S1P_87Ah | ~345 | 87Ah |
| Grepow_NMC811_24S2P_24Ah | ~326 | 24Ah |
| *(previous best: YANGDA_XTurbo / MAD)* | *~293-305* | *44-100Ah* |

## 2026-09-11: more parallel strings of the same cells (still 24S -- series would mean 48S, off this file's voltage class)

The mission needs ~41,000-49,000mAh minimum depending on vehicle/motor, which
ruled out the 10-24Ah SA88/NMC811 packs above on capacity, not mass -- so the
fix isn't a denser cell, it's more of the *same* cell in parallel (adding
strings keeps voltage/cells_s at 24, only capacity and mass scale by string
count -- more series strings instead would multiply voltage, landing at 48S,
which no motor in this lineup runs on). Same SA88/GRP5370175 cells and
per-unit mass/cost ratios as the 24S1P/24S2P entries above, just scaled by N
strings (mass, capacity, and cost all scale linearly with parallel count --
Wh/kg is unchanged):

- **Amprius_SA88_24S4P_42Ah**: 4 strings, 42000mAh, 9788g, $3888.00
  (4 x the 24S1P entry's 2447g/10500mAh/$972.00).
- **Amprius_SA88_24S6P_63Ah**: 6 strings, 63000mAh, 14682g, $5832.00.
  Matches ZYE_UltraHV_24S_66Ah's capacity class (63Ah vs 66Ah) at **6.3kg
  less mass** (14.68kg vs 21kg) -- same ~355 Wh/kg this cell has throughout.
- **Grepow_NMC811_small_24S4P_48Ah**: 4 strings of the GRP5370175 cell (the
  same one behind Grepow_NMC811_24S2P_24Ah above, just 2 more strings),
  48000mAh, 13064g, $4242.24 (cost still the file's $88.38/Ah estimate,
  price unpublished).

All three are real-cell-derived, not fabricated -- same datasheet numbers as
the 1P/2P entries, just linearly restacked. Rerunning Mission_Model.py with
these: Amprius_SA88_24S6P_63Ah becomes feasible and is now the lightest
feasible battery in the whole lineup by a wide margin.
