"""Runs each base as its own container of the GTNH Craft Monitor image,
with its own data volume, behind Traefik at <slug>.DOMAIN.

A base is exactly a self-hosted server: its users, tokens, roles and
data live in its volume, and this app only starts, stops and removes it.
Bases never run code their players supply, so sharing one network is
safe; the limits keep one busy base from starving the rest."""

import time

from hosting import config

DATA_PATH = "/app/data"
SHARED_GAMEDATA_PATH = "/gamedata-shared"
PORT = 8420
LABEL = "gcm-hosting.slug"


def container_name(slug):
    return f"gcm-base-{slug}"


def volume_name(slug):
    return f"gcm-base-{slug}-data"


def container_spec(slug, api_key):
    """docker-py's containers.run() arguments for slug's base."""
    router = f"gcm-{slug}"
    labels = {
        LABEL: slug,
        "traefik.enable": "true",
        "traefik.docker.network": config.NETWORK,
        f"traefik.http.routers.{router}.rule": f"Host(`{slug}.{config.DOMAIN}`)",
        f"traefik.http.services.{router}.loadbalancer.server.port": str(PORT),
    }
    if config.DOMAIN != "localhost":
        labels[f"traefik.http.routers.{router}.entrypoints"] = "websecure"
        labels[f"traefik.http.routers.{router}.tls.certresolver"] = config.CERT_RESOLVER
    environment = {
        "API_KEY": api_key,
        "PUBLIC_URL": config.base_url(slug),
        "SESSION_COOKIE_SECURE": "0" if config.DOMAIN == "localhost" else "1",
        "TRUSTED_PROXIES": config.NETWORK_SUBNET,
    }
    volumes = {volume_name(slug): {"bind": DATA_PATH, "mode": "rw"}}
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
        self.client.volumes.create(volume_name(slug), labels={LABEL: slug})
        self.client.containers.run(**container_spec(slug, api_key))

    def create_admin(self, slug, name, timeout=60):
        """Makes the base's first admin and returns their sign-in token.
        Waits for the server to finish starting, so the two don't both
        create the databases."""
        container = self.client.containers.get(container_name(slug))
        deadline = time.monotonic() + timeout
        while b"Serving on" not in container.logs():
            if time.monotonic() > deadline:
                raise RuntimeError(f"{slug} didn't start within {timeout}s")
            container.reload()
            if container.status == "exited":
                raise RuntimeError(f"{slug} exited while starting")
            time.sleep(0.5)
        result = container.exec_run(["python", "app.py", "create-admin", name])
        output = result.output.decode(errors="replace").strip()
        if result.exit_code != 0:
            raise RuntimeError(f"create-admin failed for {slug}: {output}")
        return output.splitlines()[-1]

    def volume_sizes(self):
        """{slug: bytes} for every base's data volume, as Docker measures
        them (it walks each volume, so not every request)."""
        sizes = {}
        for volume in self.client.df().get("Volumes") or []:
            slug = (volume.get("Labels") or {}).get(LABEL)
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
        try:
            self.client.volumes.get(volume_name(slug)).remove(force=True)
        except self.errors.NotFound:
            pass
