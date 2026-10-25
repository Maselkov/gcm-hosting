"""Keeps every base on the newest IMAGE. Every UPGRADE_CHECK_SECONDS (and
at startup) IMAGE is pulled, and a base whose container runs an older
image than the one the tag now points at is recreated on it, keeping its
data - the control panel's Recreate, done by itself. With the default
IMAGE, :latest, that's every GTNH Craft Monitor release.

Compared by image ID, not name: a moved tag (latest, 6.1) keeps its name.
The shared game data is indexed with the new image first, so the bases
don't each build their own; a base stopped on purpose is recreated and
left stopped. Its players see the base restart: a few seconds."""

import logging
import threading
import time

from hosting import config, db, indexer

log = logging.getLogger("hosting.upgrade")


def recreate(provisioner, base):
    """A new container for base on the current IMAGE, on the same data;
    stopped again if it was stopped on purpose."""
    provisioner.recreate(base["slug"], base["api_key"])
    if base["suspended_at"] or base["held_at"]:
        provisioner.stop(base["slug"])


def check(provisioner):
    """Pulls IMAGE and recreates the bases on an older one. Returns the
    slugs recreated."""
    if not provisioner.pull_image():
        return []
    current = provisioner.image_id()
    if not current:
        return []
    details = provisioner.details()
    outdated = [
        base for base in db.all_bases() if (details.get(base["slug"]) or {}).get("image_id") not in (None, current)
    ]
    if not outdated:
        return []
    indexer.run(provisioner)
    upgraded = []
    for base in outdated:
        try:
            log.info("Recreating %s on the new %s", base["slug"], config.IMAGE)
            recreate(provisioner, base)
            upgraded.append(base["slug"])
        except Exception:
            log.exception("Upgrading %s failed", base["slug"])
    return upgraded


def start(provisioner):
    if not config.UPGRADE_CHECK_SECONDS:
        return

    def loop():
        while True:
            try:
                check(provisioner)
            except Exception:
                log.exception("Upgrade check failed")
            time.sleep(config.UPGRADE_CHECK_SECONDS)

    threading.Thread(target=loop, name="upgrade", daemon=True).start()
