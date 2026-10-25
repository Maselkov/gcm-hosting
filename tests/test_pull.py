from unittest import mock

import app as entrypoint
from hosting import config, indexer, provision


class APIError(Exception):
    pass


def docker_provisioner(pull_error=None):
    provisioner = provision.DockerProvisioner.__new__(provision.DockerProvisioner)
    provisioner.client = mock.Mock()
    provisioner.client.images.pull.side_effect = pull_error
    provisioner.errors = mock.Mock(APIError=APIError)
    return provisioner


def test_the_image_is_pulled_by_its_tag(monkeypatch):
    monkeypatch.setattr(config, "IMAGE", "ghcr.io/maselkov/gtnh-craft-monitor:6.1")
    provisioner = docker_provisioner()
    assert provisioner.pull_image()
    provisioner.client.images.pull.assert_called_once_with("ghcr.io/maselkov/gtnh-craft-monitor", tag="6.1")


def test_a_local_only_image_or_a_registry_thats_down_is_used_as_it_is(monkeypatch):
    monkeypatch.setattr(config, "IMAGE", "ghcr.io/maselkov/gtnh-craft-monitor:6.1")
    assert not docker_provisioner(pull_error=APIError("not found")).pull_image()
    monkeypatch.setattr(config, "IMAGE", "localhost:5000/gcm")  # no tag: the port isn't one
    provisioner = docker_provisioner()
    assert not provisioner.pull_image()
    provisioner.client.images.pull.assert_not_called()


def test_startup_pulls_before_the_indexer_starts(provisioner, monkeypatch):
    started = []
    monkeypatch.setattr(indexer, "start", lambda p: started.append(list(p.calls)))
    entrypoint.warm_up(provisioner)
    assert started == [[("pull",)]]

    provisioner.pull_works = False
    entrypoint.warm_up(provisioner)  # still starts, on the local copy
    assert len(started) == 2
