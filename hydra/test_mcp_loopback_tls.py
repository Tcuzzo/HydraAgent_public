"""Loopback proxy isolation must preserve explicit, verified TLS trust."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

pytest.importorskip("mcp")


@pytest.fixture
def tls_material(tmp_path):
    # cryptography is part of MCP's required pyjwt[crypto] dependency.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Hydra ephemeral test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_file = tmp_path / "ca.pem"
    cert_file = tmp_path / "server.pem"
    key_file = tmp_path / "server.key"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return ca_file, cert_file, key_file


@contextmanager
def tls_mcp_server(root, tls_material):
    import uvicorn

    from hydra.mcp_server import create_server

    _, cert_file, key_file = tls_material
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    app = create_server(root).streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(uvicorn.Config(
        app, log_level="error", ssl_keyfile=str(key_file), ssl_certfile=str(cert_file),
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield {"url": f"https://127.0.0.1:{sock.getsockname()[1]}/mcp", "timeout": 5}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def unusable_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "socks5://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


@pytest.mark.parametrize("trust_source", ["file", "directory"])
def test_https_loopback_preserves_explicit_ca_trust_without_proxy(
    monkeypatch, tmp_path, tls_material, trust_source,
):
    from hydra.mcp_bridge import invoke

    unusable_proxy(monkeypatch)
    ca_file, _, _ = tls_material
    if trust_source == "file":
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
        # File trust takes precedence over a separately configured directory.
        monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing-ca-directory"))
    else:
        openssl = shutil.which("openssl")
        if not openssl:
            pytest.skip("OpenSSL executable required to prepare a hashed CA directory")
        subject_hash = subprocess.check_output(
            [openssl, "x509", "-in", str(ca_file), "-subject_hash", "-noout"], text=True,
        ).strip()
        ca_dir = tmp_path / "ca-directory"
        ca_dir.mkdir()
        (ca_dir / f"{subject_hash}.0").write_bytes(ca_file.read_bytes())
        monkeypatch.setenv("SSL_CERT_DIR", str(ca_dir))
    (tmp_path / "local.txt").write_text("verified TLS MCP stayed on loopback", encoding="utf-8")
    with tls_mcp_server(tmp_path, tls_material) as endpoint:
        result = invoke(endpoint, "fs_read", {"path": "local.txt"})
    assert not result.get("isError")
    assert "verified TLS MCP stayed on loopback" in json.dumps(result)


def test_https_loopback_still_rejects_an_untrusted_server(monkeypatch, tmp_path, tls_material):
    from hydra.mcp_bridge import request

    unusable_proxy(monkeypatch)
    with tls_mcp_server(tmp_path, tls_material) as endpoint:
        with pytest.raises(Exception) as caught:
            asyncio.run(request(endpoint))
    # Observe the actual TLS rejection rather than accepting any transport failure.
    pending, seen, errors = [caught.value], set(), []
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        errors.append(error)
        pending.extend(getattr(error, "exceptions", ()))
        pending.extend(cause for cause in (error.__cause__, error.__context__) if cause)
    assert any(isinstance(error, ssl.SSLCertVerificationError) for error in errors)


@pytest.mark.parametrize("url,trust_env", [
    ("https://127.0.0.1:8765/mcp", False),
    ("https://mcp.example.test/mcp", True),
])
def test_default_tls_trust_and_remote_proxy_semantics_stay_unchanged(monkeypatch, url, trust_env):
    import httpx2

    from hydra.mcp_bridge import connect

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    if trust_env:
        # Remote certificates continue to be interpreted by the HTTP library.
        monkeypatch.setenv("SSL_CERT_FILE", "/operator/remote-ca.pem")

    class ClientConstructionObserved(Exception):
        pass

    options = []

    def capture_options(**kwargs):
        options.append(kwargs)
        raise ClientConstructionObserved

    monkeypatch.setattr(httpx2, "AsyncClient", capture_options)

    async def open_connection():
        async with connect({"url": url, "timeout": 2}):
            pytest.fail("client construction should stop this policy probe")

    with pytest.raises(ClientConstructionObserved):
        asyncio.run(open_connection())
    assert options[0].get("trust_env", True) is trust_env
    assert options[0].get("verify", True) is True
