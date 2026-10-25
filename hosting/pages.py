"""The pages: sign in, your bases, make one, delete one."""

import logging
import re
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from hosting import auth, config, db

bp = Blueprint("pages", __name__)
log = logging.getLogger("hosting")

# A DNS label: the base's subdomain.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,28}[a-z0-9]$")
RESERVED = {"www", "api", "app", "admin", "mail", "smtp", "traefik", "hosting", "status", "static"}


def provisioner():
    return current_app.config["PROVISIONER"]


def slug_problem(slug):
    if not SLUG_RE.match(slug) or "--" in slug:
        return "Use 3 to 30 lowercase letters, digits and dashes, starting with a letter."
    if slug in RESERVED:
        return "That name is reserved."
    return None


def human_bytes(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


@bp.app_template_filter("bytes")
def bytes_filter(n):
    return human_bytes(n) if n is not None else "not measured yet"


def render_index(error=None, created=None, status=200):
    account = auth.current_account()
    bases = []
    if account:
        for base in db.bases_of(account["id"]):
            bases.append({**base, "url": config.base_url(base["slug"]), "status": provisioner().status(base["slug"])})
    return (
        render_template(
            "index.html",
            account=account,
            bases=bases,
            error=error,
            created=created,
            csrf=auth.csrf_token(),
            domain=config.DOMAIN,
            dev_login=config.DEV_LOGIN,
            discord=bool(config.DISCORD_CLIENT_ID),
            max_bases=config.MAX_BASES_PER_ACCOUNT,
        ),
        status,
    )


@bp.route("/")
def index():
    return render_index()


@bp.route("/login/discord")
def discord_login():
    if not config.DISCORD_CLIENT_ID:
        abort(404)
    return redirect(auth.discord_authorize_url())


@bp.route("/login/discord/callback")
def discord_callback():
    try:
        identity = auth.discord_identity(request.args.get("code"), request.args.get("state"))
    except Exception:
        log.exception("Discord sign-in failed")
        identity = None
    if identity is None:
        return render_index(error="Signing in with Discord didn't work. Try again.", status=400)
    auth.sign_in(*identity)
    return redirect(url_for("pages.index"))


@bp.route("/login/dev", methods=["POST"])
def dev_login():
    if not config.DEV_LOGIN:
        abort(404)
    auth.check_csrf()
    name = request.form.get("name", "").strip()[:40]
    if not name:
        return render_index(error="Give a name.", status=400)
    auth.sign_in(f"dev:{name}", name)
    return redirect(url_for("pages.index"))


@bp.route("/logout", methods=["POST"])
def logout():
    auth.check_csrf()
    session.clear()
    return redirect(url_for("pages.index"))


@bp.route("/bases", methods=["POST"])
@auth.login_required
def create_base():
    auth.check_csrf()
    account = auth.current_account()
    slug = request.form.get("slug", "").strip().lower()
    problem = slug_problem(slug)
    if problem:
        return render_index(error=problem, status=400)
    api_key = secrets.token_hex(32)
    problem = db.claim_base(slug, account["id"], api_key)
    if problem:
        return render_index(error=problem, status=409)
    try:
        provisioner().create(slug, api_key)
        token = provisioner().create_admin(slug, account["name"])
    except Exception:
        log.exception("Couldn't start base %s", slug)
        provisioner().remove(slug)
        db.release_base(slug)
        return render_index(error="Your base couldn't be started. Try again later.", status=500)
    log.info("Base %s made for %s", slug, account["id"])
    # Shown on this response only; this app doesn't keep it.
    return render_index(created={"slug": slug, "url": config.base_url(slug), "token": token}, status=201)


@bp.route("/bases/<slug>/delete", methods=["POST"])
@auth.login_required
def delete_base(slug):
    auth.check_csrf()
    if db.owner_of(slug) != auth.current_account()["id"]:
        abort(404)
    if request.form.get("confirm", "").strip().lower() != slug:
        return render_index(error=f"Type {slug} to confirm deleting it.", status=400)
    provisioner().remove(slug)
    db.release_base(slug)
    log.info("Base %s deleted", slug)
    return redirect(url_for("pages.index"))
