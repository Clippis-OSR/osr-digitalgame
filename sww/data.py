\
import json
import hashlib
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def load_json(name: str):
    p = DATA_DIR / name
    return json.loads(p.read_text(encoding="utf-8"))


def read_text(name: str) -> str:
    p = DATA_DIR / name
    return p.read_text(encoding="utf-8")


def sha256_text(name: str) -> str:
    """Stable content hash for data lockdown / save compatibility checks."""
    txt = read_text(name)
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()
