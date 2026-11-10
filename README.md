# zim-prospect: mineral prospectivity mapping for Zimbabwe

`zim-prospect` builds national-scale maps of where 11 commodities are likely to occur in
Zimbabwe, ranks target zones, and reports how well each map ranks known deposits.

It uses **only open data with no API keys**, and every map comes with a validation score.
Use it as a **first-pass screening tool** to decide where to look closer, not to decide where to drill.

## Quick start

```bash
cd ~/zim-prospect
~/.pyenv/versions/3.12.10/bin/python -m venv .venv      # Python 3.11–3.13 (3.15 lacks geo wheels)
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m zimprospect run                      # about 3 min with cached data, about 10 min on first run
.venv/bin/python -m zimprospect run --only gold,lithium  # a subset of commodities
.venv/bin/python -m zimprospect run --res 250            # finer grid (slower; rebuilds features)
                                                         #   (below 200 m it uses the 30 m Copernicus DEM)
.venv/bin/python -m zimprospect zoom --commodity gold --top 5   # 10-20 m anomalies inside the top targets
.venv/bin/python -m zimprospect tune                     # tune fuzzy weights -> config/commodities.tuned.yaml
.venv/bin/python -m zimprospect run --config config/commodities.tuned.yaml
.venv/bin/python -m pytest -q                            # tests
```

The first run downloads about 120 MB and caches it in `data/`. Sentinel-2 imagery is read through
COG overviews instead of full scenes, so nothing large is stored on disk.

## Outputs (`outputs/`)

| File | What it is |
|---|---|
| `prospectivity_map.html` | Interactive map with one layer per commodity, the top targets (teal = greenfield, blue = known district), and the known occurrences. Open it in a browser. |
| `targets_<commodity>.csv`, `targets_all.csv` | The 25 best target zones per commodity: peak and mean score, area, lat/lon, distance to the nearest known occurrence, and `greenfield` or `known district` |
| `maps/<commodity>_final.tif` | Final score as a 0–1 percentile rank, 500 m GeoTIFF. Open it in QGIS. |
| `maps/<commodity>_fuzzy.tif`, `_rf.tif` | The two component models, kept separately |
| `validation.json`, `success_curves.png` | Accuracy against known deposits |
| `overview.png` | Small multiples of all commodities |
| `zoom/<commodity>/zoom_map.html` | Target zoom: 10–20 m surface anomalies (alteration, iron oxide, mafic soils, ridges) inside each target, ranked, with copyable coordinates. **These are places to sample, not deposits.** |
| `zoom/<commodity>/tNN/` | Per target: `indices.tif` (all indices), `score.tif`, `rgb.png`, `score.png`, `anomalies.csv` |
| `tuning_report.json` | Prior and tuned fuzzy weights, both scored on held-out spatial folds (from `tune`) |

**The interactive map.** Choose a mineral from the dropdown. Zimbabwe's boundary stays visible over the map;
the **Zimbabwe** button restores the national view and **Fit targets** frames the current targets. Filter targets
by greenfield/known district or sort by zone size, mean rank and distance to recorded sites. Tick up to three
targets and open **Compare** for a side-by-side comparison, then jump to an individual target or frame the shortlist.
Click a target to copy its coordinates or get directions. **Map & legend** holds the base map, overlay opacity
and recorded-site controls. Validation metrics expand under the mineral description. On a phone, **View targets**
opens the draggable bottom sheet and selecting a target returns to the map. The selected mineral stays in the URL (`#gold`).

## Adding your own data

Everything under `data/user/` is optional and never committed. See `config/user_data.example.yaml`.

| Drop in | Becomes |
|---|---|
| `data/user/occurrences.csv` (`name, lat, lon, commodities` separated by `;`) | Extra training and validation points (rows are validated; rejects are logged) |
| `data/user/geology.gpkg` + `config/user_data.yaml` mapping units to lith classes | `user_lith` classes and `user_dist_greenstone_contact`, usable as `{layer: user_lith, weights: {...}}` |
| `data/user/faults.gpkg` | `user_dist_fault` |
| `data/user/rasters/<name>.tif` (for example aeromagnetics) | Layer `user_<name>`, added to the random forest automatically |
| `MINDAT_API_KEY` environment variable | Mindat localities merged into the known occurrences (check Mindat's terms of use) |

`ZIMPROSPECT_DATA_DIR` and `ZIMPROSPECT_OUT_DIR` move the data and output folders, for example onto a server's data disk.

## How it works

```
open data ──► 500 m evidence layers ──► per-commodity models ──► ranked targets + map + validation
```

**Evidence layers** (equal-area Albers grid centred on Zimbabwe):

| Layer | Source | Why it matters |
|---|---|---|
| Geology class (basement, Lower Karoo, Upper Karoo, Karoo volcanics, Kalahari/young cover) | USGS OFR 97-470A Africa geology | Separates metal-bearing basement from coal basins and sand cover |
| Distance to the Karoo-basement margin | derived | Coal basin margins |
| Sentinel-2 dry-season median (Jul–Oct 2025, 2 scenes per tile per orbit): ferric (B4/B2), ferrous ((B12/B8)+(B3/B4)), clay (B11/B12), NDVI, albedo | Element84 Earth Search | Greenstone and mafic terrain (dark, ferrous red soils) versus granite sandveld (pale); gossans; alteration |
| The same at 5 km and 15 km neighbourhood scales | derived | Greenstone belts are 10–50 km features, so regional context beats single pixels |
| Elevation, slope, roughness | Copernicus GLO-90 DEM | Ridges (BIF, Great Dyke), plateau versus lowveld |

**Models.**
1. **Fuzzy (knowledge-driven).** Every commodity gets one. Expert weights in `config/commodities.yaml` are combined with a fuzzy gamma operator (γ = 0.8).
2. **Random forest (data-driven).** Runs only when there are at least 20 declustered occurrences: gold, chromite, PGM, nickel, copper. Training uses presence versus background points, and validation uses 5-fold *spatial block* cross-validation (50 km blocks), so each validation score comes from a model that never saw that region.
3. **Final map.** The mean percentile rank of the two models, or the fuzzy model alone when there are too few occurrences.

**Known occurrences.** 262 USGS MRDS records plus 359 OpenStreetMap mines and quarries (621 in total), matched to commodities by whole word.

## Validation (spatially cross-validated, run of 2026-11-10)

AUC is the probability that a known deposit scores higher than a random cell; 0.5 means no better than chance.
"Area for 80%" is the share of the country you would have to flag to capture 80% of known deposits.

| Commodity | Method | Deposits | AUC | Area for 80% | In top 10% | Confidence |
|---|---|---|---|---|---|---|
| Coal | fuzzy | 15 | **0.92** | 2% | 87% | High |
| Chromite | ensemble | 56 | **0.86** | 26% | 61% | Good |
| PGM | ensemble | 20 | 0.74 | 45% | 30% | Moderate |
| Gold | ensemble | 63 | 0.72 | 44% | 25% | Moderate |
| Copper | ensemble | 53 | 0.71 | 49% | 13% | Moderate |
| Diamonds | fuzzy | 5 | 0.72 | 38% | 20% | Low (n = 5) |
| Tantalum/tin | fuzzy | 14 | 0.69 | 52% | 14% | Low |
| Emerald | fuzzy | 18 | 0.68 | 50% | 22% | Low |
| Nickel | ensemble | 30 | 0.62 | 57% | 10% | Weak |
| Iron ore | fuzzy | 10 | 0.60 | 63% | 10% | Weak |
| Lithium | fuzzy | 14 | 0.59 | 57% | 0% | Weak |

**Independent holdout** ([docs/research/method-validation.md](docs/research/method-validation.md)): 45 mines
and goldfields named in the literature, none of them from the training databases. Gold, chromite and PGM maps put
96%, 90% and 80% of them in their top 10% of Zimbabwe, against 23–29% for random points. The belts are right;
the *ranking of individual targets* is weaker, because terrain roughness pulls targets onto escarpments.

## Limitations

1. **No open lithology map separates greenstone from granite.** Every open national geology map of
   Zimbabwe (USGS, BGS, Macrostrat) records age only: 66% of the country is undivided Precambrian
   basement. That is the main reason the gold, nickel, lithium and emerald maps score only moderately.
   Greenstone terrain is approximated from soil spectra.
2. **No open magnetics.** EMAG2v3 has a data gap over Zimbabwe. The Zimbabwe Geological Survey's
   airborne magnetic and radiometric surveys are not public.
3. **The occurrence database is sparse and biased.** MRDS lists 63 gold sites, while Zimbabwe has
   thousands of workings. Known mines cluster near roads and towns on the highveld, and
   elevation is the most important feature in the random forest for that reason. "Greenfield"
   targets are greenfield only relative to this database.
4. **Sentinel-2 tile seams.** Tiles imaged on different dates cause rectangular brightness steps,
   most visible in the ferric layer.
5. Pixel size is 500 m. Pegmatites (lithium, tantalum) and kimberlites are far smaller, so those maps
   show favourable settings, not individual bodies.

## Highest-value next steps

1. **Add the 1:1M ZGS geology map** (greenstone belts, Great Dyke, faults) as `lith` classes. This
   is the biggest single accuracy gain; it needs the user-data loader in U4 of `docs/CODEX_HANDOFF.md`.
2. **Add more occurrences**, for example from Mindat (free API key), ZGS mineral databases, or the
   ministry's mining-title cadastre. More training points mean random forest models for lithium and nickel.
3. **Add ZGS aeromagnetics** for structure and ultramafic detection.
4. **Run a target-scale Sentinel-2 or ASTER alteration analysis** at 10–20 m over the top targets.
5. Tune the fuzzy weights against `validation.json`. The current weights are expert priors.

Architecture and design decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Server deployment and upgrade notes: [docs/CODEX_HANDOFF.md](docs/CODEX_HANDOFF.md).
Who this is for: [docs/research/use-case-validation.md](docs/research/use-case-validation.md).

## Project layout

```
zimprospect/
  sources.py      open-data downloads (cached)          grid.py        projection, rasterising, distances
  features.py     evidence-layer stack + terrain         s2.py          Sentinel-2 settings, seasons, seam metric
  occurrences.py  MRDS + OSM + user + Mindat points      userdata.py    optional user geology/faults/rasters
  mindat.py       Mindat localities (MINDAT_API_KEY)     fuzzy.py       fuzzy membership + gamma operator
  models.py       fuzzy, random forest, ensemble         validate.py    success-rate curve, AUC
  targets.py      ranked target zones                    tuning.py      fuzzy weight tuning (spatial CV)
  zoom.py         10-20 m target zoom                    report.py      GeoTIFF, PNG figures
  webmap.py       interactive HTML maps (+ web/)         cli.py         run / zoom / tune
config/commodities.yaml        per-commodity weights, zoom recipes and settings
config/user_data.example.yaml  how to plug in your own geology, faults, rasters and mine lists
scripts/compare_validation.py  regression check against baselines/
docs/                          architecture, Codex server handoff, research
```

## Data attribution

USGS (MRDS; Persits et al. 1997 OFR 97-470A), public domain. OpenStreetMap contributors (ODbL).
geoBoundaries (CC BY 3.0 IGO). Copernicus DEM GLO-90 and GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and
Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA. Mindat data (optional) is
CC BY-NC-SA: non-commercial use only. Contains modified Copernicus Sentinel-2 data (2025), accessed through Element84 Earth Search.
