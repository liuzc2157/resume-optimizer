"""邮件发送（验证码 / 密码重置）。

SMTP 未配置时自动进入「开发模式」：不真的发信，把验证码直接返回给调用方
（由 auth 路由决定是否回传），方便本地联调；生产环境配置 SMTP 后走真实邮件。

环境变量
--------
    SMTP_HOST          smtp 服务器，如 smtp.qq.com / smtp.resend.com
    SMTP_PORT          端口，默认 465
    SMTP_SECURITY      ssl(默认) | starttls | none
    SMTP_USER          登录账号
    SMTP_PASSWORD      授权码 / API Key
    SMTP_FROM          发件人地址（默认取 SMTP_USER）
"""

import logging
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger(__name__)

SMTP_HOST: str = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "465"))
SMTP_SECURITY: str = os.getenv("SMTP_SECURITY", "ssl").strip().lower()
SMTP_USER: str = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM: str = os.getenv("SMTP_FROM", "").strip() or SMTP_USER


def smtp_configured() -> bool:
    """是否配置了可用的 SMTP（配置了就真实发信，不再回传验证码）。"""
    return bool(SMTP_HOST and SMTP_USER and SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> None:
    """发送一封纯文本邮件；未配置 SMTP 时仅打日志（不抛错）。"""
    if not smtp_configured():
        logger.info("[mailer] SMTP 未配置，跳过发送 -> %s（主题：%s）", to, subject)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("简遇 Briefly", SMTP_FROM))
    msg["To"] = to

    if SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        if SMTP_SECURITY == "starttls":
            server.starttls()
    try:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    logger.info("[mailer] 邮件已发送 -> %s（主题：%s）", to, subject)
