import pytest
from conftest import csrf, sign_in

from hosting import config, db


def make(client, slug):
    return client.post("/bases", data={"slug": slug, "csrf": csrf(client)})


def test_a_new_base_is_started_and_its_admin_token_shown_once(client, provisioner):
    sign_in(client, "Alice")

    response = make(client, "alicebase")

    assert response.status_code == 201
    page = response.get_data(as_text=True)
    assert "gcm_token_for_Alice" in page
    assert "https://alicebase.example.test" in page
    api_key = provisioner.running["alicebase"]
    assert len(api_key) == 64
    later = client.get("/").get_data(as_text=True)
    assert "gcm_token_for_Alice" not in later
    assert api_key in later and "running" in later


@pytest.mark.parametrize("slug", ["ab", "1base", "base-", "a--b", "has space", "UPPER!", "www", "x" * 31])
def test_bad_and_reserved_names_are_refused(client, provisioner, slug):
    sign_in(client, "Alice")
    assert make(client, slug).status_code == 400
    assert provisioner.running == {}


def test_a_taken_name_is_refused(client, app):
    sign_in(client, "Alice")
    make(client, "shared")
    other = app.test_client()
    sign_in(other, "Bob")
    response = make(other, "shared")
    assert response.status_code == 409
    assert "taken" in response.get_data(as_text=True)


def test_limits_per_account_and_per_host(client, app, monkeypatch):
    sign_in(client, "Alice")
    assert make(client, "first").status_code == 201
    assert make(client, "second").status_code == 409

    monkeypatch.setattr(config, "MAX_BASES", 1)
    other = app.test_client()
    sign_in(other, "Bob")
    response = make(other, "bobs")
    assert response.status_code == 409
    assert "full" in response.get_data(as_text=True)


def test_a_base_that_fails_to_start_is_cleaned_up(client, provisioner):
    sign_in(client, "Alice")
    provisioner.fail_admin = True
    assert make(client, "broken").status_code == 500
    assert provisioner.running == {}
    assert db.owner_of("broken") is None
    provisioner.fail_admin = False
    assert make(client, "broken").status_code == 201


def test_deleting_needs_the_name_typed_and_removes_everything(client, provisioner):
    sign_in(client, "Alice")
    make(client, "doomed")
    url = "/bases/doomed/delete"
    assert client.post(url, data={"confirm": "nope", "csrf": csrf(client)}).status_code == 400
    assert "doomed" in provisioner.running

    assert client.post(url, data={"confirm": "doomed", "csrf": csrf(client)}).status_code == 302
    assert provisioner.running == {}
    assert db.owner_of("doomed") is None


def test_only_the_owner_can_delete(client, app, provisioner):
    sign_in(client, "Alice")
    make(client, "mine")
    other = app.test_client()
    sign_in(other, "Bob")
    response = other.post("/bases/mine/delete", data={"confirm": "mine", "csrf": csrf(other)})
    assert response.status_code == 404
    assert "mine" in provisioner.running


def test_forms_need_the_csrf_token(client, provisioner):
    sign_in(client, "Alice")
    assert client.post("/bases", data={"slug": "nocsrf"}).status_code == 400
    assert client.post("/bases", data={"slug": "nocsrf", "csrf": "wrong"}).status_code == 400
    assert provisioner.running == {}


def test_signed_out_requests_make_nothing(client, provisioner):
    assert client.post("/bases", data={"slug": "anon", "csrf": csrf(client)}).status_code == 302
    assert provisioner.running == {}


def test_dev_login_is_off_unless_enabled(client, monkeypatch):
    token = csrf(client)
    monkeypatch.setattr(config, "DEV_LOGIN", False)
    assert client.post("/login/dev", data={"name": "x", "csrf": token}).status_code == 404


@pytest.mark.parametrize(
    ("domain", "allowed"),
    [
        ("localhost", True),
        ("gcm.localhost", True),
        ("example.test", True),
        ("craft.example", True),
        ("example.net", False),
        ("gcm.example.com", False),
        ("test", False),
        ("mytest", False),
    ],
)
def test_dev_login_only_starts_on_a_test_domain(tmp_path, monkeypatch, provisioner, domain, allowed):
    from hosting import create_app

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DEV_LOGIN", True)
    monkeypatch.setattr(config, "DOMAIN", domain)
    if allowed:
        create_app(provisioner)
    else:
        with pytest.raises(RuntimeError, match="DEV_LOGIN is for local testing only"):
            create_app(provisioner)
