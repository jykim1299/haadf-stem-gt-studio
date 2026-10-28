"""Independent analysis adapters. No GUI imports, no implicit GT approval."""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Protocol

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial import cKDTree

from .model import Document, Point, uid

PREPROCESS = {"disk_radius": 5, "gamma": 0.7, "sigmaColor_0_255": 15., "sigmaSpace_px": 1.}
SCENARIOS = ["normal", "missing_tm", "phantom_layer", "surface_distortion", "tilted",
             "multiple_grains", "low_snr", "no_cation_mixing", "surface_cation_mixing", "contrast"]


def preprocess(raw, config=None):
    cfg = PREPROCESS | (config or {})
    if raw.dtype != np.uint16 or raw.ndim != 2:
        raise ValueError("Preprocessing requires a 2D uint16 image.")
    radius = int(cfg["disk_radius"])
    if not 1 <= radius <= 100 or not 0 < cfg["gamma"] <= 5:
        raise ValueError("Invalid top-hat radius or gamma.")
    yy, xx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    disk = ((xx * xx + yy * yy) <= radius * radius).astype(np.uint8)
    top = cv2.morphologyEx(raw, cv2.MORPH_TOPHAT, disk, borderType=cv2.BORDER_REFLECT_101).astype(np.float32)
    lo, hi = float(top.min()), float(top.max())
    norm = (top - lo) / (hi - lo) if hi > lo else np.zeros_like(top)
    gamma = np.power(norm, float(cfg["gamma"])).astype(np.float32)
    # OpenCV's float bilateral uses intensity units of its input. Explicitly use 0..255.
    result = cv2.bilateralFilter(gamma * 255., d=0, sigmaColor=float(cfg["sigmaColor_0_255"]),
                                 sigmaSpace=float(cfg["sigmaSpace_px"]), borderType=cv2.BORDER_REFLECT_101)
    return np.clip(result / 255., 0., 1.).astype(np.float32)


class Detector(Protocol):
    name: str

    def detect(self, processed: np.ndarray, separation=6, threshold_rel=0.1) -> np.ndarray: ...


class ScipyDetector:
    name = "scipy-local-maxima"

    def detect(self, processed, separation=6, threshold_rel=0.1):
        if separation < 1 or not 0 < threshold_rel < 1:
            raise ValueError("Invalid detection parameters.")
        peaks = (processed == maximum_filter(processed, size=2 * int(separation) + 1))
        peaks &= processed > float(processed.max()) * threshold_rel
        y, x = np.nonzero(peaks)
        order = np.argsort(processed[y, x])[::-1]
        xy = np.column_stack([x, y]).astype(float)[order]
        if not len(xy):
            return xy
        tree = cKDTree(xy)
        removed = set()
        keep = []
        for i in range(len(xy)):
            if i not in removed:
                keep.append(i)
                removed.update(tree.query_ball_point(xy[i], separation))
        return xy[keep]


class AtomapDetector:
    name = "atomap"

    def detect(self, processed, separation=6, threshold_rel=0.1):
        try:
            import atomap.api as am
            from hyperspy.signals import Signal2D
        except ImportError as exc:
            raise RuntimeError("Install the optional atomap extra. This adapter has not been validated with company data.") from exc
        return np.asarray(am.get_atom_positions(Signal2D(processed), separation=separation,
                                               threshold_rel=threshold_rel), dtype=float)


def detected_points(doc, detector=None, separation=6, threshold_rel=0.1):
    detector = detector or ScipyDetector()
    xy = detector.detect(doc.processed, separation, threshold_rel)
    return [doc.point(x, y, source=detector.name) for x, y in xy]


def synthetic(scenario="missing_tm", size=896, seed=42):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown synthetic scenario")
    rng = np.random.default_rng(seed)
    image = np.zeros((size, size), np.float32)
    truth, missing, cations = [], [], []
    angle = math.radians(13 if scenario == "tilted" else 3)
    spacing, layer_spacing = 12., 20.
    for row in range(-6, size // 20 + 7):
        for col in range(-10, size // 12 + 10):
            x0, y0 = col * spacing + (row % 2) * 3, row * layer_spacing
            theta = angle if scenario != "multiple_grains" or x0 < size / 2 else math.radians(-16)
            cx, cy = x0 - size / 2, y0 - size / 2
            x = cx * math.cos(theta) - cy * math.sin(theta) + size / 2
            y = cx * math.sin(theta) + cy * math.cos(theta) + size / 2
            if scenario == "surface_distortion":
                y += 5 * math.sin(x / 70) * math.exp(-max(0, y) / 140)
                x += 1.7 * math.sin(y / 50)
            x += rng.normal(0, .16)
            y += rng.normal(0, .16)
            if not (8 < x < size - 8 and 8 < y < size - 8):
                continue
            p = {"x": x, "y": y, "layer_id": row + 10, "region_id": int(scenario == "multiple_grains" and x0 >= size / 2)}
            absent = scenario == "missing_tm" and rng.random() < .065
            phantom = scenario == "phantom_layer" and row % 9 == 4
            if absent or phantom:
                missing.append(p)
            else:
                truth.append(p)
                image[round(y), round(x)] += rng.uniform(18000, 36000)
            if y + 10 < size - 3:
                cations.append({"x": x, "y": y + 10, "label": "Cation Site"})
                mixing = .1 if scenario != "no_cation_mixing" else 0
                if scenario == "surface_cation_mixing":
                    mixing = .55 if y < 90 else 0
                if rng.random() < mixing:
                    image[round(y + 10), round(x)] += rng.uniform(1500, 8000)
    image = gaussian_filter(image, 1.05)
    yy, xx = np.indices(image.shape)
    background = 600 + 400 * xx / size + 250 * np.sin(yy / 150)
    if scenario == "contrast":
        background += 12000 * xx / size
        image *= .35
    noise = 430 if scenario == "low_snr" else 85
    raw = np.clip(image + background + rng.normal(0, noise, image.shape), 0, 65535).astype(np.uint16)
    doc = Document(uid("synthetic"), raw, preprocess(raw), metadata={
        "synthetic": True, "scenario": scenario, "seed": seed, "shape": list(raw.shape),
        "calibration": {"pixel_size": None, "unit": "px"}, "magnification": None,
        "magnification_status": "unknown", "preprocessing": PREPROCESS,
        "truth": {"tm": truth, "missing_tm": missing, "cation_sites": cations},
        "notice": "Synthetic evaluation only; not evidence of actual HAADF-STEM accuracy."})
    pts = detected_points(doc)
    doc.points = {p.point_id: p for p in pts}
    doc.candidates = [asdict(p) for p in pts]
    return doc


def estimate_lattice(doc, region_id=0, verified_only=False):
    """Use only explicit TM seeds/verified TM in manually assigned layers and one region."""
    groups = {}
    for p in doc.points.values():
        if p.deleted or p.region_id != region_id or p.layer_id is None or p.label != "TM":
            continue
        if p.review_status == "rejected" or not (p.seed or p.review_status == "verified"):
            continue
        if verified_only and p.review_status != "verified":
            continue
        groups.setdefault(p.layer_id, []).append(p)
    layers = []
    for layer_id, pts in sorted(groups.items()):
        if len(pts) < 4:
            continue
        xy = np.array([[p.x, p.y] for p in pts])
        center = xy.mean(axis=0)
        _, _, vt = np.linalg.svd(xy - center, full_matrices=False)
        u = vt[0]
        if u[0] < 0:
            u = -u
        s = (xy - center) @ u
        gaps = np.diff(np.sort(s))
        gaps = gaps[gaps > 1]
        if len(gaps) < 3:
            continue
        d = float(np.median(gaps[gaps <= np.percentile(gaps, 60)]))
        normal = np.array([-u[1], u[0]])
        residual = float(np.sqrt(np.mean(((xy - center) @ normal) ** 2)))
        if residual > .25 * d:
            raise ValueError(f"Layer {layer_id} is too curved or contains mixed rows. Split the region/layer.")
        agreement = float(np.mean(np.abs(gaps / d - np.round(gaps / d)) <= .15))
        if agreement < .7:
            raise ValueError(f"Layer {layer_id} seed spacing is inconsistent ({agreement:.0%}). Review seeds.")
        layers.append({"layer_id": layer_id, "center": center.tolist(), "u": u.tolist(),
                       "d_tm": d, "residual_px": residual, "spacing_agreement": agreement,
                       "count": len(pts), "ids": [p.point_id for p in pts]})
    if not layers:
        raise ValueError("Assign at least four trusted TM seeds or verified TM to one layer in the current region.")
    directions = np.array([l["u"] for l in layers])
    u = np.mean(directions, axis=0)
    u /= np.linalg.norm(u)
    angles = np.arccos(np.clip(directions @ u, -1, 1))
    if np.max(angles) > np.deg2rad(12):
        raise ValueError("Layer directions differ by more than 12 degrees. Separate grains into regions.")
    normal = np.array([-u[1], u[0]])
    positions = np.sort([np.dot(l["center"], normal) for l in layers])
    dg = np.diff(positions)
    return {"region_id": region_id, "revision": doc.revision, "d_tm": float(np.median([l["d_tm"] for l in layers])),
            "d_layer": float(np.median(dg)) if len(dg) else None,
            "theta_deg": float(np.rad2deg(np.arctan2(u[1], u[0]))), "layers": layers,
            "warning": "Spacing aliasing is possible with sparse seeds; inspect every proposal. No cross-grain extrapolation."}


def reconstruct(doc, estimate, tolerance=.15):
    if estimate["revision"] != doc.revision:
        raise ValueError("Lattice estimate is stale; estimate again after edits.")
    # Rejected reconstruction locations must not reappear on every preview.
    existing = [p for p in doc.points.values() if (not p.deleted and p.review_status != "rejected") or p.source == "reconstruction"]
    tree = cKDTree([[p.x, p.y] for p in existing]) if existing else None
    proposals = []
    checks = []
    for layer in estimate["layers"]:
        pts = [doc.points[key] for key in layer["ids"]]
        u = np.asarray(layer["u"])
        pts.sort(key=lambda p: np.dot([p.x, p.y], u))
        d = layer["d_tm"]
        for a, b in zip(pts, pts[1:]):
            delta = np.array([b.x - a.x, b.y - a.y])
            gap = float(np.linalg.norm(delta))
            n = round(gap / d)
            if not 2 <= n <= 6 or abs(gap / d - n) > tolerance:
                continue
            if np.dot(delta / gap, u) < math.cos(math.radians(12)):
                continue
            for k in range(1, n):
                xy = np.array([a.x, a.y]) + delta * k / n
                # When adjacent trusted layers exist, require tangential support in at least one.
                neighbors = [l for l in estimate["layers"] if l["layer_id"] != layer["layer_id"]]
                if neighbors:
                    normal = np.array([-u[1], u[0]])
                    neighbor = min(neighbors, key=lambda l: abs(np.dot(np.subtract(l["center"], layer["center"]), normal)))
                    adjacent = np.array([[doc.points[key].x, doc.points[key].y] for key in neighbor["ids"]])
                    if np.min(np.abs((adjacent - xy) @ u)) > .65 * d:
                        continue
                # Existing atom candidates remain unknown until explicitly selected as TM.
                if tree is not None and tree.query(xy)[0] < .35 * d:
                    continue
                if checks and np.min(np.linalg.norm(np.asarray(checks) - xy, axis=1)) < .35 * d:
                    continue
                p = doc.point(*xy, source="reconstruction", label="TM", layer_id=layer["layer_id"],
                              region_id=estimate["region_id"])
                proposals.append(p)
                checks.append(xy)
    return proposals


def unit_cells(doc, region_id=0, orientation=1, shift_ratio=0., partner_tolerance=.5, site_fraction=.5):
    """Explicit configurable endpoint pairing; no material-specific crystallography assumed."""
    if not doc.final_current:
        raise ValueError("Finalize the current verified TM coordinates first.")
    if orientation not in (-1, 1) or not 0 < partner_tolerance <= 1 or not 0 < site_fraction < 1:
        raise ValueError("Invalid unit-cell configuration.")
    est = estimate_lattice(doc, region_id, verified_only=True)
    if len(est["layers"]) < 2:
        raise ValueError("Two assigned layers with four verified TM each are required.")
    final_ids = {p["point_id"] for p in doc.final["points"]}
    theta = np.deg2rad(est["theta_deg"])
    u, normal = np.array([np.cos(theta), np.sin(theta)]), np.array([-np.sin(theta), np.cos(theta)])
    layers = sorted(est["layers"], key=lambda l: np.dot(l["center"], normal), reverse=orientation < 0)
    cells, pairs, sites = [], set(), []
    h, w = doc.raw.shape
    for la, lb in zip(layers, layers[1:]):
        # Never bridge a missing/phantom layer silently.
        layer_gap = abs(np.dot(np.subtract(lb["center"], la["center"]), normal))
        if est["d_layer"] and layer_gap > 1.5 * est["d_layer"]:
            continue
        apts = [doc.points[k] for k in la["ids"] if k in final_ids]
        bpts = [doc.points[k] for k in lb["ids"] if k in final_ids]
        if not bpts:
            continue
        bx = np.array([[p.x, p.y] for p in bpts])
        tree = cKDTree(bx)
        for a in apts:
            ax = np.array([a.x, a.y])
            target = ax + orientation * layer_gap * normal + shift_ratio * est["d_tm"] * u
            distance, j = tree.query(target)
            if distance > partner_tolerance * est["d_tm"]:
                continue
            b = bpts[j]
            key = tuple(sorted([a.point_id, b.point_id]))
            site = ax + site_fraction * (bx[j] - ax)
            if key in pairs or not (0 <= site[0] < w and 0 <= site[1] < h):
                continue
            if sites and np.min(np.linalg.norm(np.asarray(sites) - site, axis=1)) < .25 * est["d_tm"]:
                continue
            boundary = bool(min(*ax, *bx[j], w-1-ax[0], h-1-ax[1], w-1-bx[j][0], h-1-bx[j][1]) < est["d_tm"])
            cells.append({"cell_id": uid("cell"), "tm1_id": a.point_id, "tm2_id": b.point_id,
                          "x": float(site[0]), "y": float(site[1]), "region_id": region_id,
                          "boundary": boundary, "status": "pending", "endpoint_error_px": float(distance),
                          "final_version": doc.final["version"]})
            pairs.add(key)
            sites.append(site)
    return cells, est
