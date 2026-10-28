from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import copy
import re
import uuid

import numpy as np


def stamp():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix="p"):
    return prefix + "_" + uuid.uuid4().hex[:16]


@dataclass
class Point:
    point_id: str
    x: float
    y: float
    original_x: float
    original_y: float
    raw_intensity: float
    processed_intensity: float
    source: str = "detection"
    label: str = "unknown"
    review_status: str = "pending"
    layer_id: int | None = None
    region_id: int = 0
    seed: bool = False
    deleted: bool = False


@dataclass
class Document:
    image_id: str
    raw: np.ndarray
    processed: np.ndarray
    metadata: dict = field(default_factory=dict)
    points: dict[str, Point] = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    undo_stack: list[dict] = field(default_factory=list)
    redo_stack: list[dict] = field(default_factory=list)
    revision: int = 0
    final: dict | None = None
    cells: list[dict] = field(default_factory=list)
    cell_config: dict = field(default_factory=dict)
    cells_approved: bool = False
    analysis: dict = field(default_factory=dict)

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.image_id):
            raise ValueError("Invalid image ID; path components are not allowed.")
        if self.raw.ndim != 2 or self.raw.dtype != np.uint16:
            raise ValueError("Raw data must be a 2D uint16 image; implicit conversion is forbidden.")
        if min(self.raw.shape) < 1:
            raise ValueError("Image dimensions must be positive.")
        if self.processed.shape != self.raw.shape or self.processed.dtype != np.float32:
            raise ValueError("Processed data must be float32 with the same shape as raw.")
        if not np.isfinite(self.processed).all():
            raise ValueError("Processed image contains non-finite values.")
        h, w = self.raw.shape
        for key, p in self.points.items():
            if key != p.point_id or not (0 <= p.x < w and 0 <= p.y < h):
                raise ValueError("Point identity or coordinates are invalid.")
            if p.label not in {"unknown", "TM", "non-TM"} or p.review_status not in {"pending", "verified", "rejected"}:
                raise ValueError("Invalid label or review status.")

    def point(self, x, y, source="manual", **kwargs):
        h, w = self.raw.shape
        if not np.isfinite([x, y]).all() or not (0 <= x < w and 0 <= y < h):
            raise ValueError("Point is outside the image.")
        ix, iy = min(w - 1, round(x)), min(h - 1, round(y))
        return Point(uid(), float(x), float(y), float(x), float(y),
                     float(self.raw[iy, ix]), float(self.processed[iy, ix]), source, **kwargs)

    def commit(self, description, updates: dict[str, Point | None]):
        before, after = {}, {}
        for key, value in updates.items():
            a = asdict(value) if value is not None else None
            b = asdict(self.points[key]) if key in self.points else None
            if a != b:
                before[key], after[key] = b, a
        if not after:
            return False
        command = {"description": description, "before": before, "after": after}
        self._apply(after)
        self.undo_stack.append(command)
        self.redo_stack.clear()
        self._log(description, command)
        return True

    def _apply(self, states):
        for key, value in states.items():
            if value is None:
                self.points.pop(key, None)
            else:
                self.points[key] = Point(**value)
        self.revision += 1
        self.cells_approved = False

    def _log(self, action, changes):
        self.history.append({"time": stamp(), "revision": self.revision,
                             "action": action, "changes": copy.deepcopy(changes)})

    def commit_cells(self, description, **fields):
        if not set(fields) <= {"cells", "cell_config", "cells_approved"}:
            raise ValueError("Invalid structural command")
        before = {key: copy.deepcopy(getattr(self, key)) for key in fields}
        after = copy.deepcopy(fields)
        if before == after:
            return False
        command = {"description": description, "aux_before": before, "aux_after": after}
        self._apply_aux(after)
        self.undo_stack.append(command)
        self.redo_stack.clear()
        self._log(description, command)
        return True

    def _apply_aux(self, state):
        for key, value in state.items():
            setattr(self, key, copy.deepcopy(value))

    def update(self, ids, description, **fields):
        updates = {}
        for key in ids:
            if key not in self.points or self.points[key].deleted:
                continue
            p = copy.copy(self.points[key])
            for name, value in fields.items():
                setattr(p, name, value)
            updates[key] = p
        return self.commit(description, updates)

    def move(self, key, x, y):
        p = copy.copy(self.points[key])
        sample = self.point(x, y)
        p.x, p.y = sample.x, sample.y
        p.raw_intensity, p.processed_intensity = sample.raw_intensity, sample.processed_intensity
        # A moved, previously approved coordinate needs human re-verification.
        p.review_status = "pending"
        return self.commit("Move point (re-verification required)", {key: p})

    def undo(self):
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        if "aux_before" in cmd:
            self._apply_aux(cmd["aux_before"])
        else:
            self._apply(cmd["before"])
        self.redo_stack.append(cmd)
        self._log("Undo: " + cmd["description"], {"command": cmd})
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        if "aux_after" in cmd:
            self._apply_aux(cmd["aux_after"])
        else:
            self._apply(cmd["after"])
        self.undo_stack.append(cmd)
        self._log("Redo: " + cmd["description"], cmd)
        return True

    def verified(self):
        return [p for p in self.points.values() if not p.deleted and p.label == "TM" and p.review_status == "verified"]

    def finalize(self):
        points = self.verified()
        if not points:
            raise ValueError("Verify at least one TM before finalizing.")
        version = (self.final or {}).get("version", 0) + 1
        self.final = {"version": version, "revision": self.revision, "time": stamp(),
                      "points": [asdict(p) for p in points]}
        self.cells = []
        self.cell_config = {}
        self.cells_approved = False
        self._log("Finalize TM v" + str(version), {"final": copy.deepcopy(self.final)})

    @property
    def final_current(self):
        return self.final is not None and self.final["revision"] == self.revision

    @property
    def cells_current(self):
        return self.final_current and all(c["final_version"] == self.final["version"] for c in self.cells)

    def state(self):
        return {"schema_version": 1, "image_id": self.image_id,
                **{k: copy.deepcopy(getattr(self, k)) for k in
                   ("metadata", "candidates", "history", "undo_stack", "redo_stack", "revision",
                    "final", "cells", "cell_config", "cells_approved", "analysis")},
                "points": [asdict(p) for p in self.points.values()]}

    @classmethod
    def from_state(cls, state, raw, processed):
        state = copy.deepcopy(state)
        if state.pop("schema_version") != 1:
            raise ValueError("Unsupported project schema version.")
        state["points"] = {p["point_id"]: Point(**p) for p in state["points"]}
        doc = cls(raw=raw, processed=processed, **state)
        doc.validate()
        return doc
