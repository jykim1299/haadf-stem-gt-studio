from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRunnable, QThreadPool, QTimer, Qt, Signal, QLockFile
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .model import Document, stamp, uid
from .science import (SCENARIOS, AtomapDetector, ScipyDetector, detected_points, estimate_lattice,
                      reconstruct, synthetic, unit_cells)
from .storage import external_path, export_dataset, import_image, inspect_emd, load_project, save_project
from .viewer import Camera, ImageCanvas, LAYER_COLORS

STYLE = """
QMainWindow,QWidget {background:#111a27;color:#dce6f2;font-family:'Segoe UI';font-size:12px;}
QLabel#Brand {font-size:18px;font-weight:700;color:#f0f6ff;}
QLabel#Section {color:#6f899f;font-size:11px;font-weight:700;}
QLabel#Stat {font-size:20px;font-weight:600;color:#52e3bd;}
QPushButton {background:#1d2b3d;border:1px solid #30435a;border-radius:5px;padding:7px 10px;}
QPushButton:hover {background:#2a4057;border-color:#5d9ca9;}
QPushButton:checked {background:#1d4b52;border-color:#4bdcc5;color:#a8ffdf;}
QPushButton#Primary {background:#1b695e;border-color:#37b59b;color:#eefffa;font-weight:600;}
QPushButton:disabled {color:#5c7083;background:#16212e;}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QPlainTextEdit,QListWidget,QTableWidget {
background:#0d1521;border:1px solid #2a3c51;border-radius:4px;padding:4px;selection-background-color:#285c70;}
QGroupBox {border:1px solid #2a3b50;border-radius:5px;margin-top:15px;padding:10px 5px 4px;}
QGroupBox::title {subcontrol-origin:margin;left:8px;color:#a7bfd3;}
QTabWidget::pane {border:1px solid #293b50;}
QTabBar::tab {padding:8px;background:#172335;color:#8fa8be;}
QTabBar::tab:selected {background:#24434c;color:#b5f9e5;}
QSplitter::handle {background:#263548;}
QProgressBar {border:0;background:#203044;height:5px;text-align:center;}
QProgressBar::chunk {background:#39cbae;}
QToolTip {color:#edf7ff;background:#26384b;border:1px solid #5b7892;}
"""


class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.done.emit(self.fn())
        except Exception:
            self.signals.error.emit(traceback.format_exc())


def button(text, fn, primary=False):
    w = QPushButton(text)
    w.setObjectName(text)
    w.setAccessibleName(text)
    if primary:
        w.setObjectName("Primary")
    w.clicked.connect(fn)
    return w


def label(text, name=None):
    w = QLabel(text)
    w.setWordWrap(True)
    if name:
        w.setObjectName(name)
    return w


def spin(low, high, value, decimals=0):
    w = QDoubleSpinBox() if decimals else QSpinBox()
    w.setRange(low, high)
    w.setValue(value)
    if decimals:
        w.setDecimals(decimals)
    return w


class Studio(QMainWindow):
    def __init__(self, workspace=None):
        super().__init__()
        self.root = external_path(workspace or Path.home() / "Documents" / "HAADF_GT_Studio")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(self.root / "studio.lock"))
        if not self.lock.tryLock(0):
            raise RuntimeError("This data workspace is already open in another Studio process.")
        self.project_lock = None
        self.doc = None
        self.path = None
        self.dirty = False
        self.busy = False
        self.selected = set()
        self.camera = Camera()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle("HAADF-STEM GT Studio")
        self.resize(1660, 980)
        self.setMinimumSize(1180, 760)
        self.build_ui()
        self.build_shortcuts()
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.autosave)
        self.timer.start()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(2500)
        self.debounce.timeout.connect(self.autosave)
        self.refresh_projects()
        self.refresh()

    def build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 8)
        top = QHBoxLayout()
        top.addWidget(label("HAADF / GT STUDIO", "Brand"))
        top.addWidget(label("LOCAL RESEARCH WORKSPACE", "Section"))
        top.addStretch()
        for text, fn in [("Import EMD / TIFF", self.import_dialog), ("Open project", self.open_dialog),
                         ("Save draft", self.save_dialog), ("Export dataset", self.export_dialog)]:
            top.addWidget(button(text, fn, text == "Save draft"))
        outer.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 8, 8, 0)
        lv.addWidget(label("DATASET", "Section"))
        self.scenario = QComboBox()
        self.scenario.addItems(SCENARIOS)
        self.scenario.setCurrentText("missing_tm")
        lv.addWidget(self.scenario)
        lv.addWidget(button("New synthetic • 896 px", self.new_synthetic))
        self.projects = QListWidget()
        self.projects.setMaximumHeight(105)
        self.projects.itemDoubleClicked.connect(lambda item: self.open_path(Path(item.data(Qt.UserRole))))
        lv.addWidget(self.projects)
        self.image_info = label("No image loaded")
        lv.addWidget(self.image_info)
        lv.addWidget(button("Image metadata", self.metadata_dialog))
        lv.addWidget(label("ANNOTATION TOOLS", "Section"))
        grid = QGridLayout()
        self.tool_buttons = {}
        tools = ["Select", "Add TM", "Move TM", "Delete", "Verify", "Reject", "Brush", "Rectangle",
                 "Lasso", "Polygon", "Layer Select", "Seed", "Pan"]
        for i, name in enumerate(tools):
            b = button(name, lambda checked=False, n=name: self.set_tool(n))
            b.setCheckable(True)
            b.setMinimumWidth(82)
            self.tool_buttons[name] = b
            grid.addWidget(b, i // 2, i % 2)
        lv.addLayout(grid)
        self.tool_buttons["Select"].setChecked(True)
        lv.addWidget(label("TM LAYERS", "Section"))
        self.layers = QListWidget()
        self.layers.setMinimumHeight(80)
        self.layers.itemClicked.connect(self.select_layer)
        lv.addWidget(self.layers, 1)
        lv.addWidget(button("Rename current layer", self.rename_layer))
        lv.addWidget(label("Shift: add selection · Ctrl: subtract\nWheel: zoom · Middle drag: pan\nPolygon: Enter / right-click to finish"))
        split.addWidget(left)
        middle = QWidget()
        mv = QVBoxLayout(middle)
        mv.setContentsMargins(6, 6, 6, 0)
        stats = QHBoxLayout()
        self.counts = label("—", "Stat")
        stats.addWidget(self.counts)
        stats.addStretch()
        for text, fn in [("Fit [F]", self.fit), ("100%", lambda: self.camera.set(scale=1)),
                         ("Undo", self.undo), ("Redo", self.redo), ("Fullscreen", self.fullscreen)]:
            stats.addWidget(button(text, fn))
        mv.addLayout(stats)
        self.target = label("Select a region to review points in one operation.")
        mv.addWidget(self.target)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.canvases = []
        for kind, subtitle in [("Raw", "RAW HAADF   /   uint16"), ("Processed", "PROCESSED   /   float32")]:
            frame = QWidget()
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.addWidget(label(subtitle, "Section"))
            canvas = ImageCanvas(self.camera, kind)
            canvas.selectionChanged.connect(self.selection_changed)
            canvas.editRequested.connect(self.edit)
            canvas.cursorChanged.connect(self.cursor_status)
            fl.addWidget(canvas, 1)
            row.addWidget(frame, 1)
            self.canvases.append(canvas)
        mv.addLayout(row, 1)
        bulk = QHBoxLayout()
        for text, fn in [("Verify TM [V]", self.verify), ("Reject [X]", self.reject),
                         ("Set non-TM", self.non_tm), ("Assign layer", self.assign_layer),
                         ("Clear selection", lambda: self.selection_changed(set()))]:
            bulk.addWidget(button(text, fn, text.startswith("Verify")))
        mv.addLayout(bulk)
        mv.addWidget(label("● TM verified     ● TM pending (amber)     ● Reconstruction (violet)     □ Cation site     ○ Selected (white)"))
        split.addWidget(middle)
        right = QTabWidget()
        right.setMinimumWidth(278)
        self.right = right
        selection, sv = self.tab(right, "Review")
        self.properties = label("No point selected")
        sv.addWidget(self.properties)
        form = QFormLayout()
        self.current_layer = spin(0, 9999, 1)
        self.current_region = spin(0, 9999, 0)
        form.addRow("Current layer", self.current_layer)
        form.addRow("Current region", self.current_region)
        self.current_region.valueChanged.connect(lambda: self.refresh_layers())
        sv.addLayout(form)
        self.next_layer = QCheckBox("Advance after layer assignment")
        sv.addWidget(self.next_layer)
        sv.addWidget(button("Mark selected as TM seeds", self.seed))
        sv.addWidget(button("Assign region to selection", self.assign_region))
        display = QGroupBox("Overlay & navigation")
        dv = QFormLayout(display)
        self.overlay = QCheckBox("Point overlays")
        self.overlay.setChecked(True)
        self.overlay.toggled.connect(lambda v: self.display_option("show_overlay", v))
        dv.addRow(self.overlay)
        self.line_toggle = QCheckBox("Connect TM layers")
        self.line_toggle.toggled.connect(lambda v: self.display_option("show_lines", v))
        dv.addRow(self.line_toggle)
        self.filter = QComboBox()
        self.filter.addItems(["All", "TM", "unknown", "non-TM", "Reconstruction"])
        self.filter.currentTextChanged.connect(lambda v: self.display_option("class_filter", v, True))
        dv.addRow("Class", self.filter)
        for text, attr, default in [("Unassigned only", "unassigned_only", False),
                                    ("Show rejected", "show_rejected", False), ("Color by layer", "color_by_layer", True)]:
            cb = QCheckBox(text)
            cb.setChecked(default)
            cb.toggled.connect(lambda value, a=attr: self.display_option(a, value, True))
            dv.addRow(cb)
        for text, attr, lo, hi, val in [("Marker size", "marker_size", 1, 12, 4),
                                       ("Brush radius", "brush_radius", 4, 100, 18),
                                       ("Opacity %", "opacity", 10, 100, 85)]:
            slider = QSlider(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(val)
            slider.valueChanged.connect(lambda value, a=attr: self.display_option(a, value / 100 if a == "opacity" else value))
            dv.addRow(text, slider)
        sv.addWidget(display)
        sv.addStretch()
        _, av = self.tab(right, "Analysis")
        av.addWidget(label("1 / DETECT & CLASSIFY", "Section"))
        self.detector = QComboBox()
        self.detector.addItems(["SciPy (validated synthetic)", "Atomap (optional adapter)"])
        av.addWidget(self.detector)
        self.separation = spin(2, 50, 6)
        self.threshold = spin(.01, .99, .1, 2)
        form = QFormLayout()
        form.addRow("Separation px", self.separation)
        form.addRow("Relative threshold", self.threshold)
        av.addLayout(form)
        av.addWidget(button("Detect additional candidates", self.detect))
        av.addWidget(label("Candidates start unknown. Select TM rows, verify, then assign layers. Seeds are explicit TM hints."))
        av.addWidget(label("2 / IMAGE-ADAPTIVE LATTICE", "Section"))
        av.addWidget(button("Estimate current region", self.estimate))
        self.analysis_info = label("Needs ≥4 trusted TM per assigned layer. Separate grains using regions.")
        av.addWidget(self.analysis_info)
        av.addWidget(button("Preview missing TM", self.reconstruction))
        av.addWidget(button("Select pending reconstruction", self.select_reconstruction))
        av.addWidget(label("Use Verify / Reject to review proposals. Reconstruction never becomes GT automatically."))
        av.addWidget(label("3 / FINAL & STRUCTURAL SITES", "Section"))
        av.addWidget(button("Finalize verified TM", self.finalize, True))
        self.final_info = label("No final version")
        av.addWidget(self.final_info)
        self.orientation = QComboBox()
        self.orientation.addItems(["Forward / next layer", "Reverse / previous layer"])
        self.shift = spin(-2., 2., 0., 3)
        self.tolerance = spin(.05, 1., .5, 2)
        self.fraction = spin(.05, .95, .5, 2)
        form = QFormLayout()
        form.addRow("Orientation", self.orientation)
        form.addRow("Shift / d_TM", self.shift)
        form.addRow("Partner tolerance", self.tolerance)
        form.addRow("Site fraction", self.fraction)
        av.addLayout(form)
        av.addWidget(button("Preview unit-cell pairs", self.preview_cells))
        av.addWidget(button("Review / approve pairs", self.review_cells))
        av.addWidget(label("Cation Site = structural measurement location. It does not assert cation mixing or an atomic peak."))
        av.addStretch()
        _, hv = self.tab(right, "History")
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        hv.addWidget(self.history)
        hv.addWidget(label("Every edit records coordinates before/after, IDs, UTC time and revision. Undo/redo survives project reload."))
        split.addWidget(right)
        split.setSizes([210, 1120, 300])
        split.setStretchFactor(1, 1)
        self.setCentralWidget(central)
        self.status = QLabel("Ready • all computation stays on this PC")
        self.statusBar().addWidget(self.status, 1)
        self.save_status = QLabel("No document")
        self.statusBar().addPermanentWidget(self.save_status)

    def tab(self, tabs, title):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        tabs.addTab(scroll, title)
        return content, layout

    def build_shortcuts(self):
        shortcuts = {"Ctrl+S": self.save_dialog, "Ctrl+O": self.open_dialog, "Ctrl+Z": self.undo,
                     "Ctrl+Y": self.redo, "Ctrl+Shift+Z": self.redo, "F": self.fit, "F11": self.fullscreen,
                     "V": self.verify, "X": self.reject, "Delete": self.delete_selected,
                     "Ctrl+A": self.select_all, "Ctrl+L": self.assign_layer}
        for key, tool in {"S": "Select", "A": "Add TM", "M": "Move TM", "B": "Brush", "R": "Rectangle",
                          "L": "Lasso", "P": "Polygon", "H": "Pan", "E": "Seed"}.items():
            shortcuts[key] = lambda checked=False, t=tool: self.set_tool(t)
        self.shortcuts = []
        for key, fn in shortcuts.items():
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(lambda checked=False, f=fn: self.shortcut(f))
            self.addAction(act)
            self.shortcuts.append(act)

    def shortcut(self, fn):
        if self.busy or isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit)):
            return
        fn()

    def task(self, description, fn, callback):
        if self.busy:
            return
        self.busy = True
        self.centralWidget().setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(description)
        worker = Worker(fn)
        def finished(result):
            self.task_end()
            try:
                callback(result)
            except Exception as exc:
                self.error(str(exc))
        worker.signals.done.connect(finished)
        worker.signals.error.connect(lambda message: (self.task_end(), self.error(message)))
        self.worker = worker
        self.pool.start(worker)

    def task_end(self):
        self.busy = False
        self.centralWidget().setEnabled(True)
        self.progress.hide()
        self.status.setText("Ready")

    def error(self, message):
        self.status.setText(message.splitlines()[-1])
        QMessageBox.warning(self, "Action could not be completed", message)

    def guarded(self, fn):
        if not self.doc or self.busy:
            return
        try:
            return fn()
        except Exception as exc:
            self.error(str(exc))

    def preserve(self):
        if self.doc and self.dirty:
            save_project(self.doc, self.root / (self.doc.image_id + ".autosave.hsgt"))
            self.dirty = False

    def attach_document(self, doc, path=None):
        new_lock = None
        if path:
            new_lock = QLockFile(str(path) + ".lock")
            if self.path != Path(path) and not new_lock.tryLock(0):
                raise RuntimeError("Project is open in another process.")
        if self.project_lock and self.path != path:
            self.project_lock.unlock()
        if new_lock and self.path != Path(path):
            self.project_lock = new_lock
        self.doc, self.path = doc, Path(path) if path else None
        self.dirty = not bool(path)
        self.save_status.setText("Loaded • " + Path(path).name if path else "New document • recovery autosave pending")
        self.selected = set()
        for canvas in self.canvases:
            canvas.selected = set()
            canvas.set_document(doc)
        self.fit()
        self.refresh()
        self.setWindowTitle(f"HAADF-STEM GT Studio — {doc.image_id}")
        if self.dirty:
            self.debounce.start()

    def new_synthetic(self):
        try:
            self.preserve()
        except Exception as exc:
            return self.error(str(exc))
        scenario = self.scenario.currentText()
        self.task("Generating synthetic HAADF and detecting candidates…", lambda: synthetic(scenario), self.attach_document)

    def refresh_projects(self):
        self.projects.clear()
        for path in sorted(self.root.glob("*.hsgt"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            self.projects.addItem(path.name)
            self.projects.item(self.projects.count()-1).setData(Qt.UserRole, str(path))

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project or recovery autosave", str(self.root), "Studio projects (*.hsgt)")
        if path:
            self.open_path(Path(path))

    def open_path(self, path):
        try:
            self.preserve()
        except Exception as exc:
            return self.error(str(exc))
        def load_with_recovery():
            doc = load_project(path)
            recovery = self.root / (doc.image_id + ".autosave.hsgt")
            if recovery != path and recovery.exists() and recovery.stat().st_mtime > Path(path).stat().st_mtime:
                recovered = load_project(recovery)
                if recovered.image_id == doc.image_id and recovered.state() != doc.state():
                    return recovered, True
            return doc, False
        def loaded(result):
            doc, recovered = result
            self.attach_document(doc, path)
            if recovered:
                self.dirty = True
                self.save_status.setText("Newer recovery restored • Save draft to update project")
                self.status.setText("Restored a newer autosave for this image; annotations and history recovered.")
        self.task("Loading project and checking recovery…", load_with_recovery, loaded)

    def save_dialog(self):
        if not self.doc:
            return
        path = self.path
        if not path or ".autosave." in str(path):
            filename, _ = QFileDialog.getSaveFileName(self, "Save draft outside source repository",
                                                      str(self.root / (self.doc.image_id + ".hsgt")), "Studio project (*.hsgt)")
            if not filename:
                return
            path = Path(filename).with_suffix(".hsgt")
        self.save_to(path)

    def save_to(self, path):
        try:
            path = external_path(path)
            if self.path != path:
                lock = QLockFile(str(path) + ".lock")
                if not lock.tryLock(0):
                    raise RuntimeError("Destination project is locked by another process.")
            else:
                lock = self.project_lock
        except Exception as exc:
            return self.error(str(exc))
        def saved(_):
            if self.project_lock and self.project_lock != lock:
                self.project_lock.unlock()
            self.project_lock, self.path = lock, path
            self.dirty = False
            self.save_status.setText("Draft saved • " + path.name)
            self.refresh_projects()
        self.task("Saving lossless project and edit history…", lambda: save_project(self.doc, path), saved)

    def autosave(self):
        if not self.doc or not self.dirty or self.busy:
            return
        path = self.root / (self.doc.image_id + ".autosave.hsgt")
        def saved(_):
            self.dirty = False
            self.save_status.setText("Recovery autosave • " + stamp()[11:19] + " UTC")
            self.refresh_projects()
        self.task("Autosaving recoverable draft…", lambda: save_project(self.doc, path), saved)

    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose HAADF source", str(self.root), "HAADF images (*.emd *.tif *.tiff)")
        if not path:
            return
        try:
            self.preserve()
        except Exception as exc:
            return self.error(str(exc))
        if Path(path).suffix.lower() == ".emd":
            self.task("Inspecting EMD HDF5 datasets…", lambda: inspect_emd(path), lambda info: self.choose_emd(path, info))
        else:
            self.task("Importing uint16 TIFF…", lambda: import_image(path), self.attach_document)

    def choose_emd(self, path, info):
        datasets = info["datasets"]
        if not datasets:
            return self.error("No 2D/3D uint16 dataset found. This vendor format needs a separate reader.")
        names = [d["path"] + "   " + str(d["shape"]) for d in datasets]
        choice, ok = QInputDialog.getItem(self, "Confirm HAADF channel", "Select a dataset only after confirming its channel identity:\n" + info["validation"], names, 0, False)
        if not ok:
            return
        ds = datasets[names.index(choice)]
        axis, frame = 2, 0
        if len(ds["shape"]) == 3:
            axis, ok = QInputDialog.getInt(self, "Frame axis", "Explicit frame axis (0, 1, or 2); consult vendor metadata", 2, 0, 2)
            if not ok:
                return
            frame, ok = QInputDialog.getInt(self, "Frame index", "Zero-based frame", 0, 0, ds["shape"][axis] - 1)
            if not ok:
                return
        self.task("Extracting selected HAADF frame…", lambda: import_image(path, ds["path"], frame, axis), self.attach_document)

    def metadata_dialog(self):
        if not self.doc:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Image metadata and target eligibility")
        dialog.resize(650, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Magnification remains unknown until explicitly verified against EMD metadata and filename. Synthetic data is not a 3.90 Mx acquisition."))
        form = QFormLayout()
        sample = QLineEdit(self.doc.metadata.get("sample", ""))
        mag = spin(0, 1e9, self.doc.metadata.get("magnification") or 0)
        calibration = spin(0, 1000, self.doc.metadata.get("calibration", {}).get("pixel_size") or 0, 6)
        unit = QComboBox()
        unit.addItems(["unknown", "nm", "pm", "angstrom", "px"])
        unit.setCurrentText(self.doc.metadata.get("calibration", {}).get("unit", "unknown"))
        verified = QCheckBox("I verified this magnification from source evidence")
        verified.setChecked(self.doc.metadata.get("magnification_status") == "user-verified")
        evidence = QLineEdit(self.doc.metadata.get("magnification_evidence", ""))
        form.addRow("Project / sample", sample)
        form.addRow("Magnification (×)", mag)
        form.addRow("Pixel size", calibration)
        form.addRow("Calibration unit", unit)
        form.addRow("Evidence", evidence)
        layout.addLayout(form)
        layout.addWidget(verified)
        raw = QPlainTextEdit(json.dumps({k: v for k, v in self.doc.metadata.items() if k != "truth"}, indent=2, ensure_ascii=False))
        raw.setReadOnly(True)
        layout.addWidget(raw)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            if verified.isChecked() and (not evidence.text().strip() or not mag.value() or self.doc.metadata.get("synthetic")):
                return self.error("Verified magnification requires non-synthetic source evidence and a positive value.")
            before = copy.deepcopy(self.doc.metadata)
            self.doc.metadata.update({"sample": sample.text(), "magnification": mag.value() or None,
                "magnification_status": "user-verified" if verified.isChecked() else "unknown",
                "magnification_evidence": evidence.text(),
                "calibration": {"pixel_size": calibration.value() or None, "unit": unit.currentText()}})
            self.doc._log("Update metadata", {"before": {k: v for k, v in before.items() if k != "truth"},
                                               "after": {k: v for k, v in self.doc.metadata.items() if k != "truth"}})
            self.changed()

    def fit(self):
        if self.canvases:
            self.canvases[0].fit()

    def fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def set_tool(self, name):
        for key, b in self.tool_buttons.items():
            b.setChecked(key == name)
        for canvas in self.canvases:
            canvas.tool, canvas.vertices = name, []
            canvas.dragging = False
            canvas.move_key, canvas.move_preview = None, None
            canvas.update()
        self.status.setText(name + " • Shift add / Ctrl subtract • Middle drag pan")

    def selection_changed(self, ids):
        self.selected = set(ids)
        for canvas in self.canvases:
            canvas.selected = set(ids)
            canvas.update()
        self.refresh_selection()

    def refresh_selection(self):
        n = len(self.selected)
        self.target.setText(f"{n:,} selected  ·  Verify / Reject / Label / Assign layer will affect {n:,} points")
        if not self.doc or not n:
            self.properties.setText("Select points or draw an ROI.\nBulk actions show their target count above the images.")
            return
        if n == 1:
            p = self.doc.points[next(iter(self.selected))]
            self.properties.setText(f"{p.point_id}\nx {p.x:.3f}  ·  y {p.y:.3f} px\n{p.label} / {p.review_status}\nLayer {p.layer_id} · Region {p.region_id}\nRaw {p.raw_intensity:.0f} · Processed {p.processed_intensity:.3f}\nSource: {p.source}\nOriginal: ({p.original_x:.3f}, {p.original_y:.3f})")
        else:
            self.properties.setText(f"{n:,} points selected\nLayer target: {self.current_layer.value()}\nRegion target: {self.current_region.value()}\nBulk changes can be undone as one step.")

    def cursor_status(self, x, y):
        self.status.setText(f"Image coordinates  x = {x:.2f}  y = {y:.2f} px  |  Shared zoom {self.camera.scale*100:.0f}%")

    def display_option(self, name, value, refresh=False):
        for canvas in self.canvases:
            setattr(canvas, name, value)
            canvas.refresh() if refresh else canvas.update()
        if refresh:
            visible_ids = {key for key, on in zip(self.canvases[0].ids, self.canvases[0].visible) if on}
            self.selection_changed(self.selected & visible_ids)

    def changed(self):
        self.dirty = True
        self.save_status.setText("Modified • recovery autosave pending")
        self.debounce.start()
        self.refresh()

    def refresh(self):
        if not self.doc:
            return
        self.selected.intersection_update({p.point_id for p in self.doc.points.values() if not p.deleted})
        for c in self.canvases:
            c.selected = self.selected.copy()
            c.refresh()
        live = [p for p in self.doc.points.values() if not p.deleted]
        self.counts.setText(f"{len(self.doc.verified()):,} verified TM")
        self.image_info.setText(f"{self.doc.metadata.get('sample') or self.doc.metadata.get('scenario', 'Imported image')}\n{self.doc.raw.shape[1]} × {self.doc.raw.shape[0]} px · {len(live):,} points\n" +
                                ("SYNTHETIC / validation only" if self.doc.metadata.get("synthetic") else "Magnification: " + self.doc.metadata.get("magnification_status", "unknown")))
        self.final_info.setText((f"Final v{self.doc.final['version']} • " + ("current" if self.doc.final_current else "STALE — finalize again")) if self.doc.final else "No final version")
        self.history.setPlainText("\n".join(f"r{h['revision']} · {h['time'][11:19]} · {h['action']}" for h in self.doc.history[-200:][::-1]))
        self.refresh_layers()
        self.refresh_selection()

    def refresh_layers(self):
        self.layers.clear()
        if not self.doc:
            return
        groups = {}
        for p in self.doc.points.values():
            if not p.deleted and p.layer_id is not None and p.region_id == self.current_region.value():
                groups[p.layer_id] = groups.get(p.layer_id, 0) + 1
        for layer, count in sorted(groups.items()):
            self.layers.addItem(f"Layer {layer:03d}   ·   {count} points")
            item = self.layers.item(self.layers.count()-1)
            item.setData(Qt.UserRole, layer)
            item.setForeground(QColor(LAYER_COLORS[layer % len(LAYER_COLORS)]))

    def select_layer(self, item):
        layer = item.data(Qt.UserRole)
        self.current_layer.setValue(layer)
        self.selection_changed({p.point_id for p in self.doc.points.values() if not p.deleted and
                               p.layer_id == layer and p.region_id == self.current_region.value()})

    def rename_layer(self):
        if not self.doc:
            return
        old = self.current_layer.value()
        new, ok = QInputDialog.getInt(self, "Rename layer", f"New ID for layer {old} in current region", old, 0, 9999)
        if ok and new != old:
            ids = [p.point_id for p in self.doc.points.values() if p.layer_id == old and p.region_id == self.current_region.value()]
            if self.doc.update(ids, f"Rename layer {old} to {new}", layer_id=new):
                self.current_layer.setValue(new)
                self.changed()

    def edit(self, action, values):
        def perform():
            if action == "add":
                x, y = values
                if self.canvases[0].tree is not None and self.canvases[0].tree.query([x, y])[0] < 1:
                    raise ValueError("A point already exists within one pixel. Select or move it instead.")
                p = self.doc.point(x, y, label="TM", layer_id=self.current_layer.value(), region_id=self.current_region.value())
                self.doc.commit("Add manual TM (pending)", {p.point_id: p})
                self.selected = {p.point_id}
            elif action == "move":
                self.doc.move(*values)
            else:
                fields = {"verify": {"label": "TM", "review_status": "verified"},
                          "reject": {"review_status": "rejected", "seed": False},
                          "delete": {"deleted": True, "seed": False},
                          "seed": {"label": "TM", "seed": True, "review_status": "pending"}}[action]
                self.doc.update(values, f"{action.title()} {len(values)} points", **fields)
            self.changed()
        self.guarded(perform)

    def bulk(self, description, **fields):
        if self.doc and not self.busy and self.selected:
            if self.doc.update(self.selected, f"{description}: {len(self.selected)} points", **fields):
                self.changed()

    def verify(self):
        self.bulk("Verify TM", label="TM", review_status="verified")

    def reject(self):
        self.bulk("Reject", review_status="rejected", seed=False)

    def non_tm(self):
        self.bulk("Label non-TM", label="non-TM", review_status="verified", seed=False, layer_id=None)

    def delete_selected(self):
        self.bulk("Delete", deleted=True, seed=False)

    def seed(self):
        self.bulk("Mark TM seeds", label="TM", seed=True, review_status="pending")

    def assign_layer(self):
        self.bulk("Assign layer", layer_id=self.current_layer.value(), region_id=self.current_region.value())
        if self.selected and self.next_layer.isChecked():
            self.current_layer.stepUp()

    def assign_region(self):
        self.bulk("Assign region", region_id=self.current_region.value())

    def select_all(self):
        c = self.canvases[0]
        self.selection_changed({key for key, on in zip(c.ids, c.visible) if on})

    def undo(self):
        if self.doc and not self.busy and self.doc.undo():
            self.changed()

    def redo(self):
        if self.doc and not self.busy and self.doc.redo():
            self.changed()

    def detect(self):
        if not self.doc:
            return
        detector = ScipyDetector() if self.detector.currentIndex() == 0 else AtomapDetector()
        sep, threshold = self.separation.value(), self.threshold.value()
        def work():
            pts = detected_points(self.doc, detector, sep, threshold)
            existing = [p for p in self.doc.points.values() if not p.deleted]
            if existing:
                from scipy.spatial import cKDTree
                tree = cKDTree([[p.x, p.y] for p in existing])
                pts = [p for p in pts if tree.query([p.x, p.y])[0] >= sep / 2]
            return pts
        def done(points):
            self.doc.commit(f"Detect {len(points)} additional candidates", {p.point_id: p for p in points})
            self.doc.candidates.extend(asdict(p) for p in points)
            self.doc.metadata["last_detection"] = {"adapter": detector.name, "separation": sep, "threshold_rel": threshold}
            self.changed()
            self.status.setText(f"Added {len(points)} unknown candidates; existing annotations preserved")
        self.task("Detecting candidates…", work, done)

    def estimate(self):
        if not self.doc:
            return
        region = self.current_region.value()
        def done(est):
            self.doc.analysis = est
            self.analysis_info.setText(f"d_TM = {est['d_tm']:.3f} px\nd_layer = {str(round(est['d_layer'], 3)) if est['d_layer'] else 'needs 2 layers'} px\nθ = {est['theta_deg']:.2f}°\n{len(est['layers'])} layers · region {region}\n" + est["warning"])
            self.changed()
        self.task("Estimating image-specific lattice…", lambda: estimate_lattice(self.doc, region), done)

    def reconstruction(self):
        if not self.doc:
            return
        region = self.current_region.value()
        def work():
            est = estimate_lattice(self.doc, region)
            return est, reconstruct(self.doc, est)
        def done(result):
            est, points = result
            self.doc.analysis = est
            self.doc.commit(f"Propose {len(points)} missing TM", {p.point_id: p for p in points})
            self.selection_changed({p.point_id for p in points})
            self.changed()
            self.status.setText(f"{len(points)} reconstruction proposals. Review, then Verify / Reject.")
        self.task("Reconstructing missing TM proposals…", work, done)

    def select_reconstruction(self):
        if self.doc:
            self.selection_changed({p.point_id for p in self.doc.points.values() if p.source == "reconstruction" and p.review_status == "pending" and not p.deleted})

    def finalize(self):
        def perform():
            self.doc.finalize()
            self.changed()
            self.status.setText(f"Final v{self.doc.final['version']}: {len(self.doc.final['points'])} human-verified TM")
        self.guarded(perform)

    def preview_cells(self):
        if not self.doc:
            return
        config = {"region_id": self.current_region.value(), "orientation": 1 if self.orientation.currentIndex() == 0 else -1,
                  "shift_ratio": self.shift.value(), "partner_tolerance": self.tolerance.value(), "site_fraction": self.fraction.value()}
        def done(result):
            cells, est = result
            self.doc.commit_cells("Preview unit cells", cells=cells, cell_config=config | {"estimate": est}, cells_approved=False)
            self.changed()
            self.status.setText(f"{len(self.doc.cells)} pending endpoint pairs. Open Review / approve pairs.")
        self.task("Generating structural endpoint pairs…", lambda: unit_cells(self.doc, **config), done)

    def review_cells(self):
        if not self.doc or not self.doc.cells or not self.doc.cells_current:
            return self.error("Preview unit cells from current Final TM first.")
        dlg = QDialog(self)
        dlg.setWindowTitle("Review structural pairs • not cation-mixing labels")
        dlg.resize(670, 540)
        dlg.move(self.mapToGlobal(QPoint(max(0, self.width()-690), 130)))
        layout = QVBoxLayout(dlg)
        layout.addWidget(label("Click a row to highlight its endpoints in the viewer. Exclude wrong pairs. Boundary pairs require separate explicit approval. Pending pairs never produce Cation Site GT."))
        table = QTableWidget(len(self.doc.cells), 6)
        table.setHorizontalHeaderLabels(["Review", "TM1", "TM2", "Site x", "Site y", "Boundary"])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, cell in enumerate(self.doc.cells):
            for col, value in enumerate([cell["status"], cell["tm1_id"], cell["tm2_id"], f"{cell['x']:.3f}", f"{cell['y']:.3f}", str(cell["boundary"]) ]):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.cellClicked.connect(lambda row, col: self.focus_pair(row))
        layout.addWidget(table)
        controls = QHBoxLayout()
        def change(status, all_interior=False):
            rows = range(table.rowCount()) if all_interior else {i.row() for i in table.selectedIndexes()}
            for row in rows:
                if all_interior and self.doc.cells[row]["boundary"]:
                    continue
                table.item(row, 0).setText(status)
        controls.addWidget(button("Approve all interior", lambda: change("approved", True)))
        controls.addWidget(button("Approve selected (incl. boundary)", lambda: change("approved")))
        controls.addWidget(button("Reject selected", lambda: change("rejected")))
        layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted:
            cells = copy.deepcopy(self.doc.cells)
            for row, cell in enumerate(cells):
                cell["status"] = table.item(row, 0).text()
            self.doc.commit_cells("Review structural pairs", cells=cells,
                                  cells_approved=any(c["status"] == "approved" for c in cells))
            self.changed()

    def focus_pair(self, row):
        c = self.doc.cells[row]
        self.selection_changed({c["tm1_id"], c["tm2_id"]})
        self.camera.set(c["x"], c["y"], max(3, self.camera.scale))

    def export_dialog(self):
        if not self.doc:
            return
        if not self.doc.final_current:
            return self.error("Finalize current verified TM before exporting the dataset.")
        folder = QFileDialog.getExistingDirectory(self, "Export to a data folder outside source code", str(self.root))
        if folder:
            self.task("Exporting immutable dataset revision…", lambda: export_dataset(self.doc, folder),
                      lambda path: self.status.setText("Export complete: " + str(path)))

    def closeEvent(self, event):
        if self.busy:
            self.status.setText("Please wait for the current operation before closing.")
            event.ignore()
            return
        try:
            self.preserve()
        except Exception as exc:
            self.error("Recovery save failed; the window will remain open. " + str(exc))
            event.ignore()
            return
        if self.project_lock:
            self.project_lock.unlock()
        self.lock.unlock()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="HAADF-STEM GT Studio (local desktop)")
    parser.add_argument("--workspace", type=Path, help="Data workspace outside the source repository")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--synthetic", choices=SCENARIOS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    try:
        window = Studio(args.workspace)
    except Exception as exc:
        QMessageBox.critical(None, "Cannot open workspace", str(exc))
        return 1
    window.showMaximized()
    if args.project:
        QTimer.singleShot(0, lambda: window.open_path(args.project))
    elif args.synthetic:
        window.scenario.setCurrentText(args.synthetic)
        QTimer.singleShot(0, window.new_synthetic)
    result = app.exec()
    return result


if __name__ == "__main__":
    sys.exit(main())
