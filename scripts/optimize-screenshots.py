#!/usr/bin/env python3
"""Compress the README screenshots into docs/images/.

`pnpm screenshots` (in frontend/) captures them at twice the CSS size into
frontend/test-results/screenshots/ and then runs this. Each capture is
reduced to a 256-colour palette with libimagequant (Pillow's
``Image.quantize``, dithered), which keeps UI text sharp at a fraction of
the size, and saved as an optimised PNG. Needs Pillow built with
libimagequant (most distribution and PyPI builds are).

    python3 scripts/optimize-screenshots.py [SOURCE_DIR] [TARGET_DIR]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, features

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "test-results" / "screenshots"
TARGET = ROOT / "docs" / "images"
# Larger files slow the README down; the script says so instead of failing.
BUDGET = 250 * 1024


def optimize(source: Path, target: Path) -> int:
    with Image.open(source) as image:
        rgb = image.convert("RGB")
    palette = rgb.quantize(colors=256, method=Image.Quantize.LIBIMAGEQUANT, dither=Image.Dither.FLOYDSTEINBERG)
    palette.save(target, format="PNG", optimize=True)
    return target.stat().st_size


def main() -> int:
    source_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    target_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else TARGET
    if not features.check("libimagequant"):
        print("Pillow lacks libimagequant: install a Pillow build with it", file=sys.stderr)
        return 1
    captures = sorted(source_dir.glob("*.png"))
    if not captures:
        print(f"no captures in {source_dir}: run `pnpm screenshots` in frontend/", file=sys.stderr)
        return 1
    target_dir.mkdir(parents=True, exist_ok=True)
    for capture in captures:
        size = optimize(capture, target_dir / capture.name)
        note = "  over budget" if size > BUDGET else ""
        print(f"{capture.name:28} {capture.stat().st_size // 1024:5} KB -> {size // 1024:4} KB{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
