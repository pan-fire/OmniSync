#!/usr/bin/env python3
"""Write the backend's OpenAPI schema to frontend/openapi.json.

Run from the repository root with the backend's dependencies installed:

    PYTHONPATH=. python scripts/gen-openapi.py

then, in frontend/, ``pnpm gen:types`` turns it into src/types/api.gen.ts.
CI runs both and fails when a committed file differs, so a change to the
backend's request or response models must come with regenerated files
(frontend/src/__tests__/api-types.contract.test.ts then checks the
hand-written types in src/types/index.ts against them).

The schema comes from ``backend.main.app.openapi()``; nothing is started
and no route is served (the app serves /openapi.json only with
OMNISYNC_API_DOCS=1). ``info.version`` is left as a placeholder: it follows
the VERSION file, which changes with every release and not with the API.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "frontend" / "openapi.json"


def main() -> int:
    # Importing backend.main sets up logging to OMNISYNC_LOG_PATH; keep it
    # out of /data and the repository.
    os.environ.setdefault("OMNISYNC_LOG_PATH", str(Path(tempfile.gettempdir()) / "omnisync-gen-openapi.log"))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.main import app

    schema = app.openapi()
    schema["info"]["version"] = "see VERSION"
    text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv[1:]:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date: run scripts/gen-openapi.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(schema['paths'])} paths, "
          f"{len(schema['components']['schemas'])} schemas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
