import time

import pytest
from conftest import make_base, sign_in

from hosting import activity, config, db

DAY = 86400


@pytest.fixture()
def base(client, provisioner):
    """Alice's base, made 8 days ago, which the game never reported to."""
    sign_in(client, "Alice")
    assert make_base(client, "copper-falcon-4821").status_code == 201
    with db.transaction() as conn:
        conn.execute("UPDATE bases SET created_at = ?", (time.time() - 8 * DAY,))
    return "copper-falcon-4821"


def test_a_base_the_game_never_reported_to_is_removed_after_a_week(base, provisioner, client):
    assert "Not connected yet" in client.get("/").get_data(as_text=True)

    assert activity.check(provisioner) == [base]

    assert provisioner.running == {}
    assert db.owner_of(base) is None


def test_not_before_its_week_is_up(base, provisioner, monkeypatch):
    monkeypatch.setattr(config, "NEVER_CONNECTED_DAYS", 10)
    assert activity.check(provisioner) == []
    monkeypatch.setattr(config, "NEVER_CONNECTED_DAYS", 0)  # off
    assert activity.check(provisioner) == []
    assert base in provisioner.running


def test_a_base_that_reported_once_is_never_removed(base, provisioner):
    provisioner.reports[base] = time.time() - 300 * DAY
    assert activity.check(provisioner) == []
    assert db.base(base)["last_report_at"] == provisioner.reports[base]

    # Its data gone since (or a check that can't tell): still kept.
    provisioner.reports[base] = None
    assert activity.check(provisioner) == []
    assert base in provisioner.running


@pytest.mark.parametrize("why", ["stopped", "held", "check failed"])
def test_only_a_running_base_that_answers_never_is_removed(base, provisioner, why):
    if why == "stopped":
        provisioner.stop(base)
    elif why == "held":
        db.set_held(base, time.time())
    else:
        provisioner.reports[base] = RuntimeError("exec failed")
    assert activity.check(provisioner) == []
    assert db.owner_of(base) is not None


def test_one_unused_for_60_days_is_flagged_not_removed(base, provisioner, client, operator):
    provisioner.reports[base] = time.time() - 61 * DAY
    activity.check(provisioner)

    assert "hasn't reported for 61 days" in client.get("/").get_data(as_text=True)
    panel = operator.get("/admin/").get_data(as_text=True)
    assert "no game report for 61 days" in panel
    assert base in provisioner.running

    provisioner.reports[base] = time.time() - 59 * DAY
    activity.check(provisioner)
    assert "no game report" not in operator.get("/admin/").get_data(as_text=True)


def test_the_panel_says_when_the_game_never_reported(base, operator):
    page = operator.get("/admin/").get_data(as_text=True)
    assert "game never reported" in page and "never</span>" in page
    assert "game never reported (removed" in operator.get(f"/admin/bases/{base}").get_data(as_text=True)


def test_a_new_base_isnt_flagged_on_its_first_day(client, operator):
    sign_in(client, "Bob")
    make_base(client, "quiet-rook-4833")
    assert "game never reported" not in operator.get("/admin/").get_data(as_text=True)
