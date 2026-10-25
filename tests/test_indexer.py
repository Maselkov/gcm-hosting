from conftest import csrf, sign_in

from hosting import config, indexer, provision


def make(client, slug):
    return client.post("/bases", data={"slug": slug, "csrf": csrf(client)})


def test_without_shared_game_data_nothing_is_indexed(client, provisioner):
    assert indexer.run(provisioner) is None
    sign_in(client, "Alice")
    assert make(client, "alice").status_code == 201
    assert ("index",) not in provisioner.calls


def test_a_new_base_waits_for_the_index(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    sign_in(client, "Alice")
    make(client, "alice")
    assert provisioner.calls[0] == ("index",)
    assert indexer.last()["result"]["versions"][0]["version"] == "2.9.0"


def test_a_failed_index_run_doesnt_stop_a_base_being_made(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    provisioner.index_result = RuntimeError("no such image")
    sign_in(client, "Alice")
    assert make(client, "alice").status_code == 201
    assert indexer.last()["result"] == {"error": "no such image"}


def test_recreating_indexes_first(operator, provisioner, monkeypatch, app):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    player = app.test_client()
    sign_in(player, "Alice")
    make(player, "alice")
    provisioner.calls.clear()
    operator.post("/admin/bases/alice/recreate", data={"csrf": csrf(operator)})
    assert [c[0] for c in provisioner.calls] == ["index", "recreate"]


def test_the_panel_shows_the_shared_versions_and_indexes_on_request(operator, provisioner, monkeypatch):
    assert "Shared game data" not in operator.get("/admin/").get_data(as_text=True)
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    assert "Not checked yet" in operator.get("/admin/").get_data(as_text=True)

    provisioner.index_result = {
        "versions": [
            {"version": "2.9.0", "state": "built"},
            {"version": "2.8.4", "state": "failed", "error": "bad zip"},
        ]
    }
    operator.post("/admin/gamedata/index", data={"csrf": csrf(operator)})
    page = operator.get("/admin/").get_data(as_text=True)
    assert "Indexed 2.9.0." in page and "just indexed" in page and "bad zip" in page

    provisioner.index_result = {"error": "this image has no game data index"}
    operator.post("/admin/gamedata/index", data={"csrf": csrf(operator)})
    assert "this image has no game data index" in operator.get("/admin/").get_data(as_text=True)
    assert operator.post("/admin/gamedata/index").status_code == 400  # no CSRF token


def test_the_indexer_writes_the_shared_folder_with_the_bases_image(monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    spec = provision.indexer_spec()
    assert spec["image"] == config.IMAGE
    assert spec["volumes"] == {"/srv/gamedata": {"bind": "/gamedata", "mode": "rw"}}
    assert spec["network_disabled"] and spec["remove"]
    # Bases still mount it read-only.
    assert provision.container_spec("alice", "k" * 64)["volumes"]["/srv/gamedata"]["mode"] == "ro"
