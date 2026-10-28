import h5py
import numpy as np
import pytest

from haadf_studio.science import (SCENARIOS, ScipyDetector, estimate_lattice, preprocess,
                                  reconstruct, synthetic, unit_cells)
from haadf_studio.storage import import_image, inspect_emd


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_synthetic_scenarios(scenario):
    d = synthetic(scenario, size=240, seed=7)
    assert d.raw.dtype == np.uint16
    assert d.processed.dtype == np.float32
    assert d.raw.shape == d.processed.shape
    assert len(d.metadata["truth"]["tm"]) > 100
    assert d.points
    assert all(p.label == "unknown" and p.review_status == "pending" for p in d.points.values())
    assert np.isfinite(d.processed).all() and d.processed.min() >= 0 and d.processed.max() <= 1


def test_preprocess_constant_and_intensity_contract():
    raw = np.full((31, 31), 2000, np.uint16)
    assert np.all(preprocess(raw) == 0)
    raw[15, 15] = 60000
    original = raw.copy()
    result = preprocess(raw)
    assert np.unravel_index(result.argmax(), result.shape) == (15, 15)
    assert np.array_equal(raw, original)
    with pytest.raises(ValueError):
        preprocess(raw.astype(np.float64))


def test_adaptive_spacing_and_reconstruction(lattice):
    est = estimate_lattice(lattice)
    assert est["d_tm"] == pytest.approx(10)
    assert est["d_layer"] == pytest.approx(20)
    points = reconstruct(lattice, est)
    assert sorted((p.x, p.y) for p in points) == [(80., 40.), (90., 40.)]
    assert all(p.review_status == "pending" and p.source == "reconstruction" for p in points)
    lattice.commit("Reconstruction", {p.point_id: p for p in points})
    assert reconstruct(lattice, estimate_lattice(lattice)) == []
    with pytest.raises(ValueError, match="stale"):
        reconstruct(lattice, est)


def test_no_seeds_and_false_candidates_do_not_contaminate(lattice):
    expected = estimate_lattice(lattice)["d_tm"]
    for x in range(20, 200, 3):
        p = lattice.point(x, 43, layer_id=1, label="unknown")
        lattice.points[p.point_id] = p
    assert estimate_lattice(lattice)["d_tm"] == expected
    lattice.update(lattice.points, "Untrusted", label="unknown", review_status="pending", seed=False)
    with pytest.raises(ValueError, match="four"):
        estimate_lattice(lattice)


def test_tilted_adaptive_spacing(lattice):
    for p in lattice.points.values():
        p.x *= .7
        p.y += p.x * .15
    est = estimate_lattice(lattice)
    assert est["theta_deg"] == pytest.approx(np.degrees(np.arctan(.15)))
    assert est["d_tm"] == pytest.approx(7 * np.sqrt(1 + .15**2))


def test_cells_require_final_and_deduplicate(lattice):
    with pytest.raises(ValueError, match="Finalize"):
        unit_cells(lattice)
    lattice.finalize()
    cells, _ = unit_cells(lattice)
    assert cells
    assert len({tuple(sorted([c["tm1_id"], c["tm2_id"]])) for c in cells}) == len(cells)
    assert len({(c["x"], c["y"]) for c in cells}) == len(cells)
    assert all(c["status"] == "pending" for c in cells)
    reverse, _ = unit_cells(lattice, orientation=-1)
    assert reverse


def test_emd_explicit_dataset_frame_and_metadata(tmp_path):
    path = tmp_path / "generic.emd"
    with h5py.File(path, "w") as f:
        f.attrs["Magnification"] = 3900000
        f.create_dataset("image/HAADF/Data", data=np.arange(24*30*2, dtype=np.uint16).reshape(24,30,2))
        f.create_dataset("image/BF/Data", data=np.zeros((24,30), np.uint16))
    info = inspect_emd(path)
    assert len(info["datasets"]) == 2
    assert info["attributes"]["/"]["Magnification"] == 3900000
    with pytest.raises(ValueError, match="Explicitly"):
        import_image(path)
    doc = import_image(path, "image/HAADF/Data", frame=1, frame_axis=2)
    assert doc.raw.shape == (24,30)
    assert doc.raw[0,0] == 1
    assert doc.metadata["magnification_status"] == "unknown"
