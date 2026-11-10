"""HTTPS панели: системное хранилище сертификатов и запасной набор certifi.

Сборка Windows через Wine тащит устаревший набор CA. Настоящий Windows
доверяет корню Let's Encrypt, поэтому сначала берём системное хранилище
через truststore. Если проверка им не удалась, повторяем запрос с
актуальным cacert.pem из certifi.
"""

from __future__ import annotations

import ssl

_installed = False


def install_tls() -> None:
    global _installed
    if _installed:
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass
    _installed = True


def system_context() -> ssl.SSLContext:
    install_tls()
    return ssl.create_default_context()


def certifi_context() -> ssl.SSLContext:
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def is_cert_error(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__} {exc}".lower()
    return "certificate" in text or "ssl" in text or "cert_" in text


def explain_fetch_error(exc: BaseException) -> tuple[str, str]:
    raw = f"{exc.__class__.__name__}: {exc}"
    text = raw.lower()
    if is_cert_error(exc):
        return (
            "Не удалось проверить сертификат сайта. Обычно это устаревший набор сертификатов панели, а не сам список.",
            raw,
        )
    if "timed out" in text or "timeout" in text:
        return ("Сайт списка не ответил вовремя. Повторите обновление позже.", raw)
    if "getaddrinfo" in text or "name or service not known" in text or "nodename" in text:
        return ("Не удалось найти адрес сайта. Проверьте ссылку и интернет.", raw)
    if "urlopen" in text or "connection" in text or "refused" in text:
        return ("Не удалось открыть сайт списка. Проверьте ссылку и интернет.", raw)
    return ("Не удалось скачать список.", raw)
