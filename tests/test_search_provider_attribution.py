"""comprehensive_web_search tags sources with the provider that answered."""
from unittest.mock import patch

from services.search.core import comprehensive_web_search


def test_sources_carry_provider(tmp_path):
    fake_results = [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]
    with patch("services.search.core._get_search_settings",
               return_value={"search_provider": "searxng",
                             "search_fallback_chain": ["duckduckgo"],
                             "search_url": "", "searxng_managed": False,
                             "search_result_count": 5}), \
         patch("services.search.core._call_provider",
               side_effect=lambda name, q, c, tf: fake_results if name == "duckduckgo" else []), \
         patch("services.search.core.rank_search_results", side_effect=lambda q, r: r), \
         patch("services.search.core.fetch_webpage_content",
               return_value={"success": False, "content": "", "url": "", "title": ""}):
        _text, sources = comprehensive_web_search("query", return_sources=True)
    assert sources[0]["provider"] == "duckduckgo"
