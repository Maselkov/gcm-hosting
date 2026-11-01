import json
import os
import subprocess
import sys

from conftest import csrf, make_base, sign_in

from hosting import config, indexer, provision


def test_without_shared_game_data_nothing_is_indexed(client, provisioner):
    assert indexer.run(provisioner) is None
    sign_in(client, "Alice")
    assert make_base(client, "alice").status_code == 201
    assert ("index",) not in provisioner.calls


def test_a_new_base_waits_for_the_index(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    sign_in(client, "Alice")
    make_base(client, "alice")
    assert provisioner.calls[0] == ("index",)
    assert indexer.last()["result"]["versions"][0]["version"] == "2.9.0"


def test_a_failed_index_run_doesnt_stop_a_base_being_made(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    provisioner.index_result = RuntimeError("no such image")
    sign_in(client, "Alice")
    assert make_base(client, "alice").status_code == 201
    assert indexer.last()["result"] == {"error": "no such image"}


def test_recreating_indexes_first(operator, provisioner, monkeypatch, app):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    player = app.test_client()
    sign_in(player, "Alice")
    make_base(player, "alice")
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


# Stand-ins for the bases' image's gcm/gamedata_index.py and
# gcm/recipe_index.py: an index is a copy of what it's built from, and is
# current while that's unchanged; "bad" can't be indexed.
FAKE_INDEX = """
import os
def is_current(path, source):
    return os.path.isfile(path) and open(path).read() == _read(source)
def build(source, path):
    if _read(source) == "bad":
        raise ValueError("not an index this server knows")
    with open(path, "w") as f:
        f.write(_read(source))
def _read(source):
    return open(os.path.join(source, "data.json") if os.path.isdir(source) else source).read()
"""


def fake_gcm(tmp_path, recipe_file=True):
    gcm = tmp_path / "image" / "gcm"
    gcm.mkdir(parents=True)
    (gcm / "__init__.py").write_text("")
    (gcm / "gamedata_index.py").write_text(FAKE_INDEX + 'FILE = "index.sqlite"\n')
    # Before it read an index beside the bundle, recipe_index had no FILE.
    (gcm / "recipe_index.py").write_text(FAKE_INDEX + ('FILE = "recipes.sqlite"\n' if recipe_file else ""))
    return tmp_path / "image"


def index(image, root):
    result = subprocess.run(
        [sys.executable, "-c", provision.INDEX_SCRIPT, str(root)],
        env={**os.environ, "PYTHONPATH": str(image)},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def bundle(root, version, data="data", recipes=None):
    path = root / version
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.json").write_text(data)
    if recipes is not None:
        (path / "recipes.json.gz").write_text(recipes)
    return path


def test_the_index_script_builds_the_game_data_and_recipe_indexes(tmp_path):
    image, root = fake_gcm(tmp_path), tmp_path / "shared"
    with_recipes = bundle(root, "2.9.0", recipes="recipes")
    bundle(root, "2.8.3")  # built before recipes were exported
    assert index(image, root)["versions"] == [
        {"version": "2.8.3", "state": "built"},
        {"version": "2.9.0", "state": "built"},
    ]
    assert (with_recipes / "recipes.sqlite").read_text() == "recipes"
    assert sorted(os.listdir(root / "2.8.3")) == ["data.json", "index.sqlite"]
    assert {v["state"] for v in index(image, root)["versions"]} == {"current"}
    # A version's recipes replaced on their own: just that index again.
    (with_recipes / "recipes.json.gz").write_text("new recipes")
    assert index(image, root)["versions"][1] == {"version": "2.9.0", "state": "built"}
    assert (with_recipes / "recipes.sqlite").read_text() == "new recipes"


def test_a_failed_recipe_index_is_said_and_the_game_data_still_indexed(tmp_path):
    image, root = fake_gcm(tmp_path), tmp_path / "shared"
    path = bundle(root, "2.9.0", recipes="bad")
    assert index(image, root)["versions"] == [
        {"version": "2.9.0", "state": "failed", "error": "recipes: not an index this server knows"}
    ]
    assert (path / "index.sqlite").exists()


def test_an_image_that_doesnt_read_a_shared_recipe_index_isnt_given_one(tmp_path):
    image, root = fake_gcm(tmp_path, recipe_file=False), tmp_path / "shared"
    path = bundle(root, "2.9.0", recipes="recipes")
    assert index(image, root)["versions"] == [{"version": "2.9.0", "state": "built"}]
    assert not (path / "recipes.sqlite").exists()
