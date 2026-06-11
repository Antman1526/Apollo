"""Provider chain skips SearXNG instantly when the managed sidecar is down."""
from unittest.mock import patch

from services.search.core import _build_provider_chain


def _settings(**over):
    base = {
        "search_provider": "searxng",
        "search_fallback_chain": ["duckduckgo"],
        "search_url": "",
        "searxng_managed": True,
    }
    base.update(over)
    return base


def test_skips_searxng_when_sidecar_down():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.is_serving.return_value = False
        chain = _build_provider_chain("searxng")
    assert chain == ["duckduckgo"]


def test_keeps_searxng_when_sidecar_running():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.is_serving.return_value = True
        chain = _build_provider_chain("searxng")
    assert chain == ["searxng", "duckduckgo"]


def test_keeps_searxng_with_custom_url():
    # User points at their own external instance — never skip on its behalf.
    with patch("services.search.core._get_search_settings",
               return_value=_settings(search_url="http://my-searx.lan:8080")):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"


def test_keeps_searxng_when_not_managed():
    with patch("services.search.core._get_search_settings",
               return_value=_settings(searxng_managed=False)):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"


def test_runtime_errors_fail_open():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime", side_effect=RuntimeError("boom")):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"
