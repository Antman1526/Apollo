import sys

# conftest.py stubs src.database with a fake module; webhook_manager imports
# from it, so drop the stub here to load the real module under test. Some
# order-dependent tests also stub core.database; clear that stale module too.
def _drop_module(name: str) -> None:
    sys.modules.pop(name, None)
    if "." not in name:
        return
    parent_name, _, child_name = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and hasattr(parent, child_name):
        delattr(parent, child_name)


_drop_module("src.database")
_drop_module("core.database")

import pytest
from src.webhook_manager import validate_webhook_url


def test_webhook_url_ssrf_mitigation():
    # SSRF bypasses that must be rejected, including IPv6 unspecified and
    # IPv4-mapped IPv6 (loopback + cloud metadata).
    private_urls = [
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
    ]
    for url in private_urls:
        with pytest.raises(ValueError) as exc:
            validate_webhook_url(url)
        assert "private/internal addresses" in str(exc.value)

    # A clearly public IP literal must still be accepted.
    public_url = "http://93.184.216.34/"
    assert validate_webhook_url(public_url) == public_url
