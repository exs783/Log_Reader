# Battery sourcing notes

The 26-27 RFP (v1, 9/21/2026) sets no cell-count or voltage limit (the 6S / 2-in-series
limit was in the 2025 RFP), so `12S_packs.json` adds 12S packs for the 12S motor curves.
The 6S lineup is what the MN6007II KV320 sheet was measured at. They run from 1.3 to 30 Ah so the sweep can find the
smallest pack that flies the whole mission. Every mass is the manufacturer's
listed net weight (+/-20 g) and every price is the genstattu.com list price
(Sept 2026):

| Pack | Net weight | Price | Page |
|---|---|---|---|
| Tattu FunFly 1300 100C | 225 g | $28.89 | https://genstattu.com/ta-ff-100c-1300-6s1p.html |
| Tattu R-Line 5.0 2200 150C | 346 g | $52.99 | https://genstattu.com/tattu-r-line-version-5-0-2200mah-6s-150c-22-2v-lipo-battery-pack-with-xt60-plug/ |
| Tattu 4500 25C | 670 g | $108.19 | https://genstattu.com/tattu-4500mah-22-2v-25c-6s1p-lipo-battery-pack-with-xt60-plug.html |
| Gens ace G-Tech 5500 60C | 828 g | $160.99 | https://genstattu.com/gens-ace-g-tech-5500mah-6s-22-2v-60c-lipo-battery-pack-with-xt90-s-plug/ |
| Tattu G-Tech 8000 25C | 1160 g | $161.99 | https://genstattu.com/tattu-8000mah-22-2v-25c-6s1p-lipo-battery-pack-with-xt60-plug.html |
| Tattu Plus 10000 25C | 1517 g | $227.99 | https://genstattu.com/tattu-plus-22-2v-25c-10000mah-6s-lipo-smart-battery-pack-with-ec5-plug.html |
| Tattu Plus 12000 15C | 1670 g | $282.99 | https://genstattu.com/tattu-12000mah-6s1p-15c-lipo-battery-pack-with-xt90-plug.html |
| Tattu G-Tech 22000 30C | 2460 g | $475.99 | https://genstattu.com/tattu-22-2v-30c-6s-22000mah-lipo-battery-with-xt90-s-plug-for-uav.html |
| Tattu G-Tech 30000 25C | 3673 g | $532.99 | https://genstattu.com/tattu-30000mah-22-2v-25c-6s1p-lipo-battery-pack.html |

The model doesn't check C rating. Before picking a pack, check that
capacity x C covers peak current: rotors x 44.2 A at full throttle on the P22.

## 12S packs (`12S_packs.json`)

| Pack | Net weight | Price | Page |
|---|---|---|---|
| Tattu 10000 30C | 2800 g | $467.62 | https://genstattu.com/ta-30c-10000-12s1p-as150u.html |
| Tattu Plus 16000 15C | 4250 g (page also says 4141 g) | $695.99 | https://genstattu.com/tattu-plus-1-0-compact-version-16000mah-44-4v-15c-12s1p-lipo-smart-battery-pack-with-as150u-plug/ |
| GAONENG GNB4000 HV 70C | 950 g (+/-45 g) | $221.12 | https://rcdrone.top/products/gaoneng-gnb400012s70ahvl-lipo-battery |
| Gens Ace 5000 60C | 1600 g | $249.99 | https://www.amainhobbies.com/gens-ace-12s-lipo-battery-60c-44.4v-5000mah-gea12s500060/p1336173 |

The GAONENG is an HV (4.35 V) pack. The RFP allows it only charged to 4.2 V per cell, so its
real capacity is below the 4000 mAh rating, which the model uses as listed. The 16000 mAh
pack is too heavy for the 10 lb unloaded target. Two 6S packs of one size also stack
automatically to 12S (series keeps mAh, doubles volts).
