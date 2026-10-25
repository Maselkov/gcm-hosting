import re
import time
from unittest import mock

import pytest
from conftest import csrf, make_base, sign_in

from hosting import auth, config, db, names


def test_a_new_base_is_started_and_its_admin_token_shown_once(client, provisioner):
    sign_in(client, "Alice")

    response = make_base(client, "alicebase")

    assert response.status_code == 201
    page = response.get_data(as_text=True)
    assert "gcm_token_for_Alice" in page
    assert "https://alicebase.example.test" in page
    api_key = provisioner.running["alicebase"]
    assert len(api_key) == 64
    later = client.get("/").get_data(as_text=True)
    assert "gcm_token_for_Alice" not in later
    assert api_key in later and "running" in later


def test_a_base_gets_a_made_up_name(client, provisioner):
    sign_in(client, "Alice")

    response = client.post("/bases", data={"csrf": csrf(client)})

    assert response.status_code == 201
    (slug,) = provisioner.running
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9]{4}", slug)
    assert f"https://{slug}.example.test" in response.get_data(as_text=True)


def test_a_name_already_taken_is_passed_over(client, app, provisioner):
    sign_in(client, "Alice")
    make_base(client, "copper-falcon-4821")
    other = app.test_client()
    sign_in(other, "Bob")

    with mock.patch.object(names, "generate", side_effect=["copper-falcon-4821", "quiet-rook-4833"]):
        response = other.post("/bases", data={"csrf": csrf(other)})

    assert response.status_code == 201
    assert set(provisioner.running) == {"copper-falcon-4821", "quiet-rook-4833"}


def test_the_word_lists_make_valid_distinct_dns_labels():
    assert len(set(names.ADJECTIVES)) == len(names.ADJECTIVES) == 256
    assert len(set(names.NOUNS)) == len(names.NOUNS) == 256
    longest = max(map(len, names.ADJECTIVES)) + max(map(len, names.NOUNS)) + len("--0000")
    assert longest <= 63
    assert all(w.isascii() and w.isalpha() and w.islower() for w in names.ADJECTIVES + names.NOUNS)


def test_limits_per_account_and_per_host(client, app, monkeypatch):
    sign_in(client, "Alice")
    assert make_base(client, "first").status_code == 201
    assert make_base(client, "second").status_code == 409

    monkeypatch.setattr(config, "MAX_BASES", 1)
    other = app.test_client()
    sign_in(other, "Bob")
    response = make_base(other, "bobs")
    assert response.status_code == 409
    assert "full" in response.get_data(as_text=True)


def test_a_base_that_fails_to_start_is_cleaned_up(client, provisioner):
    sign_in(client, "Alice")
    provisioner.fail_admin = True
    assert make_base(client, "broken").status_code == 500
    assert provisioner.running == {}
    assert db.owner_of("broken") is None
    provisioner.fail_admin = False
    assert make_base(client, "broken").status_code == 201


def test_deleting_needs_the_name_typed_and_removes_everything(client, provisioner):
    sign_in(client, "Alice")
    make_base(client, "doomed")
    url = "/bases/doomed/delete"
    assert client.post(url, data={"confirm": "nope", "csrf": csrf(client)}).status_code == 400
    assert "doomed" in provisioner.running

    assert client.post(url, data={"confirm": "doomed", "csrf": csrf(client)}).status_code == 302
    assert provisioner.running == {}
    assert db.owner_of("doomed") is None


def test_only_the_owner_can_delete(client, app, provisioner):
    sign_in(client, "Alice")
    make_base(client, "mine")
    other = app.test_client()
    sign_in(other, "Bob")
    response = other.post("/bases/mine/delete", data={"confirm": "mine", "csrf": csrf(other)})
    assert response.status_code == 404
    assert "mine" in provisioner.running


def test_an_owner_can_get_a_new_token_shown_once(client, provisioner):
    sign_in(client, "Alice")
    make_base(client, "mine")
    url = "/bases/mine/reset-token"
    assert "Lost your sign-in token?" in client.get("/").get_data(as_text=True)

    page = client.post(url, data={"csrf": csrf(client)}).get_data(as_text=True)
    assert "gcm_tok-new-for-Alice" in page and 'data-copy="token"' in page
    assert "gcm_tok-new-for-Alice" not in client.get("/").get_data(as_text=True)

    provisioner.admins["mine"] = ["Alice", "Carol"]
    page = client.post(url, data={"name": "Carol", "csrf": csrf(client)}).get_data(as_text=True)
    assert "gcm_tok-new-for-Carol" in page
    response = client.post(url, data={"name": "Nobody", "csrf": csrf(client)})
    assert response.status_code == 400 and "Its admins: Alice, Carol" in response.get_data(as_text=True)
    assert client.post(url, data={}).status_code == 400


def test_only_the_owner_can_reset_a_running_base_token(client, app, provisioner):
    sign_in(client, "Alice")
    make_base(client, "mine")
    other = app.test_client()
    sign_in(other, "Bob")
    assert other.post("/bases/mine/reset-token", data={"csrf": csrf(other)}).status_code == 404

    provisioner.stop("mine")
    response = client.post("/bases/mine/reset-token", data={"csrf": csrf(client)})
    assert response.status_code == 409 and "gcm_tok-new" not in response.get_data(as_text=True)


def test_the_new_token_has_a_copy_button_scripts_may_run(client):
    sign_in(client, "Alice")
    response = make_base(client, "mine")
    page = response.get_data(as_text=True)
    assert 'data-copy="token"' in page and "copy.js" in page
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_forms_need_the_csrf_token(client, provisioner):
    sign_in(client, "Alice")
    assert client.post("/bases", data={}).status_code == 400
    assert client.post("/bases", data={"csrf": "wrong"}).status_code == 400
    assert provisioner.running == {}


def test_signed_out_requests_make_nothing(client, provisioner):
    assert client.post("/bases", data={"csrf": csrf(client)}).status_code == 302
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


def test_a_new_base_is_private_unless_chosen_otherwise(client, app, provisioner):
    sign_in(client, "Alice")
    assert make_base(client, "copper-falcon-4821").status_code == 201
    assert provisioner.private == {"copper-falcon-4821"}

    other = app.test_client()
    sign_in(other, "Bob")
    with mock.patch.object(names, "generate", return_value="quiet-rook-4833"):
        response = other.post("/bases", data={"csrf": csrf(other), "visibility": "public"})
    assert response.status_code == 201
    assert provisioner.private == {"copper-falcon-4821"}


def test_an_image_too_old_to_make_it_private_says_so(client, provisioner):
    sign_in(client, "Alice")
    provisioner.fail_private = True

    response = make_base(client, "copper-falcon-4821")

    assert response.status_code == 500
    assert "make private bases yet" in response.get_data(as_text=True)
    assert provisioner.running == {}
    assert db.owner_of("copper-falcon-4821") is None


# A Discord ID from the first millisecond of 2015.
OLD_ID = str(1 << 22)


def discord_id(created):
    return str(int(created * 1000 - auth.DISCORD_EPOCH_MS) << 22)


def test_the_account_age_is_read_from_the_discord_id():
    assert auth.account_created(f"discord:{OLD_ID}") == 1420070400.001
    # A known one: Discord's own example snowflake, 2016-04-30 11:18:25.796 UTC.
    assert auth.account_created("discord:175928847299117063") == 1462015105.796
    assert auth.account_created("dev:Alice") is None


@pytest.mark.parametrize(
    ("identity", "operator", "minimum", "allowed"),
    [
        (f"discord:{OLD_ID}", False, 30, True),
        ("new", False, 30, False),
        ("new", True, 30, True),  # operators always can
        ("new", False, 0, True),  # 0: any age
        ("dev:Alice", False, 30, True),
    ],
)
def test_a_discord_account_must_be_old_enough(client, provisioner, monkeypatch, identity, operator, minimum, allowed):
    if identity == "new":
        identity = f"discord:{discord_id(time.time() - 86400)}"
    monkeypatch.setattr(config, "MIN_ACCOUNT_AGE_DAYS", minimum)
    monkeypatch.setattr(config, "ADMIN_IDENTITIES", {identity} if operator else set())
    with client.session_transaction() as session:
        session["account_id"] = db.account_for(identity, "Alice")

    response = make_base(client, "copper-falcon-4821")

    assert response.status_code == (201 if allowed else 403)
    if not allowed:
        assert "at least 30 days old" in response.get_data(as_text=True)
        assert provisioner.running == {}


def test_the_favicon_is_served_at_the_root(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.data[:4] == b"\x00\x00\x01\x00"


def test_an_address_with_no_base_gets_a_page_saying_so(client):
    response = client.get("/", headers={"Host": "copper-falcon-4812.example.test"})

    assert response.status_code == 404
    page = response.get_data(as_text=True)
    assert "No base here" in page and "copper-falcon-4812.example.test" in page
    assert 'href="https://example.test"' in page
    assert client.get("/static/style.css", headers={"Host": "copper-falcon-4812.example.test"}).status_code == 200


def test_the_forms_and_sign_in_only_answer_at_domain(client):
    host = {"Host": "copper-falcon-4812.example.test"}

    assert client.get("/login/discord", headers=host).status_code == 404
    login = client.post("/login/dev", data={"name": "Alice"}, headers=host)
    assert login.status_code == 404 and "Set-Cookie" not in login.headers
    # Not a base's address: the health check's, say.
    assert client.get("/", headers={"Host": "localhost:8000"}).status_code == 200
