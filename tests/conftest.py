import re

import pytest

from hosting import config, create_app


class FakeProvisioner:
    """Records what would be started, without Docker."""

    def __init__(self):
        self.running = {}
        self.stopped = set()
        self.sizes = {}
        self.fail_admin = False

    def create(self, slug, api_key):
        assert slug not in self.running
        self.running[slug] = api_key

    def create_admin(self, slug, name):
        if self.fail_admin:
            raise RuntimeError("container exited")
        return f"gcm_token_for_{name}"

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
        self.stopped.discard(slug)


@pytest.fixture()
def provisioner():
    return FakeProvisioner()


@pytest.fixture()
def app(tmp_path, monkeypatch, provisioner):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "DEV_LOGIN", True)
    monkeypatch.setattr(config, "DOMAIN", "example.net")
    return create_app(provisioner)


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    page = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def sign_in(client, name):
    response = client.post("/login/dev", data={"name": name, "csrf": csrf(client)})
    assert response.status_code == 302
