"""Keeps the shared game data (GAMEDATA_SHARED_DIR) indexed. A base reads
a version's index (gcm/gamedata_index.py in GTNH Craft Monitor) from the
shared folder when one is there and current; otherwise it builds its own
copy in its own volume - disk, a few seconds of startup and ~200 MB of
memory while building, for every base. So this builds them in the shared
folder instead, with the bases' own image (an index's format belongs to
the server version that reads it): at startup, every
GAMEDATA_INDEX_SECONDS, before a base is made or recreated, and from the
control panel. Versions already indexed are only checked, which is quick."""

import logging
import threading
import time

from hosting import config

log = logging.getLogger("hosting.indexer")

_run_lock = threading.Lock()  # one run at a time
_lock = threading.Lock()  # _last; never held while running
_last = {"at": None, "result": None}


def enabled():
    return bool(config.GAMEDATA_SHARED_DIR)


def run(provisioner):
    """Indexes what needs it, if there's a shared folder. Returns the
    result ({versions: [...]} or {error}), or None without one."""
    if not enabled():
        return None
    with _run_lock:
        started = time.monotonic()
        try:
            result = provisioner.index_shared_gamedata()
        except Exception as e:
            log.exception("Indexing the shared game data failed")
            result = {"error": str(e)}
        with _lock:
            _last.update(at=time.time(), result=result)
    built = [v["version"] for v in result.get("versions", []) if v["state"] == "built"]
    failed = [v["version"] for v in result.get("versions", []) if v["state"] == "failed"]
    if built or failed or result.get("error"):
        log.info(
            "Shared game data indexed in %.1fs: built %s, failed %s%s",
            time.monotonic() - started,
            built or "none",
            failed or "none",
            f", error: {result['error']}" if result.get("error") else "",
        )
    return result


def last():
    """{at, result} of the last run."""
    with _lock:
        return dict(_last)


def reset():
    with _lock:
        _last.update(at=None, result=None)


def start(provisioner):
    if not enabled():
        return

    def loop():
        while True:
            run(provisioner)
            time.sleep(config.GAMEDATA_INDEX_SECONDS)

    threading.Thread(target=loop, name="indexer", daemon=True).start()
