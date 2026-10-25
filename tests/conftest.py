import re
from unittest import mock

import pytest

from hosting import config, create_app, indexer, names, provision


class FakeProvisioner:
    """Records what would be started, without Docker."""

    def __init__(self):
        self.running = {}
        self.stopped = set()
        self.sizes = {}
        self.samples = {}  # slug -> provision.sample()'s tuple
        self.calls = []
        self.admins = {}  # slug -> admin names, for reset_admin_token()
        # What index_shared_gamedata() returns, or an exception it raises.
        self.index_result = {"versions": [{"version": "2.9.0", "state": "current"}]}
        # What download_shared_gamedata() returns, or an exception it raises.
        self.download_result = {"versions": [{"version": "2.9.0", "state": "current"}]}
        self.pull_works = True
        self.fail_admin = False
        self.private = set()
        self.fail_rename = False
        self.reports = {}  # slug -> last_report()'s answer, or an exception
        self.fail_private = False
        self.current_image = "sha256:new"  # what image_id() returns
        self.images = {}  # slug -> its container's image ID, if not the current one

    def create(self, slug, api_key):
        assert slug not in self.running
        self.running[slug] = api_key

    def create_admin(self, slug, name):
        if self.fail_admin:
            raise RuntimeError("container exited")
        return f"gcm_token_for_{name}"

    def last_report(self, slug):
        report = self.reports.get(slug)
        if isinstance(report, Exception):
            raise report
        return report

    def rename(self, old, new, api_key):
        if self.fail_rename:
            raise RuntimeError("copy failed")
        self.running[new] = self.running.pop(old)
        if old in self.stopped:
            self.stopped.discard(old)
            self.stopped.add(new)

    def make_private(self, slug):
        if self.fail_private:
            raise provision.TooOld("unknown command")
        self.private.add(slug)

    def details(self):
        return {
            slug: {
                "status": self.status(slug),
                "image": config.IMAGE,
                "image_id": self.images.get(slug, self.current_image),
                "version": "6.1.0",
                "started_at": "2026-10-25T10:00:00.000Z",
                "restarts": 0,
                "oom_killed": False,
                "cpu_limit_percent": 50.0,
            }
            for slug in self.running
        }

    def sample(self, slug):
        return self.samples.get(slug) if self.status(slug) == "running" else None

    def host(self):
        return {"cpus": 4, "memory_bytes": 8 * 1024**3}

    def logs(self, slug, lines=200):
        return f"log line from {slug}\n"

    def restart(self, slug):
        self.calls.append(("restart", slug))

    def recreate(self, slug, api_key):
        self.calls.append(("recreate", slug, api_key))
        self.stopped.discard(slug)
        self.images.pop(slug, None)

    def reset_admin_token(self, slug, name=""):
        admins = self.admins.get(slug, ["Alice"])
        if name and name not in admins:
            raise ValueError(f"No admin named {name!r}. Its admins: {', '.join(admins)}.")
        return {"name": name or admins[0], "token": f"gcm_tok-new-for-{name or admins[0]}"}

    def pull_image(self):
        self.calls.append(("pull",))
        return self.pull_works

    def image_id(self):
        return self.current_image

    def index_shared_gamedata(self):
        self.calls.append(("index",))
        if isinstance(self.index_result, Exception):
            raise self.index_result
        return self.index_result

    def download_shared_gamedata(self):
        self.calls.append(("download",))
        if isinstance(self.download_result, Exception):
            raise self.download_result
        return self.download_result

    def volume_sizes(self):
        return dict(self.sizes)

    def stop(self, slug):
        self.stopped.add(slug)

    def start(self, slug):
        self.stopped.discard(slug)

    def status(self, slug):
        if slug not in self.running:
            return None
        return "exited" if slug in self.stopped else "running"

    def remove(self, slug):
        self.running.pop(slug, None)
        self.private.discard(slug)
        self.stopped.discard(slug)


@pytest.fixture()
def provisioner():
    return FakeProvisioner()


@pytest.fixture()
def app(tmp_path, monkeypatch, provisioner):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "DEV_LOGIN", True)
    monkeypatch.setattr(config, "DOMAIN", "example.test")
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "")
    indexer.reset()
    return create_app(provisioner)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def operator(app, monkeypatch):
    """A client signed in as an operator (ADMIN_IDENTITIES)."""
    monkeypatch.setattr(config, "ADMIN_IDENTITIES", {"dev:Root"})
    client = app.test_client()
    sign_in(client, "Root")
    return client


def csrf(client):
    page = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def sign_in(client, name):
    response = client.post("/login/dev", data={"name": name, "csrf": csrf(client)})
    assert response.status_code == 302


def make_base(client, slug):
    """Makes a base named slug, in place of a made-up name."""
    with mock.patch.object(names, "generate", return_value=slug):
        return client.post("/bases", data={"csrf": csrf(client)})
