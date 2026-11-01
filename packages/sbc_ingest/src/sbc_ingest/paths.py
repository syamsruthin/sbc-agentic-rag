"""Where the SBC data lives.

The pipeline reads and writes ``data/`` under the project root and records paths relative to it (for example
``source_pdf: data/raw/...``). The project root is the current directory unless ``SBC_PROJECT_ROOT`` is set.
"""

import os
from pathlib import Path

ROOT = Path(os.environ.get("SBC_PROJECT_ROOT", ".")).resolve()
