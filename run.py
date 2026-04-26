"""Direct entry point. Run from anywhere:

    python C:/path/to/vellum/run.py

Sets VELLUM_DB_PATH to an absolute path so the database is always
stored at a fixed location regardless of the working directory.
"""

import sys
import os

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Ensure database path is always absolute
_default_db = os.path.join(_project_root, "vellum.db")
os.environ.setdefault("VELLUM_DB_PATH", _default_db)

from vellum.server import main

main()
