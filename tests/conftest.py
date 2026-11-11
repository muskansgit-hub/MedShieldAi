import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MEDSHIELD_DB", os.path.join(tempfile.mkdtemp(), "test_audit.db"))
os.environ["MEDSHIELD_JUDGE"] = ""
