# zim-prospect vs muudzo/mining — 10 November 2026

**For the working MVP, continue with zim-prospect as the computational foundation. Mining has the stronger application workflow and the more careful scientific output contracts, but its current frontend and active engine are separate prototypes.** Reuse those strengths through a defined export/provider adapter rather than replacing the working pipeline with the entire other repository.

## What was compared

- `mining`: GitHub `main` at [170688b207c1b3c1ad52faedd10d6829983b6023](https://github.com/muudzo/mining/commit/170688b207c1b3c1ad52faedd10d6829983b6023), tree `92a95bc6d36696d80d16214494e2f6178d88dba9`. The existing local copy contained that exact tree. An isolated archive of that revision was used for execution.
- The local `mining` checkout is two commits newer; its linked-target zoom fix and later documentation commit were excluded from this comparison. Its preexisting modified `overhaul.md` was preserved.
- `zim-prospect`: local working tree based on `13f57fbed9f814f1a38494e8a59136528a8c71ba`, including the frontend changes made in this conversation. Its modelling code is unchanged by those UI changes. Those additions should not be attributed to its original work style.
- Read the governing architecture and board, active engine modules/tests, frontend domain/provider/state/map/report paths and tests, workflow configuration, dependency files, and selected historical audit/pipeline code. Executed the checks below. This is a comparison of implementation and observed behavior, not independent geological field validation or a line-by-line review of all historical code.

The scopes differ: zim-prospect is national, multi-commodity screening; the new mining engine targets a small gold-system pilot. Its app is a deliberately synthetic UX prototype. These differences matter more than which framework appears in the dependency list.

## Verdict by criterion

| Criterion | Stronger implementation | Evidence |
| --- | --- | --- |
| Working real-data MVP | **zim-prospect** | A real-data pipeline produces national rasters, targets, validation and interactive maps for 11 commodities. A fresh run reproduced its complete numerical baseline earlier in this session. |
| Product workflow and frontend structure | **mining** | Typed domain/provider boundary, project routes, target polygons and evidence inspector, persistent shortlists/notes, undo and printable reports; browser-tested journeys. |
| Evidence validity and provenance contracts | **mining's active engine** | Explicit observed/anomaly/ranking states, acquisition-matched quality requirements, input/output/code hashes, geological prerequisites, and grouped-reference evaluation. |
| Simplicity and operational coherence | **zim-prospect** | One Python pipeline with a generated viewer and one offline test command. Mining combines a historical Python product, a replacement engine and a separate frontend. |
| Frontend test breadth | **mining** | 187 unit/component tests passed; 92 desktop/mobile Chromium checks passed, with four expected layout skips. |
| Automated repository integration | **Needs work in both** | Zim-prospect has no committed CI workflow. Mining's latest GitHub CI run failed, and its root command independently has an engine-collection/dependency mismatch; frontend checks are absent from the workflow. |
| Proven gold-discovery accuracy | **No winner established** | Working maps, software tests and synthetic target workflows do not establish field discovery outcomes. The repositories' reported metrics are not a common benchmark. |

## Checks actually run

| Component | Result | Scope |
| --- | --- | --- |
| zim-prospect | **151 passed; 2 network tests deselected** | Complete local offline Python suite. |
| mining active engine | **28 passed** | Run from the isolated `engine/` directory with its existing separate Python environment. |
| mining historical audit/API | **98 passed; 1 expected failure** | Explicit root `tests/` path. The expected failure documents an unperformed cross-environment certificate verification. |
| mining frontend | **187 passed** | All 28 Vitest test files. |
| mining production build | **Passed** | TypeScript project build and Vite production output. |
| mining browser checks | **92 passed; 4 skipped** | Desktop and mobile Chromium projects: journey, accessibility, responsive layouts, shortlist/report, basemap outage and absent WebGL2. Firefox/WebKit were not rerun in this comparison. |
| mining root pytest collection | **Failed with 3 collection errors** | In the legacy environment: new engine tests require `rasterio` and `goldpilot`, which the legacy setup does not install. |
| mining synthetic engine demo | **Completed** | Six artificial ranked targets; separate anomaly-only and insufficient-observation outcomes. |
| mining supplied real configuration | **Completed as insufficient observations** | Zero supplied science-pixel scenes, zero targets, zero assessable independent evaluation groups; evaluation correctly reported `not_computable`. |

The installed frontend's package and lock files matched the pinned snapshot. Packages were reused through temporary symlinks. The first Vitest run encountered Vite's filesystem restriction on a worker URL outside the temporary checkout; a review-only configuration allowed the installed dependency directory, after which all tests passed. The repository's app configuration was not changed. Build and test artifacts stayed in the isolated copy or ignored local output folders.

## What mining does better

### The frontend models the user's work

There is an actual journey: select a project, inspect a target polygon and supporting/conflicting evidence, shortlist it, add a note, return after reload, undo removal, and print a report containing only the selected targets. The automated browser tests exercise that story rather than only checking that components render. [Workspace](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/src/pages/explore/Workspace.tsx), [shortlist store](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/src/features/shortlist/shortlistStore.ts), [journey test](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/tests/e2e/shortlist.spec.ts).

The provider interface separates screens from fixtures; feature state is separated from presentation; loading/error states are explicit. Map failures retain list/detail access, including a no-WebGL fallback. Bundled assets and a local basemap stand-in make browser tests independent of tile-service availability. These are engineering advantages beyond the choice of React.

Zim-prospect's viewer is sufficient for a local result map, but UI state, rendering, comparison and mobile dragging share one JavaScript closure. Persistence, notes, report workflow and reusable typed frontend contracts would need deliberate development as the product grows.

### Its new engine makes uncertainty and missing data explicit

`pipeline.run` rejects a nonempty output directory before writing. Its manifest records source/config/output hashes and software versions. The evaluator checks frozen outputs, groups duplicate references, carries positional uncertainty, and compares geology-only, satellite-only and combined selections over a shared eligible area. These directly address weaknesses identified in the zim-prospect review. [Run/output contract](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/engine/goldpilot/pipeline.py), [evaluation](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/engine/goldpilot/evaluate.py).

The ranking policy retains missing/unknown ground separately, requires mapped hosts/structures, and does not quietly present a weakened recipe as the same gold score. Targets include polygons, source dates, evidence resolution, supporting/conflicting evidence and missing information. This is a stronger scientific software contract. It remains unvalidated on real gold-targeting data.

## Where mining is weaker as the MVP to continue today

### The app does not consume the engine

The application entry point constructs `createDemoProvider`; that provider serves hand-authored projects, targets and overlays. There is no engine connection, and some domain states intentionally describe only a prototype. A future real provider also needs contract/state work, not just a URL substitution. [App entry point](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/src/main.tsx#L27), [provider](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/src/data/providers/demoProvider.ts), [target types](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/app/src/domain/target.ts).

The demo is clearly labelled, which is good. It should be credited as a tested workflow prototype rather than counted as real prospectivity capability.

### Real ASTER preparation is deliberately unfinished

For real scenes, `prepare` records `real_preparation_not_validated` and skips processing. The code explicitly says config flags cannot override the missing verified QA/exposure adapter. This restriction can be scientifically justified, but the implementation gap is substantial. Supplying an API provider alone would not produce real gold assessments. [Preparation guard](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/engine/goldpilot/prepare.py#L141).

The supplied real Shamva configuration contains no science-pixel scenes. Its zero-observation result describes that input configuration; it does not establish absent gold or unusable NASA imagery. Current Sentinel-2 classification context is a separate supported capability.

### CI has not caught up with the repository structure

The workflow installs the legacy audit/API extras, then invokes root `pytest -q`. Root discovery now also encounters the new engine tests, but their package and raster dependencies are absent. The review reproduced `ModuleNotFoundError` for `rasterio` and `goldpilot`. The workflow also has no frontend build/unit/browser jobs. [Workflow](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/.github/workflows/ci.yml).

The latest linked-revision [GitHub CI run is failed](https://github.com/muudzo/mining/actions/runs/41940075460). Its returned job metadata contained no failed-step detail, so that remote failure is not attributed solely to the independently reproduced collection defect. The individual suites passing under their separate environments does not establish that the clean repository CI setup works.

### Historical code still increases navigation and support cost

The architecture explicitly designates `geomine` as legacy, yet root packaging, the old CLI, the API, benchmark workflow, commercial materials and historical plans remain alongside the replacement. The old satellite CLI still calls `download_scene(..., token=...)` while the function accepts `auth_token`, and constructs distance-to-deposit features before validation. These are legacy findings, not defects in the new engine. They demonstrate why old paths should be isolated and clearly retired from supported usage. [Legacy CLI](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/geomine/cli.py#L73), [download signature](https://github.com/muudzo/mining/blob/170688b207c1b3c1ad52faedd10d6829983b6023/geomine/ingest/sentinel2.py#L70).

One smaller active frontend defect was reproduced: storage probing can succeed, then `getItem` can throw; the shortlist store catches write failures but not that read failure. Extend its session fallback to reads. It does not outweigh the otherwise strong state handling.

## Where zim-prospect needs to borrow the stronger practices

The previous [repository review](REPO_REVIEW.md) records reproduced correctness issues that still apply to the backend:

- Invalid `--only` selections can overwrite aggregate outputs before failure.
- Cache identity truncates fractional resolution and does not validate grid placement or builder/source identity.
- Concentrated occurrence locations can leave no valid spatial-CV predictions and crash validation.
- The regression checker accepts missing results.
- Near-duplicate merging can discard a newly contributed commodity.
- Target-zone distance and peak-pin location differ; the UI wording is clearer now, but exported zone geometry and separate distances are still needed.

Its strength is a coherent working path through these stages. Its weakness is insufficient hardening at their boundaries. The right response is to bring in explicit run states, manifests, output protection and target/evaluation contracts while retaining the functioning mathematical core.

## Efficiency and unnecessary complexity

Measured source footprint, excluding tests/build output/docs and including comments/blank lines:

| Component | Files | Lines |
| --- | ---: | ---: |
| zim-prospect Python package | 18 | 3,299 |
| zim-prospect browser JS/CSS | 2 | 767 |
| mining active Python engine | 11 | 1,190 |
| mining frontend TS/TSX/CSS | 108 | 8,335 |
| mining historical `geomine` package | 32 | 7,888 |

The extra frontend code buys real workflow, accessibility and state-management capability. The historical package buys recoverability and a separate audit product, but is inactive in the new frontend/engine path. Separate that history from the supported build before deleting individual helpers. TypeScript component files and their tests are not dead code just because the backend is unconnected.

For runtime efficiency there is no fair head-to-head benchmark: one implementation processes national real rasters, the other ran tiny artificial fixtures and currently caps pilot grids at five million cells. Zim-prospect's known allocations remain the first optimization target: DEM tiles are expanded to full-grid arrays and then stacked, and the same 161 MiB RF predictor matrix is rebuilt per commodity. Mining's preparation also rereads/reprojects masks repeatedly across bands; reuse masks by acquisition and grid once the real adapter exists. Raw line count does not establish either implementation's speed or accuracy.

## What the work styles achieved

The more specification/review-driven mining work produced stronger evidence semantics, output contracts, state handling and interface tests. Its current result is several independently verified pieces with an unfinished real-data connection.

Zim-prospect's compact end-to-end approach delivered usable real-data results sooner, while leaving weaker input/output/cache boundaries and a less developed original UI. The frontend work in this conversation improves that UI without resolving the scientific/backend gaps.

This comparison supports combining those practices. Different sensor, data-access and scientific prerequisites prevent attributing the whole outcome to the work style or model alone.

## Recommended continuation

1. **Keep zim-prospect's working computational core.** Fix its run integrity, caching, CV coverage and commodity merge bugs first.
2. **Adopt mining's evidence/export contract.** Add a versioned run manifest, target polygons, source/quality metadata and honest supported/partial/unsupported states. Make the current holdout evaluator reproducible.
3. **Use the mining frontend as the richer product interface when ready.** Implement a provider adapter over those saved real outputs; then test the full real-output → map → shortlist → report path. A static artifact provider can precede any new backend service.
4. **Simplify supported repository paths and CI.** Explicit legacy test job; independent new-engine install/test job; frontend build/unit/browser jobs; one current entry-point README. Preserve historical experiments in a clearly separated archive/release or package boundary.

For deciding what to continue now: **zim-prospect for real computations; mining for the application workflow and scientific contracts.** No shared field benchmark yet establishes that either produces better gold-discovery outcomes.
