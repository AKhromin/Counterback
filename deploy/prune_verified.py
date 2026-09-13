#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("/var/lib/depth-recorder/data"))
    parser.add_argument("--minimum-age-hours", type=float, default=48)
    args = parser.parse_args()
    root = args.data_root.resolve()
    cutoff = time.time() - args.minimum_age_hours * 3600
    for manifest_path in root.glob("*/????-??-??/manifest-????-??-??.json"):
        marker = manifest_path.with_name(manifest_path.name + ".uploaded")
        if not marker.is_file() or manifest_path.stat().st_mtime > cutoff:
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("partial"):
            continue
        directory = manifest_path.parent.resolve()
        if root not in directory.parents:
            raise RuntimeError(f"unsafe manifest path: {directory}")
        for item in manifest.get("files", []):
            if item.get("kind") == "ledger" or not item.get("finalized", True):
                continue
            target = (directory / item["name"]).resolve()
            if target.parent != directory:
                raise RuntimeError(f"unsafe manifest entry: {target}")
            if target.exists() and target.stat().st_mtime <= cutoff:
                target.unlink()


if __name__ == "__main__":
    main()
