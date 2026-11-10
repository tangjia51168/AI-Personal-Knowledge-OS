"""Bounded SMTP transport for account links, with fixed templates and safe errors."""

import ipaddress
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlsplit

from app.core.config import get_settings

Purpose = Literal["verify_email", "reset_password"]
SMTP_TIMEOUT = 5.0
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
PLAIN_HOSTS = {"localhost", "127.0.0.1", "::1", "smtp-sink", "mailpit"}


class MailDeliveryError(Exception):
    code = "MAIL_DELIVERY_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


def valid_address(value: str) -> bool:
    return len(value) <= 320 and ADDRESS.fullmatch(value) is not None


def account_link(public_url: str, purpose: Purpose, token: str, production: bool) -> str:
    """Fragments keep bearer links out of HTTP requests and ordinary access logs."""
    if purpose not in ("verify_email", "reset_password") or TOKEN.fullmatch(token) is None:
        raise MailDeliveryError()
    origin = public_origin(public_url, production)
    fragment = "verify" if purpose == "verify_email" else "reset"
    return origin + f"/account#{fragment}={token}"


def public_origin(public_url: str, production: bool) -> str:
    """One strict origin contract shared by account tokens and ordinary reminders."""
    parsed = urlsplit(public_url)
    if (
        len(public_url) > 2048
        or any(ord(char) < 33 for char in public_url)
        or parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise MailDeliveryError()
    if production:
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            is_ip = False
        else:
            is_ip = True
        if (
            parsed.scheme != "https"
            or is_ip
            or "." not in parsed.hostname
            or parsed.hostname.endswith((".localhost", ".local", ".invalid"))
            or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)
        ):
            raise MailDeliveryError()
    return public_url.rstrip("/")


def send_account_link(recipient: str, purpose: Purpose, token: str) -> None:
    """Send exactly one configured account email; never retry or expose SMTP errors.

    This transport is for server-selected recipients and purpose-bound random tokens.
    Callers own authorization, persistence, expiry, consumption and delivery retries.
    No browser-controlled SMTP endpoint or template is accepted.
    """
    settings = get_settings()
    link = account_link(settings.public_app_url, purpose, token, settings.app_env == "production")
    subject = "验证电子邮箱" if purpose == "verify_email" else "重置账户密码"
    instruction = (
        "请打开以下链接验证你的电子邮箱。"
        if purpose == "verify_email"
        else "请打开以下链接设置新密码。"
    )
    _send(recipient, subject, f"{instruction}\n\n{link}\n\n如果这不是你的操作，请忽略此邮件。\n")


def send_learning_notification(recipient: str, *, self_test: bool) -> None:
    """Fixed private template: no note content, bearer, recipient or link from a browser."""
    settings = get_settings()
    base = public_origin(settings.public_app_url, settings.app_env == "production")
    text = "这是一封你主动请求的提醒渠道自测邮件。" if self_test else "你有一条学习提醒已到期。"
    _send(
        recipient,
        "学习提醒渠道自测" if self_test else "学习复习提醒",
        f"{text}\n请登录知识工作区查看站内通知。\n\n{base}/account#learning\n"
        "\nSMTP接受仅表示邮件服务器接收，不证明已进入收件箱。\n",
    )


def _send(recipient: str, subject: str, content: str) -> None:
    try:
        settings = get_settings()
        host, sender = settings.smtp_host, settings.smtp_from
        username, password = settings.smtp_username, settings.smtp_password.get_secret_value()
        security = settings.smtp_security
        if (
            not valid_address(recipient)
            or not valid_address(sender)
            or not host
            or len(host) > 253
            or re.fullmatch(r"[A-Za-z0-9.:-]+", host) is None
            or not 1 <= settings.smtp_port <= 65535
            or security not in ("plain", "starttls", "tls")
            or bool(username) != bool(password)
            or any(char in username for char in "\r\n\x00")
        ):
            raise MailDeliveryError()
        if security == "plain" and (
            settings.app_env == "production" or host not in PLAIN_HOSTS or username
        ):
            raise MailDeliveryError()
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(content)
        context = ssl.create_default_context()
        transport: smtplib.SMTP
        if security == "tls":
            transport = smtplib.SMTP_SSL(
                host, settings.smtp_port, timeout=SMTP_TIMEOUT, context=context
            )
        else:
            transport = smtplib.SMTP(host, settings.smtp_port, timeout=SMTP_TIMEOUT)
        with transport:
            code, _ = transport.ehlo()
            if not 200 <= code < 300:
                raise MailDeliveryError()
            if security == "starttls":
                transport.starttls(context=context)
                code, _ = transport.ehlo()
                if not 200 <= code < 300:
                    raise MailDeliveryError()
            if username:
                transport.login(username, password)
            if transport.send_message(message, from_addr=sender, to_addrs=[recipient]):
                raise MailDeliveryError()
    except Exception:
        # SMTP server text may contain addresses, credentials and the account token.
        # Never log it, attach it to a public response, or retry after uncertain delivery.
        raise MailDeliveryError() from None
