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
