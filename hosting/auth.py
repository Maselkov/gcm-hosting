"""Signing in: Discord OAuth2 (identify scope only), or by name with
DEV_LOGIN. The signed-in account is session["account_id"]; every form
carries session["csrf"]."""

import functools
import hmac
import json
import secrets
import urllib.parse
import urllib.request

from flask import abort, g, redirect, request, session, url_for

from hosting import config, db


def current_account():
    if "account" not in g:
        account_id = session.get("account_id")
        g.account = db.account(account_id) if account_id else None
    return g.account


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_account() is None:
            return redirect(url_for("pages.index"))
        return view(*args, **kwargs)

    return wrapped


def is_operator(account):
    return account is not None and account.get("identity") in config.ADMIN_IDENTITIES


def operator_required(view):
    """The control panel's pages: 404 for anyone else, so it isn't
    advertised."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_operator(current_account()):
            abort(404)
        return view(*args, **kwargs)

    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf():
    supplied = request.form.get("csrf", "")
    expected = session.get("csrf", "")
    # Signing in clears the session, so there may be no token yet: an
    # empty one must never match.
    if not expected or not hmac.compare_digest(supplied, expected):
        abort(400, "Stale form: reload the page and try again.")


def sign_in(identity, name):
    session.clear()
    session["account_id"] = db.account_for(identity, name)
    session.permanent = True


def discord_redirect_uri():
    return url_for("pages.discord_callback", _external=True)


def discord_authorize_url():
    session["oauth_state"] = state = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "client_id": config.DISCORD_CLIENT_ID,
            "redirect_uri": discord_redirect_uri(),
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
    )
    return f"https://discord.com/oauth2/authorize?{query}"


def _discord(path, data=None, token=None):
    headers = {"User-Agent": "gcm-hosting"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data else None
    request_ = urllib.request.Request(f"{config.DISCORD_API_URL}{path}", data=body, headers=headers)
    with urllib.request.urlopen(request_, timeout=15) as response:
        return json.load(response)


def discord_identity(code, state):
    """(identity, name) for the Discord user who approved, or None."""
    expected = session.pop("oauth_state", "")
    if not code or not expected or not hmac.compare_digest(state or "", expected):
        return None
    token = _discord(
        "/oauth2/token",
        {
            "client_id": config.DISCORD_CLIENT_ID,
            "client_secret": config.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": discord_redirect_uri(),
        },
    )["access_token"]
    user = _discord("/users/@me", token=token)
    return f"discord:{user['id']}", user.get("global_name") or user["username"]
