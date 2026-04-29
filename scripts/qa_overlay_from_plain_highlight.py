# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportMissingModuleSource=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
# /// script
# dependencies = [
#   "pillow",
#   "numpy",
# ]
# requires-python = ">=3.12"
# ///
"""Create QA overlays showing changed pixels between plain and highlight images."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    if len(sys.argv) != 4:
        print('usage: qa_overlay_from_plain_highlight.py <plain_png> <highlight_png> <out_prefix>')
        return 2
    plain_path = Path(sys.argv[1]).resolve()
    hi_path = Path(sys.argv[2]).resolve()
    out_prefix = Path(sys.argv[3]).resolve()

    plain = np.asarray(Image.open(plain_path).convert('RGB'), dtype=np.int16)
    hi = np.asarray(Image.open(hi_path).convert('RGB'), dtype=np.int16)
    diff = np.abs(hi - plain)
    changed = np.max(diff, axis=-1) >= 30
    ys, xs = np.where(changed)
    if len(xs) == 0:
        print(json.dumps({'changed_pixels': 0}, indent=2))
        return 0

    vals = diff[changed]
    samples = []
    for y, x in list(zip(ys.tolist(), xs.tolist()))[:10]:
        samples.append({
            'xy': [x, y],
            'plain': plain[y, x].tolist(),
            'highlight': hi[y, x].tolist(),
            'delta': (hi[y, x] - plain[y, x]).tolist(),
        })

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    overlay = np.uint8(np.clip(plain, 0, 255))
    overlay[changed] = np.array([255, 0, 0], dtype=np.uint8)
    Image.fromarray(overlay).save(out_prefix.with_suffix('.changed_pixels_red.png'))

    mask = changed.copy()
    for _ in range(3):
        padded = np.pad(mask, 1)
        mask = (
            padded[:-2, :-2] | padded[:-2, 1:-1] | padded[:-2, 2:] |
            padded[1:-1, :-2] | padded[1:-1, 1:-1] | padded[1:-1, 2:] |
            padded[2:, :-2] | padded[2:, 1:-1] | padded[2:, 2:]
        )
    overlay_thick = np.uint8(np.clip(plain, 0, 255))
    overlay_thick[mask] = np.array([255, 0, 0], dtype=np.uint8)
    Image.fromarray(overlay_thick).save(out_prefix.with_suffix('.changed_pixels_red_thick.png'))

    print(json.dumps({
        'changed_pixels': int(changed.sum()),
        'bbox_xyxy': [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        'max_delta_any_channel': int(vals.max()),
        'mean_delta_any_channel': float(vals.max(axis=1).mean()),
        'samples': samples,
        'overlay': str(out_prefix.with_suffix('.changed_pixels_red.png')),
        'overlay_thick': str(out_prefix.with_suffix('.changed_pixels_red_thick.png')),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
