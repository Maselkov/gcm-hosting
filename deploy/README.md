# Deploying from CI: testing and production on one server

Two instances of this app on one Docker host, each with its own domain,
data, bases and certificate, behind one shared Traefik:

| | testing | production |
|---|---|---|
| Deployed by | every push to `main` (or Publish run by hand on any branch) | a `v1.2.3` tag, once you approve it |
| Image tag | `sha-<short commit>` | `1.2.3` |
| Example domain | `test.gcm.example.com` | `gcm.example.com` |
| Its bases | `gcm-test-base-<name>` | `gcm-base-<name>` |

`publish.yml` tests and publishes the image, then calls `deploy.yml`. That
runs on your self-hosted runner and SSHes to the server with a key that
can run only `deploy.sh`. The script pulls the image and starts that
instance with the compose files from inside the image. If the new
release isn't healthy within two minutes, it puts the previous one back
and the job fails.

```
GitHub ──job──> runner (LAN) ──ssh "testing sha-1a2b3c4"──> server: deploy.sh
                                                              ├─ edge        Traefik, :80 :443
                                                              ├─ testing     app + its bases
                                                              └─ production  app + its bases
```

**Testing is as powerful as production.** Both have the Docker socket,
so code on `main` that hasn't been released can still do anything on the
host, production included. Only merge what you'd trust there.

## 1. The server

As root, on a host with Docker and `bash`, `flock` and `ssh` (any usual
Linux):

```bash
useradd -m -s /bin/bash -G docker deploy
mkdir -p /srv/gcm-hosting/{edge,testing,production}
chown -R deploy: /srv/gcm-hosting
chmod 700 /srv/gcm-hosting/{edge,testing,production}  # the .env files hold secrets

# The script the key is forced to run. It's owned by root so the deploy
# user can't change it; update it by hand when deploy/deploy.sh changes.
curl -fsSL https://raw.githubusercontent.com/Maselkov/gcm-hosting/main/deploy/deploy.sh \
  -o /usr/local/bin/gcm-deploy
chmod 755 /usr/local/bin/gcm-deploy
```

Read the script before installing it. It's short, and it's the only
thing between the key and a root shell.

### The key

On any machine (not the runner; the key is never stored there):

```bash
ssh-keygen -t ed25519 -N "" -C gcm-deploy -f gcm-deploy
```

Put `gcm-deploy.pub` in `/home/deploy/.ssh/authorized_keys` on the server,
on one line behind the options that pin it to the script:

```
restrict,command="/usr/local/bin/gcm-deploy" ssh-ed25519 AAAA... gcm-deploy
```

(`mkdir -p /home/deploy/.ssh`, the file owned by `deploy`, mode 600.)
`gcm-deploy` (the private half) becomes the `DEPLOY_SSH_KEY` secret
below. Then delete both files.

The server's host keys, for `DEPLOY_KNOWN_HOSTS`, from a machine on the
LAN:

```bash
ssh-keyscan -t ed25519 <server address>
```

### Settings: one `.env` per folder

`/srv/gcm-hosting/edge/.env`, for Traefik:

```bash
ACME_EMAIL=you@example.com
CF_DNS_API_TOKEN=...        # as in the main README: Zone/DNS/Edit + Zone/Zone/Read
# ACME_CA_SERVER=https://acme-staging-v02.api.letsencrypt.org/directory
# FRONT_PROXY_IPS=10.0.0.2     # behind another proxy, below
```

**Behind another reverse proxy** (one that owns the public 443, maybe
itself behind Cloudflare): let it terminate TLS and proxy to Traefik's
443 over HTTPS, and set `FRONT_PROXY_IPS` to its address (comma-separated
for several). Traefik then believes the `X-Real-Ip` it sends, and the
rate limits count each client by it. So the front proxy must set
`X-Real-Ip` to the client's address itself, never pass on one a client
sent. With Caddy:

```caddy
gtnh.example.com, *.gtnh.example.com {
	reverse_proxy https://<this host>:443 {
		header_up X-Real-IP {client_ip}
		header_up X-Forwarded-For {client_ip}
		transport http {
			tls_server_name {http.request.host}
		}
	}
}
```

`{client_ip}` is the connecting address, or with `trusted_proxies` and
`client_ip_headers` the one Cloudflare passes on. `tls_server_name`
makes Traefik serve its certificate for that name, which Caddy checks.
Bases made before `FRONT_PROXY_IPS` was set count clients by their own
labels until they're recreated (**Admin → Recreate**). A base's own
per-client limits (chart images) still see the front proxy's address.

With Cloudflare proxying, its free certificate covers only one level of
subdomain: `*.gtnh.example.com`, but not testing's `*.test.gtnh.example.com`.

`/srv/gcm-hosting/production/.env` and `/srv/gcm-hosting/testing/.env`:
each is the app's usual settings (`.env.example`), plus `INSTANCE`.
They differ in:

| | production | testing |
|---|---|---|
| `INSTANCE` | `gcm` | `gcm-test` |
| `DOMAIN` | `gcm.example.com` | `test.gcm.example.com` |
| `SECRET_KEY` | its own | another of its own |
| `MAX_BASES` / `MEMORY_LIMIT` | what the host fits | small, e.g. `3` / `256m` |

Count testing's bases as part of what the host has to fit. The
`ACME_EMAIL`, `CF_DNS_API_TOKEN` and `ACME_CA_SERVER` lines in
`.env.example` belong to Traefik and only go in `edge/.env`.

`GAMEDATA_SHARED_DIR` can be the same folder for both as long as both
`.env`s have the same `IMAGE`. Each instance builds the folder's index
with its own `IMAGE`, and the index format follows the server version.
To try a new bases' `IMAGE` on testing, give testing its own copy of the
folder first.

### GHCR

The server has to pull `ghcr.io/maselkov/gcm-hosting`. The repository is
public, so the simplest way is to make the package public too: on
GitHub, open **Packages → gcm-hosting → Package settings → Change
visibility**. Otherwise, as `deploy`, run
`docker login ghcr.io -u Maselkov` with a classic token that has only
`read:packages`.

## 2. DNS and Discord

- **Cloudflare:** besides production's `gcm` and `*.gcm` records, add
  `test.gcm` and `*.test.gcm`, pointing at the same host and set to
  **DNS only**. `*.gcm` doesn't cover `*.test.gcm`, so Traefik gets
  testing its own wildcard certificate with the same token.
- **Or keep testing on your LAN:** skip those two records and point
  `test.gcm` and `*.test.gcm` at the host in your LAN's own DNS
  instead. The certificate still comes, since the DNS challenge only
  needs the token, not a reachable name. If production has a wildcard
  record (or a front proxy, above, answers `*.gcm`), `test.gcm` still
  resolves publicly through it, so have that proxy refuse it (in Caddy,
  a `test.gcm.example.com` site that does `abort`). The Discord redirect
  below works either way: it happens in the browser.
- **Discord:** on the same application, add a second redirect,
  `https://test.gcm.example.com/login/discord/callback`.

## 3. The runner

On the LAN machine, in GitHub go to **Settings → Actions → Runners →
New self-hosted runner** and follow it, adding the label the deploy job
asks for:

```bash
./config.sh --url https://github.com/Maselkov/gcm-hosting --token <token> \
  --name gcm-deploy --labels gcm-deploy --unattended
sudo ./svc.sh install <an unprivileged user> && sudo ./svc.sh start
```

The runner needs `bash` and `ssh`, and must reach the server's port 22.
Run it as an ordinary user, not root and not in the `docker` group: all
it does is SSH.

## 4. GitHub settings

**Settings → Environments**, two of them:

- **`production`:**
  - *Required reviewers:* you. Turn off "Prevent self-review", or you
    can't approve your own release.
  - *Deployment branches and tags:* Selected, with the tag rule `v*`.
  - *Secrets:*
    - `DEPLOY_SSH_KEY`: the whole private key file.
    - `DEPLOY_KNOWN_HOSTS`: the `ssh-keyscan` line.
    - `DEPLOY_HOST`: the server's LAN address.
  - *Variables:*
    - `DOMAIN` = `gcm.example.com`, which becomes the deployment's link.
    - `DEPLOY_USER` = `deploy`.
- **`testing`:**
  - *Deployment branches:* no restriction. Running Publish on a branch
    sends it here, and only collaborators can push or run workflows.
  - The same three secrets.
  - *Variables:* `DOMAIN` = `test.gcm.example.com`, and `DEPLOY_USER`
    as for production.

Keep the secrets in the environments, not the repository. That way only
the deploy job can read them, and only for the refs each environment
allows. The host isn't really secret, but the repository is public and
so are its Actions logs: as a secret it shows as `***` there, not as
your LAN address. The user is a variable instead, because GitHub masks
every occurrence of a secret's value in the logs, and a word as common
as `deploy` would turn the job's own name into `***`.

**Settings → Actions → General → Approval for running fork pull request
workflows:** *Require approval for all external contributors.* The
repository is public, and a fork's pull request could otherwise change a
workflow to run on your runner. It still couldn't read the key, but you
don't want strangers' code on your LAN.

## Day to day

- **Merge to `main`:** testing gets it a few minutes later.
- **Release:** `git tag v1.2.3 && git push origin v1.2.3`. After the
  tests and publishing, the deploy waits for your approval under the
  run's **Review deployments**.
  - Only `vX.Y.Z` tags deploy. A pre-release like `v1.3.0-rc1` is
    published but never deployed.
- **Try a branch on testing:** Actions → Publish image → Run workflow,
  on that branch.
- **Roll back production:** Actions → Deploy → Run workflow, "Use
  workflow from" the older tag, environment `production`. This deploys
  that release's published image without rebuilding it.
  - The database only ever gains columns (`hosting/db.py`), so an older
    release runs on a newer one's data.
- **On the server:**
  - `cat /srv/gcm-hosting/<env>/deployed` shows the running tag.
  - `cd /srv/gcm-hosting/<env> && docker compose -p gcm-<env> logs hosting`
    shows the app's logs.
  - Commands like `app.py set-quota` work as in the main README, run with
    `docker compose -p gcm-<env> exec hosting`.

Changes to `edge` (Traefik) take effect with the next production deploy.
A testing deploy starts it only if it isn't running.
