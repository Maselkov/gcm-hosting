import pytest
from conftest import csrf, make_base, sign_in

from hosting import config, db, quota, stats

MB = 1024**2


def make(client, slug):
    assert make_base(client, slug).status_code == 201


@pytest.fixture()
def bases(app, provisioner):
    """Two players' bases: alice's and bob's."""
    for name in ("Alice", "Bob"):
        player = app.test_client()
        sign_in(player, name)
        make(player, name.lower())
    stats.reset()
    yield provisioner
    stats.reset()


def act(client, slug, action, **form):
    return client.post(f"/admin/bases/{slug}/{action}", data={"csrf": csrf(client), **form})


def test_only_operators_see_it(client, operator, bases):
    sign_in(client, "Alice")
    assert client.get("/admin/").status_code == 404
    assert client.get("/admin/bases/alice").status_code == 404
    assert act(client, "bob", "stop").status_code == 404
    assert bases.stopped == set()
    assert "Control panel" not in client.get("/").get_data(as_text=True)
    assert "Control panel" in operator.get("/").get_data(as_text=True)
    assert operator.get("/admin/").status_code == 200


def test_overview_shows_every_base_with_its_use_and_the_totals(operator, bases, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", 100 * MB)
    bases.sizes = {"alice": 90 * MB, "bob": 10 * MB}
    quota.check(bases)
    # Two readings: CPU is what was used between them.
    bases.samples = {"alice": (0, 0, 4, 120 * MB, 512 * MB), "bob": (0, 0, 4, 40 * MB, 512 * MB)}
    stats.sample_all(bases)
    bases.samples = {"alice": (500, 1000, 4, 130 * MB, 512 * MB), "bob": (50, 1000, 4, 40 * MB, 512 * MB)}
    stats.sample_all(bases)

    page = operator.get("/admin/").get_data(as_text=True)

    assert "alice" in page and "bob" in page and "dev:Alice" in page
    assert "200%" in page and "20%" in page  # alice used half of 4 cores' worth
    assert "CPU at its limit" in page  # alice, over her 50%
    assert "130.0 MB / 512.0 MB" in page and "90.0 MB / 100.0 MB" in page
    assert "disk near its quota" in page  # alice, at 90%
    assert "220%" in page  # the host's total CPU
    assert "170.0 MB" in page  # the host's total memory


def test_overview_sorts(operator, bases):
    bases.sizes = {"alice": 1 * MB, "bob": 50 * MB}
    quota.check(bases)
    assert "disk every 5 min" in operator.get("/admin/").get_data(as_text=True)
    page = operator.get("/admin/?sort=disk").get_data(as_text=True)
    assert page.index('"/admin/bases/bob"') < page.index('"/admin/bases/alice"')
    page = operator.get("/admin/?sort=name").get_data(as_text=True)
    assert page.index('"/admin/bases/alice"') < page.index('"/admin/bases/bob"')


def test_stopped_by_the_host_stays_stopped(operator, bases, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", 100 * MB)
    assert act(operator, "alice", "stop").status_code == 302
    assert "alice" in bases.stopped and db.base("alice")["held_at"]
    # Over quota and back under: the quota check leaves it stopped.
    bases.sizes = {"alice": 200 * MB}
    quota.check(bases)
    bases.sizes = {"alice": 10 * MB}
    quota.check(bases)
    assert "alice" in bases.stopped
    assert "stopped by host" in operator.get("/admin/").get_data(as_text=True)
    assert act(operator, "alice", "restart").status_code == 302
    assert ("restart", "alice") not in bases.calls

    act(operator, "alice", "start")
    assert "alice" not in bases.stopped and db.base("alice")["held_at"] is None


def test_a_base_over_quota_starts_only_once_it_fits(operator, bases, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", 100 * MB)
    bases.sizes = {"alice": 200 * MB}
    quota.check(bases)
    act(operator, "alice", "start")
    assert "alice" in bases.stopped
    assert "raise the quota" in operator.get("/admin/bases/alice").get_data(as_text=True)

    # Raising its quota starts it at once.
    act(operator, "alice", "quota", quota_mb="300")
    assert "alice" not in bases.stopped and db.base("alice")["quota_bytes"] == 300 * MB
    act(operator, "alice", "quota", quota_mb="")
    assert db.base("alice")["quota_bytes"] == config.QUOTA_BYTES and "alice" in bases.stopped
    act(operator, "alice", "quota", quota_mb="lots")
    assert "Give the quota in MB" in operator.get("/admin/bases/alice").get_data(as_text=True)


def test_recreate_keeps_a_stopped_base_stopped(operator, bases):
    act(operator, "bob", "stop")
    act(operator, "bob", "recreate")
    assert bases.calls[-1][:2] == ("recreate", "bob") and "bob" in bases.stopped
    act(operator, "alice", "recreate")
    assert "alice" not in bases.stopped


def test_a_reset_token_is_shown_once(operator, bases):
    page = act(operator, "alice", "reset-token").get_data(as_text=True)
    assert "gcm_tok-new-for-Alice" in page
    assert "gcm_tok-new-for-Alice" not in operator.get("/admin/bases/alice").get_data(as_text=True)

    bases.admins["alice"] = ["Alice", "Carol"]
    assert "gcm_tok-new-for-Carol" in act(operator, "alice", "reset-token", name="Carol").get_data(as_text=True)
    act(operator, "alice", "reset-token", name="Nobody")
    assert "Its admins: Alice, Carol" in operator.get("/admin/bases/alice").get_data(as_text=True)


def test_a_stopped_base_cant_have_its_token_reset(operator, bases):
    act(operator, "alice", "stop")
    act(operator, "alice", "reset-token")
    assert "Start it first" in operator.get("/admin/bases/alice").get_data(as_text=True)


def test_delete_needs_the_name_typed(operator, bases):
    act(operator, "bob", "delete", confirm="nope")
    assert db.base("bob") is not None and "bob" in bases.running
    assert act(operator, "bob", "delete", confirm="bob").status_code == 302
    assert db.base("bob") is None and "bob" not in bases.running
    assert operator.get("/admin/bases/bob").status_code == 404


def test_actions_need_the_csrf_token(operator, bases):
    assert operator.post("/admin/bases/alice/stop").status_code == 400
    assert operator.post("/admin/bases/alice/delete", data={"confirm": "alice"}).status_code == 400
    assert bases.stopped == set() and db.base("alice")


def test_base_page_shows_its_logs_and_settings(operator, bases):
    page = operator.get("/admin/bases/alice").get_data(as_text=True)
    assert "log line from alice" in page
    assert db.base("alice")["api_key"] in page


def test_base_page_shows_the_game_data_it_picked(operator, bases):
    assert "no game data picked" in operator.get("/admin/bases/alice").get_data(as_text=True)
    bases.picked["alice"] = {"version": "2.8.0", "textures": "faithful32"}
    assert "GTNH 2.8.0 (faithful32)" in operator.get("/admin/bases/alice").get_data(as_text=True)


def test_stats_forget_bases_that_stop_or_go(bases):
    bases.samples = {"alice": (0, 0, 1, MB, 2 * MB), "bob": (0, 0, 1, MB, 2 * MB)}
    stats.sample_all(bases)
    assert set(stats.current()) == {"alice", "bob"}
    assert stats.current()["alice"]["cpu_percent"] is None  # one reading isn't enough
    bases.stop("alice")
    db.release_base("bob")
    stats.sample_all(bases)
    assert stats.current() == {}


def test_renaming_moves_the_base_to_its_new_address(operator, bases):
    api_key = db.base("alice")["api_key"]
    response = act(operator, "alice", "rename", name="Maselkov")

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/bases/maselkov")
    assert "alice" not in bases.running and bases.running["maselkov"] == api_key
    assert db.base("alice") is None and db.base("maselkov")["owner_name"] == "Alice"
    assert "https://maselkov.example.test" in operator.get("/admin/bases/maselkov").get_data(as_text=True)


def test_a_stopped_base_stays_stopped_when_renamed(operator, bases):
    act(operator, "alice", "stop")
    act(operator, "alice", "rename", name="quiet")
    assert bases.status("quiet") == "exited"
    assert db.base("quiet")["held_at"] is not None


@pytest.mark.parametrize(
    ("name", "why"),
    [("bob", "taken"), ("test", "reserved"), ("-x", "letters"), ("a" * 41, "letters"), ("xn--abc", "letters")],
)
def test_a_name_thats_taken_reserved_or_not_a_dns_label_is_refused(operator, bases, name, why):
    act(operator, "alice", "rename", name=name)
    page = operator.get("/admin/bases/alice").get_data(as_text=True)
    assert why in page
    assert "alice" in bases.running and db.base("alice") is not None


def test_a_failed_rename_leaves_the_base_as_it_was(operator, bases):
    bases.fail_rename = True
    act(operator, "alice", "rename", name="newname")
    assert "Renaming failed" in operator.get("/admin/bases/alice").get_data(as_text=True)
    assert db.base("alice") is not None and db.base("newname") is None


def test_only_operators_can_rename(client, bases):
    sign_in(client, "Alice")
    assert act(client, "alice", "rename", name="mine").status_code == 404
    assert db.base("alice") is not None
