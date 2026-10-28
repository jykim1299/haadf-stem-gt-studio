from __future__ import annotations

from collections import defaultdict
import time

import numpy as np
from scipy.spatial import cKDTree
from PySide6.QtCore import QObject, QPointF, QRectF, QLineF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .storage import display_uint8

COLORS = {"unknown": "#8eabc0", "TM": "#39e7b0", "non-TM": "#fa8e9b",
          "reconstruction": "#c391ff", "rejected": "#e26876", "pending": "#f5bd63"}
LAYER_COLORS = ["#52d6ea", "#efb85a", "#bc93ff", "#ff8ca2", "#7ce1ad", "#7fa8ff", "#f5d96b"]


def polygon_mask(xy, vertices):
    if len(vertices) < 3 or not len(xy):
        return np.zeros(len(xy), bool)
    x, y = xy.T
    inside = np.zeros(len(xy), bool)
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if abs(b[1] - a[1]) < 1e-12:
            continue
        crossing = ((a[1] > y) != (b[1] > y)) & (x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0])
        inside ^= crossing
    return inside


class Camera(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.cx, self.cy, self.scale = 0., 0., 1.

    def set(self, cx=None, cy=None, scale=None):
        if cx is not None:
            self.cx = float(cx)
        if cy is not None:
            self.cy = float(cy)
        if scale is not None:
            self.scale = float(max(.025, min(64., scale)))
        self.changed.emit()


class ImageCanvas(QWidget):
    selectionChanged = Signal(object)
    editRequested = Signal(str, object)
    cursorChanged = Signal(float, float)

    def __init__(self, camera, kind, parent=None):
        super().__init__(parent)
        self.camera, self.kind = camera, kind
        self.setObjectName(kind + "Canvas")
        self.setMinimumSize(260, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.camera.changed.connect(self.update)
        self.doc = None
        self.image = None
        self.tool = "Select"
        self.selected = set()
        self.ids, self.xy = [], np.empty((0, 2))
        self.tree = None
        self.visible = np.empty(0, bool)
        self.groups = {}
        self.lines = []
        self.cell_lines, self.site_points = {}, {}
        self.show_overlay, self.show_lines = True, False
        self.marker_size, self.opacity = 4., .85
        self.class_filter = "All"
        self.unassigned_only = False
        self.show_rejected = False
        self.color_by_layer = True
        self.brush_radius = 18.
        self.hover = None
        self.dragging = False
        self.start, self.last, self.cursor = None, None, None
        self.vertices = []
        self.base_selection = set()
        self.subtract = False
        self.additive = False
        self.move_key = None
        self.move_preview = None
        self.frames = []

    def set_document(self, doc):
        self.doc = doc
        values = display_uint8(doc.raw if self.kind == "Raw" else doc.processed)
        self.image = QImage(values.data, values.shape[1], values.shape[0], values.strides[0], QImage.Format_Grayscale8).copy()
        self.refresh()

    def refresh(self):
        if not self.doc:
            return
        points = [p for p in self.doc.points.values() if not p.deleted]
        self.ids = [p.point_id for p in points]
        self.xy = np.array([[p.x, p.y] for p in points], dtype=float).reshape(-1, 2)
        self.tree = cKDTree(self.xy) if len(points) else None
        self.visible = np.array([(self.class_filter == "All" or p.label == self.class_filter or
                                  self.class_filter == "Reconstruction" and p.source == "reconstruction") and
                                 (not self.unassigned_only or p.layer_id is None) and
                                 (self.show_rejected or p.review_status != "rejected") for p in points], dtype=bool)
        groups, layers = defaultdict(list), defaultdict(list)
        for p, visible in zip(points, self.visible):
            if not visible:
                continue
            if p.review_status == "rejected":
                color = COLORS["rejected"]
            elif p.source == "reconstruction" and p.review_status == "pending":
                color = COLORS["reconstruction"]
            elif self.color_by_layer and p.layer_id is not None:
                color = LAYER_COLORS[p.layer_id % len(LAYER_COLORS)]
            elif p.label == "TM" and p.review_status == "pending":
                color = COLORS["pending"]
            else:
                color = COLORS[p.label]
            groups[color].append(QPointF(p.x, p.y))
            if p.layer_id is not None and p.label == "TM" and p.review_status != "rejected":
                layers[(p.region_id, p.layer_id)].append([p.x, p.y])
        self.groups = {k: QPolygonF(v) for k, v in groups.items()}
        self.lines = []
        for (_, layer_id), coords in layers.items():
            xy = np.array(coords)
            if len(xy) < 2:
                continue
            _, _, vt = np.linalg.svd(xy - xy.mean(axis=0), full_matrices=False)
            xy = xy[np.argsort(xy @ vt[0])]
            self.lines.append((LAYER_COLORS[layer_id % len(LAYER_COLORS)], QPolygonF([QPointF(*q) for q in xy])))
        cells, sites = defaultdict(list), defaultdict(list)
        if self.doc.cells_current:
            for c in self.doc.cells:
                if c["status"] == "rejected":
                    continue
                a, b = self.doc.points.get(c["tm1_id"]), self.doc.points.get(c["tm2_id"])
                if a is None or b is None:
                    continue
                color = "#bc93ff" if c["status"] == "approved" else "#f5bd63"
                cells[color].append(QLineF(a.x, a.y, b.x, b.y))
                sites[color].append(QPointF(c["x"], c["y"]))
        self.cell_lines = dict(cells)
        self.site_points = {color: QPolygonF(points) for color, points in sites.items()}
        self.selected.intersection_update(self.ids)
        self.update()

    def to_image(self, pos):
        return np.array([(pos.x() - self.width() / 2) / self.camera.scale + self.camera.cx,
                         (pos.y() - self.height() / 2) / self.camera.scale + self.camera.cy])

    def to_screen(self, xy):
        return QPointF((xy[0] - self.camera.cx) * self.camera.scale + self.width() / 2,
                       (xy[1] - self.camera.cy) * self.camera.scale + self.height() / 2)

    def fit(self):
        if self.doc:
            h, w = self.doc.raw.shape
            self.camera.set((w - 1) / 2, (h - 1) / 2, min((self.width() - 28) / w, (self.height() - 28) / h))

    def pick(self, xy):
        if self.tree is None:
            return None
        indices = self.tree.query_ball_point(xy, max(3., 9. / self.camera.scale))
        indices = [i for i in indices if self.visible[i]]
        if not indices:
            return None
        i = min(indices, key=lambda i: np.sum((self.xy[i] - xy) ** 2))
        return self.ids[i]

    def mini_rect(self):
        if not self.doc:
            return QRectF()
        h, w = self.doc.raw.shape
        return QRectF(self.width() - 124, 14, 108, 108 * h / w)

    def paintEvent(self, event):
        t0 = time.perf_counter()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080e16"))
        if not self.doc or not self.image:
            painter.setPen(QColor("#708699"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Open a uint16 image or create a synthetic dataset")
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self.camera.scale < 1)
        painter.save()
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(self.camera.scale, self.camera.scale)
        painter.translate(-self.camera.cx, -self.camera.cy)
        h, w = self.doc.raw.shape
        painter.drawImage(QRectF(-.5, -.5, w, h), self.image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self.show_overlay:
            painter.setOpacity(self.opacity)
            for color, points in self.groups.items():
                pen = QPen(QColor(color), self.marker_size)
                pen.setCosmetic(True)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawPoints(points)
            if self.show_lines:
                for color, points in self.lines:
                    pen = QPen(QColor(color), 1)
                    pen.setCosmetic(True)
                    painter.setPen(pen)
                    painter.drawPolyline(points)
            painter.setOpacity(1)
            for color, lines in self.cell_lines.items():
                pen = QPen(QColor(color), 1)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLines(lines)
                pen.setWidthF(4)
                pen.setCapStyle(Qt.SquareCap)
                painter.setPen(pen)
                painter.drawPoints(self.site_points[color])
            pen = QPen(QColor("#ffffff"), 1.7)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for key in self.selected | ({self.hover} if self.hover else set()):
                p = self.doc.points.get(key)
                if p is None or p.deleted:
                    continue
                if abs(p.x-self.camera.cx)*self.camera.scale > self.width()/2 + 12 or abs(p.y-self.camera.cy)*self.camera.scale > self.height()/2 + 12:
                    continue
                radius = (self.marker_size / 2 + 3) / self.camera.scale
                painter.drawEllipse(QPointF(p.x, p.y), radius, radius)
        if self.move_preview is not None:
            painter.setPen(QPen(QColor("#ffffff"), 1 / self.camera.scale))
            painter.drawEllipse(QPointF(*self.move_preview), 5 / self.camera.scale, 5 / self.camera.scale)
        painter.restore()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#61dfee"), 1.5, Qt.DashLine))
        painter.setBrush(QColor(50, 210, 230, 30))
        if self.dragging and self.tool == "Rectangle" and self.start is not None and self.cursor is not None:
            painter.drawRect(QRectF(self.to_screen(self.start), self.to_screen(self.cursor)).normalized())
        if self.tool in ("Lasso", "Polygon") and self.vertices:
            vertices = self.vertices + ([self.cursor.tolist()] if self.cursor is not None else [])
            painter.drawPolygon(QPolygonF([self.to_screen(q) for q in vertices]))
        if self.tool == "Brush" and self.cursor is not None:
            painter.drawEllipse(self.to_screen(self.cursor), self.brush_radius, self.brush_radius)
        rect = self.mini_rect()
        painter.setOpacity(.9)
        painter.drawImage(rect, self.image)
        painter.setOpacity(1)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#8fa3b7"), 1))
        painter.drawRect(rect)
        vx = self.camera.cx - self.width() / self.camera.scale / 2
        vy = self.camera.cy - self.height() / self.camera.scale / 2
        viewport = QRectF(rect.x() + vx / w * rect.width(), rect.y() + vy / h * rect.height(),
                          self.width() / self.camera.scale / w * rect.width(), self.height() / self.camera.scale / h * rect.height())
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor("#39e7b0"), 1.5))
        painter.drawRect(viewport)
        painter.setClipping(False)
        painter.setPen(QColor("#a6b5c5"))
        painter.drawText(14, self.height() - 14, f"{self.camera.scale * 100:.0f}%   •   {w} × {h} px   •   {len(self.ids):,} points")
        painter.end()
        self.frames.append((time.perf_counter() - t0) * 1000)
        self.frames = self.frames[-300:]

    def wheelEvent(self, event):
        xy = self.to_image(event.position())
        scale = np.clip(self.camera.scale * 1.2 ** (event.angleDelta().y() / 120), .025, 64)
        self.camera.set(xy[0] - (event.position().x() - self.width() / 2) / scale,
                        xy[1] - (event.position().y() - self.height() / 2) / scale, scale)
        event.accept()

    def set_selection(self, ids):
        self.selected = set(ids)
        self.selectionChanged.emit(self.selected)
        self.update()

    def apply_roi(self, mask):
        ids = {self.ids[i] for i in np.flatnonzero(mask & self.visible)}
        self.set_selection(self.base_selection - ids if self.subtract else self.base_selection | ids)

    def brush(self, a, b):
        if self.tree is None:
            return
        radius = self.brush_radius / self.camera.scale
        middle = (a + b) / 2
        indices = self.tree.query_ball_point(middle, np.linalg.norm(b-a)/2 + radius)
        if not indices:
            return
        q = self.xy[indices]
        ab = b - a
        t = np.clip((q - a) @ ab / max(float(ab @ ab), 1e-12), 0, 1)
        hit = np.linalg.norm(q - (a + t[:, None] * ab), axis=1) <= radius
        ids = {self.ids[i] for i, on in zip(indices, hit) if on and self.visible[i]}
        self.set_selection(self.selected - ids if self.subtract else self.selected | ids)

    def mousePressEvent(self, event):
        if not self.doc:
            return
        self.setFocus()
        xy = self.to_image(event.position())
        self.cursor = xy
        if self.mini_rect().contains(event.position()) and event.button() == Qt.LeftButton:
            r = self.mini_rect()
            self.camera.set((event.position().x()-r.x())/r.width()*self.doc.raw.shape[1],
                            (event.position().y()-r.y())/r.height()*self.doc.raw.shape[0])
            return
        if event.button() == Qt.RightButton:
            self.finish_polygon()
            return
        self.pan_active = event.button() == Qt.MiddleButton or self.tool == "Pan"
        self.dragging = True
        self.start = self.last = xy
        self.last_screen = event.position()
        self.subtract = bool(event.modifiers() & Qt.ControlModifier)
        self.additive = bool(event.modifiers() & Qt.ShiftModifier)
        self.base_selection = set(self.selected) if self.subtract or self.additive else set()
        if self.pan_active:
            return
        hit = self.pick(xy)
        if self.tool in ("Select", "Move TM"):
            if self.subtract and hit:
                self.set_selection(self.selected ^ {hit})
            else:
                self.set_selection(self.base_selection | ({hit} if hit else set()))
            if self.tool == "Move TM" and hit and self.doc.points[hit].label == "TM":
                self.move_key = hit
        elif self.tool == "Add TM":
            self.editRequested.emit("add", xy.tolist())
        elif self.tool in ("Delete", "Verify", "Reject", "Seed"):
            if hit:
                self.set_selection({hit})
                self.editRequested.emit(self.tool.lower(), [hit])
        elif self.tool == "Layer Select" and hit:
            p = self.doc.points[hit]
            if p.layer_id is not None:
                self.set_selection({k for k in self.ids if self.doc.points[k].layer_id == p.layer_id and self.doc.points[k].region_id == p.region_id})
        elif self.tool == "Brush":
            self.set_selection(self.base_selection)
            self.brush(xy, xy)
        elif self.tool == "Lasso":
            self.vertices = [xy.tolist()]
        elif self.tool == "Polygon":
            if not self.vertices:
                self.polygon_base = set(self.base_selection)
                self.polygon_subtract = self.subtract
            self.vertices.append(xy.tolist())
        self.update()

    def mouseMoveEvent(self, event):
        if not self.doc:
            return
        xy = self.to_image(event.position())
        self.cursor = xy
        self.cursorChanged.emit(*xy)
        if self.dragging:
            if self.pan_active:
                diff = event.position() - self.last_screen
                self.camera.set(self.camera.cx - diff.x() / self.camera.scale,
                                self.camera.cy - diff.y() / self.camera.scale)
                self.last_screen = event.position()
            elif self.move_key:
                self.move_preview = xy
            elif self.tool == "Brush":
                self.brush(self.last, xy)
            elif self.tool == "Lasso":
                if np.linalg.norm(xy - self.last) * self.camera.scale > 2:
                    self.vertices.append(xy.tolist())
            elif self.tool == "Rectangle":
                lo, hi = np.minimum(self.start, xy), np.maximum(self.start, xy)
                self.apply_roi(np.all((self.xy >= lo) & (self.xy <= hi), axis=1))
            self.last = xy
        else:
            self.hover = self.pick(xy)
        self.update()

    def mouseReleaseEvent(self, event):
        if not self.doc or not self.dragging:
            return
        xy = self.to_image(event.position())
        if self.move_key:
            self.editRequested.emit("move", [self.move_key, *xy])
        elif not self.pan_active and self.tool == "Rectangle":
            lo, hi = np.minimum(self.start, xy), np.maximum(self.start, xy)
            self.apply_roi(np.all((self.xy >= lo) & (self.xy <= hi), axis=1))
        elif not self.pan_active and self.tool == "Lasso":
            self.vertices.append(xy.tolist())
            self.apply_roi(polygon_mask(self.xy, self.vertices))
            self.vertices = []
        self.dragging = False
        self.move_key, self.move_preview = None, None
        self.update()

    def finish_polygon(self):
        if self.tool == "Polygon" and len(self.vertices) >= 3:
            self.base_selection, self.subtract = self.polygon_base, self.polygon_subtract
            self.apply_roi(polygon_mask(self.xy, self.vertices))
        self.vertices = []
        self.dragging = False
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_polygon()
        elif event.key() == Qt.Key_Escape:
            self.vertices = []
            self.move_key, self.move_preview = None, None
            self.dragging = False
            self.set_selection(set())
        else:
            super().keyPressEvent(event)
