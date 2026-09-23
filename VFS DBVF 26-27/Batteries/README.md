# Battery sourcing notes

The DBVF 26-27 lineup is all 6S packs, since that's the voltage the MN6007II KV320
sheet was measured at. They run from 1.3 to 30 Ah so the sweep can find the
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
