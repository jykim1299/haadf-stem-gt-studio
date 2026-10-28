import copy
import os

from haadf_studio.app import Studio
from haadf_studio.science import estimate_lattice, reconstruct, unit_cells
from haadf_studio.storage import load_project, save_project


def test_rejected_reconstruction_does_not_reappear(lattice):
    pts = reconstruct(lattice, estimate_lattice(lattice))
    lattice.commit("propose", {p.point_id: p for p in pts})
    lattice.update([p.point_id for p in pts], "reject", review_status="rejected")
    assert reconstruct(lattice, estimate_lattice(lattice)) == []


def test_cell_approval_undo_and_final_version(lattice):
    lattice.finalize()
    cells, est = unit_cells(lattice)
    lattice.commit_cells("Preview", cells=cells, cells_approved=False)
    approved = copy.deepcopy(cells)
    for c in approved:
        c["status"] = "approved"
    lattice.commit_cells("Approve", cells=approved, cells_approved=True)
    assert lattice.cells_current and lattice.cells_approved
    lattice.undo()
    assert not lattice.cells_approved and all(c["status"] == "pending" for c in lattice.cells)
    assert lattice.final_current
    lattice.redo()
    assert lattice.cells_approved
    lattice.finalize()
    assert lattice.cells == []
    lattice.undo()
    assert not lattice.cells_current


def test_newer_recovery_wins(qtbot, lattice, tmp_path):
    project = tmp_path / "named.hsgt"
    save_project(lattice, project)
    before = project.stat().st_mtime
    lattice.update(lattice.points, "Bulk reject", review_status="rejected")
    recovery = tmp_path / (lattice.image_id + ".autosave.hsgt")
    save_project(lattice, recovery)
    os.utime(recovery, (before+2, before+2))
    w = Studio(tmp_path)
    qtbot.addWidget(w)
    w.open_path(project)
    qtbot.waitUntil(lambda: not w.busy)
    assert len(w.doc.verified()) == 0
    assert "recovery" in w.save_status.text().lower()
    assert w.doc.undo()
    assert len(w.doc.verified()) == len(lattice.points)
    w.close()


def test_unit_cell_estimate_excludes_unverified_seeds(lattice):
    lattice.finalize()
    baseline, est = unit_cells(lattice)
    p = lattice.point(80, 46, label="TM", layer_id=1, seed=True)
    lattice.points[p.point_id] = p
    cells, new_est = unit_cells(lattice)
    assert est["theta_deg"] == new_est["theta_deg"]
    assert len(cells) == len(baseline)
