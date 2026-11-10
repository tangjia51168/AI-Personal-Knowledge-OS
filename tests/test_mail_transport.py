"""Real loopback SMTP protocol checks; no account mail leaves the local machine."""

import socketserver
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest
from app.integrations import mail
from pydantic import SecretStr


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        if self.server.silent:
            self.server.release.wait(0.3)
            return
        self.wfile.write(b"220 loopback fixture\r\n")
        while line := self.rfile.readline(8192):
            command = line.decode("ascii").strip()
            self.server.commands.append(command)
            if command.startswith("ehlo "):
                self.wfile.write(b"250-fixture\r\n250 STARTTLS\r\n")
            elif command.lower() == "starttls":
                if self.server.close_during_tls:
                    self.wfile.write(b"220 begin TLS\r\n")
                    return
                self.wfile.write(b"454 private-error-password-and-token\r\n")
            elif command.lower() == "data":
                self.wfile.write(b"354 send content\r\n")
                content = bytearray()
                while (part := self.rfile.readline(8192)) not in (b".\r\n", b""):
                    content.extend(part[1:] if part.startswith(b"..") else part)
                self.server.messages.append(bytes(content))
                self.wfile.write(b"250 accepted\r\n")
            elif command.lower() == "quit":
                self.wfile.write(b"221 goodbye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class SMTPSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, silent=False, close_during_tls=False):
        self.commands = []
        self.messages = []
        self.silent = silent
        self.close_during_tls = close_during_tls
        self.release = threading.Event()
        super().__init__(("127.0.0.1", 0), SMTPHandler)


@contextmanager
def sink(silent=False, close_during_tls=False):
    with SMTPSink(silent=silent, close_during_tls=close_during_tls) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server
        finally:
            server.release.set()
            server.shutdown()
            thread.join(timeout=2)


def configure(monkeypatch, port=2525, **changes):
    settings = SimpleNamespace(
        app_env="test",
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_username="",
        smtp_password=SecretStr(""),
        smtp_from="accounts@example.invalid",
        smtp_security="plain",
        public_app_url="http://localhost:3000",
    )
    for key, value in changes.items():
        setattr(settings, key, value)
    monkeypatch.setattr(mail, "get_settings", lambda: settings)


@pytest.mark.parametrize(
    "purpose,fragment", [("verify_email", "verify"), ("reset_password", "reset")]
)
def test_actual_smtp_delivers_fixed_plain_text_fragment_link(monkeypatch, purpose, fragment):
    with sink() as server:
        configure(monkeypatch, server.server_address[1])
        mail.send_account_link("member@example.invalid", purpose, "a" * 43)
        assert len(server.messages) == 1
        message = BytesParser(policy=policy.default).parsebytes(server.messages[0])
        assert message["From"] == "accounts@example.invalid"
        assert message["To"] == "member@example.invalid"
        assert message.get_content_type() == "text/plain"
        assert f"http://localhost:3000/account#{fragment}=" + "a" * 43 in message.get_content()
        assert "?token" not in message.get_content()


@pytest.mark.parametrize("close_during_tls", [False, True])
def test_starttls_failure_never_downgrades_or_sends_sensitive_commands(
    monkeypatch, close_during_tls
):
    with sink(close_during_tls=close_during_tls) as server:
        configure(
            monkeypatch,
            server.server_address[1],
            smtp_security="starttls",
            smtp_username="fixture-user",
            smtp_password=SecretStr("fixture-password"),
        )
        with pytest.raises(mail.MailDeliveryError) as error:
            mail.send_account_link("member@example.invalid", "verify_email", "a" * 43)
        assert str(error.value) == "MAIL_DELIVERY_FAILED"
        assert any(command.lower() == "starttls" for command in server.commands)
        assert not any(
            command.lower().startswith(("auth", "mail", "rcpt", "data"))
            for command in server.commands
        )
        assert not server.messages


@pytest.mark.parametrize(
    "changes,recipient,token",
    [
        ({}, "member@example.invalid\r\nBcc: stolen@example.invalid", "a" * 43),
        (
            {"smtp_from": "from@example.invalid\r\nCc: stolen@example.invalid"},
            "ok@example.invalid",
            "a" * 43,
        ),
        (
            {"app_env": "production", "public_app_url": "https://knowledge.example.com"},
            "ok@example.invalid",
            "a" * 43,
        ),
        ({"smtp_security": "starttls", "app_env": "production"}, "ok@example.invalid", "a" * 43),
        (
            {"public_app_url": "https://knowledge.example.com?redirect=evil"},
            "ok@example.invalid",
            "a" * 43,
        ),
        ({}, "ok@example.invalid", "unsafe-token\n"),
    ],
)
def test_unsafe_inputs_rejected_before_connect(monkeypatch, changes, recipient, token):
    configure(monkeypatch, **changes)
    connections = []

    def unexpected_connection(*args, **kwargs):
        connections.append(True)
        raise AssertionError("unsafe input reached SMTP connection")

    monkeypatch.setattr(mail.smtplib, "SMTP", unexpected_connection)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", unexpected_connection)
    with pytest.raises(mail.MailDeliveryError) as error:
        mail.send_account_link(recipient, "verify_email", token)
    assert str(error.value) == "MAIL_DELIVERY_FAILED"
    assert not connections


def test_socket_timeout_is_bounded_and_sanitized(monkeypatch):
    with sink(silent=True) as server:
        configure(monkeypatch, server.server_address[1])
        monkeypatch.setattr(mail, "SMTP_TIMEOUT", 0.03)
        with pytest.raises(mail.MailDeliveryError) as error:
            mail.send_account_link("private@example.invalid", "reset_password", "a" * 43)
        assert str(error.value) == "MAIL_DELIVERY_FAILED"
        assert not server.messages
