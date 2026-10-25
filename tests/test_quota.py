from conftest import csrf, sign_in

from hosting import config, db, quota

GB = 1024**3


def make(client, slug):
    assert client.post("/bases", data={"slug": slug, "csrf": csrf(client)}).status_code == 201


def test_a_base_over_quota_is_stopped_and_its_card_says_so(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", GB)
    sign_in(client, "Alice")
    make(client, "small")
    make_other = client.application.test_client()
    sign_in(make_other, "Bob")
    make(make_other, "big")
    provisioner.sizes = {"small": 300 * 1024**2, "big": 2 * GB}

    assert quota.check(provisioner) == ["big"]

    assert provisioner.stopped == {"big"}
    page = make_other.get("/").get_data(as_text=True)
    assert "over quota" in page and "2.0 GB" in page
    assert "Disk: 300.0 MB of 1.0 GB" in client.get("/").get_data(as_text=True)
    # Already stopped: not stopped again.
    assert quota.check(provisioner) == []


def test_raising_the_quota_starts_it_again(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", GB)
    sign_in(client, "Alice")
    make(client, "big")
    provisioner.sizes = {"big": 2 * GB}
    quota.check(provisioner)
    assert db.set_quota("big", 4 * GB)

    quota.check(provisioner)

    assert provisioner.stopped == set()
    (base,) = db.all_bases()
    assert base["suspended_at"] is None and base["quota_bytes"] == 4 * GB


def test_an_unmeasured_base_is_left_running(client, provisioner):
    sign_in(client, "Alice")
    make(client, "fresh")
    assert quota.check(provisioner) == []
    assert provisioner.stopped == set()
    assert "not measured yet" in client.get("/").get_data(as_text=True)


def test_one_failing_base_doesnt_stop_the_check(client, provisioner, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_BYTES", GB)
    sign_in(client, "Alice")
    make(client, "aaa")
    other = client.application.test_client()
    sign_in(other, "Bob")
    make(other, "bbb")
    provisioner.sizes = {"aaa": 2 * GB, "bbb": 2 * GB}
    real_stop = provisioner.stop

    def stop(slug):
        if slug == "aaa":
            raise RuntimeError("docker hiccup")
        real_stop(slug)

    provisioner.stop = stop
    assert quota.check(provisioner) == ["bbb"]
    # aaa is tried again next time.
    provisioner.stop = real_stop
    assert quota.check(provisioner) == ["aaa"]
