# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportMissingModuleSource=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
# /// script
# dependencies = [
#   "pillow",
#   "numpy",
# ]
# requires-python = ">=3.12"
# ///
"""Inspect a primitive's plain vs highlight raster in the stage2 recolor dataset."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    if len(sys.argv) < 3:
        print('usage: inspect_stage2_highlight.py <dataset_root> <primitive_id>')
        return 2
    root = Path(sys.argv[1]).resolve()
    primitive_id = sys.argv[2].replace('.png', '')
    manifest = root / 'flat' / 'primitive_manifest.jsonl'
    rows: dict[str, dict] = {}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row['primitive_id']] = row
    row = rows.get(primitive_id)
    if row is None:
        print(json.dumps({'error': 'primitive_not_found', 'primitive_id': primitive_id}, indent=2))
        return 1
    plain = Path(row['full_render_crop_raster_path'])
    hi = Path(row['full_render_crop_highlight_raster_path']) if row.get('full_render_crop_highlight_raster_path') else None
    out = {
        'primitive_id': primitive_id,
        'entity_type': row.get('entity_type'),
        'handle': row.get('handle'),
        'layer': row.get('layer'),
        'source_block_name': row.get('source_block_name'),
        'source_block_class': row.get('source_block_class'),
        'visible_in_render': row.get('visible_in_render'),
        'visible_in_render_diff_pixels': row.get('visible_in_render_diff_pixels'),
        'visible_in_render_diff_ratio': row.get('visible_in_render_diff_ratio'),
        'plain_path': str(plain),
        'highlight_path': str(hi) if hi else None,
        'plain_exists': plain.exists(),
        'highlight_exists': hi.exists() if hi else False,
    }
    if plain.exists() and hi and hi.exists():
        a = np.asarray(Image.open(plain).convert('RGB'), dtype=np.int16)
        b = np.asarray(Image.open(hi).convert('RGB'), dtype=np.int16)
        diff = np.abs(b - a)
        changed = np.max(diff, axis=-1) >= 30
        mag = ((b[:, :, 0] > 200) & (b[:, :, 1] < 80) & (b[:, :, 2] > 200))
        out.update(
            {
                'computed_changed_pixels': int(changed.sum()),
                'computed_changed_ratio': float(changed.mean()),
                'magenta_like_pixels': int(mag.sum()),
                'image_hw': [int(a.shape[0]), int(a.shape[1])],
            }
        )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
