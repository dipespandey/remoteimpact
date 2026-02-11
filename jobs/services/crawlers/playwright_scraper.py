"""
Playwright-based scraper for JS-rendered job pages.

Handles sites like:
- probablygood.org detail pages (Webflow)
- Workday career sites
- Other JS-heavy career platforms
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Timeout for page loads (ms)
PAGE_TIMEOUT = 30000

# Selectors for common career page content
CONTENT_SELECTORS = {
    "workday": [
        "[data-automation-id='jobPostingDescription']",
        ".job-description",
        "[data-automation-id='job-posting-details']",
    ],
    "probablygood": [
        ".job-description",
        ".job-content",
        ".w-richtext",  # Webflow rich text
        "main",
        "article",
    ],
    "generic": [
        ".job-description",
        ".posting-description",
        "[class*='description']",
        "main",
        "article",
        "#content",
        ".content",
    ],
}


def _detect_site_type(url: str) -> str:
    """Detect the type of career site from URL."""
    domain = urlparse(url).netloc.lower()
    
    if "workday" in domain or "myworkdayjobs" in domain:
        return "workday"
    if "probablygood.org" in domain:
        return "probablygood"
    return "generic"


def _clean_text(text: str) -> str:
    """Clean extracted text content."""
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    return text


async def fetch_page_content_async(url: str, timeout: int = PAGE_TIMEOUT) -> Optional[str]:
    """
    Fetch page content using Playwright (async).
    
    Args:
        url: URL to fetch
        timeout: Page load timeout in milliseconds
        
    Returns:
        Extracted text content or None if failed
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None
    
    site_type = _detect_site_type(url)
    selectors = CONTENT_SELECTORS.get(site_type, CONTENT_SELECTORS["generic"])
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Navigate to page
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            
            # Wait a bit for JS to render
            await page.wait_for_timeout(2000)
            
            # Try each selector until we find content
            content = None
            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.inner_text()
                        if text and len(text) > 100:  # Minimum content threshold
                            content = text
                            logger.debug(f"Found content using selector: {selector}")
                            break
                except Exception:
                    continue
            
            # Fallback: get all body text
            if not content:
                try:
                    body = await page.query_selector("body")
                    if body:
                        content = await body.inner_text()
                except Exception:
                    pass
            
            await browser.close()
            
            if content:
                content = _clean_text(content)
                # Limit length
                if len(content) > 15000:
                    content = content[:15000] + "..."
                return content
            
            return None
            
    except Exception as e:
        logger.error(f"Playwright error fetching {url}: {e}")
        return None


def fetch_page_content(url: str, timeout: int = PAGE_TIMEOUT) -> Optional[str]:
    """
    Fetch page content using Playwright (sync wrapper).
    
    Args:
        url: URL to fetch
        timeout: Page load timeout in milliseconds
        
    Returns:
        Extracted text content or None if failed
    """
    try:
        return asyncio.run(fetch_page_content_async(url, timeout))
    except RuntimeError:
        # If already in an async context, create a new event loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(fetch_page_content_async(url, timeout))
        finally:
            loop.close()


async def fetch_probablygood_detail(url: str) -> Optional[str]:
    """
    Fetch job description from a Probably Good detail page.
    
    These are Webflow pages that require JS to render the job content.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed")
        return None
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(url, timeout=PAGE_TIMEOUT, wait_until="networkidle")
            await page.wait_for_timeout(1500)  # Extra wait for Webflow
            
            # Look for job content in Webflow structure
            content = None
            
            # Try to find the main job description content
            selectors = [
                ".job-description",
                ".job-content", 
                ".w-richtext",
                "[class*='job']",
                "main .w-container",
                "article",
            ]
            
            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for element in elements:
                        text = await element.inner_text()
                        if text and len(text) > 200:
                            content = text
                            break
                    if content:
                        break
                except Exception:
                    continue
            
            await browser.close()
            
            if content:
                return _clean_text(content)
            
            return None
            
    except Exception as e:
        logger.error(f"Error fetching Probably Good page {url}: {e}")
        return None
