import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509

from scripts import tls_gateway


def test_disposable_ca_is_local_short_lived_and_never_saved(tmp_path):
    tls_gateway.prepare_certificate(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {"ca.crt", "server.crt", "server.key"}
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
    server = x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())
    server.verify_directly_issued_by(ca)
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert server.not_valid_after_utc <= datetime.now(UTC) + timedelta(days=7)
    with pytest.raises(RuntimeError, match="not empty"):
        tls_gateway.prepare_certificate(tmp_path)


def test_missing_gateway_is_failure_without_issuing_mutations(monkeypatch):
    calls = []

    def no_container(*args):
        calls.append(args)
        return b""

    monkeypatch.setattr(tls_gateway, "docker", no_container)
    with pytest.raises(RuntimeError, match="one isolated"):
        tls_gateway.inspect_gateway()
    assert len(calls) == 1 and calls[0][0] == "ps"


def protocol_transport(monkeypatch, negotiated, *, error_at=None):
    """Offline handshake boundary; this does not prove a running gateway's TLS."""
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Handshake(Connection):
        def __init__(self, version):
            self.negotiated = version

        def version(self):
            return self.negotiated

    def connect(address, timeout):
        assert address == ("127.0.0.1", 14443)
        assert timeout == 5
        return Connection()

    def wrap(context, connection, *, server_hostname):
        assert isinstance(connection, Connection)
        assert server_hostname == "localhost"
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.cert_store_stats()["x509_ca"] >= 1
        calls.append((context.minimum_version, context.maximum_version))
        if len(calls) == error_at:
            raise ssl.SSLError("protocol unavailable")
        return Handshake(negotiated[len(calls) - 1])

    monkeypatch.setattr(tls_gateway.socket, "create_connection", connect)
    monkeypatch.setattr(tls_gateway.ssl.SSLContext, "wrap_socket", wrap)
    return calls


def test_both_tls_protocols_are_pinned_and_certificates_remain_required(monkeypatch, tmp_path):
    tls_gateway.prepare_certificate(tmp_path)
    calls = protocol_transport(monkeypatch, ("TLSv1.2", "TLSv1.3"))
    assert tls_gateway.verify_protocols(tmp_path / "ca.crt") == ["TLSv1.2", "TLSv1.3"]
    assert calls == [
        (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2),
        (ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3),
    ]


@pytest.mark.parametrize("second", ["TLSv1.2", "TLSv1.1", None])
def test_missing_or_downgraded_tls13_cannot_pass(monkeypatch, tmp_path, second):
    tls_gateway.prepare_certificate(tmp_path)
    protocol_transport(monkeypatch, ("TLSv1.2", second))
    monkeypatch.setattr(tls_gateway.httpx, "Client", lambda **kw: pytest.fail("HTTP ran"))
    with pytest.raises(RuntimeError, match="Required TLS protocol"):
        tls_gateway.verify_http(tmp_path / "ca.crt")


@pytest.mark.parametrize("error_at", [1, 2])
def test_either_protocol_handshake_failure_stops_acceptance(monkeypatch, tmp_path, error_at):
    tls_gateway.prepare_certificate(tmp_path)
    calls = protocol_transport(monkeypatch, ("TLSv1.2", "TLSv1.3"), error_at=error_at)
    monkeypatch.setattr(tls_gateway.httpx, "Client", lambda **kw: pytest.fail("HTTP ran"))
    with pytest.raises(ssl.SSLError):
        tls_gateway.verify_http(tmp_path / "ca.crt")
    assert len(calls) == error_at
