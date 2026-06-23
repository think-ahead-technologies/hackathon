# ABOUTME: Unit tests for the NATS TLS chain — CA shape, server-cert SANs, and chain validity.
# ABOUTME: Guards that clients verifying the CA will accept the server cert for the expected hostnames.

import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtensionOID

import tls_certs


def test_ca_is_a_ca():
    ca_pem, _ = tls_certs.generate_ca()
    ca = x509.load_pem_x509_certificate(ca_pem)
    bc = ca.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
    assert bc.ca is True
    assert ca.issuer == ca.subject  # self-signed


def test_server_cert_is_signed_by_the_ca():
    ca_pem, ca_key_pem = tls_certs.generate_ca()
    srv_pem, _ = tls_certs.generate_server_cert(ca_pem, ca_key_pem, ["nats", "localhost", "127.0.0.1"])
    ca = x509.load_pem_x509_certificate(ca_pem)
    srv = x509.load_pem_x509_certificate(srv_pem)
    assert srv.issuer == ca.subject
    # The CA's public key verifies the server cert's signature (the trust chain a client walks).
    ca.public_key().verify(srv.signature, srv.tbs_certificate_bytes,
                           ec.ECDSA(srv.signature_hash_algorithm))


def test_server_cert_carries_the_requested_sans():
    ca_pem, ca_key_pem = tls_certs.generate_ca()
    srv_pem, _ = tls_certs.generate_server_cert(ca_pem, ca_key_pem, ["nats", "localhost", "127.0.0.1"])
    srv = x509.load_pem_x509_certificate(srv_pem)
    san = srv.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert "nats" in san.get_values_for_type(x509.DNSName)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)


def test_server_cert_is_not_a_ca():
    ca_pem, ca_key_pem = tls_certs.generate_ca()
    srv_pem, _ = tls_certs.generate_server_cert(ca_pem, ca_key_pem, ["nats"])
    srv = x509.load_pem_x509_certificate(srv_pem)
    bc = srv.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
    assert bc.ca is False
