import os
import sys
from pathlib import Path

# Make version2 modules importable and force offline mode: tests never call the API.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "version2"))
os.environ["ESCAPE_ROOM_OFFLINE"] = "1"
