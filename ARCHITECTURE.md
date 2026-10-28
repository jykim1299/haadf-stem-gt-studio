# Architecture decision: local Qt annotation workstation

Decision date: 2026-10-28. New implementation; no code imported from earlier editors.

The primary task is inspecting and correcting thousands of coordinates with a mouse. We use **PySide6 Widgets + a custom batched QPainter canvas**, backed by independent Python/NumPy/SciPy modules. There is no web server, Streamlit, Electron shell, or per-atom graphics widget.

| Criterion | React/TypeScript + Canvas/WebGL + Python API | Qt Graphics View with one item per atom | Selected Qt custom canvas + Python |
|---|---|---|---|
| Thousands of points | Strong with GPU batching; image/API serialization required | Item bookkeeping and event dispatch grow with count | A few QPolygonF batches; cKDTree hit testing |
| Zoom/pan | Good with explicit viewport transforms | Mature built-in transforms | One shared camera and explicit inverse transforms |
| Mouse editing | Requires browser pointer/key handling | Native events, but many objects | Native Qt events and native file dialogs |
| Brush/lasso/polygon | Custom geometry | Custom geometry + scene queries | Vectorized ROI and spatial brush queries |
| Raw/processed sync | Shared state across canvases | Shared transforms across views | Same Camera instance for both panes |
| Python analysis | HTTP/WebSocket/job orchestration | Direct library access | Direct access; QRunnable workers |
| Windows setup | Node build + Python/server lifecycle | Python + Qt runtime | Python + minimal Qt Essentials; one launcher |
| Large images | GPU texture limits/tiling require work | Image items/tiling require work | Full-resolution image and display copy; tiling deferred |
| Maintenance | Two languages, protocol/schema coupling | Straightforward, but item scaling risk | Small Python modules, no process protocol |
| GitHub updates | Build assets + backend dependencies | Python environment updates | Git pull + pinned dependencies + run.ps1 |

Qt was selected for the local mouse workflow and direct scientific-library integration. WebGL remains a possible future rendering backend if measured workloads require it; we do not claim GPU acceleration. CPU batching already meets the measured 4,110-candidate workload. One QWidget per image, never one widget per atom.

## Modules and invariants

- `model.py`: stable point IDs, immutable original coordinates, current intensity samples, labels, review status, layer/region, reversible delta commands, append-only audit history, versioned Final snapshots.
- `viewer.py`: two canvases sharing `Camera`; batched QPolygonF rendering, KD-tree hover/brush queries, NumPy rectangle and polygon masks. Selection and camera are UI state, independent of image processing.
- `science.py`: exact preprocessing range contract, detection adapters, deterministic synthetic generator, region/layer-specific trusted-seed lattice estimation, conservative missing-site proposals, configurable endpoint pairing.
- `storage.py`: explicit HDF5 EMD inventory and frame selection, TIFF import, atomic lossless projects, immutable dataset exports. No filesystem access at import time.
- `app.py`: native controls, history/property/layer views, QRunnable jobs, autosave/recovery, source/data separation and workspace/project locks.

Raw pixels are uint16 and never overwritten. Processed values are float32 in [0,1] and have exactly the same shape and axes. Coordinates are floating image pixel coordinates, origin at the top-left pixel **center** (0,0), x right, y down. Image drawing uses boundaries at -0.5 to keep marker centers aligned. Calibration is metadata, not a rescaling of coordinates.

Preprocessing: discrete circular disk **radius** 5 → uint16 white top-hat with reflected border → float32 min-max normalization, constant input maps to zero → power 0.7 → multiply by 255 → float32 bilateral with sigmaColor=15 in those intensity units and sigmaSpace=1 pixel → divide by 255 and clip. There is no uint8 quantization in the scientific pipeline. Display copies use independent 0.2/99.8 percentile contrast and do not modify data. The ambiguous term `disk_size=5` is explicitly interpreted as radius, not diameter.

Detection produces `unknown/pending`, never implicit TM. SciPy is the tested independent adapter; Atomap is optional and unvalidated here. Moving a verified TM changes it to pending and resamples both intensity fields. Bulk label changes are a single reversible transaction. Deleted points are tombstones, preserving IDs and traceability.

Lattice estimation requires >=4 trusted TM per manually assigned layer and one region. It estimates direction with PCA, robust typical intralayer gaps, spacing consistency, RMS transverse distortion and median assigned-layer spacing. It refuses strongly curved/mixed rows and inconsistent orientations. Sparse or regularly subsampled seeds can alias spacing; the UI explicitly warns about this. Reconstruction only fills bounded gaps of 2–6 spacings with a 0.15 integer-ratio tolerance, local angle agreement, adjacent-layer tangential support when available, and coordinate deduplication. No extrapolation across grains or beyond endpoints. A reconstructed point remains pending until human approval; rejected proposals are not reintroduced on each preview.

## Scientific scope of unit cells

The implemented model is an **explicit configurable endpoint pair and site-fraction model**, not a universal crystallographic solver. Users choose adjacent-layer orientation, tangential shift/d_TM, partner tolerance and interpolation fraction. Estimation uses only final verified TM, pairs are deduplicated by unordered endpoint IDs and site coordinates, boundary pairs need separate approval. Material-specific BLUE/FAR and shift_-1 topology is **not assumed or claimed validated**. Set a shift such as -0.641 only when independently justified for the particular image/material. Nonlinear unit-cell geometry, uncertainty propagation, and automatic crystal indexing are deferred.

Cation Site records have independent IDs and refer to structural measurement locations, not atomic detections or evidence of cation mixing. Only approved pairs produce exported Cation Sites. A new final version clears old previews; stale structural commands cannot be exported.

## Responsiveness, storage and recovery

Analysis, loading, explicit saving, autosaving and exports execute in QThreadPool workers. Editing controls are temporarily disabled during a job to avoid concurrent mutation; painting/event processing remains live. Snapshot/history rebuild and point grouping are on the UI thread, so very large edits still have a bounded synchronous cost, measured in the report. Loading uses full-resolution arrays; **out-of-core tiling is not implemented**. Current raw-image limit is 512 MiB; practical peak memory is several times raw size. Project decompressed payload limit is 4 GiB. A 64-bit Python runtime is required for serious use.

`.hsgt` is a ZIP containing versioned JSON plus NumPy arrays with `allow_pickle=False`. A new temporary file is flushed/fsynced and atomically replaces the destination only when complete. Autosave runs after 2.5 seconds of inactivity and every 30 seconds while dirty, in the data workspace. On opening a named project, a newer matching recovery archive is restored. Draft paths and dataset outputs are rejected beneath a Git repository or source project. Data is local; the application makes no network calls. QLockFile prevents simultaneous writers to a workspace/named project. Recovery writes on close must succeed before the window closes.

Final snapshots include only nondeleted TM with `verified` review status; pending/rejected points are excluded. Any point mutation invalidates the current final. Previous snapshots remain in the audit log. Export writes to a private staging directory, then renames it to a new immutable revision directory only after every output succeeds. It includes typed TIFFs, full candidate provenance, proposals, final TM, layers, reviewed pairs/sites, history, configuration, overlays and inventory. Failed staging outputs are cleaned up with strict parent-path checks; abrupt process/power loss may leave a hidden staging directory, never a published complete export.

## References

Qt's official [QPainter API](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QPainter.html) documents batched point/polyline primitives. [QRunnable](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QRunnable.html) documents the thread-pool execution model. These inform the implementation; performance claims come only from our own synthetic tests.
