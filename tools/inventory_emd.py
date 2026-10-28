"""Read-only inventory. Magnification hints never automatically imply target eligibility."""
import argparse
import json
from pathlib import Path

from haadf_studio.storage import external_path, inspect_emd, write_csv

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve(strict=True)
output = external_path(args.output)
output.parent.mkdir(parents=True,exist_ok=True)
rows = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.suffix.lower() != ".emd":
        continue
    record = {"relative_path":str(path.relative_to(root)),"sample":str(path.parent.relative_to(root)),
              "bytes":path.stat().st_size,"datasets":"", "magnification_hints":"",
              "filename_3_90_hint":"3.90" in path.name, "magnification_status":"unknown",
              "target_3_90_mx":False,"error":""}
    try:
        info = inspect_emd(path)
        record["datasets"] = json.dumps(info["datasets"])
        record["magnification_hints"] = json.dumps({f"{obj}/{key}":value for obj, attrs in info["attributes"].items()
            for key, value in attrs.items() if "magnification" in key.lower()},ensure_ascii=False)
    except Exception as exc:
        record["error"] = str(exc)
    rows.append(record)
fields = ["relative_path","sample","bytes","datasets","magnification_hints","filename_3_90_hint",
          "magnification_status","target_3_90_mx","error"]
write_csv(output,rows,fields)
print(f"Inventoried {len(rows)} EMD files. All magnifications remain unverified; review source evidence in Studio.")
