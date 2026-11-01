import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading

import pytest
from conftest import make_base, sign_in

from hosting import config, indexer, provision


class FakeGitHub:
    """Serves a repo's releases and their assets, as GitHub does."""

    def __init__(self):
        self.files = {}  # path -> bytes
        self.releases = []
        self.requests = []
        github = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                github.requests.append(self.path)
                if self.path.startswith("/repos/owner/data/releases"):
                    body = json.dumps(github.releases).encode()
                elif self.path in github.files:
                    body = github.files[self.path]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version, asset_id, contents, data=None, draft=False):
        """A gtnh-data-<version> release of {name: bytes}, with data.json
        listing their checksums (or data, as given)."""
        if data is None:
            data = {"files": {n: {"sha256": hashlib.sha256(b).hexdigest()} for n, b in contents.items()}}
        contents = {"data.json": json.dumps(data).encode(), **contents}
        assets = []
        for name, body in contents.items():
            path = f"/dl/{version}/{asset_id}/{name}"
            self.files[path] = body
            assets.append(
                {
                    "name": name,
                    "id": asset_id if name == "data.json" else asset_id * 100 + len(assets),
                    "size": len(body),
                    "browser_download_url": self.url + path,
                }
            )
        self.releases = [r for r in self.releases if r["tag_name"] != f"gtnh-data-{version}"]
        self.releases.append({"tag_name": f"gtnh-data-{version}", "draft": draft, "assets": assets})


BUNDLE = {
    "icons_lookup.json": b"{}",
    "item_catalog.txt": b"catalog",
    "images.zip": b"default icons",
    "images-faithful32.zip": b"faithful icons",
}


@pytest.fixture()
def github():
    github = FakeGitHub()
    yield github
    github.server.shutdown()


def download(root, github):
    result = subprocess.run(
        [sys.executable, "-I", "-c", provision.DOWNLOAD_SCRIPT, str(root), github.url, "owner/data", "gcm"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def states(result):
    return {v["version"]: v["state"] for v in result["versions"]}


def test_every_published_version_is_downloaded_with_every_texture_set(tmp_path, github):
    github.publish("2.8.4", 1, BUNDLE)
    github.publish("2.9.0", 2, {**BUNDLE, "ore_dict.json": b"{}", "item_info.json": b"{}"})
    github.publish("2.9.1", 3, BUNDLE, draft=True)
    github.publish("2.9.2", 4, {"icons_lookup.json": b"{}", "item_catalog.txt": b""})  # no icons
    assert states(download(tmp_path, github)) == {"2.8.4": "downloaded", "2.9.0": "downloaded"}
    assert (tmp_path / "2.8.4" / "images-faithful32.zip").read_bytes() == b"faithful icons"
    assert (tmp_path / "2.9.0" / "ore_dict.json").exists()
    assert json.loads((tmp_path / "2.9.0" / "release.json").read_text()) == {"data_asset_id": 2}
    # Nothing half-done is left where a base or the indexer would look.
    assert sorted(os.listdir(tmp_path)) == [".incoming", "2.8.4", "2.9.0"]
    assert os.listdir(tmp_path / ".incoming") == []

    github.requests.clear()
    assert states(download(tmp_path, github)) == {"2.8.4": "current", "2.9.0": "current"}
    assert len(github.requests) == 1  # only the release list


def test_a_rebuilt_release_replaces_the_version(tmp_path, github):
    github.publish("2.9.0", 1, BUNDLE)
    download(tmp_path, github)
    github.publish("2.9.0", 2, {**BUNDLE, "images.zip": b"new icons"})
    assert states(download(tmp_path, github)) == {"2.9.0": "downloaded"}
    assert (tmp_path / "2.9.0" / "images.zip").read_bytes() == b"new icons"


def test_a_missing_texture_set_is_fetched_alone(tmp_path, github):
    github.publish("2.9.0", 1, BUNDLE)
    download(tmp_path, github)
    (tmp_path / "2.9.0" / "images-faithful32.zip").unlink()
    github.requests.clear()
    assert states(download(tmp_path, github)) == {"2.9.0": "downloaded"}
    assert (tmp_path / "2.9.0" / "images-faithful32.zip").exists()
    assert github.requests[1:] == ["/dl/2.9.0/1/images-faithful32.zip"]
    assert os.listdir(tmp_path / ".incoming") == []


def test_recipes_are_fetched_for_a_version_downloaded_before_they_were(tmp_path, github):
    github.publish("2.9.0", 1, {**BUNDLE, "recipes.json.gz": b"recipes"})
    download(tmp_path, github)
    assert (tmp_path / "2.9.0" / "recipes.json.gz").read_bytes() == b"recipes"
    # As a version downloaded when this didn't fetch recipes has it.
    (tmp_path / "2.9.0" / "recipes.json.gz").unlink()
    (tmp_path / "2.9.0" / "index.sqlite").write_bytes(b"index")
    github.requests.clear()
    assert states(download(tmp_path, github)) == {"2.9.0": "downloaded"}
    assert (tmp_path / "2.9.0" / "recipes.json.gz").read_bytes() == b"recipes"
    # Not the whole version again, and its index is kept.
    assert github.requests[1:] == ["/dl/2.9.0/1/recipes.json.gz"]
    assert (tmp_path / "2.9.0" / "index.sqlite").exists()


def test_a_missing_file_with_a_bad_checksum_isnt_put_in_place(tmp_path, github):
    github.publish("2.9.0", 1, {**BUNDLE, "recipes.json.gz": b"recipes"})
    download(tmp_path, github)
    (tmp_path / "2.9.0" / "recipes.json.gz").unlink()
    github.files["/dl/2.9.0/1/recipes.json.gz"] = b"something else"
    result = download(tmp_path, github)
    assert result["versions"][0]["state"] == "failed" and "checksum" in result["versions"][0]["error"]
    assert not (tmp_path / "2.9.0" / "recipes.json.gz").exists()
    assert os.listdir(tmp_path / ".incoming") == []


def test_a_version_put_there_by_hand_is_left_alone(tmp_path, github):
    (tmp_path / "2.9.0").mkdir()
    (tmp_path / "2.9.0" / "data.json").write_text("{}")
    github.publish("2.9.0", 1, BUNDLE)
    assert states(download(tmp_path, github)) == {"2.9.0": "manual"}
    assert os.listdir(tmp_path / "2.9.0") == ["data.json"]


def test_a_bad_checksum_keeps_what_was_there(tmp_path, github):
    github.publish("2.9.0", 1, BUNDLE)
    download(tmp_path, github)
    data = {"files": {n: {"sha256": "0" * 64} for n in BUNDLE}}
    github.publish("2.9.0", 2, {**BUNDLE, "images.zip": b"new icons"}, data=data)
    result = download(tmp_path, github)
    assert result["versions"][0]["state"] == "failed"
    assert "checksum" in result["versions"][0]["error"]
    assert (tmp_path / "2.9.0" / "images.zip").read_bytes() == b"default icons"
    assert os.listdir(tmp_path / ".incoming") == []


def test_github_unreachable_is_an_error(tmp_path, github):
    github.server.shutdown()
    github.server.server_close()
    assert "couldn't list owner/data's releases" in download(tmp_path, github)["error"]


def test_the_downloader_writes_the_shared_folder_with_the_network(monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    spec = provision.downloader_spec()
    assert spec["image"] == config.IMAGE
    assert spec["volumes"] == {"/srv/gamedata": {"bind": "/gamedata", "mode": "rw"}}
    assert "network_disabled" not in spec and spec["remove"]
    assert spec["command"][3:] == ["/gamedata", "https://api.github.com", config.GAMEDATA_REPO, config.INSTANCE]


def test_downloading_is_off_without_a_shared_folder_or_when_turned_off(provisioner, monkeypatch):
    indexer.reset()
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "")
    assert indexer.download(provisioner) is None
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    monkeypatch.setattr(config, "GAMEDATA_DOWNLOAD", False)
    assert indexer.download(provisioner) is None
    assert provisioner.calls == []
    monkeypatch.setattr(config, "GAMEDATA_DOWNLOAD", True)
    provisioner.download_result = RuntimeError("no network")
    assert indexer.download(provisioner) == {"error": "no network"}
    assert indexer.last_download()["result"] == {"error": "no network"}


def test_the_panel_shows_what_was_downloaded(operator, provisioner, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    monkeypatch.setattr(config, "GAMEDATA_DOWNLOAD", False)
    assert "is downloaded there" not in operator.get("/admin/").get_data(as_text=True)
    monkeypatch.setattr(config, "GAMEDATA_DOWNLOAD", True)
    assert "the first download takes a while" in operator.get("/admin/").get_data(as_text=True)
    provisioner.download_result = {
        "versions": [
            {"version": "2.9.0", "state": "downloaded"},
            {"version": "2.8.4", "state": "manual"},
            {"version": "2.8.3", "state": "failed", "error": "timed out"},
        ]
    }
    indexer.download(provisioner)
    page = operator.get("/admin/").get_data(as_text=True)
    assert "Just downloaded 2.9.0." in page
    assert "left alone: 2.8.4" in page
    assert "Downloading 2.8.3 failed: timed out" in page


@pytest.fixture()
def bases(app, provisioner, monkeypatch):
    """alice's, bob's and carol's bases on 2.9.0, 2.8.4 and 2.9.0, with the
    shared game data downloaded."""
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    for name, version in (("Alice", "2.9.0"), ("Bob", "2.8.4"), ("Carol", "2.9.0")):
        player = app.test_client()
        sign_in(player, name)
        assert make_base(player, name.lower()).status_code == 201
        provisioner.picked[name.lower()] = {"version": version, "textures": "default"}
    provisioner.calls.clear()
    return provisioner


def restarted(provisioner):
    return [call[1] for call in provisioner.calls if call[0] == "restart"]


def test_running_bases_on_a_re_downloaded_version_restart_once_its_indexed(bases):
    bases.download_result = {"versions": [{"version": "2.9.0", "state": "downloaded"}]}
    bases.index_result = {
        "versions": [{"version": "2.9.0", "state": "built"}, {"version": "2.8.4", "state": "current"}]
    }
    bases.stopped.add("carol")
    indexer.refresh(bases)
    assert restarted(bases) == ["alice"]
    assert bases.calls.index(("download",)) < bases.calls.index(("index",), 1) < bases.calls.index(("restart", "alice"))


def test_nothing_restarts_when_nothing_was_downloaded(bases):
    indexer.refresh(bases)
    assert restarted(bases) == []
    assert bases.calls == [("index",), ("download",)]


def test_a_version_whose_index_failed_doesnt_restart_its_bases(bases):
    bases.download_result = {"versions": [{"version": "2.9.0", "state": "downloaded"}]}
    bases.index_result = {"versions": [{"version": "2.9.0", "state": "failed", "error": "bad"}]}
    indexer.refresh(bases)
    assert restarted(bases) == []
    bases.index_result = RuntimeError("docker down")
    indexer.refresh(bases)
    assert restarted(bases) == []
