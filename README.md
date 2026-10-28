# HAADF-STEM GT Annotation Studio

Windows에서 실행하는 로컬 연구용 annotation 도구입니다. **수천 개 후보의 영역 선택·일괄 검증·좌표 교정**을 중심으로 구현했습니다. 기존 Streamlit editor나 `atomic-point-annotator` 코드를 사용하지 않은 신규 프로젝트입니다.

상태: **v0.1 / synthetic-validated research build**. 실제 회사 EMD 호환성, Atomap 실행, 소재별 Unit Cell 해석은 회사 환경에서 추가 검증해야 합니다. 상세 근거는 [VALIDATION.md](VALIDATION.md), 기술 선택은 [ARCHITECTURE.md](ARCHITECTURE.md)를 확인하세요.

## Windows 설치와 실행

필수: **Windows 10/11 x64, Python 3.12 x64 (`py` launcher 포함), Git**. 앱 사용 중에는 서버나 인터넷이 필요 없습니다. 저장소 접근에는 해당 Private 저장소의 GitHub 권한이 필요합니다.

```powershell
git clone https://github.com/jykim1299/haadf-stem-gt-studio.git
cd haadf-stem-gt-studio
.\install.ps1
.\run.ps1 -Synthetic missing_tm
```

이후에는 ` .\run.ps1 ` 한 명령으로 실행합니다. PowerShell 정책 때문에 스크립트 실행이 제한된 회사 환경에서는 회사 정책을 변경하지 말고 아래 Python 명령을 사용하거나 IT에 승인된 실행 방법을 문의하세요.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m haadf_studio.app --synthetic missing_tm
```

별도 연구 데이터 경로 지정:

```powershell
.\run.ps1 -Workspace 'D:\HAADF_GT_Data'
.\run.ps1 -Workspace 'D:\HAADF_GT_Data' -Project 'D:\HAADF_GT_Data\my-image.hsgt'
```

기본 데이터 경로는 `%USERPROFILE%\Documents\HAADF_GT_Studio`입니다. Source 저장소 아래에는 프로젝트/출력을 저장할 수 없습니다. 같은 data workspace는 한 앱에서만 열 수 있습니다. Frontend build는 필요 없습니다. 실행 환경 패키징이며 서명된 EXE/MSI 배포는 아직 제공하지 않습니다.

## 처음 사용할 때

1. 왼쪽 scenario를 고르고 **New synthetic**으로 연습하거나 **Import EMD / TIFF**로 원본을 선택합니다. 검출점은 모두 `unknown/pending`으로 시작합니다.
2. Raw/Processed 좌우 화면에서 휠 확대, 가운데 버튼 드래그 또는 Pan으로 이동합니다. 두 화면은 하나의 image coordinate와 camera를 공유합니다. 오른쪽 위 minimap을 클릭해 이동할 수 있습니다.
3. Rectangle / Brush / Lasso / Polygon으로 TM 후보를 선택합니다. **화면 위에 변경할 점 개수**가 나옵니다. Shift는 선택 추가, Ctrl은 영역 선택에서 제외입니다. Polygon은 Enter 또는 우클릭으로 완료합니다.
4. **Verify TM**으로 사람이 확인한 TM만 승인합니다. **Reject**는 후보 제외, **Set non-TM**은 검출 후보의 non-TM 분류입니다. 영상을 보고 판단 없이 전체 후보를 TM으로 승인하면 안 됩니다.
5. Add TM, Move TM, Delete로 보정합니다. 수동 추가는 pending이며, 이동한 verified TM도 pending으로 돌아갑니다. 필요하면 Verify합니다. 편집은 전처리를 재실행하지 않습니다.
6. Current layer / region을 설정하고 **Assign layer**를 누릅니다. `Advance after layer assignment`로 다음 layer 번호로 이동합니다. Layer 목록을 누르면 같은 region의 layer 전체가 선택됩니다. Connect TM layers / Color by layer / Unassigned only를 활용하세요.
7. 필요하면 일부 TM을 Seed로 지정합니다. 분석에 사용할 layer마다 최소 4개의 신뢰 가능한 TM이 필요합니다. 결정립 방향이 다르면 region을 분리하세요.
8. Analysis → **Estimate current region** → **Preview missing TM**. 간격과 방향은 이미지/region별로 추정합니다. 보라색 reconstruction은 제안이며 Verify/Reject해야 합니다.
9. **Finalize verified TM**으로 현재 사람이 검증한 TM snapshot을 만듭니다. 이후 좌표/label 변경은 Final을 stale로 만들므로 다시 Finalize해야 합니다.
10. Orientation / Shift / Partner tolerance / Site fraction을 소재에 맞게 설정하고 **Preview unit-cell pairs** → **Review / approve pairs**. 행 클릭은 endpoint를 강조합니다. 잘못된 pair는 Reject, boundary pair는 별도로 검토·승인하세요. Preview와 pair 일괄 검토도 Undo/Redo됩니다.
11. **Save draft**, **Export dataset**. Cation Site는 구조적으로 정의한 Li-layer 측정 위치이며 cation mixing을 뜻하지 않습니다. 검출 후보의 non-TM label과 별도 파일/ID입니다.

현재 Unit Cell 기능은 두 TM endpoint 사이의 설정 가능한 위치를 제안합니다. BLUE/FAR, shift_-1 또는 -0.641이 모든 소재에 타당하다고 가정하지 않습니다. 실제 crystallographic geometry는 연구자가 확인해야 하며 고급 material-specific 모델은 미구현입니다.

## 단축키

| 키 | 동작 |
|---|---|
| S / A / M | Select / Add TM / Move TM |
| B / R / L / P | Brush / Rectangle / Lasso / Polygon |
| H / E | Pan / Seed |
| V / X / Delete | 선택 TM 승인 / Reject / 삭제 |
| Ctrl+A | 현재 필터에 보이는 모든 후보 선택 |
| Ctrl+L | 선택점에 current layer/region 지정 |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+S / Ctrl+O | Save draft / Open project |
| F / F11 | Fit image / Fullscreen |
| Enter / Esc | Polygon 완료 / 그리기·선택 취소 |

단축키는 text/spinbox를 편집할 때 동작하지 않습니다. 선택점은 두 viewer에서 흰색으로 강조됩니다. 이미지 좌표와 intensity는 우측 property panel에 표시됩니다.

## 저장·복원

Draft `.hsgt`에는 raw uint16, processed float32, 후보/점의 원래·현재 좌표, source/review/layer/region, history, Undo/Redo, Final, unit-cell 검토 상태가 함께 저장됩니다. 이미지마다 독립적인 프로젝트 파일을 사용하세요.

자동 저장은 편집 후 2.5초 idle 및 30초 주기로 data workspace에 `image_id.autosave.hsgt`를 씁니다. 종료 시 미저장 변경도 recovery에 보존합니다. 기존 이름의 프로젝트보다 새로운 recovery가 있으면 다시 열 때 복원합니다. 왼쪽 Dataset에는 이 workspace의 최근 프로젝트·recovery가 표시됩니다. 다른 경로의 파일은 Open project로 선택합니다. 새 synthetic/import 전에 현재 변경도 recovery에 저장합니다.

원본 EMD/TIFF 파일은 변경하지 않습니다. 작업 데이터와 Git 저장소를 분리하세요. `.gitignore`는 EMD, TIFF, archive, NumPy arrays, checkpoint, local config, venv/cache를 차단합니다. 임의의 실제 연구 데이터를 강제로 add하지 마세요.

## 회사 EMD 연결

`Import EMD / TIFF`에서 회사의 `D:\workspace\HR-STEM_Crystal_Structure\STEM images`를 찾아 이동합니다. 앱이 이 폴더를 가정하거나 자동 스캔하지 않습니다. 샘플 7개 / EMD 27개 / 3.90 Mx 15개라는 이전 조사 결과는 회사 PC에서 재확인해야 합니다.

파일을 변경하지 않는 선택적 inventory 명령도 제공합니다. 배율 hint는 기록하지만 모든 파일의 target eligibility는 검증 전 false로 유지합니다.

```powershell
.\.venv\Scripts\python.exe tools\inventory_emd.py --root 'D:\workspace\HR-STEM_Crystal_Structure\STEM images' --output 'D:\HAADF_GT_Data\emd_inventory.csv'
```

- EMD reader는 독립적인 **실험적 generic HDF5 adapter**입니다. 2D/3D uint16 dataset을 열거하며 **사용자가 HAADF 채널을 명시적으로 선택**합니다. 첫 번째 채널을 자동으로 HAADF라 간주하지 않습니다.
- 3D 데이터는 frame axis와 index를 사용자가 지정합니다. 축 순서, 검출기 이름과 적절한 frame을 vendor metadata와 대조하세요. 비-HDF5, 압축 vendor stream, 4D, spectrum-image, 비-uint16 형식은 지원하지 않습니다.
- HDF5 attributes와 제한 크기 이하 metadata payload를 보존합니다. Image metadata 화면에서 확인할 수 있습니다. vendor-specific calibration/magnification 자동 해석은 아직 없습니다. 큰 attribute/payload는 생략될 수 있으므로 원본 EMD를 따로 보존하세요.
- Magnification은 기본 `unknown`. 근거와 수치를 입력하고 사람이 확인해야 `user-verified`가 됩니다. **정확히 3,900,000×로 확인된 경우만** inventory의 target_3_90_mx가 true입니다. filename만으로 임의 분류하지 않습니다.
- Pixel size와 nm/pm/angstrom 단위를 검증해 입력하세요. 저장 좌표는 항상 pixel 기준입니다.
- 현재 메모리 기반 viewer이며 raw 512 MiB limit이 있습니다. 초대형 이미지를 위한 타일링은 미구현입니다. 회사 PC에서는 먼저 작은 파일/프레임 하나로 검증하세요.

Atomap adapter 설치(선택 사항):

```powershell
.\.venv\Scripts\python.exe -m pip install '.[atomap]'
```

Analysis에서 Atomap을 선택하고 `Detect additional candidates`를 실행합니다. separation=6, threshold_rel=0.1이 기본입니다. 기존 점을 덮어쓰지 않고 가까운 중복을 제외한 unknown 후보만 추가합니다. Atomap은 이 개발 환경에서 실행 검증하지 않았습니다. 설치로 scientific dependency가 변경되면 테스트를 다시 실행하고 버전을 기록하세요.

## Export

Export는 output root 아래 새 revision 폴더를 만들며 기존 결과를 덮어쓰지 않습니다.

```text
<image_id>_v<version>_<export_id>/
  inventory.csv
  dataset_summary.csv
  REPORT.md
  images/<image_id>/
    raw_uint16.tif
    processed_float32.tif
    atom_candidates.csv
    tm_proposals.csv
    tm_gt_verified.csv
    tm_layer_table.csv
    unit_cells.csv
    cation_sites.csv
    annotation_history.json
    tm_overlay.png
    unit_cell_overlay.png
    config.json
    synthetic_truth.json  # synthetic 입력인 경우에만
```

`atom_candidates.csv`는 최초 검출 provenance, `tm_proposals.csv`는 검토 상태를 포함한 TM proposal audit, `tm_gt_verified.csv`는 현재 Final snapshot입니다. `unit_cells.csv`는 모든 pair와 검토 상태를 포함하고, `cation_sites.csv`에는 승인된 pair만 들어갑니다. boundary/pending/rejected pair를 자동 승인하지 않습니다. CSV는 UTF-8 BOM, 좌표는 소수 pixel입니다. GNN 입력을 만들 때 후보 label과 Cation Site label을 병합하지 마세요.

## 업데이트 / Proxy / 오프라인

앱을 닫고 `.hsgt`를 별도 백업한 다음:

```powershell
git pull --ff-only
.\install.ps1
.\run.ps1
```

Proxy는 조직에서 승인한 pip/Git 설정을 사용하세요. 인증정보가 들어 있는 proxy URL을 소스나 로그에 넣지 마세요. 앱 자체에는 네트워크 연결이 필요 없습니다.

인터넷 가능한 동일 Windows x64 / Python 3.12 PC에서 wheelhouse를 준비합니다:

```powershell
py -3.12 -m pip download -r requirements-windows.lock setuptools wheel -d D:\HAADF_Offline\wheels
```

회사로 전달 가능한 범용 source와 wheelhouse만 IT 승인 절차에 따라 옮긴 뒤:

```powershell
.\install.ps1 -Wheelhouse D:\HAADF_Offline\wheels
```

오프라인 설치는 Python과 Git 자체 설치를 포함하지 않습니다. Atomap optional dependencies도 별도 wheel 준비가 필요합니다. 오프라인/proxy 설치는 문서화했고 해당 회사 환경에서 검증하지 않았습니다.

## 개발 테스트

```powershell
.\install.ps1 -TestDependencies
.\.venv\Scripts\python.exe -m pytest -q -s
.\.venv\Scripts\python.exe tools\validate_workflow.py --output D:\HAADF_GT_Data\synthetic-validation
.\.venv\Scripts\python.exe tools\capture_demo.py --output D:\HAADF_GT_Data\screenshots
```

테스트는 normal, missing TM, phantom layer, surface distortion, tilted lattice, multiple grains, low SNR, no cation mixing, surface-only mixing, contrast mismatch를 생성합니다. Synthetic 정답은 자동 검증용 oracle이며 실제 연구 데이터 성능을 의미하지 않습니다. 회사 데이터나 내부 문서는 테스트 fixture에 넣지 마세요.
