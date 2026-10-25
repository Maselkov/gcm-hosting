# gcm-hosting

Hosted [GTNH Craft Monitor](https://github.com/Maselkov/gtnh-craft-monitor):
players sign in with Discord, name a base, and get their own monitor at
`<name>.<your domain>` without running a server.

Each base is a separate, ordinary GTNH Craft Monitor server: its own
container of the published image, with its own data volume, users,
tokens and API key. This app only starts and removes them; it never sees
what's inside one. Traefik routes `<name>.DOMAIN` to each base by the
labels this app puts on its container, with a Let's Encrypt certificate
per base.

## Running it

You need a host with Docker, ports 80 and 443 open, and DNS for both
`DOMAIN` and `*.DOMAIN` pointing at it.

1. Make a Discord application (https://discord.com/developers/applications),
   and under OAuth2 add the redirect `https://DOMAIN/login/discord/callback`.
2. `cp .env.example .env` and fill it in.
3. `docker compose up -d --build`

Open `https://DOMAIN`, sign in, make a base. The page shows the base's
admin token once; from there the base works like any self-hosted one
(the in-game setup is under **Settings → Status → Connect a computer**
on the base's own page).

### Shared game data

Each base needs a GTNH version's game data (icons and item catalog,
about 300 MB). Set `GAMEDATA_SHARED_DIR` to an absolute path on the host
holding `<version>/` folders, laid out like a server's
`data/gamedata/` (copy them from any server that downloaded them), and
every base reads them from there, mounted read-only, instead of each
downloading its own. Replace a folder there when its version is rebuilt.
Each version also needs an index there (its icon and item lookups as one
file, read in place), or every base builds its own copy of it. This app
builds them for you: with the bases' `IMAGE`, since an index's format
belongs to the server version, in a short-lived container that's the
only thing mounting the folder writable, as the folder's owner. It runs
at startup, every `GAMEDATA_INDEX_SECONDS` (900), before a base is made
or recreated, and from the control panel's **Index now**. A version
already indexed is only checked, which takes well under a second, and a
new one takes a couple of seconds. The control panel shows each version's
state.
This needs a GTNH Craft Monitor release with `GAMEDATA_SHARED_DIR`
(after 5.1.0); older images ignore it and download their own.

### Limits

Each base runs with `MEMORY_LIMIT` (512 MB), `CPU_LIMIT` (half a core)
and 128 processes. A base in use (GTNH 2.9 game data live, a full scan
of ~11k items, icons being served) holds about 290 MB, peaking around
315 MB, and almost none of it is shared with other bases. So a 4 GB host
fits about 10 (`MAX_BASES`). `MAX_BASES_PER_ACCOUNT` defaults to 1.

From the GTNH Craft Monitor release with the game data index (#119,
#120) on, the same base holds about 54 MB, peaking around 80 MB (with the
index pre-built in the shared folder): set `MEMORY_LIMIT=160m` and
roughly 55 fit in 4 GB.

**Disk.** Docker can't cap a volume's size, so this app measures every
base's data volume every `QUOTA_CHECK_SECONDS` (300) and stops one over
`QUOTA_MB` (200). Its card then says it's over quota; the owner can still
delete it. To give a base more room (it starts again at once if it fits):

```bash
docker compose exec hosting python app.py set-quota <name> <MB>
```

What fills a base is its item history: about 28 bytes per item each time
its count changes. A busy base (2000 items changing every 10-minute scan)
writes about 7.6 MB a day, so bases thin it sooner than a self-hosted
server does: full detail for `HISTORY_HOURLY_AFTER_DAYS` (7), hourly to
`HISTORY_DAILY_AFTER_DAYS` (30), daily after. That levels such a base off
around 100 MB in its first year. Game data a base downloads itself (a
version or textures missing from `GAMEDATA_SHARED_DIR`, ~300 MB each) is
on a second volume that doesn't count; the base keeps at most two.

A base can still grow past its quota between checks, so keep Docker's
volumes (`/var/lib/docker/volumes`, or Docker's whole `data-root`) on
their own partition: a base filling it can't take down the host or this
app's database with it. Servers from GTNH Craft Monitor 5.2 on also
refuse oversized network scans (`MAX_SCAN_ITEMS`), which stops the
quickest way to fill one.

## Control panel

`/admin`, for the accounts in `ADMIN_IDENTITIES` (anyone else gets a
404; a "Control panel" link appears on the main page for you). It shows:

- **The host:** bases (of `MAX_BASES`) and how many are running, over
  quota or stopped by you; total CPU against the host's cores; total
  memory against the host's and against the bases' limits combined;
  total disk against the quotas combined; accounts; bases on an old image.
- **Every base:** owner, state, CPU against its limit, memory against its
  limit, disk against its quota, uptime, and what needs a look - near a
  limit, killed for memory, restarting, stopped, or on an old image.
  Sortable by CPU, memory, disk, name or owner.

CPU and memory are read every `STATS_SECONDS` (10), disk at each quota
check. The pages are plain HTML with no scripts. Add `?refresh=15`, or use
the link, to reload them by themselves.

A base's page has its last 200 log lines, its in-game settings, and:

- **Stop / Start / Restart.** A base you stop stays stopped, even through
  the quota check, until you start it here.
- **Recreate:** a new container from the current `IMAGE` (pulled first)
  on the same data - how bases get a new release.
- **Reset an admin's token:** for an owner who lost theirs. It replaces
  that admin's sign-in token (by default the base's oldest admin) and
  shows the new one once. If the base has no admin left, it makes one.
- **Disk quota:** in MB, or empty for the default. It takes effect at
  once, stopping or starting the base.
- **Delete:** the container and all its data, after typing its name.

Every action is logged with who did it.

## Things to know before opening it to the public

- **The Docker socket is root on the host.** This app needs it to start
  and remove bases, so anyone who takes over this app takes over the
  host. Keep it updated, and consider a socket proxy that only allows
  the container, volume and exec calls it makes.
- **Let's Encrypt allows 50 new certificates per domain per week.** Each
  new base gets its own. For more, switch Traefik to a wildcard
  certificate (DNS challenge).
- **Players' Minecraft servers must allow the OpenComputers Internet
  Card** to reach this host. Some multiplayer servers turn it off.
- Bases share one Docker network and trust its subnet's
  `X-Forwarded-*` headers. That's safe because bases never run code
  their players supply; they're all the same image.
- You hold players' data: say how long you keep it and how to delete it
  (deleting a base on the page removes its container and volume).

## Developing

```bash
uv venv .venv && uv pip install -p .venv -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`DOMAIN=localhost DEV_LOGIN=1 python app.py` runs it without Discord:
sign in with any name. With a local Traefik on the `gcm-bases` network,
bases answer at `http://<name>.localhost`.
