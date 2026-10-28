"""Repeatable local rendering/selection/edit timing. Not an input-latency benchmark."""
import argparse
import json
import platform
from pathlib import Path
import tempfile
import time

import numpy as np
from PySide6.QtWidgets import QApplication

from haadf_studio.app import Studio
from haadf_studio.model import Document
from haadf_studio.science import synthetic
from haadf_studio.viewer import polygon_mask

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True,type=Path)
args = parser.parse_args()
app = QApplication([])
results = []
for count in [4110,20000]:
    w = Studio(tempfile.mkdtemp(prefix="haadf-benchmark-"))
    w.timer.stop()
    w.debounce.stop()
    w.show()
    app.processEvents()
    doc = synthetic() if count == 4110 else Document("stress-20000", np.zeros((1600,2000),np.uint16),np.zeros((1600,2000),np.float32))
    if count == 20000:
        for y in range(8,1600,16):
            for x in range(5,2000,10):
                p = doc.point(x,y)
                doc.points[p.point_id] = p
    w.attach_document(doc)
    app.processEvents()
    for c in w.canvases:
        c.frames.clear()
    for i in range(60):
        w.camera.set(doc.raw.shape[1]/2+i,doc.raw.shape[0]/2, .6+i*.006)
        for c in w.canvases:
            c.repaint()
        app.processEvents()
    frames = w.canvases[0].frames+w.canvases[1].frames
    t = time.perf_counter()
    polygon_mask(w.canvases[0].xy,[[0,0],[1000,0],[1000,800],[0,800]])
    roi_ms = (time.perf_counter()-t)*1000
    w.select_all()
    t = time.perf_counter()
    w.verify()
    bulk_ms = (time.perf_counter()-t)*1000
    t = time.perf_counter()
    w.undo()
    undo_ms = (time.perf_counter()-t)*1000
    results.append({"points":len(doc.points), "per_pane_paint_median_ms":round(float(np.median(frames)),2),
                    "per_pane_paint_p95_ms":round(float(np.percentile(frames,95)),2),
                    "polygon_mask_ms":round(roi_ms,3), "bulk_verify_ms":round(bulk_ms,2),"bulk_undo_ms":round(undo_ms,2)})
    w.dirty = False
    w.close()
report = {"system":platform.platform(),"python":platform.python_version(),"results":results,
          "scope":"Local synthetic paint/mutation timing, not input-to-photon latency or a universal FPS guarantee. 20k selected-point edits exceed a frame budget."}
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
