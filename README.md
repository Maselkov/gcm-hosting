# gcm-hosting

Hosted [GTNH Craft Monitor](https://github.com/Maselkov/gtnh-craft-monitor):
players sign in with Discord, make a base, and get their own monitor at
an address like `copper-falcon-4821.<your domain>` without running a
server.

Each base is a separate, ordinary GTNH Craft Monitor server: its own
container of the published image, with its own data volume, users,
tokens and API key. This app only starts and removes them; it never sees
what's inside one. Traefik routes `<name>.DOMAIN` to each base by the
labels this app puts on its container, all under one Let's Encrypt
wildcard certificate.

## Running it

You need a host with Docker and ports 80 and 443 open, and `DOMAIN`'s
DNS on Cloudflare (`DOMAIN` can be a subdomain, like `gcm.example.com`).

1. In Cloudflare, add two records pointing at the host: `DOMAIN` and
   `*.DOMAIN` (for `gcm.example.com`, the names `gcm` and `*.gcm`).
   Make them **DNS only** (grey cloud), not proxied: Cloudflare's free
   certificate doesn't cover a wildcard below a subdomain, and Traefik
   serves its own.
2. Make a Cloudflare API token (My Profile → API Tokens → Create Token →
   the "Edit zone DNS" template) with **Zone / DNS / Edit** and
   **Zone / Zone / Read**, for that zone only. Traefik uses it to add the
   TXT record that proves the certificate is yours.
3. Make a Discord application (https://discord.com/developers/applications),
   and under OAuth2 add the redirect `https://DOMAIN/login/discord/callback`.
4. `cp .env.example .env` and fill it in, the token as `CF_DNS_API_TOKEN`.
5. `docker compose up -d --build`

Traefik then gets one certificate for `DOMAIN` and `*.DOMAIN`, which takes
a minute or so, and renews it by itself. Every base uses it, so making a
base never asks Let's Encrypt for anything, and the public certificate
logs (which anyone can search) show only those two names, not the
bases'. To try it without using up Let's Encrypt's limits, set
`ACME_CA_SERVER` to its staging CA (see `.env.example`) first; delete
the `letsencrypt` volume before switching back. For a DNS provider other
than Cloudflare, change the provider and its settings in
`docker-compose.yml`.

`publish.yml` publishes the image as `ghcr.io/maselkov/gcm-hosting` once
the tests pass (`latest` from `main`, `1.2.3` and `1.2` from a `v1.2.3`
tag); to use it instead of
building, put `image: ghcr.io/maselkov/gcm-hosting:latest` in place of
`build: .` in `docker-compose.yml`.

To have CI deploy it instead, a testing and a production instance on
one server from a self-hosted runner, see
[deploy/README.md](deploy/README.md).

Open `https://DOMAIN`, sign in, make a base. The page shows the base's
admin token once; from there the base works like any self-hosted one
(the in-game setup is under **Settings → Status → Connect a computer**
on the base's own page).

### Shared game data

Each base needs a GTNH version's game data (icons and item catalog,
about 300 MB per texture set). Set `GAMEDATA_SHARED_DIR` to an absolute
path on the host and every base reads `<version>/` folders from there,
mounted read-only, instead of each downloading its own.

This app fills it: every version `GAMEDATA_REPO`
(`Maselkov/gtnh-craft-monitor-data`) publishes, with every texture set,
at startup and every `GAMEDATA_INDEX_SECONDS`, and again when a release
is rebuilt. That's about 8.5 GB for the 8 versions out now, and growing
by about 1.2 GB per release. It runs in a short-lived container of the
bases' `IMAGE` with network access, as the folder's owner, downloads
into `.incoming/` and swaps each version in whole. The first run takes
a while; until a version is there, a base that picks it downloads its
own. A `<version>/` folder you put there yourself (one without a
`release.json`) is left alone. `GAMEDATA_DOWNLOAD=0` turns this off:
then copy `<version>/` folders in from any server that downloaded them,
laid out like its `data/gamedata/`, and replace one when its version is
rebuilt.
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
This needs GTNH Craft Monitor 6.0 or later; older images ignore
`GAMEDATA_SHARED_DIR` and download their own. The app pulls `IMAGE` at
startup, before the first index, so a tag that's moved (`latest`, `6.1`)
isn't left at an older copy Docker had cached; if the pull fails, it uses
the local copy. Existing bases still need **Recreate** to run it.

### Limits

Each base runs with `MEMORY_LIMIT` (160 MB), `CPU_LIMIT` (half a core)
and 128 processes. A base in use on the 6.0 image (GTNH 2.9 game data
live, a full scan of ~11k items, icons being served) holds about 54 MB,
peaking around 80 MB, plus about 70 MB the first time it draws a chart
(a link preview, the power graph's image). Its game data is read from
the shared folder's index, which every base shares in the OS's cache. So
a 4 GB host fits about 55 (`MAX_BASES`, default 10).
`MAX_BASES_PER_ACCOUNT` defaults to 1.

Without `GAMEDATA_SHARED_DIR`, each base downloads its own game data
and builds its own index: give it `256m`. Images before 6.0 hold about
290 MB, peaking around 315 MB: they need `512m`.

A limit applies when a base's container is made: after changing it,
**Recreate** each base in the control panel.

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
app's database with it. Servers from GTNH Craft Monitor 6.0 on also
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
- **Rename:** a new address for the same base. It's stopped, its data
  copied to volumes under the new name (Docker can't rename one), and it
  starts on the current `IMAGE`; the old address stops working, so its
  computers need the new `SERVER_URL` and everyone signs in again. Names
  in `RESERVED_NAMES` (`test`, `www`, ... - the testing instance lives at
  `test.DOMAIN`) are refused. If a step fails, the base is left as it was.
- **Delete:** the container and all its data, after typing its name.

Every action is logged with who did it.

## Things to know before opening it to the public

- **The Docker socket is root on the host.** This app needs it to start
  and remove bases, so anyone who takes over this app takes over the
  host. Keep it updated, and consider a socket proxy that only allows
  the container, volume and exec calls it makes.
- **Bases' addresses are made up** (`<adjective>-<noun>-<4 digits>`,
  about 655 million of them), so nobody can take a name like `official`,
  pass for someone else, or claim a deleted base's address. With the
  wildcard certificate, they can't be listed either. That isn't access
  control, though: addresses get shared.
- **New bases are private unless their owner picks otherwise**: only
  their signed-in users see the crafts, network and power, and link
  previews show nothing. The form offers "Anyone with the link" instead,
  and owners can change it later in their base (**Manage users**). This
  needs a GTNH Craft Monitor image from 6.0 on (`app.py visibility`);
  with an older `IMAGE`, making a private base fails with a message
  saying so, and public ones still work.
- **Players' Minecraft servers must allow the OpenComputers Internet
  Card** to reach this host. Some multiplayer servers turn it off.
- **Who can make a base.** A Discord account must be
  `MIN_ACCOUNT_AGE_DAYS` (30) old, read from its ID, so throwaway
  accounts can't fill the slots; operators always can. Slots also come
  back by themselves: a base the game never reported to is removed
  `NEVER_CONNECTED_DAYS` (7) after it was made (it holds nothing yet;
  its owner's card says when). One the game stopped reporting to for
  `INACTIVE_DAYS` (60) is only flagged, in the control panel and on its
  owner's card: delete it yourself if you want the slot back. "The game
  reported" is the newest power reading, network scan or craft ending in
  the base's databases, checked hourly.
- **Rate limits.** Traefik answers 429 to one IP past
  `RATE_LIMIT_AVERAGE` (50) requests a second to a base, with bursts up
  to `RATE_LIMIT_BURST` (500): enough for the page's polling and a
  Network tab's worth of icons at once. This app's own pages allow 10 a
  second, bursts of 50 (`docker-compose.yml`).
- **Networks.** Bases share the `gcm-bases` network and trust its
  subnet's `X-Forwarded-*` headers. That's safe because bases never run
  code their players supply; they're all the same image. This app is on
  `gcm-control` with Traefik only, so a base can't reach it except
  through Traefik. To also keep bases from reaching anything private
  from the host's side (other machines on your LAN, a cloud metadata
  address), add host firewall rules, for example (as root; the second
  rule, inserted above the first, keeps Traefik's own connections to the
  bases, which are inside that subnet too):

  ```bash
  iptables -I DOCKER-USER -s 172.30.0.0/16 -d 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16 -m conntrack --ctstate NEW -j DROP
  iptables -I DOCKER-USER -s 172.30.0.0/16 -d 172.30.0.0/16 -j RETURN
  ```

  They're an example, not part of this app: make them persist the way
  your distribution does, and check a base still loads afterwards.

  Bases still reach the internet, for push notifications and webhooks.
- You hold players' data: say how long you keep it and how to delete it
  (deleting a base on the page removes its container and volume).

## Developing

```bash
uv venv .venv && uv pip install -p .venv -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`DOMAIN=localhost DEV_LOGIN=1 python app.py` runs it without Discord:
sign in with any name. It refuses to start with `DEV_LOGIN` unless
`DOMAIN` is `localhost` or a reserved test name (`*.localhost`, `*.test`,
`*.example`, `*.invalid`), so it can't end up on a public site. With a local Traefik on the `gcm-bases` network,
bases answer at `http://<name>.localhost`.

The sign-in page shows `hosting/static/crafts.webp`, a screenshot of a
base's Crafts tab (the tab bar and the first two rows of CPUs, in dark
mode). Replace it by hand when that tab's look changes; keep the `width`
and `height` in `index.html` in step with it.

## License

GPL-3.0, like GTNH Craft Monitor itself: see [LICENSE](LICENSE).
