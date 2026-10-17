from src.config import BUNDLED_CERT_DIR, UNOFFICIAL_NOTICE


def test_bundled_certs_present() -> None:
    assert (BUNDLED_CERT_DIR / "client.crt").is_file()
    assert (BUNDLED_CERT_DIR / "private_key.pem").is_file()


def test_unofficial_notice() -> None:
    assert "not affiliated" in UNOFFICIAL_NOTICE.lower()
    assert "warranty" in UNOFFICIAL_NOTICE.lower()
