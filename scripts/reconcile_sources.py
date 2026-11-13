"""Read-only legacy source audit. Does not initialize or modify the DB/index."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.document_access import reconcile_sources  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile_sources(json.loads(Path(args.chunks).read_text())),
                     ensure_ascii=False, indent=2))
