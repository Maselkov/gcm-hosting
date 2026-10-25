"""Every setting, from the environment. Other modules read them as
config.NAME at call time, so tests can monkeypatch them."""

import os

# Bases are served at <slug>.DOMAIN; this app at DOMAIN itself.
DOMAIN = os.environ.get("DOMAIN", "localhost").strip().lower()
SECRET_KEY = os.environ.get("SECRET_KEY", "")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

# Discord sign-in: an application's OAuth2 client, with
# https://DOMAIN/login/discord/callback as its redirect.
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_API_URL = os.environ.get("DISCORD_API_URL", "https://discord.com/api").rstrip("/")
# Sign in as anyone by name, without Discord. For local testing only.
DEV_LOGIN = os.environ.get("DEV_LOGIN", "") == "1"

# Each base's container (hosting/provision.py).
IMAGE = os.environ.get("IMAGE", "ghcr.io/maselkov/gtnh-craft-monitor:5.1")
NETWORK = os.environ.get("NETWORK", "gcm-bases")
# The network's subnet: the proxy's address is in it, so bases trust its
# X-Forwarded-* headers.
NETWORK_SUBNET = os.environ.get("NETWORK_SUBNET", "172.30.0.0/16")
# A host directory of game data bundles every base reads, read-only
# (GAMEDATA_SHARED_DIR in the base). Empty: each base downloads its own.
GAMEDATA_SHARED_DIR = os.environ.get("GAMEDATA_SHARED_DIR", "").strip()
CERT_RESOLVER = os.environ.get("CERT_RESOLVER", "le")
MEMORY_LIMIT = os.environ.get("MEMORY_LIMIT", "256m")
CPU_LIMIT = float(os.environ.get("CPU_LIMIT", "0.5"))
PIDS_LIMIT = int(os.environ.get("PIDS_LIMIT", "128"))

# Disk quota per base (hosting/quota.py): checked every
# QUOTA_CHECK_SECONDS; a base over it is stopped until it's raised.
# Game data a base downloads itself is on a separate volume and doesn't
# count (the base keeps at most two versions of it).
QUOTA_BYTES = int(float(os.environ.get("QUOTA_MB", "200")) * 1024**2)
QUOTA_CHECK_SECONDS = int(os.environ.get("QUOTA_CHECK_SECONDS", "300"))
# Bases' item history thinning (HISTORY_*_AFTER_DAYS in the base), sooner
# than a self-hosted server's 30/365 to fit the quota: a busy base, 2000
# items changing every scan, writes ~7.6 MB a day of full history, so it
# levels off around 100 MB in its first year.
HISTORY_HOURLY_AFTER_DAYS = os.environ.get("HISTORY_HOURLY_AFTER_DAYS", "7")
HISTORY_DAILY_AFTER_DAYS = os.environ.get("HISTORY_DAILY_AFTER_DAYS", "30")

MAX_BASES_PER_ACCOUNT = int(os.environ.get("MAX_BASES_PER_ACCOUNT", "1"))
MAX_BASES = int(os.environ.get("MAX_BASES", "30"))


def base_url(slug):
    scheme = "http" if DOMAIN == "localhost" else "https"
    return f"{scheme}://{slug}.{DOMAIN}"
