# Repository review — 10 November 2026

**Subsequent frontend work:** the country outline, mineral selection, target filtering/sorting, three-target
comparison, map controls and mobile sheet have since been updated. Finding 8 (hash synchronization) is fixed,
the resolution subtitle is dynamic, and zone-distance wording is clearer. The original audit below records
the state before that follow-up; the backend/cache/model findings still apply. The test suite now has 151
passing offline tests. Screenshots and check evidence are in `outputs/frontend_review/`.

The project has a sound MVP structure and reproducible baseline results. The most valuable next work is to harden run/output integrity, cache validity and validation edge cases, then reduce raster memory use. There is little obvious dead application code; duplicated logic and incomplete contracts create more complexity than unused functions do.

This review covers all application Python modules, browser JavaScript/CSS, both configuration files, all tests and scripts, dependency files, the baseline, and repository documentation. It includes execution against the existing cached data and browser checks. External geological, commercial and legal research claims were not independently reverified. Fresh satellite downloads, live Mindat, a national 250 m build and a full tuning run were outside the executed checks.

## Running and verified

- National map: <http://localhost:8000/prospectivity_map.html>
- Gold zoom: <http://localhost:8000/zoom/gold/zoom_map.html>
- Chromite zoom: <http://localhost:8000/zoom/chromite/zoom_map.html>
- Server command: `.venv/bin/python -m http.server 8000 --bind 127.0.0.1 --directory outputs`.
- Python 3.12.10; installed versions match `requirements.lock`; `pip check` passed.
- `pytest -q`: **148 passed, 2 network tests deselected**, with 12 pending deprecation warnings about Affine array multiplication.
- A fresh default 500 m run completed for all 11 commodities in a separate temporary directory. Its **entire `validation.json` equals the committed baseline**, not just the AUC fields. The regression comparison passed for every commodity.
- Chromium checks at 1440 × 900 and 390 × 844: all 11 mineral buttons switched; one selected button at a time; initial `#gold` selection worked after a full load; desktop coordinate copying and base-map selection worked; mobile chip swiping and panel expansion worked; no page horizontal overflow or uncaught JavaScript errors during those checks. Both existing zoom maps loaded, with three targets each.
- Separate failure probes reproduced the findings below. Those probes used temporary files; the clustered-occurrence probe used the real sklearn classifier with ten trees to make the fold-handling check inexpensive.

Review artifacts are under ignored `outputs/repo_review/`: browser evidence, failure-probe evidence, fresh validation JSON and desktop/mobile screenshots. Original application source and existing top-level outputs were left intact. The review report is the only new source-controlled-path file.

## What was done well

1. **The architecture follows the work.** Data sources, grid operations, feature construction, models, validation, targets and presentation have distinct modules. Most modules can be understood independently. A static result viewer fits a batch geospatial tool and keeps operation simple.
2. **The mathematical core is largely pure and testable.** `grid.py`, `fuzzy.py`, `validate.py` and the pure parts of `zoom.py` operate on explicit inputs. Dataclasses give the main intermediate results useful names. Geospatial transforms and physical-area target filtering have synthetic tests.
3. **The validation design has useful safeguards.** Spatial block validation is implemented, fitted RF maps are separated from out-of-fold maps, percentile ranking handles ties, tuning retains the expert prior unless held-out scores improve, and tuned configurations carry an optimistic-score marker when reused on their training occurrences. Edge cases still need attention, as detailed below.
4. **User-data integration is practical.** Optional files avoid making the default setup dependent on private datasets. CRS checks, geometry filtering, clear CSV rejection logs, raster-name collision handling and one-hot categorical features are already present.
5. **The map code handles external text carefully.** Labels use `textContent`, the JSON serializer escapes script-breaking characters, outbound links have the expected relationship attributes, and the UI includes keyboard selection, visible focus, live announcements and reduced-motion/transparency settings.
6. **Reproduction has a foundation.** There is a complete dependency snapshot, a committed numerical baseline, deterministic seeds, network-free tests and substantial architectural documentation. The fresh run reproduced that baseline exactly.

## Prioritized findings

P1 means address early because the behavior can corrupt results or prevent an otherwise valid workflow. P2 means a material correctness, reliability or usability issue. P3 means cleanup.

### 1. P1 — an invalid commodity selection overwrites summaries before failing

**Location:** [cli.py](../zimprospect/cli.py), `run`, lines 45–101; [report.py](../zimprospect/report.py), `overview_figure`.

`run --only gld` accepts an unknown key, builds the feature stack and loads occurrences, then processes no commodities. It writes `{}` to `validation.json` and an empty `targets_all.csv` before `overview_figure` divides by zero. A synthetic run with a preexisting validation file reproduced both its replacement and the `ZeroDivisionError`.

A mixed selection such as `gold,gld` silently ignores the typo. A valid subset run also replaces aggregate outputs while leaving other commodities' older TIFFs, CSVs and zoom folders in place. A failure halfway through a normal run can similarly leave mixed generations of results.

**Change:** validate the complete config, finite positive resolution, and every selected commodity before loading data or writing files. Define whether a subset run produces a separate run or explicitly merges results. Generate into a staging/run directory and publish a manifest or current-run pointer only after every required output succeeds. Preserve the last successful run when generation fails.

**Verification to add:** unknown-only and mixed selections cause no output writes; an injected mid-run failure preserves the previous published result; subset output membership is explicit.

### 2. P1 — cache identity does not establish that rasters belong to the grid

**Location:** [features.py](../zimprospect/features.py), `cache_path` and `build_layers`, lines 308–349.

Cache names truncate the resolution with `int(res_m)`: **500.1 m and 500.9 m resolve to the same path**. Cache reads do not validate CRS, transform, shape, mask/boundary, layer schema or feature-building version. A same-size grid shifted by 50 km reused the original cached raster in the probe. Different shapes can fail later; equal shapes with different placement can silently assign evidence to the wrong locations.

Sentinel-2 settings hashes are a useful partial solution, but do not cover these other inputs. `grid_500m.json` is written after loading and is not used to validate those loads. NPZ files and their sidecars are also written directly, so interrupted writes can leave unusable cache entries.

**Change:** attach a cache manifest containing exact resolution, CRS, affine transform, dimensions, mask/boundary identity, expected layers, source identity and a builder/schema version. Validate it before reuse. Write cache data and metadata via temporary files and atomic replacement, rebuilding cleanly on corruption. Decide explicitly how existing baseline caches migrate.

### 3. P1 — enough occurrences does not guarantee usable spatial cross-validation

**Location:** [models.py](../zimprospect/models.py), `random_forest`, lines 192–203; [cli.py](../zimprospect/cli.py), `_validate` and RF validation calls.

The RF gate checks occurrence count, while fold viability depends on spatial distribution and both training classes. With **20 positives in one 50 km block**, the model holding out that block has no positive training examples and is skipped. The probe produced **zero finite out-of-fold predictions at the deposit cells**; `_validate` then raised `ValueError: no deposit points fall on valid cells` because it checks the original row count rather than finite predictions.

On a small grid with fewer occupied blocks than folds, the loop can also attempt `predict_proba` on an empty held-out matrix, which was reproduced. Where only some folds are skipped, `ensemble` can substitute fuzzy-only values at missing RF cells without making that mixed coverage explicit.

**Change:** establish fold feasibility before training; skip empty held-out sets; check both training classes; report fold/point coverage and an explicit insufficient-CV status. Validate only adequately supported results and show the number excluded. Keep fitted-map availability separate from whether a defensible CV score can be reported. Add concentrated-point, few-block and partial-coverage tests.

### 4. P2 — the numerical regression gate passes missing results

**Location:** [compare_validation.py](../scripts/compare_validation.py), `compare`, lines 21–33.

The checker starts with `ok = True` and continues when either score is missing. Comparing a valid gold baseline with **`{}` passes**, as does a gold entry containing no score. A severely incomplete run can therefore pass the documented quality gate.

**Change:** fail when a baseline commodity or required finite metric is absent, and report it as an error. Require an explicit subset option when intentionally comparing fewer commodities. Include tested-point count, evaluation method and optimistic status in the compatibility check; track top-10% capture and area-for-80% as well as AUC.

### 5. P2 — deduplication discards new commodity information

**Location:** [occurrences.py](../zimprospect/occurrences.py), `_commodity_words`, `_same_commodity` and `drop_near`, lines 63–90.

An existing `gold` record causes a nearby new **`gold, copper` record to be dropped entirely**, losing copper evidence. The probe reproduced this. Conversely, `chromium` and `chromite` do not share a literal token, so otherwise duplicate commodity records survive. Default MRDS/OSM rows are concatenated without the same near-duplicate merge; cell declustering only removes duplicates in the same cell.

**Change:** normalize commodity aliases to canonical keys, merge commodity sets for duplicate sites and retain source provenance. Preserve newly contributed commodities rather than discarding the whole row. Define distance/precision rules consistently across default and optional sources, with tests for multi-commodity and synonym cases.

### 6. P2 — distance and “known district” labels describe a zone, while the pin gives its peak

**Location:** [targets.py](../zimprospect/targets.py), `extract_targets`; [webmap.py](../zimprospect/webmap.py), `target_points`.

The distance is the **minimum over the entire connected target zone**. The displayed coordinate is the peak cell. For the current gold target #1, the zone is **1,103.8 km²**, its reported nearest-known distance is **0 km**, and the nearest recorded gold point to the pin is **32.61 km away**. The existing research document identifies the same underlying issue.

The zone calculation is coherent, but the interface lacks the geometry needed to interpret it. Copying a pin beside “contains a known site” can suggest a much closer relationship than the data establish.

**Change:** export and display target-zone polygons; distinguish `zone_nearest_known_km` from `peak_nearest_known_km`; explain which drives the district label. Evaluate compact subtargets and a ranking rule that is less sensitive to one extreme pixel. Compare any changed ranking against a separate spatial holdout.

### 7. P2 — the viewer is not self-contained offline

**Location:** [webmap.py](../zimprospect/webmap.py), `write_page`; generated HTML assets; offline claims in [ARCHITECTURE.md](ARCHITECTURE.md).

Images and application CSS/JS are embedded, but Folium injects external Leaflet, jQuery, Bootstrap and Awesome Markers scripts. Base maps also require the network. A clean browser context allowed the local HTML but blocked external requests: **no panel rendered**, with `L is not defined` errors. This is more than missing satellite imagery.

**Change:** bundle the Leaflet assets required for interaction, and distinguish an offline overlay/target viewer from an offline basemap package. Remove Folium's unused default browser assets after checking the generated scaffold. Make missing online tiles understandable without preventing target access. Update the documentation to state the actual offline behavior.

### 8. P2 — fragment changes do not update an already-loaded map

**Location:** [web/map.js](../zimprospect/web/map.js), startup, lines 361–364, and `selectGroup`.

The code reads `location.hash` only during startup. After selecting Diamonds, navigating within the same document to `#gold` leaves Diamonds selected. This was reproduced on desktop and mobile; a reload then correctly selected Gold.

**Change:** use one group-key resolver for initial load and `hashchange`/relevant history events. Avoid history-update loops and test direct links, same-document fragment changes and browser navigation. This needs a small event-handling fix, not a frontend rewrite.

### 9. P2 — completion and provenance are not recorded consistently

**Location:** [features.py](../zimprospect/features.py), `_read_scenes` and `_build_sentinel2`; [zoom.py](../zimprospect/zoom.py), `run_zoom`; [sources.py](../zimprospect/sources.py), raw downloads.

The national Sentinel-2 sidecar records the **selected** scene list/count even if some scenes failed to read. Coverage can therefore differ from the provenance used by seam diagnostics. Most raw-source/cache writes are not atomic. In `run_zoom`, if every target fails, the summary is updated but any older `zoom_map.html` remains, and the CLI still logs completion. These follow directly from the control flow; fresh remote-download failure scenarios were not exercised.

**Change:** use a small run manifest recording config/code version, grid, actual sources/scenes, coverage, warnings, successful/skipped outputs and completion status. Reuse the staging/publishing mechanism from finding 1 for zoom runs. Distinguish complete, partial and failed results in exit status and presentation. Apply bounded retries and schema validation consistently at external-source boundaries.

## Efficiency opportunities, in order

| Area | Evidence | Recommended change |
|---|---|---|
| DEM mosaicking | `terrain_layers` keeps each tile as a full-grid array, then `_mosaic` stacks another copy. This grid requests up to 72 tiles. One 500 m float32 grid is 9.8 MiB: if all tiles read, the tile arrays plus stack alone are about 1.38 GiB. At 250 m that allocation is roughly four times larger, before reduction temporaries. This is an allocation estimate, not measured peak RSS. | Crop tile contributions and accumulate a running sum/count, avoiding a full `tiles × height × width` array. Bound in-flight reads. Verify seam and nodata behavior. |
| RF predictor preparation | Measured 1,563,062 in-mask cells × 27 features: **161.0 MiB**. `random_forest` recreates the same matrix for each of five RF commodities. One matrix build took about **0.64 s** in this run. | Prepare predictors, flattened indices and block metadata once per run and pass them to commodity models. This reduces allocations and repeated setup; the measured matrix time shows it is not the dominant compute cost. |
| Fold assignment | `np.vectorize(fold_of_block.get)` executes Python dictionary lookups over all masked cells. Tuning already uses `np.unique(..., return_inverse=True)`. RF fold RNG is consumed after background sampling, so RF/tuning do not actually use identical fold assignments just because they share a seed. | Reuse one array-based block/fold helper. Preserve current assignments for a pure speed change; treat changing the fold policy as a separate validation/baseline change. |
| Sentinel-2 compositing | All successful scene-band dictionaries are retained; each band's median then allocates another full-grid stack. A median needs more state than a mean. | Composite in spatial chunks with bounded workers and preserve exact scene order/depth semantics. Measure peak memory before increasing scene counts or national resolution. |
| Derived contexts | The cached base stack plus 12 context rasters totals **215.6 MiB**. Loading and building the complete cached stack took **1.34 s** here. Context rasters are recomputed every run. | Cache derived contexts using the validated feature identity only if profiling shows a worthwhile repeated-run cost. |
| Dictionary construction | `fetch_mineral_names` copies its growing dictionary for every inserted mineral; raster loading similarly reconstructs local dictionaries. | Mutate a newly created local accumulator with normal assignment. The no-input-mutation rule does not require repeatedly copying an owned local dictionary. Keep this behind the raster/model work in priority. |

The current cached run is usable. The largest scaling risk is a cold raster build, not the size of individual Python modules. Worker counts and RF `n_jobs=-1` should become configurable when running concurrent jobs or sharing a server.

## Dead code and avoidable complexity

There is **no evidence of a large abandoned application layer**. Most helpers are exercised by production code, tests, diagnostic scripts or documented APIs. Removing the user-data loader, Mindat or tuning just because they are optional would remove working features.

Concrete cleanup candidates:

- Remove the unused `DRY_SEASON` import in `features.py`.
- Remove the unused `OCC_COLUMNS` constant in `userdata.py`, or make it the actual occurrence-schema definition.
- Consolidate `features.grid_lonlat_bounds` with `grid.lonlat_bounds`; they provide the same grid-bounds operation.
- Consolidate national/zoom scene reading around a public shared helper. `zoom.s2_composite` duplicates the thread-pool/error-handling path and reaches into private `features` helpers.
- Move the evidence-list flattening contract to a shared public helper. Tuning relies on `models._flatten` and `_available`; the underscore no longer reflects their cross-module role.
- Treat the legacy `sentinel2_layers(season=...)` argument and `cop90_tile_urls` as compatibility candidates, **not proven dead code**. Tests and callers still use them; update those consumers deliberately before removal.
- Give validation summaries a typed result with an explicit JSON conversion, instead of putting private `_curve` plotting data into ordinary metric dictionaries and stripping it with a nested comprehension later.
- Consider using `RFResult.n_positive` in diagnostics or removing it if there is no intended public consumer. It currently has no repository reader.
- Reuse a small GeoTIFF-profile helper for single-band and multiband writing if those paths change together. Avoid adding a generic output framework for two short functions.

## Model credibility and documentation

- The checked-in research already identifies roughness-driven targets and missing lithological controls. Current code still rewards roughness without that geological condition. Better-conditioned evidence and better target geometry deserve more attention than merely increasing pixel resolution.
- The README describes literature points as an independent holdout. `docs/research/method-validation.md` explicitly notes that famous mines overlap training sites and that 13 of 24 gold points lie within 5 km of gold training records. Independence of the source database does not establish independence of the mine/site. Present the spatially separated subset clearly and retain a reproducible evaluator for it.
- The holdout CSV and narrative are committed, but the script that generated the 2 km maxima, matched random baselines and statistical tables is absent. Check in that evaluator and freeze its source/training identities before using the claims as a regression gate.
- The national subtitle hardcodes “500 m cells” even when `run --res` changes the grid. Build it from `grid.res_m` and include the generation date/config identity.
- Handoff installation notes still say “not a git repository” and “expect 17 passed”; the repository has history and currently has 148 passing offline tests. The README next-step list also includes upgrades already implemented. Align the backlog with code and acceptance checks.
- `requirements.lock` is valuable but manually frozen; `requirements.txt` installs unconstrained versions and includes pytest as a runtime dependency. Make the reproducible install path the documented default and separate development tooling. A minimal package definition and CI would help clean-machine setup without changing the runtime architecture.
- There is no committed CI workflow or browser test harness. Add the missing boundary tests first, then a small browser smoke test covering mineral state, target actions and fragment navigation. Keep live data tests explicitly opt-in.

## Recommended implementation sequence

1. **Protect results:** validate config/selections early, make publication atomic, and make the regression gate reject missing results. Add tests for the reproduced failures.
2. **Protect model evidence:** validate cache identities, handle insufficient spatial-CV coverage, and preserve commodity information during deduplication. Re-run the baseline; explain intentional scoring changes.
3. **Make targets interpretable:** separate peak/zone distances, export zone geometry, and make the independent evaluator reproducible before changing ranking or roughness evidence.
4. **Reduce memory:** stream DEM mosaics first, then share RF predictors and spatial metadata. Measure fresh-build peak RSS and cached-run timings; do not infer speedups from line-count reduction.
5. **Finish the viewer contract:** correct fragment synchronization, offline asset behavior and resolution metadata. Add browser checks and update the documentation.

Keep the modular batch pipeline and static viewer. A small validated config boundary, shared spatial/scene helpers and a run manifest address the observed complexity without introducing a new application framework.
