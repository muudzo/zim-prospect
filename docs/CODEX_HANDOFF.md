# zim-prospect: server deployment and upgrade plan

This is a handoff for Codex. You are taking over `zim-prospect`, a Python tool that maps where
11 minerals are likely to occur in Zimbabwe. It writes ranked target zones and scores each map
against known deposits. Read `README.md` first for the method and the outputs.

Your job has two parts:

1. **Bring the tool up on the Linux server** and reproduce the baseline scores (Sections 1–2).
2. **Implement the upgrades in Section 4, in order.** Each upgrade has a spec, required tests
   and a "done when" check.

Section 3 explains how precise the tool can become, which is why the upgrades are ordered as they are.
Section 6 lists the dead ends that have already been checked. Do not research them again.

---

## 0. Working rules

- **Python 3.12.** It is the tested version; use 3.11–3.13 at most. Newer versions lacked
  wheels for the geospatial packages when this was built.
- **Write tests first** for each upgrade, in `tests/`. Unit tests must not use the network.
  Put any test that needs the network behind `@pytest.mark.network` and skip that marker by default.
- **Code style:** modules under 800 lines, functions under 50 lines, frozen dataclasses, no
  in-place mutation of inputs, type hints on every function, and `logging` instead of `print`.
- **Secrets** (`MINDAT_API_KEY`, any tokens) come only from environment variables. Never put
  them in code, config or git.
- **Never commit** `data/`, `outputs/`, `logs/` or `.venv/`. They are already in `.gitignore`.
  Ask the user before every commit, and use conventional messages (`feat:`, `fix:`, `docs:` …).
  Do not add AI attribution lines.
- **After every upgrade:** run `pytest -q`, run the pipeline and compare `outputs/validation.json`
  with the baseline (Section 2). If any commodity's AUC drops by more than 0.02, explain why in
  writing or revert the change.
- **Data downloaded from the web is untrusted.** Validate it before using it, and fail with a clear message.

---

## 1. Server bring-up

### Requirements

| | Minimum | Recommended (250 m grid and zoom) |
|---|---|---|
| OS | Linux x86_64 or arm64 (instructions assume Ubuntu 22.04 or later) | same |
| CPU | 4 cores | 8 or more |
| RAM | 8 GB | 32 GB |
| Free disk | 5 GB | 50 GB |
| Network | outbound HTTPS to the hosts below | 100 Mbit/s or faster (Sentinel-2 is read over the network) |

You do not need a system GDAL: the `rasterio` and `pyogrio` wheels bundle their own. Do not
install the distribution's `python3-gdal` into the venv.

The pipeline makes outbound HTTPS connections to these hosts:

```
www.geoboundaries.org, github.com, raw.githubusercontent.com, media.githubusercontent.com
naturalearth.s3.amazonaws.com        pubs.usgs.gov          mrdata.usgs.gov
overpass-api.de                      earth-search.aws.element84.com
sentinel-cogs.s3.us-west-2.amazonaws.com
copernicus-dem-90m.s3.amazonaws.com  copernicus-dem-30m.s3.amazonaws.com (Upgrade U3)
```

Only the browser that opens the HTML map loads `server.arcgisonline.com` and the OpenStreetMap tiles.

### Getting the code onto the server

The user copies the project from their Mac:

```bash
rsync -av --exclude .venv --exclude __pycache__ --exclude outputs \
  ~/zim-prospect/ USER@SERVER:~/zim-prospect/
```

This includes `data/` (about 70 MB of cached downloads), so the first run is faster. The project
is not a git repository yet. Run `git init` on the server and ask the user before the first commit.

### Install

```bash
cd ~/zim-prospect
python3.12 -m venv .venv            # Ubuntu 24.04 ships 3.12. On 22.04, use uv: `uv venv --python 3.12 .venv`
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.lock   # exact versions that produced the baseline
.venv/bin/python -m pytest -q                # expect: 17 passed
```

`requirements.txt` lists the packages without versions. `requirements.lock` pins the exact versions
used for the baseline.

### Run

```bash
mkdir -p logs
tmux new -s zim
.venv/bin/python -m zimprospect run 2>&1 | tee logs/run_$(date +%F).log
.venv/bin/python -m zimprospect run --only gold,chromite      # a subset of commodities
```

On the Mac, the first run took about 10 minutes and later runs about 3 minutes. Features are
cached in `data/cache/{layer}_{res}m.npz`; delete those files to force a rebuild (U1 adds a `--refresh` flag).

### Viewing outputs

Do **not** serve `outputs/` publicly, because the target lists may be commercially sensitive. Use an SSH tunnel:

```bash
# on the server
cd ~/zim-prospect/outputs && python3 -m http.server 8000 --bind 127.0.0.1
# on the user's laptop, then open http://localhost:8000/prospectivity_map.html
ssh -N -L 8000:127.0.0.1:8000 USER@SERVER
```

Alternatively, `rsync` the `outputs/` folder back to the laptop. If the user later wants a shared
web view, put it behind HTTPS with authentication (for example Caddy with basic auth), and confirm with the user first.

### Re-running

No cron job is needed. Re-run whenever the config or the input data changes, and once a year after
the dry season ends (October), with the season updated (see U5).

---

## 2. Baseline to reproduce

`baselines/validation_500m_2026-11-10.json` is the reference. A fresh server run should
match it within ±0.02 AUC. Small differences can come from changes in the Sentinel-2 catalogue.

| Commodity | Method | n | AUC |
|---|---|---|---|
| coal | fuzzy | 15 | 0.919 |
| chromite | ensemble | 56 | 0.859 |
| pgm | ensemble | 20 | 0.737 |
| gold | ensemble | 63 | 0.722 |
| diamonds | fuzzy | 5 | 0.721 |
| copper | ensemble | 53 | 0.712 |
| tantalum_tin | fuzzy | 14 | 0.690 |
| emerald | fuzzy | 18 | 0.679 |
| nickel | ensemble | 30 | 0.616 |
| iron_ore | fuzzy | 10 | 0.600 |
| lithium | fuzzy | 14 | 0.594 |

For ensemble commodities the AUC is read from `ensemble_cv` (spatial block cross-validation);
for the others it is read from `fuzzy`. A gold sanity check: target #2 (−17.3241, 31.5659) should fall in the Shamva mine area.

---

## 3. How precise can this get?

Pixel size is not the same as accuracy. A 10 m map built on weak evidence is just precisely
wrong. The main limit today is **evidence quality**: there is no open map that separates
greenstone from granite, no open magnetics, and only 621 known occurrences. Pixel size is a smaller limit.

| Scale | Pixel | What it answers | Limited by | Where it comes from |
|---|---|---|---|---|
| National (now) | 500 m | Which belts and districts | Geology map at 1:5M scale (boundaries off by kilometres); sparse, approximate mine points | done |
| Regional | 100–250 m | Which part of a belt | The same geology, unless the user supplies the ZGS map | U3, U4 |
| Target | 10–20 m | Which ridges, outcrops and alteration patches to walk to | Sentinel-2 resolution (about 10 m geolocation); sees only the top millimetre of the ground; vegetation hides the signal | **U2** |
| Field | 1–25 m | Where to pit, trench or drill | Field work: mapping, soil sampling on 25–50 m grids, handheld XRF, ground magnetics or IP; positions from GPS (3–5 m) | people on the ground |
| Drill | under 1 m | Ore or not | Drilling | — |

Satellites cannot see through soil. Surface signals such as gossans, iron-oxide staining and
alteration halos are often tens to hundreds of metres away from the ore body. The tool can get to
"sample here" spots at 10–20 m. Getting to metres takes field work. Say this plainly in any user-facing
output the upgrades add.

---

## 4. Upgrades, in order

### U1. Operations hardening (small)

- Add the environment variables `ZIMPROSPECT_DATA_DIR` and `ZIMPROSPECT_OUT_DIR`. They default to
  `./data` and `./outputs`. Wire them into `sources.RAW`, `features.CACHE` and the default
  `--out` in `cli.py`.
- Add a `--refresh` flag to `run`, passed through to `features.build(res_m, refresh=True)`.
- Add `scripts/compare_validation.py BASELINE NEW`. It prints the AUC change per commodity and exits
  with a non-zero code if any commodity drops by more than 0.02.
- Add a pytest config (`pytest.ini` or `pyproject.toml`) that registers the `network` marker and
  sets `addopts = -m "not network"`.
- **Done when:** tests pass, a run with both environment variables writes to the new directories,
  and comparing the baseline with itself exits 0.

### U2. Target zoom at 10–20 m (the biggest step toward metres)

Add a new subcommand that drills into the top national targets using full-resolution Sentinel-2:

```bash
python -m zimprospect zoom --commodity gold --top 10 [--box-km 8] [--res 20]
```

**Inputs.** Read `outputs/targets_<commodity>.csv` (columns `rank`, `peak_lat`, `peak_lon` …).
If the file is missing, exit with: "run `python -m zimprospect run` first".

**Grid.** Add `local_grid(lat, lon, box_km, res_m) -> Grid` to `grid.py`. It returns a square box
centred on the target, in `ZIM_CRS`, with the mask set to all True. 20 m is the native resolution
of the SWIR bands; `--res 10` is allowed.

**Imagery.** Use `sources.s2_scenes(bbox, start, end, max_cloud=10, per_tile=8)` with the box
bbox. Then reuse `features._s2_scene_bands(scene, grid)` and `features._median_mosaic(...)`.
These already work for any `Grid`. At 20 m, `_overview_level` returns `None`, so pixels are read at full
resolution, and only windows of the cloud-optimised files are fetched. You may rename the helpers
to public names; if you do, update every caller.

**Terrain.** Slope and hillshade come from GLO-30 once U3 lands, and from GLO-90 until then.

**Indices.** Use the same definitions as the national layers so the two are consistent:
- ferric = red/blue
- ferrous = swir22/nir + green/red
- clay = swir16/swir22
- NDVI
- albedo (mean of the 6 bands)
- slope

**Masks.** Mask water and no-data. Mask dense vegetation with `NDVI > 0.45`. Make every threshold
a named constant.

**Local anomaly.** For each index, compute a robust z-score within the box:
`z = (x − median) / (1.4826 · MAD)`. Because the score is relative to the local background, most
tile-to-tile brightness differences cancel out.

**Commodity recipes.** Put these in a new `zoom:` block per commodity in `config/commodities.yaml`.
Each recipe is a list of `(index, direction, weight)`. The score is the weighted mean of the
signed z-scores, clipped to the range −3 to 3.

| Commodity | Recipe |
|---|---|
| gold, copper | clay high (alteration) + ferric high (gossan, iron oxide) |
| chromite, pgm, nickel | ferrous high + albedo low (mafic and ultramafic soils) |
| iron_ore | ferric high + slope high (banded-iron ridges) |
| lithium, tantalum_tin, emerald | albedo high + clay high (pegmatite). Mark these **experimental**. |
| coal, diamonds | skip, and log that these have no useful surface signature at this scale |

**Anomaly clusters.**
- Cells with a score of at least `2.0` (a named constant) are anomalous.
- Label them with 8-connectivity.
- Drop clusters of fewer than 3 cells.
- Rank by peak score, then by area.
- Report the centroid lat/lon to 5 decimal places.

**Outputs.** Write one folder per target, `outputs/zoom/<commodity>/t<rank:02d>/`, containing:
- `indices.tif`: multi-band, with a description set on each band
- `score.tif`
- `rgb.png`: true colour
- `score.png`
- `anomalies.csv`: rank, peak_z, area_m2, lat, lon, ndvi_mean, n_scenes

Per commodity, also write:
- `outputs/zoom/<commodity>/zoom_summary.csv`
- `outputs/zoom/<commodity>/zoom_map.html`: a folium map with Esri imagery as the base layer,
  per-target score overlays and anomaly markers. Its popups must say the anomalies are surface
  features to sample, not deposits.

**Performance.** An 8 km box at 20 m is 400 × 400 px. Process boxes in parallel with
`ThreadPoolExecutor(4)` and aim for under 60 s per box.

**Tests (no network):**
- the robust z-score on synthetic arrays
- recipe combination when some inputs are NaN
- cluster extraction on a synthetic blob
- the centre and shape of a `local_grid`
- the clear error from the CLI when the targets CSV is missing

**Done when:**
- `zoom --commodity gold --top 3` finishes.
- Gold target #2 (Shamva area) produces indices, anomalies and an HTML map.
- `score.png` shows no rectangular seam steps.
- A "Target zoom" section is added to the README.

### U3. GLO-30 DEM and a finer national grid

- Add `COP30_URL` next to `COP90_URL` in `sources.py`. It uses the same pattern, with
  `copernicus-dem-30m` and `COG_10` in place of `copernicus-dem-90m` and `COG_30`. Check a single tile
  with an HTTP HEAD request before relying on the pattern.
- Use 30 m tiles when `grid.res_m < 200`, and keep 90 m tiles otherwise. A missing tile already
  returns `None`; keep it that way.
- `targets.extract_targets(min_cells=2)` depends on resolution. Replace it with
  `min_area_km2 = 0.5` and convert that to cells using `grid.res_m`. Add a test.
- `context_layers` already converts kilometres to cells, and `block_km` is in kilometres, so
  neither needs changing.
- Run `--res 250` (about 3,060 × 3,360 cells, about 1 GB of float32 features). Compare the result
  with the baseline using `compare_validation.py`.
- **Done when:** no commodity's AUC is more than 0.02 worse. Keep 500 m as the default unless
  250 m scores equal or better, and report the changes to the user.

### U4. Plug-ins for user-supplied data (prepares for ZGS geology and aeromagnetics)

The single biggest accuracy gain will come from data only the user can obtain (Section 5). Make
the pipeline accept that data without code changes. Every input below is **optional**: when
`data/user/` is empty, the outputs must match the baseline exactly. Never commit `data/user/`.

- **`data/user/occurrences.csv`** has columns `name, lat, lon, commodities` (words separated by
  `;`) and an optional `source`.
  - Validate each row: lat between −22.5 and −15.5, lon between 25.0 and 33.1, and a non-empty
    commodity list.
  - Log each rejected row with the reason.
  - Drop rows within 100 m of an existing point.
  - Merge the rest in `occurrences.load_all()` with `source="user"`.
- **`data/user/geology.gpkg`** (or `.shp`) holds polygons. Add a `user_geology:` block to the
  config: `{path, column, classes: {<map unit>: <lith class>}}`.
  - Rasterise the polygons to a `lith` class layer. Example classes: `greenstone_mafic`,
    `greenstone_ultramafic`, `greenstone_felsic`, `bif`, `granite_gneiss`, `great_dyke`.
  - Add `dist_greenstone_contact` using `grid.contact_cells`. The greenstone–granite contact is the
    main control on gold.
  - `models.evidence_layer` currently special-cases `layer: geo` only, despite what the README
    says. Generalise it to any class layer that has a class list, so config entries like
    `{layer: lith, weights: {...}}` work.
- **`data/user/faults.gpkg`** holds lines. Add a `dist_fault` layer.
- **`data/user/rasters/*.tif`** (for example aeromagnetic or radiometric grids):
  - Each file becomes a continuous layer named after the file's stem.
  - Warp it with `features.warp_to_grid` using bilinear resampling.
  - Append it to the random forest feature list automatically, and make it usable in the fuzzy config.
- **Tests:** use a tiny synthetic CSV, GeoPackage and GeoTIFF written to `tmp_path`, and cover rejected rows.
- **Done when:** a synthetic user raster appears in the stack and the random forest feature list,
  and a run with no user data reproduces `validation.json` exactly.

### U5. Reduce Sentinel-2 tile seams

- Make the season configurable as a list of windows. Default to the dry seasons of 2024, 2025 and
  2026 (15 Jul–15 Oct each year), and raise `per_tile` from 2 to 6. Raise
  `_median_mosaic(max_depth=…)` to match.
- Add a seam metric: the mean absolute step in `s2_ferric` across MGRS tile boundaries, divided by
  the same statistic within tiles. Test it on a synthetic step image.
- Only if seams remain, add overlap-based normalisation: per tile and band, fit a gain and offset
  to the already-mosaicked neighbours in the overlap area.
- **Done when:** the seam metric drops by at least 50% and no AUC falls by more than 0.02.

### U6. Mindat occurrences (optional; needs the user's free API key)

- Read the key from `MINDAT_API_KEY`. If it is not set, log a warning and skip this source.
- Fetch Zimbabwe localities that have coordinates and mineral lists. Cache them in
  `data/raw/mindat_zwe.json`. Map minerals to commodity keys in the config.
- Check the endpoint and filter parameters against the current Mindat or OpenMindat API docs;
  do not guess them.
- Read Mindat's terms of use and tell the user about any limits on commercial use before relying on the data.
- If the API reports coordinate precision, drop localities that are only approximate (district centroids).
- **Done when:** the new points merge and deduplicate, and the AUC changes are reported. Any commodity
  that now reaches 20 or more points (`min_training_points`) automatically gets a random forest.

### U7. Fuzzy weight tuning (do this last)

- Tune each commodity's weights with the same 50 km spatial blocks:
  - Fit the weights on 4 folds and score them on the held-out fold.
  - Use a random or grid search over weights from 0.3 to 1.0, with at most 200 evaluations per commodity.
- Only tune commodities with at least 15 points.
- Keep the expert prior unless the held-out AUC improves by at least 0.02.
- Write the result to `config/commodities.tuned.yaml`. Never overwrite `commodities.yaml`.
- **Done when:** a report lists the prior and tuned held-out AUC for each commodity.

---

## 5. What only the user can supply

Codex cannot fetch these. Ask the user for them when U4 is done.

| Item | Where it goes | Why it matters |
|---|---|---|
| ZGS 1:1M (or 1:250k) geology and structural map | `data/user/geology.gpkg`, `data/user/faults.gpkg` | Greenstone versus granite is the most important missing control for gold, nickel, lithium and emerald |
| ZGS aeromagnetic and radiometric grids | `data/user/rasters/` | Structure and ultramafic bodies hidden under cover |
| The user's own mine and claim lists, or the mining-title cadastre | `data/user/occurrences.csv` | More training points and fewer biased ones |
| Mindat API key | `MINDAT_API_KEY` environment variable | U6 |
| Whether outputs need a shared web view | — | Auth and hosting decision |

---

## 6. Dead ends already checked (do not re-research)

- **No open lithology map separates greenstone from granite.** The USGS OFR 97-470A layer
  `geo2_7g` records age only (GLG codes); Macrostrat and BGS likewise. `llf2_7g` is a graticule, not faults.
- **EMAG2v3 has no data over Zimbabwe**, and the NOAA "DataTiff" is an RGB picture, not values.
  There is no usable open magnetic grid.
- **Earth Search Sentinel-2 items have `earthsearch:boa_offset_applied: True`.** Do not subtract
  the 0.1 offset again, because doing so turns the blue band into NaN. The relative orbit appears
  only inside `s2:product_uri` (`_R###_`); `sat:relative_orbit` is missing.
- **MRDS has only 262 Zimbabwe records.** OSM adds about 360, mostly unlabelled quarries. Expect
  gold to stay under-sampled until U4 or U6 adds more points.
- **The random forest ranks elevation as its top feature** partly because known mines cluster on
  the highveld near roads (sampling bias), not only because of geology. Keep this in mind when
  reading feature importances.
