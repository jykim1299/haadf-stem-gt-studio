"""Real Qt widgets and input events; no mocked annotation handlers."""
from dataclasses import asdict
import json
import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from haadf_studio.app import Studio, STYLE
from haadf_studio.storage import load_project, save_project, export_dataset
from haadf_studio.science import synthetic


@pytest.fixture
def studio(qtbot, lattice, tmp_path):
    w = Studio(tmp_path)
    w.timer.stop()
    w.debounce.setInterval(600000)
    qtbot.addWidget(w)
    w.resize(1660, 980)
    w.show()
    QApplication.processEvents()
    w.attach_document(lattice)
    QApplication.processEvents()
    yield w
    qtbot.waitUntil(lambda: not w.busy, timeout=10000)
    w.close()


def pos(canvas, x, y):
    return canvas.to_screen((x, y)).toPoint()


def stroke(qtbot, canvas, points, modifier=Qt.NoModifier):
    qtbot.mousePress(canvas, Qt.LeftButton, modifier, pos=pos(canvas, *points[0]))
    for xy in points[1:]:
        qtbot.mouseMove(canvas, pos(canvas, *xy), delay=5)
    qtbot.mouseRelease(canvas, Qt.LeftButton, modifier, pos=pos(canvas, *points[-1]))


def test_viewer_alignment_zoom_pan(studio, qtbot):
    w = studio
    a, b = w.canvases
    xy = np.array([100., 60.])
    assert np.allclose(a.to_image(a.to_screen(xy)), xy)
    assert np.allclose(b.to_image(b.to_screen(xy)), xy)
    anchor = a.to_screen(xy)
    ev = QWheelEvent(anchor, a.mapToGlobal(anchor.toPoint()), QPoint(0,0), QPoint(0,120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(a, ev)
    assert np.allclose(a.to_image(anchor), xy)
    assert a.camera is b.camera
    assert np.allclose(b.to_image(b.to_screen(xy)), xy)
    old = (w.camera.cx, w.camera.cy)
    w.set_tool("Pan")
    stroke(qtbot, a, [(100,60),(110,65)])
    assert old != (w.camera.cx,w.camera.cy)


def test_manual_add_move_delete_undo(studio, qtbot):
    w, c = studio, studio.canvases[0]
    w.set_tool("Add TM")
    qtbot.mouseClick(c, Qt.LeftButton, pos=pos(c, 100,110))
    key = next(iter(w.selected))
    assert w.doc.points[key].source == "manual"
    assert w.doc.points[key].review_status == "pending"
    w.verify()
    w.set_tool("Move TM")
    stroke(qtbot, c, [(100,110),(107,115)])
    assert w.doc.points[key].x == pytest.approx(107, abs=1)
    assert w.doc.points[key].review_status == "pending"
    w.delete_selected()
    assert w.doc.points[key].deleted
    w.undo()
    assert not w.doc.points[key].deleted
    w.undo()
    assert w.doc.points[key].x == pytest.approx(100,abs=1)
    assert w.doc.points[key].review_status == "verified"
    w.redo()
    assert w.doc.points[key].x == pytest.approx(107,abs=1)


@pytest.mark.parametrize("tool", ["Rectangle", "Lasso", "Brush", "Polygon"])
def test_roi_bulk_layer_and_undo(studio, qtbot, tool):
    w, c = studio, studio.canvases[0]
    w.set_tool(tool)
    if tool == "Rectangle":
        stroke(qtbot,c,[(15,35),(65,45)])
    elif tool == "Lasso":
        stroke(qtbot,c,[(15,35),(65,35),(65,45),(15,45),(15,35)])
    elif tool == "Brush":
        c.brush_radius = 7 * c.camera.scale
        stroke(qtbot,c,[(20,40),(60,40)])
    else:
        for xy in [(15,35),(65,35),(65,45),(15,45)]:
            qtbot.mouseClick(c,Qt.LeftButton,pos=pos(c,*xy))
        qtbot.keyClick(c, Qt.Key_Return)
    assert len(w.selected) == 5
    ids = w.selected.copy()
    w.reject()
    assert all(w.doc.points[k].review_status == "rejected" for k in ids)
    w.undo()
    assert all(w.doc.points[k].review_status == "verified" for k in ids)
    w.current_layer.setValue(99)
    w.assign_layer()
    assert all(w.doc.points[k].layer_id == 99 for k in ids)
    w.undo()
    assert all(w.doc.points[k].layer_id == 1 for k in ids)


def test_reconstruction_final_cells_workflow(studio, qtbot, tmp_path):
    w = studio
    w.reconstruction()
    qtbot.waitUntil(lambda: not w.busy, timeout=10000)
    assert len(w.selected) == 2
    ids = w.selected.copy()
    w.reject()
    w.undo()
    w.selection_changed(ids)
    w.verify()
    w.finalize()
    assert w.doc.final_current
    w.preview_cells()
    qtbot.waitUntil(lambda: not w.busy, timeout=10000)
    assert w.doc.cells
    # Pair review UI uses a modal; verify its real controls via QTimer.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialogButtonBox, QPushButton
    def approve():
        dialog = QApplication.activeModalWidget()
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Approve all interior").click()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    QTimer.singleShot(100, approve)
    w.review_cells()
    assert w.doc.cells_approved
    w.save_to(tmp_path / "workflow.hsgt")
    qtbot.waitUntil(lambda: not w.busy, timeout=10000)
    restored = load_project(tmp_path / "workflow.hsgt")
    assert restored.state() == w.doc.state()
    out = export_dataset(restored, tmp_path / "export")
    assert (out / "images" / restored.image_id / "cation_sites.csv").exists()


def test_thousands_bulk_and_recovery(qtbot, tmp_path):
    app = QApplication.instance()
    app.setStyleSheet(STYLE)
    w = Studio(tmp_path)
    qtbot.addWidget(w)
    w.timer.stop()
    w.debounce.setInterval(600000)
    w.showMaximized()
    QApplication.processEvents()
    doc = synthetic("missing_tm", size=896)
    w.attach_document(doc)
    assert len(doc.points) > 2500
    w.select_all()
    selected = len(w.selected)
    start = time.perf_counter()
    w.verify()
    bulk_ms = (time.perf_counter()-start)*1000
    assert len(doc.verified()) == selected
    w.undo()
    assert len(doc.verified()) == 0
    w.redo()
    assert len(doc.verified()) == selected
    w.selection_changed(set())
    for i in range(20):
        w.camera.set(448+i,448+i, .6+i*.01)
        w.canvases[0].repaint()
        w.canvases[1].repaint()
        QApplication.processEvents()
    times = w.canvases[0].frames + w.canvases[1].frames
    metrics = {"candidate_count": selected, "bulk_verify_ms": round(bulk_ms,2),
               "paint_median_ms": round(float(np.median(times)),2), "paint_p95_ms": round(float(np.percentile(times,95)),2),
               "scope": "Qt paint duration, not input-to-photon latency; synthetic local machine only"}
    print("PERFORMANCE="+json.dumps(metrics))
    (tmp_path / "performance.json").write_text(json.dumps(metrics,indent=2))
    w.finalize()
    w.autosave()
    qtbot.waitUntil(lambda: not w.busy, timeout=20000)
    state = doc.state()
    w.close()
    reopened = Studio(tmp_path)
    qtbot.addWidget(reopened)
    reopened.open_path(tmp_path / (doc.image_id+".autosave.hsgt"))
    qtbot.waitUntil(lambda: not reopened.busy, timeout=20000)
    assert reopened.doc.state() == state
    assert len(reopened.doc.verified()) == selected
    reopened.close()
