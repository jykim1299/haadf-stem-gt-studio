"""Capture our own Qt widget rendering for reproducible visual inspection."""
import argparse
import json
from pathlib import Path
import tempfile

from PySide6.QtWidgets import QApplication
from haadf_studio.app import Studio, STYLE
from haadf_studio.science import synthetic

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
w = Studio(tempfile.mkdtemp(prefix="haadf-capture-"))
w.show()
app.processEvents()
w.attach_document(synthetic("missing_tm"))
app.processEvents()
w.grab().save(str(args.output / "studio-overview.png"))
w.camera.set(440,440,2.8)
app.processEvents()
w.grab().save(str(args.output / "studio-zoom.png"))
w.close()
