# ABOUTME: Unit tests for the NATS nkey codec — format, CRC integrity, uniqueness, round-trip.
# ABOUTME: Guards the wire format so an issued seed actually authenticates against the server config.

import pytest

import nkey


def test_user_seed_and_public_have_the_right_human_prefixes():
    seed, public = nkey.create_user()
    assert seed.startswith("SU")   # seed marker + user type
    assert public.startswith("U")  # user public key


def test_seed_derives_its_advertised_public_key():
    seed, public = nkey.create_user()
    assert nkey.public_from_seed(seed) == public


def test_every_generated_identity_is_unique():
    # The whole point of CRA "unique credentials": no two boards share a key.
    seeds = {nkey.create_user()[0] for _ in range(50)}
    assert len(seeds) == 50


def test_decode_verifies_the_crc():
    _, public = nkey.create_user()
    prefix, payload = nkey.decode(public)
    assert prefix == nkey.PREFIX_USER
    assert len(payload) == 32


def test_a_corrupted_credential_is_rejected():
    _, public = nkey.create_user()
    # Flip one character in the body — the appended CRC must catch it.
    corrupt = ("A" if public[5] != "A" else "B").join([public[:5], public[6:]])
    with pytest.raises(ValueError, match="checksum"):
        nkey.decode(corrupt)


def test_wrong_length_inputs_are_rejected():
    with pytest.raises(ValueError):
        nkey.encode_public(nkey.PREFIX_USER, b"\x00" * 31)
    with pytest.raises(ValueError):
        nkey.encode_seed(nkey.PREFIX_USER, b"\x00" * 33)
