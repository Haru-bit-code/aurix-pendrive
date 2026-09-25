"""Loads config.json and resolves paths relative to the pendrive root."""
import json
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent          # <drive>/aurix
ROOT = PKG_DIR.parent                               # <drive>

with open(PKG_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

WORKSPACE = (ROOT / CONFIG["workspace"]).resolve()
DB_PATH = (ROOT / CONFIG["database"]).resolve()
WEB_DIR = PKG_DIR / "web"

WORKSPACE.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
