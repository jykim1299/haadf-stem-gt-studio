from __future__ import annotations

from dataclasses import asdict
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import h5py
import numpy as np
from PIL import Image, ImageDraw
import tifffile

from .model import Document, stamp, uid
from .science import PREPROCESS, preprocess, detected_points


def external_path(path):
    path = Path(path).resolve()
    for parent in [path, *path.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            raise ValueError("Research projects and exports must be outside a source repository. Choose a separate data folder.")
    return path


def save_project(doc, path):
    doc.validate()
    path = external_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
                z.writestr("project.json", json.dumps(doc.state(), ensure_ascii=False, allow_nan=False))
                for name, data in [("raw.npy", doc.raw), ("processed.npy", doc.processed)]:
                    buffer = io.BytesIO()
                    np.save(buffer, data, allow_pickle=False)
                    z.writestr(name, buffer.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_project(path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 4 * 1024 ** 3:
            raise ValueError("Project exceeds the 4 GiB safety limit.")
        state = json.loads(z.read("project.json"))
        raw = np.load(io.BytesIO(z.read("raw.npy")), allow_pickle=False)
        processed = np.load(io.BytesIO(z.read("processed.npy")), allow_pickle=False)
    return Document.from_state(state, raw, processed)


def jsonable(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        return value.tolist() if value.size < 4096 else {"shape": list(value.shape), "note": "large attribute omitted"}
    if isinstance(value, np.generic):
        return value.item()
    return str(value) if not isinstance(value, (str, int, float, bool, list, dict, type(None))) else value


def inspect_emd(path):
    """HDF5 inventory only. Never infer HAADF identity from an arbitrary first dataset."""
    datasets, attrs, metadata_payloads = [], {}, {}
    with h5py.File(path, "r") as f:
        attrs["/"] = {str(k): jsonable(v) for k, v in f.attrs.items()}
        def visitor(name, obj):
            if obj.attrs:
                attrs[name] = {str(k): jsonable(v) for k, v in obj.attrs.items()}
            if isinstance(obj, h5py.Dataset):
                if obj.dtype == np.uint16 and obj.ndim in (2, 3):
                    datasets.append({"path": name, "shape": list(obj.shape), "dtype": str(obj.dtype)})
                if "metadata" in name.lower() and obj.size * obj.dtype.itemsize <= 2 * 1024 ** 2:
                    data = obj[()]
                    if obj.dtype.kind in "uSiO":
                        try:
                            text = data.tobytes().decode("utf-8").rstrip("\x00")
                            metadata_payloads[name] = text
                        except (UnicodeDecodeError, AttributeError):
                            metadata_payloads[name] = str(data)[:100000]
        f.visititems(visitor)
    return {"datasets": datasets, "attributes": attrs, "metadata_payloads": metadata_payloads,
            "validation": "Experimental generic HDF5 adapter; real vendor EMD validation pending.",
            "magnification_status": "unknown"}


def import_image(path, dataset=None, frame=0, frame_axis=2):
    path = Path(path)
    metadata = {"source_filename": path.name, "source_path": str(path.resolve()),
                "synthetic": False, "magnification": None, "magnification_status": "unknown",
                "calibration": {"pixel_size": None, "unit": "unknown"}, "preprocessing": PREPROCESS}
    if path.suffix.lower() == ".emd":
        info = inspect_emd(path)
        metadata["emd"] = info
        if dataset not in {d["path"] for d in info["datasets"]}:
            raise ValueError("Explicitly select and confirm a uint16 HAADF dataset.")
        with h5py.File(path, "r") as f:
            ds = f[dataset]
            if ds.ndim == 3:
                if frame_axis not in (0, 1, 2) or not 0 <= frame < ds.shape[frame_axis]:
                    raise ValueError("Invalid frame or frame axis.")
                if np.prod([s for i, s in enumerate(ds.shape) if i != frame_axis]) * 2 > 512 * 1024 ** 2:
                    raise ValueError("Frame exceeds the 512 MiB raw image limit; tiled import is not implemented.")
                key = [slice(None)] * 3
                key[frame_axis] = frame
                raw = ds[tuple(key)]
            else:
                if ds.size * ds.dtype.itemsize > 512 * 1024 ** 2:
                    raise ValueError("Image exceeds the 512 MiB raw image limit; tiled import is not implemented.")
                raw = ds[()]
        metadata.update({"emd_dataset": dataset, "frame": frame, "frame_axis": frame_axis,
                         "channel_identity": "HAADF user-confirmed"})
    else:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            if len(series.shape) != 2 or series.dtype != np.uint16:
                raise ValueError("Only explicit 2D uint16 TIFF images are supported.")
            if np.prod(series.shape) * 2 > 512 * 1024 ** 2:
                raise ValueError("Image exceeds the 512 MiB raw image limit; tiled import is not implemented.")
            raw = series.asarray()
            metadata["tiff_description"] = tif.pages[0].description or ""
    if raw.ndim != 2 or raw.dtype != np.uint16:
        raise ValueError("Only explicit 2D uint16 raw images are supported. No lossy conversion was performed.")
    if raw.nbytes > 512 * 1024 ** 2:
        raise ValueError("Image exceeds the current 512 MiB raw image limit; tiled import is not yet implemented.")
    metadata["shape"] = list(raw.shape)
    metadata["raw_sha256"] = hashlib.sha256(raw.tobytes()).hexdigest()
    doc = Document(uid("image"), raw, preprocess(raw), metadata=metadata)
    pts = detected_points(doc)
    doc.points = {p.point_id: p for p in pts}
    doc.candidates = [asdict(p) for p in pts]
    return doc


def write_csv(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


POINT_FIELDS = ["point_id", "x", "y", "original_x", "original_y", "raw_intensity", "processed_intensity",
                "source", "label", "review_status", "layer_id", "region_id", "seed", "deleted"]
CELL_FIELDS = ["cell_id", "tm1_id", "tm2_id", "x", "y", "region_id", "boundary", "status", "endpoint_error_px", "final_version"]


def export_dataset(doc, root):
    doc.validate()
    if not doc.final_current:
        raise ValueError("Final TM is missing or stale. Finalize current annotations before dataset export.")
    if not doc.cells_current:
        raise ValueError("Unit-cell preview belongs to an older final version; regenerate it before export.")
    parent = external_path(root)
    parent.mkdir(parents=True, exist_ok=True)
    # Immutable export revision avoids mixing images/configuration from different annotation states.
    destination = parent / (doc.image_id + "_v" + str(doc.final["version"]) + "_" + uid("export")[-8:])
    staging = Path(tempfile.mkdtemp(prefix=".haadf-export-", dir=parent)).resolve()
    try:
        _write_dataset(doc, staging)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            # Strict containment check before recursive cleanup of our own staging folder.
            if staging.parent != parent or not staging.name.startswith(".haadf-export-"):
                raise RuntimeError("Unsafe export staging path; cleanup refused.")
            shutil.rmtree(staging)
    return destination


def _write_dataset(doc, root):
    image = root / "images" / doc.image_id
    image.mkdir(parents=True)
    tifffile.imwrite(image / "raw_uint16.tif", doc.raw)
    tifffile.imwrite(image / "processed_float32.tif", doc.processed)
    pts = [asdict(p) for p in doc.points.values()]
    write_csv(image / "atom_candidates.csv", doc.candidates, POINT_FIELDS)
    write_csv(image / "tm_proposals.csv", [p for p in pts if p["label"] == "TM"], POINT_FIELDS)
    write_csv(image / "tm_gt_verified.csv", doc.final["points"], POINT_FIELDS)
    write_csv(image / "tm_layer_table.csv", doc.final["points"], ["point_id", "layer_id", "region_id", "x", "y"])
    write_csv(image / "unit_cells.csv", doc.cells, CELL_FIELDS)
    approved = [c for c in doc.cells if c["status"] == "approved"] if doc.cells_approved else []
    sites = []
    for cell in approved:
        x, y = cell["x"], cell["y"]
        ix, iy = min(doc.raw.shape[1] - 1, round(x)), min(doc.raw.shape[0] - 1, round(y))
        sites.append({"site_id": "site_" + cell["cell_id"], "cell_id": cell["cell_id"], "x": x, "y": y,
                      "label": "Cation Site", "meaning": "structural measurement location; not evidence of cation mixing",
                      "raw_intensity": float(doc.raw[iy, ix]), "processed_intensity": float(doc.processed[iy, ix])})
    write_csv(image / "cation_sites.csv", sites, ["site_id", "cell_id", "x", "y", "label", "meaning", "raw_intensity", "processed_intensity"])
    (image / "annotation_history.json").write_text(json.dumps(doc.history, indent=2), encoding="utf-8")
    config = {"schema_version": 1, "coordinate_system": "image pixels, origin top-left, x right, y down; integer centers",
              "metadata": doc.metadata, "analysis": doc.analysis, "unit_cell_config": doc.cell_config,
              "final": {k: v for k, v in doc.final.items() if k != "points"}, "cells_approved": doc.cells_approved}
    (image / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    if doc.metadata.get("synthetic"):
        (image / "synthetic_truth.json").write_text(json.dumps(doc.metadata["truth"]), encoding="utf-8")
    display = display_uint8(doc.raw)
    overlay = Image.fromarray(display).convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for p in doc.final["points"]:
        x, y = p["x"], p["y"]
        draw.ellipse((x-2, y-2, x+2, y+2), outline="#39e7b0")
    overlay.save(image / "tm_overlay.png")
    for c in doc.cells:
        if c["status"] == "rejected":
            continue
        a, b = doc.points[c["tm1_id"]], doc.points[c["tm2_id"]]
        draw.line((a.x, a.y, b.x, b.y), fill="#ba88ff" if c["status"] == "approved" else "#f0b45b")
    overlay.save(image / "unit_cell_overlay.png")
    inventory = {"image_id": doc.image_id, "width": doc.raw.shape[1], "height": doc.raw.shape[0],
                 "synthetic": doc.metadata.get("synthetic", False), "magnification": doc.metadata.get("magnification"),
                 "magnification_status": doc.metadata.get("magnification_status", "unknown"),
                 "target_3_90_mx": doc.metadata.get("magnification_status") == "user-verified" and doc.metadata.get("magnification") == 3900000}
    write_csv(root / "inventory.csv", [inventory], list(inventory))
    summary = {"image_id": doc.image_id, "candidates": len(doc.candidates), "verified_tm": len(doc.final["points"]),
               "approved_cation_sites": len(sites), "pending": sum(p["review_status"] == "pending" and not p["deleted"] for p in pts)}
    write_csv(root / "dataset_summary.csv", [summary], list(summary))
    (root / "REPORT.md").write_text(
        f"# GT export\n\nCreated {stamp()}\n\nVerified TM: {summary['verified_tm']}; approved structural cation sites: {len(sites)}.\n\n"
        "Only human-verified TM enter tm_gt_verified.csv. Candidate labels are separate from structural Cation Sites.\n"
        "unit_cells.csv includes review status; only approved cells enter cation_sites.csv.\n"
        "Synthetic tests do not establish performance on real EMD. Unknown magnification is excluded from 3.90 Mx.\n", encoding="utf-8")
    return root


def display_uint8(data):
    lo, hi = np.percentile(data, [.2, 99.8])
    return (np.clip((data.astype(np.float32) - lo) / max(hi - lo, 1e-9), 0, 1) * 255).astype(np.uint8)
