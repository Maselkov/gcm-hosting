"""When each base's game last reported, for reclaiming slots: a base the
game never reported to is removed NEVER_CONNECTED_DAYS after it was made
(it holds nothing: no history, no users but its owner), and one it
stopped reporting to INACTIVE_DAYS ago is flagged in the control panel
and on its owner's card. Nothing that ever had data is removed by itself.

Checked every ACTIVITY_CHECK_SECONDS, only for running bases, and a base
is only removed when it was just asked and answered "never": a check
that failed, or a base that's stopped or crashed, keeps it."""

import logging
import threading
import time

from hosting import config, db

log = logging.getLogger("hosting.activity")

DAY = 86400


def check(provisioner):
    """Records each running base's last report and removes the
    never-connected ones past their time. Returns the slugs removed."""
    removed = []
    for base in db.all_bases():
        try:
            if _check_one(provisioner, base):
                removed.append(base["slug"])
        except Exception:
            log.exception("Activity check of %s failed", base["slug"])
    return removed


def _check_one(provisioner, base):
    """Returns True if it removed the base."""
    slug = base["slug"]
    if base["held_at"] or provisioner.status(slug) != "running":
        return False
    reported = provisioner.last_report(slug)
    if reported is not None:
        if reported != base["last_report_at"]:
            db.record_report(slug, reported)
        return False
    if base["last_report_at"] is not None or not never_connected_past(base):
        return False
    log.warning("Removing %s: the game never reported to it in %s days", slug, config.NEVER_CONNECTED_DAYS)
    provisioner.remove(slug)
    db.release_base(slug)
    return True


def never_connected_past(base):
    return bool(config.NEVER_CONNECTED_DAYS) and time.time() - base["created_at"] > config.NEVER_CONNECTED_DAYS * DAY


def removal_due(base):
    """When a base the game hasn't reported to yet will be removed, or
    None."""
    if base["last_report_at"] is not None or not config.NEVER_CONNECTED_DAYS:
        return None
    return base["created_at"] + config.NEVER_CONNECTED_DAYS * DAY


def inactive_days(base):
    """Whole days since the game last reported, once that's past
    INACTIVE_DAYS; otherwise None."""
    if base["last_report_at"] is None or not config.INACTIVE_DAYS:
        return None
    days = (time.time() - base["last_report_at"]) / DAY
    return int(days) if days >= config.INACTIVE_DAYS else None


def start(provisioner):
    def loop():
        while True:
            try:
                check(provisioner)
            except Exception:
                log.exception("Activity check failed")
            time.sleep(config.ACTIVITY_CHECK_SECONDS)

    threading.Thread(target=loop, name="activity", daemon=True).start()
