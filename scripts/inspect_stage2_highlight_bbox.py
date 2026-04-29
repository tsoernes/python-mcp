# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportMissingModuleSource=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
# /// script
# dependencies = [
#   "pillow",
#   "numpy",
# ]
# requires-python = ">=3.12"
# ///
"""Report changed-pixel bounding box for plain vs highlight raster."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    if len(sys.argv) != 3:
        print('usage: inspect_stage2_highlight_bbox.py <plain_png> <highlight_png>')
        return 2
    plain = Path(sys.argv[1])
    hi = Path(sys.argv[2])
    a = np.asarray(Image.open(plain).convert('RGB'), dtype=np.int16)
    b = np.asarray(Image.open(hi).convert('RGB'), dtype=np.int16)
    diff = np.abs(b - a)
    changed = np.max(diff, axis=-1) >= 30
    ys, xs = np.where(changed)
    if len(xs) == 0:
        print('{"changed": false}')
        return 0
    print({
        'changed': True,
        'changed_pixels': int(changed.sum()),
        'bbox_xyxy': [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
    })
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
