import ssl
from urllib.error import URLError

from cascade_panel.lists import fetch_bytes
from cascade_panel.tls import explain_fetch_error


def test_expired_certificate_message_is_russian():
    message, detail = explain_fetch_error(
        URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")
    )
    assert "сертификат" in message.lower()
    assert "CERTIFICATE_VERIFY_FAILED" in detail


def test_fetch_falls_back_to_certifi_after_ssl_error(monkeypatch):
    contexts = []

    class Body:
        def read(self, _limit):
            return b"domain.example\n"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_urlopen(_req, timeout=0, context=None):
        contexts.append(context)
        if len(contexts) == 1:
            raise URLError(ssl.SSLCertVerificationError("certificate has expired"))
        return Body()

    monkeypatch.setattr("cascade_panel.lists.urlopen", fake_urlopen)
    assert b"domain.example" in fetch_bytes("https://example.test/domains.lst")
    assert len(contexts) == 2
    assert contexts[0] is not contexts[1]
