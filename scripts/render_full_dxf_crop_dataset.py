# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportUnusedVariable=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false, reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportImplicitStringConcatenation=false, reportAssignmentType=false, reportMissingImports=false, reportMissingTypeArgument=false, reportMissingModuleSource=false, reportArgumentType=false, reportUnannotatedClassAttribute=false
# /// script
# dependencies = [
#   "ezdxf",
#   "pillow",
#   "numpy",
# ]
# requires-python = ">=3.12"
# ///
"""Render plain and highlighted primitive crops from a blocked DXF using source-handle recolor.

Workflow:
- render the full blocked DXF once for the plain background
- crop plain images for every primitive
- group primitives by source handle
- recolor the actual DXF entity for that handle in a temporary DXF
- rerender the full drawing once per unique handle
- crop highlighted images from the recolored full render
- mark visibility from plain-vs-recolored crop diffs
- exclude invisible primitives from training outputs

This avoids synthetic geometry overlays and instead uses the CAD renderer as ground truth.
The script now also supports resume and per-handle error logging.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import traceback
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
from PIL import Image

DEFAULT_DIFF_PIXEL_THRESHOLD = 30
DEFAULT_MIN_CHANGED_PIXELS = 3
DEFAULT_RECOLOR_RGB = (255, 0, 255)


def parse_viewbox(svg_path: Path) -> list[float]:
    root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    vb = root.attrib.get("viewBox")
    if not vb:
        raise ValueError(f"SVG has no viewBox: {svg_path}")
    parts = [float(x) for x in vb.replace(",", " ").split()]
    if len(parts) != 4:
        raise ValueError(f"Invalid viewBox in {svg_path}: {vb}")
    return parts


def crop_from_world_window(
    img: Image.Image,
    full_viewbox: list[float],
    crop_window: list[float],
    flip_y: bool,
) -> Image.Image:
    fx, fy, fw, fh = [float(v) for v in full_viewbox]
    cx0, cy0, cx1, cy1 = [float(v) for v in crop_window]
    img_w, img_h = img.size
    px0 = (cx0 - fx) / fw * img_w
    px1 = (cx1 - fx) / fw * img_w
    if flip_y:
        py0 = (fy + fh - cy1) / fh * img_h
        py1 = (fy + fh - cy0) / fh * img_h
    else:
        py0 = (cy0 - fy) / fh * img_h
        py1 = (cy1 - fy) / fh * img_h
    left = max(0, int(math.floor(min(px0, px1))))
    top = max(0, int(math.floor(min(py0, py1))))
    right = min(img_w, int(math.ceil(max(px0, px1))))
    bottom = min(img_h, int(math.ceil(max(py0, py1))))
    if right <= left:
        right = min(img_w, left + 1)
    if bottom <= top:
        bottom = min(img_h, top + 1)
    return img.crop((left, top, right, bottom))


def ensure_link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def build_qcad_env(qcad_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(qcad_root) + ((":" + env["LD_LIBRARY_PATH"]) if env.get("LD_LIBRARY_PATH") else "")
    return env


def render_full_dxf(
    blocked_dxf: Path,
    out_png: Path,
    full_viewbox: list[float],
    full_width: int,
    qcad_root: Path,
) -> tuple[int, int]:
    full_height = max(1, int(round(full_width * full_viewbox[3] / full_viewbox[2])))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    qcad_bin = qcad_root / "qcad-bin"
    cmd = [
        str(qcad_bin),
        "-platform",
        "offscreen",
        "-style",
        "Fusion",
        "-no-dock-icon",
        "-no-gui",
        "-allow-multiple-instances",
        "-autostart",
        "scripts/Pro/Tools/Dwg2Bmp/Dwg2Bmp.js",
        "dwg2bmp",
        "-f",
        "-b",
        "white",
        "-c",
        "-a",
        "-w",
        f"{full_viewbox[0]},{full_viewbox[1]},{full_viewbox[2]},{full_viewbox[3]}",
        "-x",
        str(full_width),
        "-y",
        str(full_height),
        "-o",
        str(out_png),
        str(blocked_dxf),
    ]
    result = subprocess.run(
        cmd,
        cwd=qcad_root,
        env=build_qcad_env(qcad_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
        raise RuntimeError(
            f"Full DXF render failed for {blocked_dxf}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return full_width, full_height


def find_entity_by_handle(doc: ezdxf.document.Drawing, handle: str):
    if not handle:
        return None
    try:
        entity = doc.entitydb.get(handle)
        if entity is not None:
            return entity
    except Exception:
        pass
    try:
        for entity in doc.modelspace():
            if getattr(entity.dxf, "handle", None) == handle:
                return entity
    except Exception:
        pass
    try:
        for block in doc.blocks:
            for entity in block:
                if getattr(entity.dxf, "handle", None) == handle:
                    return entity
    except Exception:
        pass
    return None


def recolor_entity_in_dxf(source_dxf: Path, handle: str, out_dxf: Path, rgb: tuple[int, int, int]) -> bool:
    doc = ezdxf.readfile(source_dxf)
    entity = find_entity_by_handle(doc, handle)
    if entity is None:
        return False
    try:
        entity.rgb = rgb
    except Exception:
        pass
    try:
        entity.dxf.true_color = (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
    except Exception:
        pass
    try:
        entity.dxf.color = 1
    except Exception:
        pass
    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_dxf)
    return True


def compute_diff_metrics(
    plain_img: Image.Image,
    recolor_img: Image.Image,
    pixel_threshold: int,
    min_changed_pixels: int,
) -> dict[str, Any]:
    plain_arr = np.asarray(plain_img.convert("RGB"), dtype=np.int16)
    recolor_arr = np.asarray(recolor_img.convert("RGB"), dtype=np.int16)
    diff = np.abs(recolor_arr - plain_arr)
    changed = np.max(diff, axis=-1) >= int(pixel_threshold)
    changed_pixels = int(changed.sum())
    total_pixels = int(changed.size)
    changed_ratio = float(changed_pixels / total_pixels) if total_pixels else 0.0
    visible = bool(changed_pixels >= int(min_changed_pixels))
    return {
        "visible_in_render": visible,
        "visible_in_render_diff_pixels": changed_pixels,
        "visible_in_render_diff_ratio": changed_ratio,
    }


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in items:
            fh.write(json.dumps(row) + "\n")


def write_outputs(
    *,
    source_root: Path,
    output_root: Path,
    blocked_dxf: Path,
    flat_svg: Path,
    full_viewbox: list[float],
    full_plain_png: Path,
    render_size: tuple[int, int],
    combined_rows: list[dict[str, Any]],
    rows_by_handle: dict[str, list[dict[str, Any]]],
    plain_dir: Path,
    highlight_dir: Path,
    training_plain_dir: Path,
    training_highlight_dir: Path,
    flat_out_dir: Path,
    failed_handles: list[dict[str, Any]],
    skipped_existing_handles: int,
    diff_pixel_threshold: int,
    min_changed_pixels: int,
) -> dict[str, Any]:
    visible_rows = [row for row in combined_rows if row.get("visible_in_render") is True]
    hidden_rows = [row for row in combined_rows if row.get("visible_in_render") is False]
    manifest_all = flat_out_dir / "primitive_manifest.jsonl"
    manifest_visible = flat_out_dir / "primitive_manifest.visible_only.jsonl"
    manifest_hidden = flat_out_dir / "primitive_manifest.hidden_only.jsonl"
    failures_json = output_root / "failed_handles.json"
    for path, items in [
        (manifest_all, combined_rows),
        (manifest_visible, visible_rows),
        (manifest_hidden, hidden_rows),
    ]:
        write_jsonl(path, items)
    failures_json.write_text(json.dumps(failed_handles, indent=2), encoding="utf-8")
    summary = {
        "source_root": str(source_root),
        "blocked_dxf": str(blocked_dxf),
        "flat_svg": str(flat_svg),
        "full_viewbox": full_viewbox,
        "full_plain_render_png": str(full_plain_png),
        "full_render_size": list(render_size),
        "primitive_count": len(combined_rows),
        "unique_handle_count": len(rows_by_handle),
        "visible_in_render_count": len(visible_rows),
        "hidden_in_render_count": len(hidden_rows),
        "failed_handle_count": len(failed_handles),
        "skipped_existing_handles": int(skipped_existing_handles),
        "plain_dataset_dir": str(plain_dir),
        "highlight_dataset_dir": str(highlight_dir),
        "training_plain_dataset_dir": str(training_plain_dir),
        "training_highlight_dataset_dir": str(training_highlight_dir),
        "combined_manifest": str(manifest_all),
        "visible_manifest": str(manifest_visible),
        "hidden_manifest": str(manifest_hidden),
        "failed_handles_json": str(failures_json),
        "render_backend": "qcad full dxf render -> crop",
        "highlight_style": "source handle recolor rerender",
        "diff_pixel_threshold": int(diff_pixel_threshold),
        "min_changed_pixels": int(min_changed_pixels),
    }
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--full-width", type=int, default=12000)
    parser.add_argument("--rerender-full", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--diff-pixel-threshold", type=int, default=DEFAULT_DIFF_PIXEL_THRESHOLD)
    parser.add_argument("--min-changed-pixels", type=int, default=DEFAULT_MIN_CHANGED_PIXELS)
    parser.add_argument("--qcad-root", type=Path, default=Path("/home/torstein.sornes/opt/qcad-3.32.6-pro-linux-qt5.14-x86_64"))
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    qcad_root = args.qcad_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    flat_svg = source_root / "flat" / f"{source_root.name}.flat.svg"
    blocked_dxf = source_root / "working" / f"{source_root.name}.blocked.dxf"
    manifest_path = source_root / "flat" / "primitive_manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    full_viewbox = parse_viewbox(flat_svg)

    full_dir = output_root / "full"
    plain_dir = output_root / "plain" / "primitive_rasters"
    highlight_dir = output_root / "highlight" / "primitive_rasters"
    training_plain_dir = output_root / "training" / "plain" / "primitive_rasters"
    training_highlight_dir = output_root / "training" / "highlight" / "primitive_rasters"
    flat_out_dir = output_root / "flat"
    tmp_dir = output_root / "_tmp_recolor"
    errors_dir = output_root / "errors"
    for path in [full_dir, plain_dir, highlight_dir, training_plain_dir, training_highlight_dir, flat_out_dir, tmp_dir, errors_dir]:
        path.mkdir(parents=True, exist_ok=True)

    full_plain_png = full_dir / "full_dxf_plain.png"
    if args.rerender_full or not full_plain_png.exists():
        render_size = render_full_dxf(blocked_dxf, full_plain_png, full_viewbox, args.full_width, qcad_root)
    else:
        with Image.open(full_plain_png) as existing:
            render_size = existing.size
    full_plain_img = Image.open(full_plain_png).convert("RGB")

    plain_crops_by_id: dict[str, Path] = {}
    rows_by_handle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    combined_rows: list[dict[str, Any]] = []

    for row in rows:
        primitive_id = row["primitive_id"]
        crop_vb = row.get("spotlight_view_box")
        if not crop_vb:
            continue
        crop_window = [crop_vb[0], crop_vb[1], crop_vb[0] + crop_vb[2], crop_vb[1] + crop_vb[3]]
        plain_path = plain_dir / f"{primitive_id}.png"
        if not (args.resume and plain_path.exists() and plain_path.stat().st_size > 0):
            plain_crop = crop_from_world_window(full_plain_img, full_viewbox, crop_window, flip_y=True)
            plain_crop.save(plain_path)
        plain_crops_by_id[primitive_id] = plain_path
        updated = dict(row)
        updated["full_render_crop_raster_path"] = str(plain_path)
        updated["full_render_crop_window"] = crop_window
        updated["full_render_highlight_method"] = "source_handle_recolor"
        updated["visible_in_render"] = False
        updated["visible_in_render_diff_pixels"] = 0
        updated["visible_in_render_diff_ratio"] = 0.0
        updated["full_render_crop_highlight_raster_path"] = None
        updated["source_handle_recolor_found"] = False
        updated["processing_error"] = None
        combined_rows.append(updated)
        handle = str(row.get("handle") or "").strip()
        if handle:
            rows_by_handle[handle].append(updated)

    failed_handles: list[dict[str, Any]] = []
    skipped_existing_handles = 0

    for index, (handle, grouped_rows) in enumerate(rows_by_handle.items(), start=1):
        expected_highlights = [highlight_dir / f"{row['primitive_id']}.png" for row in grouped_rows]
        if args.resume and expected_highlights and all(path.exists() and path.stat().st_size > 0 for path in expected_highlights):
            skipped_existing_handles += 1
            for row in grouped_rows:
                primitive_id = row["primitive_id"]
                highlight_path = highlight_dir / f"{primitive_id}.png"
                row["full_render_crop_highlight_raster_path"] = str(highlight_path)
                row["source_handle_recolor_found"] = True
                visible = (training_highlight_dir / f"{primitive_id}.png").exists()
                row["visible_in_render"] = bool(visible)
                row["visible_in_render_diff_pixels"] = 1 if visible else 0
                row["visible_in_render_diff_ratio"] = 0.0
                if visible:
                    ensure_link_or_copy(plain_crops_by_id[primitive_id], training_plain_dir / f"{primitive_id}.png")
                    ensure_link_or_copy(highlight_path, training_highlight_dir / f"{primitive_id}.png")
            continue

        temp_dxf = tmp_dir / f"{handle}.dxf"
        temp_png = tmp_dir / f"{handle}.png"
        try:
            for temp_path in [temp_dxf, temp_png]:
                if temp_path.exists():
                    temp_path.unlink()
            found = recolor_entity_in_dxf(blocked_dxf, handle, temp_dxf, DEFAULT_RECOLOR_RGB)
            if not found:
                for row in grouped_rows:
                    row["source_handle_recolor_found"] = False
                continue
            render_full_dxf(temp_dxf, temp_png, full_viewbox, args.full_width, qcad_root)
            with Image.open(temp_png) as recolor_open:
                recolor_full_img = recolor_open.convert("RGB")
                for row in grouped_rows:
                    primitive_id = row["primitive_id"]
                    crop_window = row["full_render_crop_window"]
                    recolor_crop = crop_from_world_window(recolor_full_img, full_viewbox, crop_window, flip_y=True)
                    highlight_path = highlight_dir / f"{primitive_id}.png"
                    recolor_crop.save(highlight_path)
                    row["full_render_crop_highlight_raster_path"] = str(highlight_path)
                    row["source_handle_recolor_found"] = True
                    with Image.open(plain_crops_by_id[primitive_id]) as plain_handle:
                        plain_crop = plain_handle.convert("RGB")
                    metrics = compute_diff_metrics(
                        plain_crop,
                        recolor_crop,
                        pixel_threshold=int(args.diff_pixel_threshold),
                        min_changed_pixels=int(args.min_changed_pixels),
                    )
                    row.update(metrics)
                    if row["visible_in_render"]:
                        ensure_link_or_copy(plain_crops_by_id[primitive_id], training_plain_dir / f"{primitive_id}.png")
                        ensure_link_or_copy(highlight_path, training_highlight_dir / f"{primitive_id}.png")
                    else:
                        for stale in [
                            training_plain_dir / f"{primitive_id}.png",
                            training_highlight_dir / f"{primitive_id}.png",
                        ]:
                            if stale.exists() or stale.is_symlink():
                                stale.unlink()
        except Exception as exc:
            error = {
                "handle": handle,
                "primitive_ids": [row["primitive_id"] for row in grouped_rows],
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            }
            failed_handles.append(error)
            (errors_dir / f"{handle}.json").write_text(json.dumps(error, indent=2), encoding="utf-8")
            for row in grouped_rows:
                row["processing_error"] = repr(exc)
            if args.stop_on_error:
                raise
        finally:
            for temp_path in [temp_dxf, temp_png]:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception:
                    pass

        if index % max(1, int(args.progress_every)) == 0:
            summary = write_outputs(
                source_root=source_root,
                output_root=output_root,
                blocked_dxf=blocked_dxf,
                flat_svg=flat_svg,
                full_viewbox=full_viewbox,
                full_plain_png=full_plain_png,
                render_size=render_size,
                combined_rows=combined_rows,
                rows_by_handle=rows_by_handle,
                plain_dir=plain_dir,
                highlight_dir=highlight_dir,
                training_plain_dir=training_plain_dir,
                training_highlight_dir=training_highlight_dir,
                flat_out_dir=flat_out_dir,
                failed_handles=failed_handles,
                skipped_existing_handles=skipped_existing_handles,
                diff_pixel_threshold=int(args.diff_pixel_threshold),
                min_changed_pixels=int(args.min_changed_pixels),
            )
            print(json.dumps({"progress_handle_index": index, **summary}, indent=2))

    summary = write_outputs(
        source_root=source_root,
        output_root=output_root,
        blocked_dxf=blocked_dxf,
        flat_svg=flat_svg,
        full_viewbox=full_viewbox,
        full_plain_png=full_plain_png,
        render_size=render_size,
        combined_rows=combined_rows,
        rows_by_handle=rows_by_handle,
        plain_dir=plain_dir,
        highlight_dir=highlight_dir,
        training_plain_dir=training_plain_dir,
        training_highlight_dir=training_highlight_dir,
        flat_out_dir=flat_out_dir,
        failed_handles=failed_handles,
        skipped_existing_handles=skipped_existing_handles,
        diff_pixel_threshold=int(args.diff_pixel_threshold),
        min_changed_pixels=int(args.min_changed_pixels),
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
