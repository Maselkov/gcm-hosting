"""Entrypoint: python app.py serves on PORT (8000).

Raise (or lower) one base's disk quota; a base stopped for being over it
starts again at once if it now fits:
    python app.py set-quota <slug> <MB>
"""

import logging
import os
import sys
import threading

from waitress import serve

from hosting import activity, config, create_app, db, indexer, quota, stats, upgrade

log = logging.getLogger("hosting")


def warm_up(provisioner):
    """Pulls IMAGE, then starts the indexer with it, then the upgrades."""
    if provisioner.pull_image():
        log.info("Pulled %s", config.IMAGE)
    else:
        log.warning("Couldn't pull %s: using the local copy, if there is one", config.IMAGE)
    indexer.start(provisioner)
    upgrade.start(provisioner)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    provisioner = app.config["PROVISIONER"]
    if len(sys.argv) == 4 and sys.argv[1] == "set-quota":
        if not db.set_quota(sys.argv[2], int(float(sys.argv[3]) * 1024**2)):
            raise SystemExit(f"No base named {sys.argv[2]!r}")
        quota.check(provisioner)
        sys.exit(0)
    # The image first, so the indexer and new bases don't run a stale
    # cached one; in the background, as a new image takes a while.
    threading.Thread(target=warm_up, args=(provisioner,), name="pull", daemon=True).start()
    quota.start(provisioner)
    activity.start(provisioner)
    stats.start(provisioner)
    # Keep Traefik's X-Forwarded-* for ProxyFix (hosting/__init__.py):
    # waitress drops them otherwise, and Discord's redirect_uri came out http.
    serve(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        threads=8,
        clear_untrusted_proxy_headers=False,
    )
