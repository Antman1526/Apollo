"""Crawl4AI-backed source extraction for Apollo research.

The integration follows Crawl4AI's documented async shape:
``AsyncWebCrawler`` with ``BrowserConfig`` and ``CrawlerRunConfig``.
Source: https://docs.crawl4ai.com/core/simple-crawling/
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.url_safety import check_outbound_url


class Crawl4AIUnavailable(RuntimeError):
    pass


class Crawl4AIBlockedURL(ValueError):
    pass


@dataclass
class Crawl4AIExtract:
    url: str
    success: bool
    status_code: int | None
    markdown: str
    title: str
    links: dict[str, Any]
    media: dict[str, Any]
    error: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_available() -> bool:
    return importlib.util.find_spec("crawl4ai") is not None


def status() -> dict[str, Any]:
    return {
        "available": is_available(),
        "package": "crawl4ai",
        "install_hint": "pip install -r requirements.txt && python -m playwright install chromium",
        "purpose": "Research/source extraction into clean Markdown for RAG and Apollo reports.",
    }


def validate_public_crawl_url(url: str) -> str:
    block_private = os.getenv("APOLLO_CRAWL4AI_ALLOW_PRIVATE", "false").lower() != "true"
    ok, reason = check_outbound_url(url, block_private=block_private)
    if not ok:
        raise Crawl4AIBlockedURL(reason)
    return url.strip()


def _markdown_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ("fit_markdown", "raw_markdown", "markdown"):
        text = getattr(value, attr, None)
        if isinstance(text, str) and text.strip():
            return text
    return str(value)


async def crawl_url(
    url: str,
    *,
    word_count_threshold: int = 10,
    timeout_seconds: float = 90,
    crawler_factory: Callable[..., Any] | None = None,
) -> Crawl4AIExtract:
    """Crawl a public URL and return clean Markdown plus Crawl4AI metadata."""

    safe_url = validate_public_crawl_url(url)
    if crawler_factory is None:
        if not is_available():
            raise Crawl4AIUnavailable(status()["install_hint"])
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import BrowserConfig, CacheMode, CrawlerRunConfig

        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(
            word_count_threshold=max(1, int(word_count_threshold)),
            remove_overlay_elements=True,
            process_iframes=True,
            cache_mode=CacheMode.ENABLED,
        )
        crawler_factory = lambda: AsyncWebCrawler(config=browser_config)
    else:
        run_config = None

    async def _run() -> Any:
        async with crawler_factory() as crawler:
            if run_config is None:
                return await crawler.arun(url=safe_url)
            return await crawler.arun(url=safe_url, config=run_config)

    result = await asyncio.wait_for(_run(), timeout=timeout_seconds)
    success = bool(getattr(result, "success", False))
    markdown = _markdown_text(getattr(result, "markdown", ""))
    return Crawl4AIExtract(
        url=safe_url,
        success=success,
        status_code=getattr(result, "status_code", None),
        markdown=markdown,
        title=str(getattr(result, "title", "") or ""),
        links=getattr(result, "links", {}) or {},
        media=getattr(result, "media", {}) or {},
        error=str(getattr(result, "error_message", "") or ""),
    )
