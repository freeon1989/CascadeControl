"""Детектор блокировки на входе: прямой запрос, затем классификация ответа.

Сброс, таймаут и страница-заглушка помечают домен как «через каскад».
Обычный ответ не трогаем. Решение записывает вызывающий код вместе с источником.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass

STUB_MARKERS = (
    "доступ ограничен",
    "интернет-ресурс заблокирован",
    "рkn.gov.ru",
    "rkn.gov.ru",
    "eais.rkn.gov.ru",
    "block.rkn",
    "ресурс заблокирован",
)


@dataclass(frozen=True)
class Probe:
    status: str  # ok | reset | timeout | stub | error
    detail: str = ""


def classify(probe: Probe) -> str | None:
    """Возвращает действие, которое стоит записать, либо None если менять маршрут не нужно."""
    if probe.status in {"reset", "timeout", "stub"}:
        return "cascade"
    return None


def classify_body(status_code: int, body: str) -> Probe:
    lowered = body.lower()
    if any(marker in lowered for marker in STUB_MARKERS):
        return Probe("stub", f"http {status_code}")
    if status_code >= 500:
        return Probe("error", f"http {status_code}")
    return Probe("ok", f"http {status_code}")


def probe_domain(domain: str, timeout: float = 4.0, port: int = 443) -> Probe:
    """Прямое TCP/TLS-соединение. Не ходит в каскад: так агент отличает блок от падения выхода."""
    try:
        sock = socket.create_connection((domain, port), timeout=timeout)
    except socket.timeout:
        return Probe("timeout", "tcp timeout")
    except ConnectionResetError:
        return Probe("reset", "tcp reset")
    except OSError as exc:
        text = str(exc).lower()
        if "timed out" in text:
            return Probe("timeout", text)
        if "reset" in text:
            return Probe("reset", text)
        return Probe("error", text)
    try:
        from .tls import install_tls

        install_tls()
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(sock, server_hostname=domain)
        try:
            req = f"GET / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\nUser-Agent: cascade-probe\r\n\r\n"
            tls.sendall(req.encode())
            chunks = []
            tls.settimeout(timeout)
            while len(chunks) < 8:
                try:
                    part = tls.recv(4096)
                except socket.timeout:
                    break
                if not part:
                    break
                chunks.append(part)
                if sum(len(c) for c in chunks) > 65536:
                    break
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            head, _, body = raw.partition("\r\n\r\n")
            code = 0
            parts = head.split(" ", 2)
            if len(parts) > 1 and parts[1].isdigit():
                code = int(parts[1])
            return classify_body(code, body[:8000])
        finally:
            tls.close()
    except socket.timeout:
        return Probe("timeout", "tls timeout")
    except ConnectionResetError:
        return Probe("reset", "tls reset")
    except ssl.SSLError as exc:
        return Probe("error", f"tls: {exc}")
    except OSError as exc:
        text = str(exc).lower()
        if "reset" in text:
            return Probe("reset", text)
        return Probe("error", text)
    finally:
        try:
            sock.close()
        except OSError:
            pass
