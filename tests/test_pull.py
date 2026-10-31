from unittest import mock

import app as entrypoint
from hosting import config, indexer, provision, upgrade


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
    monkeypatch.setattr(upgrade, "start", lambda p: None)
    entrypoint.warm_up(provisioner)
    assert started == [[("pull",)]]

    provisioner.pull_works = False
    entrypoint.warm_up(provisioner)  # still starts, on the local copy
    assert len(started) == 2


def selected_archive(content):
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("selected.json")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return [buffer.getvalue()], {}


def test_a_bases_picked_game_data_is_read_from_its_container():
    provisioner = docker_provisioner()
    archive = provisioner.client.containers.get.return_value.get_archive
    archive.return_value = selected_archive(b'{"version": "2.8.0", "textures": null, "previous": null}')
    assert provisioner.gamedata("alice") == {"version": "2.8.0", "textures": "default"}
    archive.assert_called_once_with("/app/data/gamedata/selected.json")
    archive.side_effect = APIError("no such file")  # never picked
    assert provisioner.gamedata("alice") is None
    archive.side_effect, archive.return_value = None, selected_archive(b"not json")
    assert provisioner.gamedata("alice") is None
