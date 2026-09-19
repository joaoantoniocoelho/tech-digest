import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
root = str(ROOT)

if root not in sys.path:
    sys.path.insert(0, root)

venv_lib = ROOT / ".venv" / "lib"
if venv_lib.is_dir():
    for site_packages in sorted(venv_lib.glob("python*/site-packages")):
        path = str(site_packages)
        if path not in sys.path:
            sys.path.insert(0, path)

os.chdir(ROOT)
