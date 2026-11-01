"""The host's control panel, /admin: every base at a glance (state, CPU,
memory, disk against quota) with totals for the host, and per base its
logs and what can be done to it - start, stop, restart, recreate on the
current image, reset an admin's sign-in token, set its quota, delete it.
Only for accounts in ADMIN_IDENTITIES; anyone else gets a 404."""

import datetime
import logging
import re
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from hosting import activity, auth, config, db, indexer, provision, quota, stats, upgrade

bp = Blueprint("admin", __name__, url_prefix="/admin")
log = logging.getLogger("hosting.admin")

SORTS = {
    "name": lambda b: b["slug"],
    "cpu": lambda b: -(b["cpu_percent"] or 0),
    "memory": lambda b: -(b["memory_bytes"] or 0),
    "disk": lambda b: -(b["disk_percent"] or 0),
    "owner": lambda b: b["owner_name"].lower(),
}
WARN_AT = 0.8  # of a limit: the row is flagged
# A base's new name (rename): one DNS label, at most 40 characters.
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


def provisioner():
    return current_app.config["PROVISIONER"]


def _percent(used, limit):
    return used / limit * 100 if used is not None and limit else None


def _uptime(started_at):
    if not started_at:
        return None
    seconds = time.time() - provision.started_timestamp(started_at)
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rest // 60}m"


def _rows():
    details = provisioner().details()
    current = provisioner().image_id()
    live = stats.current()
    rows = []
    for base in db.all_bases():
        slug = base["slug"]
        container = details.get(slug) or {}
        reading = live.get(slug) or {}
        status = container.get("status") or "missing"
        if base["suspended_at"]:
            state = "over quota"
        elif base["held_at"]:
            state = "stopped by host"
        else:
            state = status
        row = {
            **base,
            "url": config.base_url(slug),
            "status": status,
            "state": state,
            "image": container.get("image"),
            "version": container.get("version"),
            # By ID: a moved tag (latest) keeps its name.
            "image_current": container.get("image_id") == current
            if current
            else container.get("image") == config.IMAGE,
            "uptime": _uptime(container.get("started_at")),
            "restarts": container.get("restarts", 0),
            "oom_killed": container.get("oom_killed", False),
            "cpu_percent": reading.get("cpu_percent"),
            "cpu_limit_percent": container.get("cpu_limit_percent"),
            "memory_bytes": reading.get("memory_bytes"),
            "memory_limit_bytes": reading.get("memory_limit_bytes"),
            "memory_percent": _percent(reading.get("memory_bytes"), reading.get("memory_limit_bytes")),
            "disk_percent": _percent(base["size_bytes"], base["quota_bytes"]),
            "inactive_days": activity.inactive_days(base),
            "removal_due": activity.removal_due(base),
        }
        row["warnings"] = [
            text
            for text, on in (
                ("memory near its limit", (row["memory_percent"] or 0) >= WARN_AT * 100),
                (
                    "CPU at its limit",
                    row["cpu_limit_percent"] and (row["cpu_percent"] or 0) >= WARN_AT * row["cpu_limit_percent"],
                ),
                ("disk near its quota", (row["disk_percent"] or 0) >= WARN_AT * 100 and not base["suspended_at"]),
                ("was killed for memory", row["oom_killed"]),
                (f"restarted {row['restarts']}x", row["restarts"]),
                ("old image", container.get("image") and not row["image_current"]),
                ("not running", state == "exited" or status == "missing"),
                (f"no game report for {row['inactive_days']} days", row["inactive_days"]),
                (
                    "game never reported",
                    row["removal_due"] and time.time() - base["created_at"] > activity.DAY,
                ),
            )
            if on
        ]
        rows.append(row)
    return rows


def _totals(rows):
    host = provisioner().host()
    running = [r for r in rows if r["status"] == "running"]
    return {
        "bases": len(rows),
        "running": len(running),
        "over_quota": sum(1 for r in rows if r["suspended_at"]),
        "held": sum(1 for r in rows if r["held_at"]),
        "flagged": sum(1 for r in rows if r["warnings"]),
        "accounts": db.count_accounts(),
        "max_bases": config.MAX_BASES,
        "cpu_percent": sum(r["cpu_percent"] or 0 for r in running),
        "cpu_capacity_percent": (host.get("cpus") or 1) * 100,
        "cpus": host.get("cpus"),
        "memory_bytes": sum(r["memory_bytes"] or 0 for r in running),
        "memory_limits_bytes": sum(r["memory_limit_bytes"] or 0 for r in running),
        "host_memory_bytes": host.get("memory_bytes"),
        "disk_bytes": sum(r["size_bytes"] or 0 for r in rows),
        "quota_bytes": sum(r["quota_bytes"] for r in rows),
        "outdated": sum(1 for r in rows if r["image"] and not r["image_current"]),
    }


def _refresh():
    """Seconds for the page to reload itself after (?refresh=), or None."""
    try:
        seconds = int(request.args.get("refresh", ""))
    except ValueError:
        return None
    return max(5, min(seconds, 300))


@bp.route("/")
@auth.operator_required
def overview():
    rows = _rows()
    sort = request.args.get("sort", "name")
    rows.sort(key=SORTS.get(sort, SORTS["name"]))
    return render_template(
        "admin.html",
        rows=rows,
        totals=_totals(rows),
        sort=sort,
        refresh=_refresh(),
        image=config.IMAGE,
        upgrade_check_seconds=config.UPGRADE_CHECK_SECONDS,
        stats_seconds=config.STATS_SECONDS,
        gamedata=indexer.last() if indexer.enabled() else None,
        gamedata_download=indexer.last_download() if indexer.downloading() else None,
        gamedata_repo=config.GAMEDATA_REPO,
        gamedata_index_seconds=config.GAMEDATA_INDEX_SECONDS,
        quota_check_seconds=config.QUOTA_CHECK_SECONDS,
        csrf=auth.csrf_token(),
        account=auth.current_account(),
    )


@bp.route("/gamedata/index", methods=["POST"])
@auth.operator_required
def index_gamedata():
    auth.check_csrf()
    result = indexer.run(provisioner())
    if result is None:
        flash("There's no shared game data: GAMEDATA_SHARED_DIR isn't set.")
    elif result.get("error"):
        flash(f"Indexing failed: {result['error']}")
    else:
        built = [v["version"] for v in result["versions"] if v["state"] == "built"]
        flash(f"Indexed {', '.join(built)}." if built else "Every version was already indexed.")
    log.info("shared game data indexed (by %s)", auth.current_account()["identity"])
    return redirect(url_for("admin.overview"))


def _row(slug):
    row = next((r for r in _rows() if r["slug"] == slug), None)
    if row is None:
        abort(404)
    return row


def _render_base(slug, token=None, status=200):
    row = _row(slug)
    return (
        render_template(
            "admin_base.html",
            base=row,
            logs=provisioner().logs(slug),
            gamedata=provisioner().gamedata(slug),
            token=token,
            image=config.IMAGE,
            default_quota_bytes=config.QUOTA_BYTES,
            refresh=_refresh(),
            csrf=auth.csrf_token(),
            domain=config.DOMAIN,
            account=auth.current_account(),
        ),
        status,
    )


@bp.route("/bases/<slug>")
@auth.operator_required
def base_page(slug):
    return _render_base(slug)


def _action(slug):
    """A POSTed action's base, after the checks every action makes."""
    auth.check_csrf()
    base = db.base(slug)
    if base is None:
        abort(404)
    return base


def _done(slug, message):
    log.info("%s: %s (by %s)", slug, message, auth.current_account()["identity"])
    flash(message)
    return redirect(url_for("admin.base_page", slug=slug))


@bp.route("/bases/<slug>/start", methods=["POST"])
@auth.operator_required
def start(slug):
    base = _action(slug)
    if base["suspended_at"]:
        flash("It's over its quota: raise the quota to start it.")
        return redirect(url_for("admin.base_page", slug=slug))
    db.set_held(slug, None)
    provisioner().start(slug)
    return _done(slug, "Started.")


@bp.route("/bases/<slug>/stop", methods=["POST"])
@auth.operator_required
def stop(slug):
    _action(slug)
    db.set_held(slug, time.time())
    provisioner().stop(slug)
    return _done(slug, "Stopped. It stays stopped until it's started here.")


@bp.route("/bases/<slug>/restart", methods=["POST"])
@auth.operator_required
def restart(slug):
    base = _action(slug)
    if base["suspended_at"] or base["held_at"]:
        flash("It's stopped on purpose: start it instead.")
        return redirect(url_for("admin.base_page", slug=slug))
    provisioner().restart(slug)
    return _done(slug, "Restarted.")


@bp.route("/upgrade", methods=["POST"])
@auth.operator_required
def upgrade_now():
    auth.check_csrf()
    upgraded = upgrade.check(provisioner())
    if upgraded is None:
        flash(f"Couldn't pull {config.IMAGE}: nothing was upgraded.")
    elif upgraded:
        flash(f"Recreated {', '.join(upgraded)} on the new {config.IMAGE}.")
    else:
        flash(f"Every base is already on the newest {config.IMAGE}.")
    log.info("upgrade check (by %s): %s", auth.current_account()["identity"], upgraded)
    return redirect(url_for("admin.overview"))


@bp.route("/bases/<slug>/recreate", methods=["POST"])
@auth.operator_required
def recreate(slug):
    base = _action(slug)
    # A new image may read a new index format: built in the shared folder
    # first, so the base doesn't build its own.
    indexer.run(provisioner())
    upgrade.recreate(provisioner(), base)
    return _done(slug, f"Recreated from {config.IMAGE}, keeping its data.")


@bp.route("/bases/<slug>/reset-token", methods=["POST"])
@auth.operator_required
def reset_token(slug):
    _action(slug)
    if provisioner().status(slug) != "running":
        flash("Start it first: the token is made inside the base.")
        return redirect(url_for("admin.base_page", slug=slug))
    name = request.form.get("name", "").strip()[:60]
    try:
        token = provisioner().reset_admin_token(slug, name)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("admin.base_page", slug=slug))
    except Exception:
        log.exception("Resetting %s's admin token failed", slug)
        flash("Resetting the token failed; its logs may say why.")
        return redirect(url_for("admin.base_page", slug=slug))
    log.info("%s: admin token reset for %s (by %s)", slug, token["name"], auth.current_account()["identity"])
    # Shown on this response only, never stored or flashed.
    return _render_base(slug, token=token)


@bp.route("/bases/<slug>/quota", methods=["POST"])
@auth.operator_required
def set_quota(slug):
    _action(slug)
    raw = request.form.get("quota_mb", "").strip()
    if raw:
        try:
            mb = float(raw)
        except ValueError:
            mb = -1
        if mb <= 0:
            flash("Give the quota in MB, or leave it empty for the default.")
            return redirect(url_for("admin.base_page", slug=slug))
        db.set_quota(slug, int(mb * 1024**2))
    else:
        db.set_quota(slug, None)
    # At once, rather than at the next check: it may start it again.
    quota.check(provisioner())
    return _done(slug, f"Quota set to {raw} MB." if raw else "Quota back to the default.")


def name_problem(name):
    if not NAME_RE.match(name) or name.startswith("xn--"):
        return "Use 1 to 40 lowercase letters, digits and dashes, not starting or ending with a dash."
    if name in config.RESERVED_NAMES:
        return f"{name} is reserved (RESERVED_NAMES)."
    if db.base(name) is not None:
        return db.NAME_TAKEN
    return None


@bp.route("/bases/<slug>/rename", methods=["POST"])
@auth.operator_required
def rename(slug):
    base = _action(slug)
    new = request.form.get("name", "").strip().lower()
    if new == slug:
        return redirect(url_for("admin.base_page", slug=slug))
    problem = name_problem(new)
    if problem:
        flash(problem)
        return redirect(url_for("admin.base_page", slug=slug))
    try:
        provisioner().rename(slug, new, base["api_key"])
    except Exception:
        log.exception("Renaming %s to %s failed", slug, new)
        flash(f"Renaming failed, so it's still {slug}; its logs may say why.")
        return redirect(url_for("admin.base_page", slug=slug))
    db.rename_base(slug, new)
    stats.reset()
    return _done(new, f"Renamed from {slug}: it's now at {config.base_url(new)}. Point its computers there.")


@bp.route("/bases/<slug>/delete", methods=["POST"])
@auth.operator_required
def delete(slug):
    _action(slug)
    if request.form.get("confirm", "").strip().lower() != slug:
        flash(f"Type {slug} to confirm deleting it.")
        return redirect(url_for("admin.base_page", slug=slug))
    provisioner().remove(slug)
    db.release_base(slug)
    log.info("%s: deleted (by %s)", slug, auth.current_account()["identity"])
    flash(f"Deleted {slug} and all its data.")
    return redirect(url_for("admin.overview"))


@bp.app_template_filter("percent")
def percent_filter(value):
    return "–" if value is None else f"{value:.0f}%"


@bp.app_template_filter("when")
def when_filter(timestamp):
    if not timestamp:
        return ""
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
