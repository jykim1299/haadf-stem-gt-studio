# Validation report — 2026-10-28

Scope: newly implemented local research build; exclusively generated synthetic data and temporary HDF5 fixtures. No company EMD, company TIFF, GT, internal code or internal documents were read or uploaded. Native OS input validation is incomplete. This release is **not a claim of production qualification on company data**.

## Results

| Area | Status | Evidence / limit |
|---|---|---|
| Annotation editor | PASS in Qt integration tests | Native Qt widgets instantiated and shown; mouse/key events exercise selection, add, move, delete, zoom and pan |
| Bulk annotation | PASS | Brush, rectangle, lasso, polygon; 4,110-point approve/undo/redo; ROI affected counts |
| TM layer annotation | PASS | Region/layer assignment, layer colors and connection lines, rename/select/filter; per-layer ROI workflow |
| Save/reload | PASS | Lossless array equality and complete JSON state equality; Undo/Redo persisted; newer recovery restoration; failure injection preserves previous save |
| Reconstruction | PASS for tested synthetic cases / PARTIAL scientific scope | Per-region trusted seed estimation, 174 proposals in full-image workflow; duplicate and rejected-proposal suppression; real materials unvalidated |
| Unit Cell / Cation Site | PARTIAL | Configurable adjacent-layer endpoints, deduplication, preview/review/undo, explicit boundary approval, structural-site export; material-specific BLUE/FAR and shift_-1 topology not implemented |
| Dataset export | PASS | uint16/float32 TIFF, UTF-8 CSV, history/config/overlay, Final eligibility checks, atomic publication with failure test |
| Native Windows input + capture | BLOCKED | Computer Use reported `GetCursorPos failed: access denied (0x80070005)`; retry reported `FrameArrived timed out` |
| Visual inspection | PASS for widget render captures | Overview, 280% synchronized zoom and completed workflow inspected from QWidget.grab PNGs; this is not an OS screenshot assertion |
| Windows source launcher | PASS | Python 3.12 venv, dependency install, `run.ps1 -Help`, native window process launch |
| Installer / signed EXE | NOT IMPLEMENTED | Source + environment scripts only |
| Actual EMD compatibility | NOT VALIDATED | Generic HDF5 fixtures only; explicit dataset/frame selection required |
| Atomap execution | NOT VALIDATED | Optional adapter implemented; SciPy used in this environment |
| Company proxy/offline | DOCUMENTED, NOT VALIDATED | Wheelhouse procedure in README |

### Test suite

36 tests passed with exit code 0 in 7.86 seconds on the last native test run. After an additional image-ID path-containment test, the final **offscreen Qt run passed all 37 tests in 5.96 seconds**, with no diagnostic output. The suite includes 8 tests in the dedicated Qt GUI module and 29 scientific/model/storage/regression tests (including one recovery GUI regression; parameterized cases counted separately). Offscreen tests still instantiate Qt widgets and dispatch Qt mouse/key events, but do not test Windows input injection. Ten synthetic scenarios each verify generation/dtypes/candidate state, **not reconstruction accuracy across all scenarios**.

Native Windows test reruns also emitted a diagnostic `Windows fatal exception: code 0x8001010d` at the initial `QApplication.processEvents()` in the GUI fixture. The process continued, all assertions passed, and exit code was 0. Root cause is **unresolved**, so this is not reported as a clean native OS validation. Earlier runs completed without this diagnostic. An unlocked, interactive company Windows session needs a manual smoke test, including file dialogs and accessibility behavior. No environment security settings were changed and the diagnostic was not suppressed.

Important regression cases: zoom retains the cursor anchor; raw/processed inverse transforms agree; moved coordinates preserve original coordinates and require re-verification; bulk undo restores all selected points; pending/rejected TM never enter Final; editing invalidates Final; pending seeds do not contaminate finalized unit-cell estimation; rejected reconstruction does not recur; pair-review undo restores approval state; new Final versions invalidate old pair previews; failed save/export cannot publish partial results; unknown magnification never becomes 3.90 Mx automatically.

### Full synthetic workflow

Command:

```powershell
.\.venv\Scripts\python.exe tools\validate_workflow.py --output D:\HAADF_GT_Data\synthetic-validation
```

The deterministic seed=42 missing-TM image is 896×896. A **truth oracle substitutes for the human reviewer** in this automated test, using actual Qt polygon input events plus review commands. The oracle is in the test script, never in the normal editor's approval pipeline.

| Item | Result |
|---|---:|
| Detected candidates (initially unknown/pending) | 4,110 |
| Per-layer polygon ROI gestures | 46 |
| Detected TM verified after oracle review | 3,023 |
| Missing-TM proposals reviewed | 174 |
| Final TM | 3,197 |
| Endpoint pair previews | 3,094 |
| Approved interior Cation Sites | 3,049 |
| Exact save/reload state preservation | True |
| Script elapsed time | 20.77 s |

This elapsed time is automation throughput, not human annotation time. Reconstruction proposals are explicitly approved in the script; this does not imply the reconstruction is universally correct. Boundary pairs were excluded. Generated arrays, synthetic truth, project archives, exported CSVs and screenshots are stored outside the source checkout; they can be regenerated from the scripts.

### Performance measurements

Environment: Windows 10 build 19045, Python 3.12.10, PySide6 Essentials 6.12.0. Custom CPU QPainter rendering. Measured with `tools/benchmark.py`; no GPU acceleration or universal FPS claim.

| Metric | 4,110 candidates | 20,000-point synthetic grid |
|---|---:|---:|
| Per-pane paint median | 10.32 ms | 19.60 ms |
| Per-pane paint p95 | 11.64 ms | 25.49 ms |
| Polygon mask calculation | 0.144 ms | 0.268 ms |
| Bulk verify, including view refresh | 163.54 ms | 844.18 ms |
| Bulk undo, including view refresh | 95.56 ms | 618.59 ms |

Both panes must be painted; per-pane timing is not frame latency. Selected-point rings, large histories, layer lines and structural overlays can add cost. At 20,000 points a bulk edit causes a noticeable pause; rendering remains usable but the stronger interpretation of “every operation is instantaneous” is **not met**. No hours-long soak test or large-EMD memory benchmark was performed. Image tiling and further incremental refresh optimization are deferred.

## Reproduce

```powershell
.\install.ps1 -TestDependencies
.\.venv\Scripts\python.exe -m pytest -q -s
.\.venv\Scripts\python.exe tools\benchmark.py --output D:\HAADF_GT_Data\benchmark.json
.\.venv\Scripts\python.exe tools\capture_demo.py --output D:\HAADF_GT_Data\screenshots
```

## Company PC acceptance checklist

1. Confirm folder/sample/file inventory and identify the actual vendor EMD schema. Use a copied single file first; never overwrite originals.
2. Verify selected HAADF dataset, frame axis/index, orientation, uint16 range, shape and calibration against the microscope software.
3. Compare magnification metadata with filename and source evidence; keep uncertain acquisitions outside 3.90 Mx eligibility.
4. Perform native mouse/keyboard/file-dialog checks, including drag, wheel, polygon, batch Undo/Redo, save/close/reopen, and failed destination handling.
5. Inspect real low-SNR/multi-grain/surface-distorted images, choose seeds/regions, and validate spacing, rejected candidates and duplicate prevention.
6. Validate material-specific endpoint geometry and cation-site meaning before approving structural sites. Do not assume the configurable linear site model reproduces prior BLUE/FAR rules.
7. Confirm exports retain coordinates, intensity dtypes, statuses and provenance; back up projects independently of source control.
