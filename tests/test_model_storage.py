from dataclasses import asdict
import csv
import json

import numpy as np
import pytest
import tifffile

from haadf_studio.model import Document
from haadf_studio.storage import save_project, load_project, export_dataset, external_path
from haadf_studio.science import estimate_lattice, reconstruct, unit_cells


def test_bulk_undo_redo_and_reload(lattice, tmp_path):
    doc = lattice
    original = {k: asdict(v) for k, v in doc.points.items()}
    doc.update(doc.points, "Bulk reject", review_status="rejected")
    assert not doc.verified()
    assert doc.undo()
    assert {k: asdict(v) for k, v in doc.points.items()} == original
    assert doc.redo()
    file = tmp_path / "draft.hsgt"
    save_project(doc, file)
    restored = load_project(file)
    assert restored.state() == doc.state()
    assert np.array_equal(restored.raw, doc.raw)
    assert restored.undo()
    assert len(restored.verified()) == len(original)


def test_final_excludes_pending_rejected_and_moved(lattice):
    doc = lattice
    ids = list(doc.points)
    doc.update(ids[:3], "Pending", review_status="pending")
    doc.update(ids[3:5], "Reject", review_status="rejected")
    doc.finalize()
    assert len(doc.final["points"]) == len(ids) - 5
    assert doc.final_current
    key = ids[6]
    p = doc.points[key]
    ox, oy = p.x, p.y
    doc.move(key, ox + 1.2, oy + .6)
    assert not doc.final_current
    assert doc.points[key].original_x == ox
    assert doc.points[key].review_status == "pending"
    assert doc.undo()
    assert doc.points[key].x == ox
    assert doc.points[key].review_status == "verified"


def test_atomic_save_failure_preserves_old_file(lattice, tmp_path, monkeypatch):
    from haadf_studio import storage
    path = tmp_path / "draft.hsgt"
    save_project(lattice, path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        save_project(lattice, path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_repository_data_guard(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="outside"):
        external_path(tmp_path / "data" / "project.hsgt")


def test_export_dtypes_final_and_structural_meaning(lattice, tmp_path):
    doc = lattice
    doc.finalize()
    doc.cells, _ = unit_cells(doc)
    for c in doc.cells[:4]:
        c["status"] = "approved"
    doc.cells_approved = True
    root = export_dataset(doc, tmp_path)
    folder = root / "images" / doc.image_id
    assert tifffile.imread(folder / "raw_uint16.tif").dtype == np.uint16
    assert tifffile.imread(folder / "processed_float32.tif").dtype == np.float32
    rows = list(csv.DictReader((folder / "cation_sites.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 4
    assert all(r["label"] == "Cation Site" and "not evidence" in r["meaning"] for r in rows)
    assert len(list(csv.DictReader((folder / "tm_gt_verified.csv").open(encoding="utf-8-sig")))) == len(doc.verified())
    assert (folder / "tm_overlay.png").exists()
    assert not json.loads((folder / "config.json").read_text())["metadata"].get("magnification")
    key = next(iter(doc.points))
    doc.update([key], "Reject after final", review_status="rejected")
    with pytest.raises(ValueError, match="stale"):
        export_dataset(doc, tmp_path)


def test_out_of_bounds_and_dtype(lattice):
    with pytest.raises(ValueError):
        lattice.point(-1, 10)
    with pytest.raises(ValueError):
        lattice.point(float("nan"), 10)
    lattice.raw = lattice.raw.astype(np.uint8)
    with pytest.raises(ValueError, match="uint16"):
        lattice.validate()


def test_delete_undo_identity(lattice):
    key = next(iter(lattice.points))
    before = asdict(lattice.points[key])
    lattice.update([key], "Delete", deleted=True)
    assert key not in [p.point_id for p in lattice.verified()]
    lattice.undo()
    assert asdict(lattice.points[key]) == before


def test_failed_export_is_not_published(lattice, tmp_path, monkeypatch):
    from haadf_studio import storage
    lattice.finalize()
    def fail(*args, **kwargs):
        raise OSError("Simulated TIFF write failure")
    monkeypatch.setattr(storage.tifffile, "imwrite", fail)
    with pytest.raises(OSError):
        export_dataset(lattice, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_project_id_cannot_escape_export_root(lattice, tmp_path):
    lattice.finalize()
    lattice.image_id = "../../escape"
    with pytest.raises(ValueError, match="image ID"):
        export_dataset(lattice, tmp_path)
    assert not list(tmp_path.iterdir())
