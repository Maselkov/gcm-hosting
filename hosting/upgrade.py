"""Keeps every base on the newest IMAGE. Every UPGRADE_CHECK_SECONDS (and
at startup) IMAGE is pulled, and a base whose container runs an older
image than the one the tag now points at is recreated on it, keeping its
data - the control panel's Recreate, done by itself. With the default
IMAGE, :latest, that's every GTNH Craft Monitor release. The control
panel's Check now runs the same check on request, and its Recreate all
recreates every base whatever its image (to pick up new labels, say).

Compared by image ID, not name: a moved tag (latest, 6.1) keeps its name.
The shared game data is indexed with the new image first, so the bases
don't each build their own; a base stopped on purpose is recreated and
left stopped. Its players see the base restart: a few seconds."""

import logging
import threading
import time

from hosting import config, db, indexer

log = logging.getLogger("hosting.upgrade")

_lock = threading.Lock()  # one check at a time: the loop's or the panel's


def recreate(provisioner, base):
    """A new container for base on the current IMAGE, on the same data;
    stopped again if it was stopped on purpose."""
    provisioner.recreate(base["slug"], base["api_key"])
    if base["suspended_at"] or base["held_at"]:
        provisioner.stop(base["slug"])


def check(provisioner):
    """Pulls IMAGE and recreates the bases on an older one. Returns the
    slugs recreated, or None if IMAGE couldn't be pulled."""
    with _lock:
        return _check(provisioner)


def _check(provisioner):
    if not provisioner.pull_image():
        return None
    current = provisioner.image_id()
    if not current:
        return None
    details = provisioner.details()
    outdated = [
        base for base in db.all_bases() if (details.get(base["slug"]) or {}).get("image_id") not in (None, current)
    ]
    if not outdated:
        return []
    return _recreate_each(provisioner, outdated)


def recreate_all(provisioner):
    """Recreates every base on the current IMAGE, as it was pulled last,
    whether or not it's already on it: how a change to the containers
    themselves (their labels, limits) reaches the bases already made.
    Returns the slugs recreated."""
    with _lock:
        bases = db.all_bases()
        return _recreate_each(provisioner, bases) if bases else []


def _recreate_each(provisioner, bases):
    indexer.run(provisioner)
    recreated = []
    for base in bases:
        try:
            log.info("Recreating %s on %s", base["slug"], config.IMAGE)
            recreate(provisioner, base)
            recreated.append(base["slug"])
        except Exception:
            log.exception("Recreating %s failed", base["slug"])
    return recreated


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
