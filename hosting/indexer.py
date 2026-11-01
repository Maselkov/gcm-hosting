"""Keeps the shared game data (GAMEDATA_SHARED_DIR) downloaded and
indexed.

With GAMEDATA_DOWNLOAD, every version GAMEDATA_REPO publishes is fetched
into the shared folder, every texture set, and fetched again when its
release is rebuilt: at startup and every GAMEDATA_INDEX_SECONDS. So a base
never downloads its own, whatever version its admin picks.

A base reads a version's indexes (gcm/gamedata_index.py and, for the
pattern check, gcm/recipe_index.py in GTNH Craft Monitor) from the shared
folder when they're there and current; otherwise it builds its own copies
in its own volume - disk, a few seconds and ~200 MB of memory while
building each, for every base, which for the recipe index is more than a
base's MEMORY_LIMIT. So this builds them in the shared folder instead, with the bases' own image (an index's format
belongs to the server version that reads it): at startup, every
GAMEDATA_INDEX_SECONDS, after a download, before a base is made or
recreated, and from the control panel. Versions already indexed are only
checked, which is quick."""

import logging
import threading
import time

from hosting import config

log = logging.getLogger("hosting.indexer")

_run_lock = threading.Lock()  # one run at a time
_download_lock = threading.Lock()  # one download at a time
_lock = threading.Lock()  # _last and _last_download; never held while running
_last = {"at": None, "result": None}
_last_download = {"at": None, "result": None}


def enabled():
    return bool(config.GAMEDATA_SHARED_DIR)


def downloading():
    return enabled() and config.GAMEDATA_DOWNLOAD


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


def download(provisioner):
    """Downloads every published version that's missing or rebuilt, if
    that's on. Returns the result ({versions: [...]} or {error}), or None
    when it's off. Doesn't index: run() after."""
    if not downloading():
        return None
    with _download_lock:
        started = time.monotonic()
        try:
            result = provisioner.download_shared_gamedata()
        except Exception as e:
            log.exception("Downloading the shared game data failed")
            result = {"error": str(e)}
        with _lock:
            _last_download.update(at=time.time(), result=result)
    fetched = [v["version"] for v in result.get("versions", []) if v["state"] == "downloaded"]
    failed = [v["version"] for v in result.get("versions", []) if v["state"] == "failed"]
    if fetched or failed or result.get("error"):
        log.info(
            "Shared game data downloaded in %.1fs: fetched %s, failed %s%s",
            time.monotonic() - started,
            fetched or "none",
            failed or "none",
            f", error: {result['error']}" if result.get("error") else "",
        )
    return result


def downloaded_any(result):
    return any(v["state"] == "downloaded" for v in (result or {}).get("versions", []))


def last():
    """{at, result} of the last run."""
    with _lock:
        return dict(_last)


def last_download():
    """{at, result} of the last download."""
    with _lock:
        return dict(_last_download)


def reset():
    with _lock:
        _last.update(at=None, result=None)
        _last_download.update(at=None, result=None)


def start(provisioner):
    if not enabled():
        return

    def loop():
        while True:
            # What's there is indexed first: downloading can take minutes.
            run(provisioner)
            if downloaded_any(download(provisioner)):
                run(provisioner)
            time.sleep(config.GAMEDATA_INDEX_SECONDS)

    threading.Thread(target=loop, name="indexer", daemon=True).start()
