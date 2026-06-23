# ABOUTME: Mints the NATS TLS chain — a self-signed CA and a server cert signed by it.
# ABOUTME: Gives the fabric confidentiality + integrity in transit (CRA Annex I 2(e)); clients verify the CA.

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# EC P-256 — same family as the model-signing key, small certs, widely supported by TLS clients.
_CURVE = ec.SECP256R1


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def generate_ca(common_name: str = "edge-fleet-ca") -> tuple[bytes, bytes]:
    """Generate a self-signed CA. Returns (cert_pem, key_pem). The cert is what clients trust."""
    key = ec.generate_private_key(_CURVE())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=False, content_commitment=False,
                          key_encipherment=False, data_encipherment=False, key_agreement=False,
                          key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
            critical=True)
        .sign(key, hashes.SHA256())
    )
    return _pem_cert(cert), _pem_key(key)


def generate_server_cert(ca_cert_pem: bytes, ca_key_pem: bytes,
                         hostnames: list[str]) -> tuple[bytes, bytes]:
    """Issue a server cert signed by the CA, with SANs for `hostnames` (DNS names or IPs)."""
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)
    key = ec.generate_private_key(_CURVE())

    sans: list[x509.GeneralName] = []
    for h in hostnames:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))

    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return _pem_cert(cert), _pem_key(key)
