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
# Sign in as anyone by name, without Discord. For local testing only: the
# app won't start with it unless DOMAIN is a name that can never be a
# public site (is_test_domain()).
DEV_LOGIN = os.environ.get("DEV_LOGIN", "") == "1"
# Reserved for testing, never delegated (RFC 2606, RFC 6761).
TEST_DOMAIN_SUFFIXES = (".localhost", ".test", ".example", ".invalid")
# Who may use the control panel (/admin): comma-separated identities, as
# accounts sign in - "discord:<user id>", or "dev:<name>" with DEV_LOGIN.
ADMIN_IDENTITIES = {i.strip() for i in os.environ.get("ADMIN_IDENTITIES", "").split(",") if i.strip()}
# How often the control panel's CPU and memory figures are read.
STATS_SECONDS = int(os.environ.get("STATS_SECONDS", "10"))

# Each base's container (hosting/provision.py).
# Prefixes every base's container, volumes and router, and labels them,
# so two instances on one Docker host (deploy/README.md) don't see or
# touch each other's bases.
INSTANCE = os.environ.get("INSTANCE", "gcm").strip()
IMAGE = os.environ.get("IMAGE", "ghcr.io/maselkov/gtnh-craft-monitor:6.0")
NETWORK = os.environ.get("NETWORK", "gcm-bases")
# The network's subnet: the proxy's address is in it, so bases trust its
# X-Forwarded-* headers.
NETWORK_SUBNET = os.environ.get("NETWORK_SUBNET", "172.30.0.0/16")
# A host directory of game data bundles every base reads, read-only
# (GAMEDATA_SHARED_DIR in the base). Empty: each base downloads its own.
GAMEDATA_SHARED_DIR = os.environ.get("GAMEDATA_SHARED_DIR", "").strip()
# How often the shared game data is checked for versions to index
# (hosting/indexer.py); it's also done at startup and before a base is
# made or recreated.
GAMEDATA_INDEX_SECONDS = int(os.environ.get("GAMEDATA_INDEX_SECONDS", "900"))
CERT_RESOLVER = os.environ.get("CERT_RESOLVER", "le")
# Requests per second from one IP to one base (Traefik's ratelimit), on
# average and in a burst; past them it answers 429. Generous: the page
# polls every second or so, and opening the Network tab on a big screen
# loads a few hundred icons at once. 0: no limit.
RATE_LIMIT_AVERAGE = int(os.environ.get("RATE_LIMIT_AVERAGE", "50"))
RATE_LIMIT_BURST = int(os.environ.get("RATE_LIMIT_BURST", "500"))
# A base with a GTNH 2.9 bundle live, a full scan and icons served holds
# ~54 MB and peaks ~80 MB on 6.0 with its game data index pre-built
# (GAMEDATA_SHARED_DIR), plus ~70 MB once it draws a chart. Images before
# 6.0 need 512m: they hold ~290 MB.
MEMORY_LIMIT = os.environ.get("MEMORY_LIMIT", "160m")
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

# Names a base can't be renamed to (the control panel), as they're, or
# may become, another site's under DOMAIN - the testing instance at
# test.DOMAIN, say. Made-up names never are (they end in digits).
RESERVED_NAMES = {
    n.strip()
    for n in os.environ.get("RESERVED_NAMES", "www,test,api,app,admin,mail,smtp,traefik,hosting,status,static").split(
        ","
    )
    if n.strip()
}

# Reclaiming slots (hosting/activity.py): a base the game never reported
# to is removed this many days after it was made; one it stopped
# reporting to is flagged after INACTIVE_DAYS, never removed by itself.
# 0 turns either off. Checked every ACTIVITY_CHECK_SECONDS.
NEVER_CONNECTED_DAYS = float(os.environ.get("NEVER_CONNECTED_DAYS", "7"))
INACTIVE_DAYS = float(os.environ.get("INACTIVE_DAYS", "60"))
ACTIVITY_CHECK_SECONDS = int(os.environ.get("ACTIVITY_CHECK_SECONDS", "3600"))

MAX_BASES_PER_ACCOUNT = int(os.environ.get("MAX_BASES_PER_ACCOUNT", "1"))
# How old a Discord account must be to make a base, so a throwaway one
# made to grab a slot can't. 0: any age. Operators can always.
MIN_ACCOUNT_AGE_DAYS = float(os.environ.get("MIN_ACCOUNT_AGE_DAYS", "30"))
MAX_BASES = int(os.environ.get("MAX_BASES", "10"))


def is_test_domain():
    return DOMAIN == "localhost" or DOMAIN.endswith(TEST_DOMAIN_SUFFIXES)


def base_url(slug):
    scheme = "http" if DOMAIN == "localhost" else "https"
    return f"{scheme}://{slug}.{DOMAIN}"
