"""Entrypoint: python app.py serves on PORT (8000).

Raise (or lower) one base's disk quota; a base stopped for being over it
starts again at once if it now fits:
    python app.py set-quota <slug> <MB>
"""

import logging
import os
import sys

from waitress import serve

from hosting import activity, create_app, db, indexer, quota, stats

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    provisioner = app.config["PROVISIONER"]
    if len(sys.argv) == 4 and sys.argv[1] == "set-quota":
        if not db.set_quota(sys.argv[2], int(float(sys.argv[3]) * 1024**2)):
            raise SystemExit(f"No base named {sys.argv[2]!r}")
        quota.check(provisioner)
        sys.exit(0)
    indexer.start(provisioner)
    quota.start(provisioner)
    activity.start(provisioner)
    stats.start(provisioner)
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threads=8)
