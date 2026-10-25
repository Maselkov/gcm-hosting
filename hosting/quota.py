"""Per-base disk quotas. Every base's data volume shares the host's disk,
and Docker can't cap a volume's size, so this measures them instead:
a base over its quota is stopped, and its card says so, until the quota
is raised (python app.py set-quota) or the base is deleted. Between
checks a base can still go over; keeping Docker's volumes on their own
partition stops that from reaching the host itself (README.md)."""

import logging
import threading
import time

from hosting import config, db

log = logging.getLogger("hosting.quota")


def check(provisioner):
    """Measures every base, stops the ones over quota and starts the
    stopped ones back up that fit again - unless the host stopped them
    too (the control panel). Returns the slugs stopped."""
    sizes = provisioner.volume_sizes()
    stopped = []
    for base in db.all_bases():
        try:
            if _check_one(provisioner, base, sizes):
                stopped.append(base["slug"])
        except Exception:
            log.exception("Quota check of %s failed", base["slug"])
    return stopped


def _check_one(provisioner, base, sizes):
    """Returns True if it stopped the base."""
    slug = base["slug"]
    size = sizes.get(slug, base["size_bytes"])
    suspended_at = base["suspended_at"]
    over = size is not None and size > base["quota_bytes"]
    stopping = over and suspended_at is None
    if stopping:
        log.warning("Stopping %s: %s bytes, over its quota of %s", slug, size, base["quota_bytes"])
        provisioner.stop(slug)
        suspended_at = time.time()
    elif not over and suspended_at is not None:
        # Fits again. Stopped by the host too, it stays stopped.
        if base["held_at"] is None:
            log.info("Starting %s again: %s bytes fits its quota of %s", slug, size, base["quota_bytes"])
            provisioner.start(slug)
        suspended_at = None
    db.record_size(slug, size, suspended_at)
    return stopping


def start(provisioner):
    def loop():
        while True:
            try:
                check(provisioner)
            except Exception:
                log.exception("Quota check failed")
            time.sleep(config.QUOTA_CHECK_SECONDS)

    threading.Thread(target=loop, name="quota", daemon=True).start()
