"""Synthetic oracle drives real Qt ROI interactions; never a real-data accuracy claim."""
import argparse
import csv
import json
from pathlib import Path
import time

import numpy as np
from scipy.spatial import cKDTree
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from haadf_studio.app import Studio, STYLE
from haadf_studio.science import synthetic
from haadf_studio.storage import save_project, load_project, export_dataset


def wait(app, window):
    start = time.monotonic()
    while window.busy:
        app.processEvents()
        if time.monotonic() - start > 60:
            raise RuntimeError("Background task did not finish")
        QTest.qWait(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    w = Studio(args.output / "workspace")
    w.timer.stop()
    w.debounce.setInterval(600000)
    w.show()
    app.processEvents()
    doc = synthetic("missing_tm",size=896)
    w.attach_document(doc)
    app.processEvents()
    canvas = w.canvases[0]
    truth = doc.metadata["truth"]["tm"]
    tree = cKDTree([[p["x"],p["y"]] for p in truth])
    rows = {}
    for p in truth:
        rows.setdefault(p["layer_id"],[]).append([p["x"],p["y"]])
    start = time.perf_counter()
    roi_count = 0
    # Polygon per row selects hundreds of candidates in a few gestures.
    # The truth oracle substitutes for a researcher rejecting false peaks in this automated test.
    for layer, coords in sorted(rows.items()):
        xy = np.array(coords)
        if len(xy) < 4:
            continue
        center = xy.mean(axis=0)
        _, _, vt = np.linalg.svd(xy-center,full_matrices=False)
        u = vt[0]
        normal = np.array([-u[1],u[0]])
        s = (xy-center)@u
        a,b = center+(s.min()-3)*u, center+(s.max()+3)*u
        verts = [a-2.8*normal,b-2.8*normal,b+2.8*normal,a+2.8*normal]
        w.set_tool("Polygon")
        for vertex in verts:
            QTest.mouseClick(canvas,Qt.LeftButton,pos=canvas.to_screen(vertex).toPoint())
        QTest.keyClick(canvas,Qt.Key_Return)
        selected = w.selected.copy()
        incorrect = {key for key in selected if tree.query([doc.points[key].x,doc.points[key].y])[0] > 2.2}
        if incorrect:
            w.selection_changed(incorrect)
            w.reject()
        w.selection_changed(selected-incorrect)
        w.verify()
        w.current_layer.setValue(layer)
        w.assign_layer()
        roi_count += 1
    # Reject unreviewed detections via a bulk selection, like a final cleanup pass.
    w.selection_changed({p.point_id for p in doc.points.values() if p.review_status == "pending"})
    w.reject()
    verified_before = len(doc.verified())
    assert verified_before > 2500, verified_before
    w.reconstruction()
    wait(app,w)
    reconstruction_count = len(w.selected)
    assert reconstruction_count > 50, reconstruction_count
    w.verify()
    w.finalize()
    w.preview_cells()
    wait(app,w)
    assert len(doc.cells) > 2000, len(doc.cells)
    # Real review dialog controls also covered by test_gui.py; explicit model command here.
    import copy
    reviewed = copy.deepcopy(doc.cells)
    for c in reviewed:
        c["status"] = "approved" if not c["boundary"] else "rejected"
    doc.commit_cells("Synthetic oracle approves interior pairs", cells=reviewed, cells_approved=True)
    project = args.output / "workflow.hsgt"
    save_project(doc,project)
    restored = load_project(project)
    assert restored.state() == doc.state()
    exported = export_dataset(restored,args.output / "exports")
    w.selection_changed(set())
    w.line_toggle.setChecked(True)
    w.camera.set(448,448,1.9)
    app.processEvents()
    w.grab().save(str(args.output / "workflow-verified.png"))
    result = {"scenario":"missing_tm", "seed":42, "candidate_count":len(doc.candidates),
              "roi_gestures":roi_count, "verified_detected_tm":verified_before,
              "reconstruction_proposals":reconstruction_count, "final_tm":len(doc.final["points"]),
              "unit_cells":len(doc.cells), "approved_cation_sites":sum(c["status"]=="approved" for c in doc.cells),
              "save_reload_exact":True, "elapsed_seconds":round(time.perf_counter()-start,2),
              "export_path":str(exported),
              "caveat":"Ground-truth oracle simulates human review. Synthetic workflow correctness only, not detector/reconstruction accuracy on real material."}
    (args.output / "workflow-results.json").write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    w.dirty = False
    w.close()


if __name__ == "__main__":
    main()
