import pytest
from conftest import csrf, make_base, sign_in

from hosting import config, db, upgrade


@pytest.fixture()
def bases(app, provisioner):
    """Two players' bases, alice's and bob's, on the current image."""
    for name in ("Alice", "Bob"):
        player = app.test_client()
        sign_in(player, name)
        assert make_base(player, name.lower()).status_code == 201
    provisioner.calls.clear()
    return provisioner


def recreated(provisioner):
    return [call[1] for call in provisioner.calls if call[0] == "recreate"]


def test_a_base_on_an_older_image_is_recreated_on_the_new_one(bases, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    bases.images["alice"] = "sha256:old"
    assert upgrade.check(bases) == ["alice"]
    assert bases.calls[0] == ("pull",)
    # The shared index is built with the new image before the base starts on it.
    assert bases.calls.index(("index",)) < bases.calls.index(("recreate", "alice", db.base("alice")["api_key"]))
    assert recreated(bases) == ["alice"]


def test_bases_already_current_are_left_alone(bases):
    assert upgrade.check(bases) == []
    assert bases.calls == [("pull",)]


def test_a_failed_pull_upgrades_nothing(bases):
    bases.images["alice"] = "sha256:old"
    bases.pull_works = False
    assert upgrade.check(bases) is None
    assert recreated(bases) == []


def test_a_base_stopped_on_purpose_is_upgraded_and_stays_stopped(bases):
    bases.images["bob"] = "sha256:old"
    bases.stop("bob")
    db.set_held("bob", 1.0)
    assert upgrade.check(bases) == ["bob"]
    assert "bob" in bases.stopped


def test_one_failing_base_doesnt_stop_the_others(bases, monkeypatch):
    bases.images.update(alice="sha256:old", bob="sha256:old")
    real = bases.recreate

    def recreate(slug, api_key):
        if slug == "alice":
            raise RuntimeError("docker said no")
        real(slug, api_key)

    monkeypatch.setattr(bases, "recreate", recreate)
    assert upgrade.check(bases) == ["bob"]


def test_the_control_panel_flags_a_base_behind_a_moved_tag(operator, bases):
    # Same name as IMAGE, older image under it.
    bases.images["alice"] = "sha256:old"
    page = operator.get("/admin/").get_data(as_text=True)
    assert "1 base on an old image" in page
    assert "(6.1.0)" in operator.get("/admin/bases/bob").get_data(as_text=True)


def test_off_without_a_check_interval(provisioner, monkeypatch):
    monkeypatch.setattr(config, "UPGRADE_CHECK_SECONDS", 0)
    upgrade.start(provisioner)
    assert provisioner.calls == []


def test_the_control_panel_checks_on_request(operator, bases):
    bases.images["alice"] = "sha256:old"
    page = operator.post("/admin/upgrade", data={"csrf": csrf(operator)}, follow_redirects=True).get_data(as_text=True)
    assert "Recreated alice on the new" in page
    assert recreated(bases) == ["alice"]
    page = operator.post("/admin/upgrade", data={"csrf": csrf(operator)}, follow_redirects=True).get_data(as_text=True)
    assert "Every base is already on the newest" in page
    bases.pull_works = False
    page = operator.post("/admin/upgrade", data={"csrf": csrf(operator)}, follow_redirects=True).get_data(as_text=True)
    assert "Couldn&#39;t pull" in page
    assert operator.post("/admin/upgrade").status_code == 400  # no CSRF token


def test_players_cant_check(app, bases):
    player = app.test_client()
    sign_in(player, "Alice")
    assert player.post("/admin/upgrade", data={"csrf": csrf(player)}).status_code == 404
    assert recreated(bases) == []


def test_recreate_all_recreates_every_base_on_any_image(bases, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")
    bases.stop("bob")
    db.set_held("bob", 1.0)
    assert upgrade.recreate_all(bases) == ["alice", "bob"]
    assert ("pull",) not in bases.calls
    assert bases.calls.index(("index",)) < bases.calls.index(("recreate", "alice", db.base("alice")["api_key"]))
    assert "bob" in bases.stopped


def test_the_control_panel_recreates_all_on_request(operator, bases, monkeypatch):
    page = operator.post("/admin/recreate-all", data={"csrf": csrf(operator)}, follow_redirects=True).get_data(
        as_text=True
    )
    assert "Recreated 2 bases from" in page
    assert sorted(recreated(bases)) == ["alice", "bob"]
    real = bases.recreate

    def recreate(slug, api_key):
        if slug == "alice":
            raise RuntimeError("docker said no")
        real(slug, api_key)

    monkeypatch.setattr(bases, "recreate", recreate)
    page = operator.post("/admin/recreate-all", data={"csrf": csrf(operator)}, follow_redirects=True).get_data(
        as_text=True
    )
    assert "Recreated 1 base from" in page and "1 failed" in page
    assert operator.post("/admin/recreate-all").status_code == 400  # no CSRF token


def test_players_cant_recreate_all(app, bases):
    player = app.test_client()
    sign_in(player, "Alice")
    assert player.post("/admin/recreate-all", data={"csrf": csrf(player)}).status_code == 404
    assert recreated(bases) == []
