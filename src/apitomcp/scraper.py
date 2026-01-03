"""Web scraping and operation extraction for API documentation."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from markitdown import MarkItDown

# Number of parallel fetches
MAX_WORKERS = 8


@dataclass
class Operation:
    """Represents a single API operation extracted from documentation."""

    method: str  # GET, POST, PUT, PATCH, DELETE
    path: str  # /artists/{id}
    summary: str = ""
    description: str = ""
    examples: list[str] = field(default_factory=list)  # cURL commands, JSON responses
    parameters_text: str = ""  # Parameter tables/descriptions
    source_url: str = ""


@dataclass
class ScrapingResult:
    """Result of scraping API documentation."""

    page_markdowns: list[PageMarkdown]  # Individual page contents for LLM extraction
    base_url: str
    pages_scraped: int
    raw_markdown: str  # Combined markdown for fallback
    auth_content: str = ""  # Extracted authentication documentation


@dataclass
class PageMarkdown:
    """A scraped page's markdown content and metadata."""
    url: str
    markdown: str


@dataclass
class ScrapeProgress:
    """Progress update during scraping."""

    pages_scraped: int
    pages_queued: int
    current_url: str


# Navigation link keywords (prioritize these)
NAV_KEYWORDS = [
    "api",
    "reference",
    "endpoint",
    "resource",
    "method",
    "operation",
    "rest",
    "documentation",
    "docs",
    "guide",
]

# Keywords that indicate an actual API endpoint page (highest priority)
ENDPOINT_KEYWORDS = [
    "get-",
    "create-",
    "update-",
    "delete-",
    "list-",
    "search-",
    "save-",
    "remove-",
    "check-",
    "add-",
    "set-",
    "start-",
    "stop-",
    "pause-",
    "play-",
    "skip-",
    "seek-",
    "transfer-",
    "follow-",
    "unfollow-",
]

# Keywords that indicate we should NOT follow a link
SKIP_KEYWORDS = [
    "changelog",
    "blog",
    "pricing",
    "support",
    "contact",
    "login",
    "signup",
    "register",
    "download",
    "community",
    "forum",
    "faq",
    "terms",
    "privacy",
    "legal",
    "status",
    "careers",
]

# Keywords that indicate authentication documentation
AUTH_URL_KEYWORDS = [
    "auth",
    "authentication",
    "authorization",
    "oauth",
    "token",
    "credentials",
    "api-key",
    "apikey",
    "access-token",
    "getting-started",
]

# Heading patterns that indicate auth sections
AUTH_HEADING_PATTERN = re.compile(
    r"^#+\s*(authentication|authorization|auth|oauth|getting\s+started|access\s+token|api\s+key|credentials|client\s+credentials)",
    re.IGNORECASE | re.MULTILINE,
)


def scrape_documentation(
    url: str,
    max_pages: int = 200,
    on_progress: Callable[[ScrapeProgress], None] | None = None,
) -> ScrapingResult:
    """
    Scrape API documentation from a URL.

    Args:
        url: The starting URL to scrape
        max_pages: Maximum number of pages to crawl
        on_progress: Optional callback for progress updates

    Returns:
        ScrapingResult with page markdowns for LLM extraction
    """
    visited: set[str] = set()
    page_markdowns: list[PageMarkdown] = []
    markdown_parts: list[str] = []  # For combined raw markdown
    auth_content_parts: list[str] = []  # Collect auth documentation

    # Parse the starting URL to get the domain
    parsed_start = urlparse(url)
    domain = f"{parsed_start.scheme}://{parsed_start.netloc}"
    base_path = parsed_start.path.rsplit("/", 1)[0] if "/" in parsed_start.path else ""

    # Initialize MarkItDown converter
    md_converter = MarkItDown()

    # Priority queue: (priority, url) - lower priority = process first
    pages_to_visit: list[tuple[int, str]] = [(0, url)]

    def is_auth_related_url(page_url: str) -> bool:
        """Check if URL is likely about authentication."""
        lower_url = page_url.lower()
        return any(kw in lower_url for kw in AUTH_URL_KEYWORDS)

    def extract_auth_sections(markdown: str) -> str:
        """Extract authentication-related sections from markdown."""
        sections: list[str] = []

        # Find all auth-related headings and extract their sections
        matches = list(AUTH_HEADING_PATTERN.finditer(markdown))

        for i, match in enumerate(matches):
            section_start = match.start()
            # Find next heading of same or higher level, or end of document
            heading_level = len(match.group(0).split()[0])  # Count #s
            next_heading_pattern = re.compile(
                rf"^#{{1,{heading_level}}}\s+\S",
                re.MULTILINE,
            )
            next_match = next_heading_pattern.search(markdown, match.end())
            section_end = next_match.start() if next_match else len(markdown)

            section = markdown[section_start:section_end].strip()
            if section:
                sections.append(section)

        return "\n\n---\n\n".join(sections)

    def get_link_priority(href: str, link_text: str) -> int:
        """Determine priority of a link (lower = higher priority)."""
        lower_href = href.lower()
        lower_text = link_text.lower()

        # Skip links with bad keywords
        for keyword in SKIP_KEYWORDS:
            if keyword in lower_href or keyword in lower_text:
                return 999  # Very low priority (skip)

        # Highest priority: actual endpoint documentation pages
        for keyword in ENDPOINT_KEYWORDS:
            if keyword in lower_href or keyword in lower_text:
                return 1  # Very high priority for endpoint pages

        # High priority: sibling pages (same path prefix as starting URL)
        if base_path and href.startswith(base_path):
            return 5

        # Medium-high priority for navigation keywords
        for i, keyword in enumerate(NAV_KEYWORDS):
            if keyword in lower_href or keyword in lower_text:
                return 10 + i  # Priority based on keyword position

        return 50  # Default medium priority

    def is_valid_doc_link(href: str, base_domain: str) -> bool:
        """Check if a link is likely documentation and on the same domain."""
        if not href:
            return False

        # Skip anchors, javascript, mailto, etc.
        if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return False

        # Skip file downloads
        if any(href.lower().endswith(ext) for ext in [".pdf", ".zip", ".tar", ".gz"]):
            return False

        # Parse the link
        parsed = urlparse(href)

        # If it's a relative link, it's on the same domain
        if not parsed.netloc:
            return True

        # Check if it's the same domain
        return parsed.netloc == urlparse(base_domain).netloc

    def extract_navigation_links(soup: BeautifulSoup, page_url: str) -> list[tuple[int, str]]:
        """Extract links from navigation elements with priority."""
        links: list[tuple[int, str]] = []

        # Look for navigation elements first (sidebar, nav, menu)
        nav_elements = soup.find_all(["nav", "aside"])
        nav_elements.extend(soup.find_all(class_=re.compile(r"(sidebar|menu|nav|toc)", re.I)))
        nav_elements.extend(soup.find_all(id=re.compile(r"(sidebar|menu|nav|toc)", re.I)))

        # Collect links from nav elements with higher priority
        nav_links = set()
        for nav in nav_elements:
            for link in nav.find_all("a", href=True):
                href = link["href"]
                absolute_url = urljoin(page_url, href)
                parsed = urlparse(absolute_url)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

                if is_valid_doc_link(href, domain) and clean_url not in visited:
                    link_text = link.get_text(strip=True)
                    priority = get_link_priority(href, link_text)
                    if priority < 999:
                        nav_links.add(clean_url)
                        links.append((priority, clean_url))

        # Also get links from main content but with lower priority
        for link in soup.find_all("a", href=True):
            href = link["href"]
            absolute_url = urljoin(page_url, href)
            parsed = urlparse(absolute_url)
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            if clean_url not in nav_links and clean_url not in visited:
                if is_valid_doc_link(href, domain):
                    link_text = link.get_text(strip=True)
                    priority = get_link_priority(href, link_text) + 20  # Lower priority than nav
                    if priority < 999:
                        links.append((priority, clean_url))

        return links

    @dataclass
    class PageResult:
        """Result from scraping a single page."""
        url: str
        links: list[tuple[int, str]]
        markdown: str
        auth_content: str

    def fetch_page(page_url: str) -> PageResult | None:
        """Fetch and parse a single page. Thread-safe (no shared state mutation)."""
        try:
            response = httpx.get(
                page_url,
                follow_redirects=True,
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; apitomcp/1.0; +https://github.com/apitomcp)"
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        # Parse HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script, style, and irrelevant elements
        for element in soup(["script", "style", "footer", "header"]):
            element.decompose()

        # Extract navigation links
        links = extract_navigation_links(soup, page_url)

        # Convert to markdown (each thread needs its own converter)
        local_converter = MarkItDown()
        try:
            result = local_converter.convert_stream(
                response.text.encode("utf-8"),
                file_extension=".html",
            )
            markdown = result.text_content
        except Exception:
            markdown = soup.get_text(separator="\n", strip=True)

        # Extract auth content
        auth_content = ""
        if markdown:
            if is_auth_related_url(page_url):
                auth_content = f"# Auth Source: {page_url}\n\n{markdown}"
            else:
                auth_sections = extract_auth_sections(markdown)
                if auth_sections:
                    auth_content = f"# Auth Section from: {page_url}\n\n{auth_sections}"

        return PageResult(
            url=page_url,
            links=links,
            markdown=markdown or "",
            auth_content=auth_content,
        )

    # Parallel scraping with thread pool
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while pages_to_visit and len(visited) < max_pages:
            # Sort by priority and get batch of highest priority URLs
            pages_to_visit.sort(key=lambda x: x[0])
            
            # Get batch of URLs to fetch (up to MAX_WORKERS, respecting max_pages)
            batch_urls: list[str] = []
            while pages_to_visit and len(batch_urls) < MAX_WORKERS and len(visited) + len(batch_urls) < max_pages:
                _, url = pages_to_visit.pop(0)
                if url not in visited:
                    batch_urls.append(url)
                    visited.add(url)  # Mark as visited before fetching to avoid duplicates

            if not batch_urls:
                break

            # Fetch pages in parallel
            futures = {executor.submit(fetch_page, url): url for url in batch_urls}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    # Collect results
                    if result.markdown:
                        page_markdowns.append(PageMarkdown(
                            url=result.url,
                            markdown=result.markdown,
                        ))
                        markdown_parts.append(f"# Source: {result.url}\n\n{result.markdown}\n\n---\n")
                    if result.auth_content:
                        auth_content_parts.append(result.auth_content)
                    
                    # Add new links to queue
                    for priority, link in result.links:
                        if link not in visited:
                            pages_to_visit.append((priority, link))
                    
                    # Update progress after each page completes
                    if on_progress:
                        on_progress(ScrapeProgress(
                            pages_scraped=len(visited),
                            pages_queued=len(pages_to_visit),
                            current_url=result.url,
                        ))

    # Detect base URL
    base_url = detect_api_base_url(markdown_parts, domain)

    # Combine markdown for fallback
    combined_markdown = "\n".join(markdown_parts)

    # Combine auth content
    combined_auth_content = "\n\n---\n\n".join(auth_content_parts)

    return ScrapingResult(
        page_markdowns=page_markdowns,
        base_url=base_url,
        pages_scraped=len(visited),
        raw_markdown=combined_markdown,
        auth_content=combined_auth_content,
    )


def detect_api_base_url(markdown_parts: list[str], domain: str) -> str:
    """Try to detect the API base URL from documentation content."""
    combined = "\n".join(markdown_parts)

    # Common patterns for API base URLs
    patterns = [
        r"https?://api\.[a-zA-Z0-9.-]+(?:/v\d+)?",
        r"https?://[a-zA-Z0-9.-]+/api(?:/v\d+)?",
        r"https?://[a-zA-Z0-9.-]+/v\d+",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, combined)
        if matches:
            # Return the most common match
            from collections import Counter

            counter = Counter(matches)
            most_common = counter.most_common(1)
            if most_common:
                return most_common[0][0]

    # Fallback: construct from domain
    parsed = urlparse(domain)
    return f"https://api.{parsed.netloc.replace('www.', '')}"


