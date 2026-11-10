"""Sentinel-2 tile-seam score of a cached layer.

    python scripts/seam_report.py [--res 500] [--layer s2_ferric]
                                  [--seasons 2024,2025,2026 --per-tile 6 --max-cloud 5]
                                  [--npz PATH] [--footprints SIDECAR.json]

The score is the mean absolute step between neighbouring cells that lie in different
tile/orbit footprint regions, divided by the same statistic inside regions (1.0 = no seams).
Footprints come from the cache's sidecar JSON (written by `features.build`), or from
`--footprints`, or else from a fresh STAC query with the same settings (needs the network).
Honours ZIMPROSPECT_DATA_DIR. Exits 1 if the inputs are missing or inconsistent.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zimprospect import features, s2, sources  # noqa: E402
from zimprospect.grid import Grid, make_grid  # noqa: E402

log = logging.getLogger("seam_report")


def _settings(args: argparse.Namespace) -> s2.S2Settings:
    seasons = (s2.dry_seasons(int(y) for y in args.seasons.split(",")) if args.seasons
               else s2.LEGACY_S2.seasons)
    return s2.S2Settings(seasons=seasons, per_tile=args.per_tile, max_cloud=args.max_cloud)


def _records(sidecar: Path, grid: Grid, settings: s2.S2Settings) -> list[dict]:
    if sidecar.exists():
        scenes = json.loads(sidecar.read_text()).get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError(f"{sidecar} lists no scenes")
        return scenes
    log.info("no sidecar at %s: querying STAC for the scenes of %s", sidecar, settings)
    return s2.scene_records(features.select_s2_scenes(grid, settings))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--res", type=float, default=500.0)
    parser.add_argument("--layer", default="s2_ferric")
    parser.add_argument("--seasons", default=None, help="comma-separated dry-season years (default: baseline)")
    parser.add_argument("--per-tile", type=int, default=s2.LEGACY_S2.per_tile)
    parser.add_argument("--max-cloud", type=float, default=s2.LEGACY_S2.max_cloud)
    parser.add_argument("--npz", type=Path, default=None, help="cache file (default: from the settings)")
    parser.add_argument("--footprints", type=Path, default=None, help="sidecar JSON to take footprints from")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    args = _parse(argv)
    settings = _settings(args)
    npz = args.npz or features.cache_path("sentinel2", args.res, settings)
    if not npz.exists():
        log.error("no cached Sentinel-2 layers at %s", npz)
        return 1
    grid = make_grid(sources.boundary(), res_m=args.res)
    with np.load(npz) as cached:
        if args.layer not in cached.files:
            log.error("%s has no layer %r (has %s)", npz, args.layer, cached.files)
            return 1
        layer = cached[args.layer]
    if layer.shape != grid.shape:
        log.error("layer shape %s does not match the %g m grid %s", layer.shape, args.res, grid.shape)
        return 1
    records = _records(args.footprints or npz.with_suffix(".json"), grid, settings)
    score = s2.layer_seam_score(layer, records, grid)
    log.info("%s  layer=%s  scenes=%d  groups=%d  seam_score=%.3f", npz.name, args.layer, len(records),
             len({r["group"] for r in records}), score)
    return 0


if __name__ == "__main__":
    sys.exit(main())
