"""Runs each base as its own container of the GTNH Craft Monitor image,
with its own data volume, behind Traefik at <slug>.DOMAIN.

A base is exactly a self-hosted server: its users, tokens, roles and
data live in its volume, and this app only starts, stops and removes it.
Bases never run code their players supply, so sharing one network is
safe; the limits keep one busy base from starving the rest."""

import datetime
import json
import time

from hosting import config

DATA_PATH = "/app/data"
OWN_GAMEDATA_PATH = "/app/data/gamedata"
SHARED_GAMEDATA_PATH = "/gamedata-shared"
PORT = 8420
LABEL = "gcm-hosting.slug"
# Which instance (config.INSTANCE) a base's container and volumes belong
# to. Ones without it are from before it and belong to the default, "gcm".
INSTANCE_LABEL = "gcm-hosting.instance"
DEFAULT_INSTANCE = "gcm"

# Run inside a base by reset_admin_token(): replaces an admin's sign-in
# token, as `app.py new-token` does, but finds the admin itself - the one
# named, else the oldest enabled one - and makes one if there's none.
# Prints {name, token} or {error, admins} as its last line.
RESET_ADMIN_SCRIPT = """
import json, sys
from gcm import create_app, store
create_app()
name = sys.argv[1]
admins = sorted((u for u in store.users.all_with_tokens() if u["role"] == "admin" and not u["disabled_at"]),
                key=lambda u: u["created_at"])
if not admins and not name:
    name = "Admin"
if name and not any(u["display_name"] == name for u in admins):
    if admins:
        print(json.dumps({"error": f"No admin named {name!r}", "admins": [u["display_name"] for u in admins]}))
        sys.exit(1)
    created = store.users.create(name, "admin")
    if not created:
        print(json.dumps({"error": f"{name!r} is taken by a user who isn't an admin", "admins": []}))
        sys.exit(1)
    print(json.dumps({"name": name, "token": created[1], "created": True}))
    sys.exit(0)
pick = next(u for u in admins if u["display_name"] == name) if name else admins[0]
print(json.dumps({"name": pick["display_name"], "token": store.users.replace_credentials(pick["id"])}))
"""


# Run in a one-off container of IMAGE over the shared game data
# (hosting/indexer.py): builds each version's index (gcm/gamedata_index.py)
# that's missing or out of date, so bases read the shared one instead of
# each building its own. With the bases' image, so the index is the format
# their server reads. Prints {versions: [{version, state[, error]}]} or
# {error}.
INDEX_SCRIPT = """
import json, os, sys
root = sys.argv[1]
# Files owned like the folder they're in, not by the container's root.
if os.getuid() == 0:
    owner = os.stat(root)
    os.setgid(owner.st_gid)
    os.setuid(owner.st_uid)
try:
    from gcm import gamedata_index as g
except ImportError as e:
    print(json.dumps({"error": f"this image has no game data index ({e})"}))
    sys.exit(0)
out = []
for name in sorted(os.listdir(root)):
    bundle = os.path.join(root, name)
    if not os.path.isfile(os.path.join(bundle, "data.json")):
        continue
    path = os.path.join(bundle, g.FILE)
    try:
        if g.is_current(path, bundle):
            out.append({"version": name, "state": "current"})
        else:
            g.build(bundle, path)
            out.append({"version": name, "state": "built"})
    except Exception as e:
        out.append({"version": name, "state": "failed", "error": str(e)})
print(json.dumps({"versions": out}))
"""
INDEXER_MOUNT = "/gamedata"


def indexer_spec():
    """docker-py's containers.run() arguments for an indexing run."""
    return {
        "image": config.IMAGE,
        "command": ["python", "-c", INDEX_SCRIPT, INDEXER_MOUNT],
        "volumes": {config.GAMEDATA_SHARED_DIR: {"bind": INDEXER_MOUNT, "mode": "rw"}},
        "labels": {"gcm-hosting.indexer": "1", INSTANCE_LABEL: config.INSTANCE},
        "network_disabled": True,
        # Building holds a version's whole lookup and zip directory at once.
        "mem_limit": "1g",
        "remove": True,
    }


def started_timestamp(docker_time):
    """Docker's "2026-10-25T12:46:52.128342874Z" as whole Unix seconds."""
    return int(datetime.datetime.fromisoformat(docker_time[:19]).replace(tzinfo=datetime.UTC).timestamp())


def container_name(slug):
    return f"{config.INSTANCE}-base-{slug}"


def ours(labels):
    """Whether a container or volume with these labels is this instance's."""
    return (labels or {}).get(INSTANCE_LABEL, DEFAULT_INSTANCE) == config.INSTANCE


class TooOld(RuntimeError):
    """The base's image doesn't have a command this app runs in it."""


def volume_name(slug):
    return f"{config.INSTANCE}-base-{slug}-data"


def gamedata_volume_name(slug):
    """Game data the base downloads itself: kept apart from its data
    volume so it doesn't count toward the quota."""
    return f"{config.INSTANCE}-base-{slug}-gamedata"


def container_spec(slug, api_key):
    """docker-py's containers.run() arguments for slug's base."""
    router = f"{config.INSTANCE}-{slug}"
    labels = {
        LABEL: slug,
        INSTANCE_LABEL: config.INSTANCE,
        "traefik.enable": "true",
        "traefik.docker.network": config.NETWORK,
        f"traefik.http.routers.{router}.rule": f"Host(`{slug}.{config.DOMAIN}`)",
        f"traefik.http.services.{router}.loadbalancer.server.port": str(PORT),
    }
    if config.DOMAIN != "localhost":
        labels[f"traefik.http.routers.{router}.entrypoints"] = "websecure"
        labels[f"traefik.http.routers.{router}.tls.certresolver"] = config.CERT_RESOLVER
        # The one wildcard certificate this app's own router asks for
        # (docker-compose.yml), not one per base: Traefik sees it already
        # has it. So there's nothing new to issue when a base is made, and
        # base names stay out of the public certificate logs.
        labels[f"traefik.http.routers.{router}.tls.domains[0].main"] = config.DOMAIN
        labels[f"traefik.http.routers.{router}.tls.domains[0].sans"] = f"*.{config.DOMAIN}"
    environment = {
        "API_KEY": api_key,
        "PUBLIC_URL": config.base_url(slug),
        "SESSION_COOKIE_SECURE": "0" if config.DOMAIN == "localhost" else "1",
        "TRUSTED_PROXIES": config.NETWORK_SUBNET,
        "HISTORY_HOURLY_AFTER_DAYS": config.HISTORY_HOURLY_AFTER_DAYS,
        "HISTORY_DAILY_AFTER_DAYS": config.HISTORY_DAILY_AFTER_DAYS,
    }
    volumes = {
        volume_name(slug): {"bind": DATA_PATH, "mode": "rw"},
        gamedata_volume_name(slug): {"bind": OWN_GAMEDATA_PATH, "mode": "rw"},
    }
    if config.GAMEDATA_SHARED_DIR:
        environment["GAMEDATA_SHARED_DIR"] = SHARED_GAMEDATA_PATH
        volumes[config.GAMEDATA_SHARED_DIR] = {"bind": SHARED_GAMEDATA_PATH, "mode": "ro"}
    return {
        "image": config.IMAGE,
        "name": container_name(slug),
        "detach": True,
        "labels": labels,
        "environment": environment,
        "volumes": volumes,
        "network": config.NETWORK,
        "restart_policy": {"Name": "unless-stopped"},
        "mem_limit": config.MEMORY_LIMIT,
        "nano_cpus": int(config.CPU_LIMIT * 1e9),
        "pids_limit": config.PIDS_LIMIT,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
    }


class DockerProvisioner:
    def __init__(self):
        import docker

        self.client = docker.from_env()
        self.errors = docker.errors

    def create(self, slug, api_key):
        # Made first, so it carries the label volume_sizes() finds it by.
        self.client.volumes.create(volume_name(slug), labels={LABEL: slug, INSTANCE_LABEL: config.INSTANCE})
        self.client.volumes.create(
            gamedata_volume_name(slug), labels={"gcm-hosting.gamedata": slug, INSTANCE_LABEL: config.INSTANCE}
        )
        self.client.containers.run(**container_spec(slug, api_key))

    def create_admin(self, slug, name, timeout=60):
        """Makes the base's first admin and returns their sign-in token.
        Waits for the server to finish starting, so the two don't both
        create the databases."""
        container = self._started(slug, timeout)
        result = container.exec_run(["python", "app.py", "create-admin", name])
        output = result.output.decode(errors="replace").strip()
        if result.exit_code != 0:
            raise RuntimeError(f"create-admin failed for {slug}: {output}")
        return output.splitlines()[-1]

    def make_private(self, slug):
        """Only the base's signed-in users see its crafts, network and
        power (`app.py visibility`, from GTNH Craft Monitor 5.2 on). Its
        admins can change it in Manage users after."""
        container = self.client.containers.get(container_name(slug))
        result = container.exec_run(["python", "app.py", "visibility", "private"])
        if result.exit_code != 0:
            output = result.output.decode(errors="replace").strip()
            raise TooOld(f"visibility failed for {slug}: {output[-300:]}")

    def _started(self, slug, timeout=60):
        """slug's container once its server is serving. Only the logs since
        it last started count: a restarted one still has the old run's."""
        container = self.client.containers.get(container_name(slug))
        started = started_timestamp(container.attrs["State"]["StartedAt"])
        deadline = time.monotonic() + timeout
        while b"Serving on" not in container.logs(since=started):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{slug} didn't start within {timeout}s")
            container.reload()
            if container.status == "exited":
                raise RuntimeError(f"{slug} exited while starting")
            time.sleep(0.5)
        return container

    def reset_admin_token(self, slug, name=""):
        """Replaces an admin's sign-in token (see RESET_ADMIN_SCRIPT).
        Returns {name, token[, created]}, or raises ValueError with what
        went wrong and the base's admins."""
        container = self._started(slug)
        result = container.exec_run(["python", "-c", RESET_ADMIN_SCRIPT, name])
        lines = result.output.decode(errors="replace").strip().splitlines()
        try:
            answer = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise RuntimeError(f"resetting {slug}'s admin token failed: {' '.join(lines[-3:])}") from None
        if "error" in answer:
            admins = ", ".join(answer["admins"]) or "none"
            raise ValueError(f"{answer['error']}. Its admins: {admins}.")
        return answer

    def details(self):
        """{slug: {status, image, started_at, restarts, oom_killed,
        cpu_limit_percent}} for every base's container."""
        out = {}
        for container in self.client.containers.list(all=True, filters={"label": LABEL}):
            if not ours(container.labels):
                continue
            state = container.attrs.get("State") or {}
            out[container.labels[LABEL]] = {
                "status": container.status,
                "image": (container.attrs.get("Config") or {}).get("Image"),
                "started_at": state.get("StartedAt") if container.status == "running" else None,
                "restarts": container.attrs.get("RestartCount", 0),
                "oom_killed": bool(state.get("OOMKilled")),
                # Of one core, like the CPU figures: 0.5 cores is 50.
                "cpu_limit_percent": ((container.attrs.get("HostConfig") or {}).get("NanoCpus") or 0) / 1e7 or None,
            }
        return out

    def sample(self, slug):
        """One reading of slug's container: (cpu_total_ns, system_total_ns,
        online_cpus, memory_bytes, memory_limit_bytes), or None if it isn't
        running. CPU is turned into a percentage between two readings
        (hosting/stats.py); memory is what Docker's own stats show (usage
        less reclaimable file cache)."""
        try:
            s = self.client.containers.get(container_name(slug)).stats(stream=False, one_shot=True)
        except self.errors.NotFound:
            return None
        cpu, memory = s.get("cpu_stats") or {}, s.get("memory_stats") or {}
        if not memory.get("usage"):
            return None
        used = memory["usage"] - (memory.get("stats") or {}).get("inactive_file", 0)
        return (
            (cpu.get("cpu_usage") or {}).get("total_usage", 0),
            cpu.get("system_cpu_usage", 0),
            cpu.get("online_cpus") or 1,
            used,
            memory.get("limit"),
        )

    def host(self):
        """{cpus, memory_bytes} of the Docker host."""
        info = self.client.info()
        return {"cpus": info.get("NCPU"), "memory_bytes": info.get("MemTotal")}

    def logs(self, slug, lines=200):
        try:
            return self.client.containers.get(container_name(slug)).logs(tail=lines).decode(errors="replace")
        except self.errors.NotFound:
            return ""

    def restart(self, slug):
        self.client.containers.get(container_name(slug)).restart()

    def recreate(self, slug, api_key):
        """A new container for slug from the current IMAGE (pulled first)
        and settings, on the same volumes: an upgrade."""
        repository, _, tag = config.IMAGE.rpartition(":")
        if repository and "/" not in tag:
            try:
                self.client.images.pull(repository, tag=tag)
            except self.errors.APIError:
                pass  # a local-only image: use it as it is
        try:
            self.client.containers.get(container_name(slug)).remove(force=True)
        except self.errors.NotFound:
            pass
        self.client.containers.run(**container_spec(slug, api_key))

    def index_shared_gamedata(self):
        """Runs INDEX_SCRIPT over GAMEDATA_SHARED_DIR; returns what it
        printed, parsed."""
        output = self.client.containers.run(**indexer_spec())
        lines = output.decode(errors="replace").strip().splitlines()
        try:
            return json.loads(lines[-1])
        except (IndexError, ValueError):
            return {"error": "the indexer printed nothing useful: " + " ".join(lines[-3:])}

    def volume_sizes(self):
        """{slug: bytes} for every base's data volume, as Docker measures
        them (it walks each volume, so not every request)."""
        sizes = {}
        for volume in self.client.df().get("Volumes") or []:
            labels = volume.get("Labels") or {}
            slug = labels.get(LABEL) if ours(labels) else None
            size = (volume.get("UsageData") or {}).get("Size", -1)
            if slug and size >= 0:
                sizes[slug] = size
        return sizes

    def stop(self, slug):
        try:
            self.client.containers.get(container_name(slug)).stop()
        except self.errors.NotFound:
            pass

    def start(self, slug):
        try:
            self.client.containers.get(container_name(slug)).start()
        except self.errors.NotFound:
            pass

    def status(self, slug):
        """running, exited, ..., or None if there's no container."""
        try:
            return self.client.containers.get(container_name(slug)).status
        except self.errors.NotFound:
            return None

    def remove(self, slug):
        """Removes the container and its data volume, for good."""
        try:
            self.client.containers.get(container_name(slug)).remove(force=True)
        except self.errors.NotFound:
            pass
        for name in (volume_name(slug), gamedata_volume_name(slug)):
            try:
                self.client.volumes.get(name).remove(force=True)
            except self.errors.NotFound:
                pass
