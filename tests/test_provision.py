from hosting import config, provision


def test_a_base_container_is_routed_limited_and_isolated(monkeypatch):
    monkeypatch.setattr(config, "DOMAIN", "example.net")
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "/srv/gamedata")

    spec = provision.container_spec("alice", "k" * 64)

    labels = spec["labels"]
    assert labels["traefik.http.routers.gcm-alice.rule"] == "Host(`alice.example.net`)"
    assert labels["traefik.http.routers.gcm-alice.tls.certresolver"] == "le"
    assert labels["traefik.http.routers.gcm-alice.tls.domains[0].main"] == "example.net"
    assert labels["traefik.http.routers.gcm-alice.tls.domains[0].sans"] == "*.example.net"
    assert labels["traefik.http.services.gcm-alice.loadbalancer.server.port"] == "8420"
    env = spec["environment"]
    assert env["API_KEY"] == "k" * 64
    assert env["PUBLIC_URL"] == "https://alice.example.net"
    assert env["SESSION_COOKIE_SECURE"] == "1"
    assert env["TRUSTED_PROXIES"] == config.NETWORK_SUBNET
    assert env["GAMEDATA_SHARED_DIR"] == "/gamedata-shared"
    assert spec["volumes"] == {
        "gcm-base-alice-data": {"bind": "/app/data", "mode": "rw"},
        # Downloads of its own, outside the quota.
        "gcm-base-alice-gamedata": {"bind": "/app/data/gamedata", "mode": "rw"},
        "/srv/gamedata": {"bind": "/gamedata-shared", "mode": "ro"},
    }
    assert env["HISTORY_HOURLY_AFTER_DAYS"] == "7" and env["HISTORY_DAILY_AFTER_DAYS"] == "30"
    assert spec["cap_drop"] == ["ALL"] and spec["mem_limit"] and spec["pids_limit"]


def test_without_shared_game_data_each_base_has_only_its_own_volume(monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_SHARED_DIR", "")
    spec = provision.container_spec("bob", "k" * 64)
    assert "GAMEDATA_SHARED_DIR" not in spec["environment"]
    assert list(spec["volumes"]) == ["gcm-base-bob-data", "gcm-base-bob-gamedata"]
