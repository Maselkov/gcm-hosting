"""gcm-hosting: lets players make their own GTNH Craft Monitor base,
each one a separate server of the same image, without self-hosting."""

import datetime

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from hosting import admin, config, db, pages

CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def create_app(provisioner=None):
    if config.DEV_LOGIN and not config.is_test_domain():
        # Anyone could sign in as anyone - an operator included.
        raise RuntimeError(
            f"DEV_LOGIN is for local testing only, and DOMAIN is {config.DOMAIN}. Unset DEV_LOGIN, "
            "or use localhost or a reserved test name (*.localhost, *.test, *.example, *.invalid)."
        )
    if not config.SECRET_KEY and not config.DEV_LOGIN:
        raise RuntimeError("Set SECRET_KEY to a long random string.")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY or "dev-only",
        SESSION_COOKIE_SECURE=config.DOMAIN != "localhost",
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="gcm_hosting",
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if provisioner is None:
        from hosting.provision import DockerProvisioner

        provisioner = DockerProvisioner()
    app.config["PROVISIONER"] = provisioner
    # Only ever reached through Traefik, which sets these.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init()
    app.register_blueprint(pages.bp)
    app.register_blueprint(admin.bp)

    @app.after_request
    def headers(response):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
