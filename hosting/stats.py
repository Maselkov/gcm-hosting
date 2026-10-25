"""Live CPU and memory of every base's container, for the control panel
(hosting/admin.py). A background thread reads each running base every
STATS_SECONDS; CPU is the share of one core it used between its last two
readings, so a base shows none until it's been read twice."""

import logging
import threading
import time

from hosting import config, db

log = logging.getLogger("hosting.stats")

_lock = threading.Lock()
# slug -> the last reading (provision.sample()'s tuple) and when it was taken
_last = {}
# slug -> {cpu_percent, memory_bytes, memory_limit_bytes, at}
_current = {}


def reset():
    with _lock:
        _last.clear()
        _current.clear()


def sample_all(provisioner):
    """Reads every base once. Bases gone since are forgotten."""
    slugs = [b["slug"] for b in db.all_bases()]
    for slug in slugs:
        try:
            reading = provisioner.sample(slug)
        except Exception:
            log.exception("Reading %s's stats failed", slug)
            reading = None
        with _lock:
            if reading is None:
                _last.pop(slug, None)
                _current.pop(slug, None)
                continue
            cpu_total, system_total, cpus, memory, limit = reading
            previous = _last.get(slug)
            cpu_percent = None
            if previous and system_total > previous[1]:
                # Of one core: 100% is a whole CPU's worth, as `docker stats`.
                cpu_percent = (cpu_total - previous[0]) / (system_total - previous[1]) * cpus * 100
            _last[slug] = reading
            _current[slug] = {
                "cpu_percent": cpu_percent,
                "memory_bytes": memory,
                "memory_limit_bytes": limit,
                "at": time.time(),
            }
    with _lock:
        for gone in set(_current) - set(slugs):
            _current.pop(gone, None)
            _last.pop(gone, None)


def current():
    """{slug: {cpu_percent, memory_bytes, memory_limit_bytes, at}} for the
    running bases; cpu_percent None until a base's second reading."""
    with _lock:
        return {slug: dict(v) for slug, v in _current.items()}


def start(provisioner):
    def loop():
        while True:
            try:
                sample_all(provisioner)
            except Exception:
                log.exception("Reading stats failed")
            time.sleep(config.STATS_SECONDS)

    threading.Thread(target=loop, name="stats", daemon=True).start()
