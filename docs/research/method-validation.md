# Method validation: are the maps geologically right?

Independent check of the 2026-11-10 maps and target lists. Points and per-point scores: [`method-validation-points.csv`](method-validation-points.csv).

**Bottom line.** The maps find the right **belts**. They do not yet rank **targets** reliably.

- **Belts: right.** Named goldfields and Great Dyke localities score far above random, even more than 25 km from training records and against same-elevation cells.
- **Targets: not reliable.** Only 5 of the top 25 chromite targets lie on the Great Dyke, and 5 of the top 25 gold targets sit on the Zambezi escarpment. Both errors follow rugged terrain.
- **Lithium: weak.**

## 1. Holdout test

**Points.** I used 45 points that come from neither MRDS nor OSM:

- **Mines:** Wikidata items, mostly imported from the GeoNames/NGA gazetteer, accurate to about ±1 km.
- **Goldfields:** the coordinates of the town the goldfield is named after, from Wikipedia. These can sit 5–10 km from the workings. For example, Bindura stands in for Freda Rebecca, Penhalonga for Redwing, and Fort Rixon for Zulu.

**Data caveats.**

- **The GeoTIFF values are not percentiles.** For ensemble commodities they are the *mean of two percentile ranks*, so only 1.7–6% of cells score ≥ 0.9. I re-ranked every map to true area percentiles before sampling.
- **Training overlap.** Famous mines are also in the training data. Of the 24 gold points, 13 lie within 5 km of a gold training record, so I also report the points more than 5 km from training.

**Scoring.** Each point takes the maximum score within 2 km (coordinate error). The window inflates scores, so the baseline is 20,000 random cells given the same 2 km maximum; p is a permutation test of the median.

**Table 1 – Holdout points vs random cells (2 km maximum; random value in brackets)**

| Map | Points | n | Median pct, pixel | Median pct, 2 km | In top 10% | In top 25% | AUC pixel / 2 km | p |
|---|---|---|---|---|---|---|---|---|
| Gold | Named goldfields and mines | 24 | 0.81 | **0.98** (0.74) | **96%** (29%) | 100% (49%) | 0.79 / 0.90 | <0.001 |
| Gold | … more than 5 km from training | 11 | 0.86 | 0.96 | 91% | 100% | 0.77 / 0.86 | 0.003 |
| Chromite | Great Dyke localities | 10 | 0.88 | **0.98** (0.70) | **90%** (25%) | 100% (45%) | 0.85 / 0.89 | <0.001 |
| Chromite | … more than 5 km from training | 5 | 0.85 | 0.94 | 100% | 100% | 0.83 / 0.86 | 0.04 |
| PGM | Great Dyke localities | 10 | 0.90 | **0.99** (0.68) | **80%** (23%) | 90% (42%) | 0.84 / 0.85 | <0.001 |
| Nickel | Great Dyke localities | 10 | 0.74 | 0.97 (0.71) | 70% (27%) | 90% (46%) | 0.70 / 0.82 | <0.001 |
| Nickel | Trojan, Shangani, Epoch, Madziwa, Damba | 5 | 0.81 | 0.99 | 100% | 100% | 0.76 / 0.95 | 0.001 |
| Lithium | Bikita, Arcadia, Sandawana, Kamativi, Zulu | 5 | 0.73 | **0.96** (0.70) | **60%** (29%) | 80% (46%) | 0.60 / 0.78 | 0.03 |

- **Sensitivity.** A 5 km window gives the same conclusions (all p ≤ 0.02).
- **Weakest points.**
  - Mutorashanga, at the northern end of the dyke, scores at the 67th–83rd percentile for PGM and nickel.
  - The Zulu proxy scores 0.62.
- **Nickel overlap.** Shangani and Epoch coincide with training points.
- **Lithium.** The lithium map is fuzzy-only, so its test is independent by construction, but n = 5.

**Table 2 – 673 GeoNames "mine" features against the gold map.** The commodity of most features is not recorded, but they are mostly gold workings.

| Distance to the nearest gold training point | n | Median pct, pixel | AUC, pixel | In top 10%, 2 km (random 29%) |
|---|---|---|---|---|
| All | 673 | 0.86 | 0.81 | 80% |
| 10–25 km | 185 | 0.89 | 0.84 | 80% |
| More than 25 km | 249 | 0.78 | 0.74 | 70% |
| More than 10 km from *any* MRDS/OSM point | 155 | 0.80 | 0.76 | 62% |

**Elevation-matched baseline (random cells from the same 50 m elevation band):**

| Points | AUC vs all cells | AUC vs same-elevation cells |
|---|---|---|
| Gold, GeoNames mines | 0.81 | 0.79 |
| Gold, named goldfields and mines | 0.79 | 0.74 |
| Chromite, Great Dyke | 0.85 | 0.75 |
| PGM, Great Dyke | 0.84 | 0.71 |

Gold skill is not just elevation; independent AUCs (0.74–0.81) match the README's spatial-CV 0.72.

**Top-25 targets with a literature location within 10 km of the target's peak:**

- **Gold.** 28% are near a named goldfield or mine (random cells: 2%), and 56% are near any GeoNames mine (random cells: 13%).
  - Ranks 1, 6, 7, 12 and 22 lie on the Zambezi escarpment around Kariba and Makuti, 20–41 km from the nearest gold record. Their roughness is in the 94th–97th percentile.
  - Rank 1 covers 1,104 km² and is labelled "known district" with a distance of 0 km. That is only because the zone touches Gil Gil Mine. Its peak is 33 km away.
- **Chromite.** 20% are within 10 km of the Great Dyke axis (random cells: 2%). The axis is a line drawn through the dyke localities above.
  - Ten targets lie east of 31°E, in the granite and gneiss inselberg country of Mashonaland East. No comparable chromite district is known there.
  - The median target peak is in the 96th percentile for roughness.

## 2. Is the method appropriate?

- **Fuzzy gamma (γ = 0.8).** This is the standard knowledge-driven operator (An et al. 1991; Bonham-Carter 1994); typical γ is 0.7–0.95. The choice is fine. Map quality depends on the uncalibrated expert membership values, not on γ.
- **Presence/background random forest with spatial block cross-validation.**
  - The random forest itself is standard practice (Rodriguez-Galiano et al. 2015; Carranza & Laborte 2015).
  - Spatial blocks are better practice than most published studies (Roberts et al. 2017; Airola et al. 2019).
  - **Remaining risks:**
    - The background contains unrecorded deposits (a positive-unlabelled problem).
    - Biased occurrence data teach the model *access*, which shows up as elevation.
    - 50 km blocks are shorter than many greenstone belts.
- **Sentinel-2 band ratios.** The ratios were designed on a well-exposed, arid epithermal system (van der Meer et al. 2014).
  - **What they cannot do.** Sentinel-2 has two broad SWIR bands, so it cannot separate sericite, carbonate and chlorite alteration; ASTER has six (Sabins 1999).
  - **What they map here.** On Zimbabwe's weathered, wooded and farmed highveld, at 500 m pixels, the ratios map **soil and lithology**: red mafic soils versus pale granite sandveld. That is a valid proxy for greenstone; gold alteration halos (metres to hundreds of metres) are invisible at 500 m.
- **Comparable AUCs.**
  - Published values above 0.9 usually come from random cross-validation. Spatial validation typically removes 0.1–0.2; one bauxite study drops from 0.98 to 0.77–0.84 (Airola et al. 2019; Research Square 2025).
  - Lawley et al. (2021) is a national-scale model with full geology and geophysics: about 8% of Canada holds more than 80% of known nickel.
  - This tool needs 26–57% of the country to capture 80% of deposits. That is plausible for open data only, but 3–6× less efficient.
- **Zimbabwe precedent.** Mhlanga (2002) modelled Bubi-belt gold with weights of evidence (lithology, granite–greenstone contact, shears); a 2015 study mapped Great Dyke subchambers with Landsat ETM+. I found no published national map.

## 3. Verdict and the top 5 changes

**Verdict.**

- **Gold, chromite and PGM: geologically right at belt scale.** Independent points sit at the 94th–99th percentile; random points sit at the 68th–74th.
- **Nickel:** moderate.
- **Lithium:** weak.
- **Targets: not reliably ranked.** The rule meant to reward Great Dyke and banded-iron ridges also rewards granite inselbergs and escarpments.

Use the maps to choose belts, not targets.

**Top five changes, by expected accuracy gain:**

1. **Add greenstone-belt and Great Dyke polygons, plus granite–greenstone contacts and major shears.** Source them from the 1:1M ZGS map or published compilations. A distance-to-dyke layer would demote most of the 20 off-dyke chromite targets. Shurugwi-type greenstone ultramafics would need a layer of their own.
2. **Make terrain evidence conditional and fix target ranking.**
   - Reward ridges only inside mafic or ultramafic corridors.
   - Re-rank the ensemble to true percentiles.
   - Rank zones by mean score, normalised for size.
3. **Add the GeoNames mine gazetteer, about 670 mines, roughly 10× the MRDS gold sites.** Use it as training data and as a target-group background, so the random forest stops learning access.
4. **Use ASTER SWIR (2000–2008) for Mg-OH, serpentine and carbonate** to map the ultramafics that host chromite, PGM and nickel. Aggregate it as the share of each cell that is ultramafic, not as a mean ratio.
5. **Validate with leave-one-belt-out cross-validation plus this holdout.**
   - Tune the fuzzy membership values only within the training folds.
   - Report area-for-80% with bootstrap confidence intervals.

## Sources

- **Coordinates**
  - [Wikidata SPARQL](https://query.wikidata.org/), Zimbabwe mines from GeoNames/NGA, e.g. [Globe and Phoenix](https://www.wikidata.org/wiki/Q23680127), [Blanket](https://www.wikidata.org/wiki/Q4925161), [Ngezi](https://www.wikidata.org/wiki/Q16979254), [Unki](https://www.wikidata.org/wiki/Q16990101), [Mimosa](https://www.wikidata.org/wiki/Q16979342), [Bikita](https://www.wikidata.org/wiki/Q16254733), [Sandawana](https://www.wikidata.org/wiki/Q123515707), [Kamativi](https://www.wikidata.org/wiki/Q16976753)
  - [Wikipedia API](https://en.wikipedia.org/w/api.php), towns
  - [Freda Rebecca setting](https://www.mining-technology.com/projects/freda-rebecca/)
  - [Zulu location](https://www.miningweekly.com/article/zulu-lithiumtantalum-project-zimbabwe-update-2022-08-05)
  - [Great Dyke dimensions](https://earthobservatory.nasa.gov/images/4414/great-dyke-zimbabwe)
- **Method literature**
  - An, Moon & Rencz 1991, *Can. J. Explor. Geophys.* 27; Bonham-Carter 1994, *GIS for Geoscientists*
  - [Rodriguez-Galiano et al. 2015](https://doi.org/10.1016/j.oregeorev.2015.01.001)
  - [Carranza & Laborte 2015](https://doi.org/10.1016/j.cageo.2014.10.004)
  - [Roberts et al. 2017](https://doi.org/10.1111/ecog.02881)
  - [Airola et al. 2019](https://link.springer.com/10.1007/s10618-018-00607-x)
  - [Spatial-CV bauxite preprint](https://www.researchsquare.com/article/rs-10387437/v1)
  - [van der Meer et al. 2014](https://doi.org/10.1016/j.rse.2014.03.022)
  - [Sabins 1999](https://doi.org/10.1016/S0169-1368(99)00007-4)
  - [Lawley et al. 2021](https://doi.org/10.1016/j.oregeorev.2021.103985)
  - [Mhlanga 2002, ITC thesis](https://www.itc.nl/library/papers/msc_2002/ereg/mhlanga.pdf)
  - [Great Dyke ETM+ mapping 2015](https://cgsjournals.com/article/doi/10.6046/gtzyyg.2015.04.14)
