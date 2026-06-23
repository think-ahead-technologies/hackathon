# ABOUTME: Unit tests for provisioning — least-privilege permissions, unique identities, valid config render.
# ABOUTME: Guards the CRA properties: no shared credential, and a device can't reach beyond its own subject.

import pytest

import nkey
import provision

FLEET = {
    "devices": [
        {"id": "cnc-7", "line": "line1"},
        {"id": "press-3", "line": "line1"},
    ],
    "services": [
        {"id": "vector", "role": "vector"},
        {"id": "bridge", "role": "bridge"},
        {"id": "fakegen", "role": "fakegen", "line": "line1"},
        {"id": "pipeline", "role": "pipeline"},
    ],
}


def test_device_is_scoped_to_its_own_subject():
    perms = provision.permissions_for("device", "line1", "cnc-7")
    assert perms["publish"] == ["edge.line1.cnc-7"]
    # It can receive its own rollouts/control but cannot publish anywhere else.
    assert "models.line1.deploy" in perms["subscribe"]
    assert all(p == "edge.line1.cnc-7" for p in perms["publish"])


def test_a_device_cannot_publish_as_another_device():
    cnc = provision.permissions_for("device", "line1", "cnc-7")
    press = provision.permissions_for("device", "line1", "press-3")
    assert "edge.line1.press-3" not in cnc["publish"]
    assert "edge.line1.cnc-7" not in press["publish"]


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        provision.permissions_for("intruder", "line1", "x")


def test_every_identity_gets_a_unique_credential():
    identities = provision.build_identities(FLEET)
    seeds = {i["seed"] for i in identities}
    publics = {i["public"] for i in identities}
    assert len(seeds) == len(identities)    # no shared/default secret
    assert len(publics) == len(identities)


def test_issued_seed_matches_its_advertised_public_key():
    identities = provision.build_identities(FLEET)
    for ident in identities:
        assert nkey.public_from_seed(ident["seed"]) == ident["public"]


def test_rendered_config_authorizes_each_public_key_and_leaks_no_seed():
    identities = provision.build_identities(FLEET)
    conf = provision.render_server_config(identities)
    for ident in identities:
        assert f'nkey: "{ident["public"]}"' in conf
        assert ident["seed"] not in conf        # secrets never land in the server config
    # Secure-by-default: no anonymous fallback user.
    assert "no_auth_user" not in conf
    assert "jetstream {}" in conf


def test_seed_file_has_no_trailing_newline(tmp_path):
    # nats-py reads the exact file bytes; a trailing newline corrupts the seed (real bug, regression-guarded).
    identities = provision.build_identities({"devices": [{"id": "cnc-7", "line": "line1"}], "services": []})
    provision.write_artifacts(identities, str(tmp_path))
    raw = (tmp_path / "creds" / "cnc-7.nk").read_bytes()
    assert not raw.endswith(b"\n")
    # Byte-exact read (as nats-py does) must still derive the advertised public key.
    assert nkey.public_from_seed(raw.decode()) == identities[0]["public"]


def test_config_carries_least_privilege_subjects_verbatim():
    identities = provision.build_identities({"devices": [{"id": "cnc-7", "line": "line1"}], "services": []})
    conf = provision.render_server_config(identities)
    assert 'publish = { allow = ["edge.line1.cnc-7"] }' in conf


def test_tls_block_is_opt_in():
    identities = provision.build_identities({"devices": [{"id": "cnc-7", "line": "line1"}], "services": []})
    # Default off: the firmware login test renders auth-only configs over plaintext.
    assert "tls {" not in provision.render_server_config(identities)
    # On request: a server-auth TLS block pointing at the mounted cert/key.
    secured = provision.render_server_config(identities, tls=True)
    assert "tls {" in secured
    assert provision.TLS_CERT_PATH in secured
    assert provision.TLS_KEY_PATH in secured


def test_write_artifacts_emits_the_tls_chain(tmp_path):
    identities = provision.build_identities({"devices": [{"id": "cnc-7", "line": "line1"}], "services": []})
    provision.write_artifacts(identities, str(tmp_path))
    assert (tmp_path / "tls" / "ca.pem").exists()
    assert (tmp_path / "tls" / "server-cert.pem").exists()
    assert (tmp_path / "tls" / "server-key.pem").exists()
    # The CA private key must NOT be left on disk.
    assert not (tmp_path / "tls" / "ca-key.pem").exists()
    # The written config turns TLS on.
    assert "tls {" in (tmp_path / "nats-server.conf").read_text()
