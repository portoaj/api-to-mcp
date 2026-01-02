"""Web scraping and operation extraction for API documentation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from markitdown import MarkItDown


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

    operations: list[Operation]
    base_url: str
    pages_scraped: int
    raw_markdown: str  # For fallback if operation extraction fails
    auth_content: str = ""  # Extracted authentication documentation


# Patterns for identifying API operations
HTTP_METHOD_PATTERN = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s\)\"\'<>\n]+)",
    re.IGNORECASE,
)

CURL_PATTERN = re.compile(
    r"curl\s+.*?(?:--request|-X)\s+(GET|POST|PUT|PATCH|DELETE)\s+.*?(https?://[^\s\"\']+|[\"\'](https?://[^\s\"\']+)[\"\'])",
    re.IGNORECASE | re.DOTALL,
)

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
) -> ScrapingResult:
    """
    Scrape API documentation from a URL and extract operations.

    Args:
        url: The starting URL to scrape
        max_pages: Maximum number of pages to crawl

    Returns:
        ScrapingResult with extracted operations and metadata
    """
    visited: set[str] = set()
    all_operations: list[Operation] = []
    markdown_parts: list[str] = []
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

    def extract_operations_from_content(
        content: str, source_url: str
    ) -> list[Operation]:
        """Extract API operations from page content."""
        operations: list[Operation] = []
        seen_ops: set[tuple[str, str]] = set()

        # Find HTTP method + path patterns
        for match in HTTP_METHOD_PATTERN.finditer(content):
            method = match.group(1).upper()
            path = match.group(2)

            # Clean up the path
            path = path.rstrip(".,;:)")

            # Skip if it doesn't look like an API path
            if not path.startswith("/") or len(path) < 2:
                continue

            # Skip duplicates
            op_key = (method, path)
            if op_key in seen_ops:
                continue
            seen_ops.add(op_key)

            # Extract context around the match
            start = max(0, match.start() - 500)
            end = min(len(content), match.end() + 2000)
            context = content[start:end]

            # Try to extract description (text before the method)
            pre_match = content[max(0, match.start() - 500) : match.start()]
            lines = pre_match.strip().split("\n")
            description = ""
            for line in reversed(lines):
                line = line.strip()
                if line and not line.startswith("#") and len(line) > 10:
                    description = line
                    break

            operations.append(
                Operation(
                    method=method,
                    path=path,
                    description=description,
                    parameters_text=context,
                    source_url=source_url,
                )
            )

        # Also look for cURL examples
        for match in CURL_PATTERN.finditer(content):
            method = match.group(1).upper()
            url_match = match.group(2) or match.group(3)

            if url_match:
                # Extract path from URL
                parsed = urlparse(url_match.strip("\"'"))
                path = parsed.path

                if path and path != "/":
                    op_key = (method, path)
                    if op_key not in seen_ops:
                        seen_ops.add(op_key)

                        # Get the full cURL command
                        curl_start = content.rfind("curl", 0, match.start() + 10)
                        if curl_start != -1:
                            curl_end = content.find("\n\n", match.end())
                            if curl_end == -1:
                                curl_end = min(len(content), match.end() + 500)
                            curl_example = content[curl_start:curl_end].strip()

                            operations.append(
                                Operation(
                                    method=method,
                                    path=path,
                                    examples=[curl_example],
                                    source_url=source_url,
                                )
                            )

        return operations

    def scrape_page(page_url: str) -> list[tuple[int, str]]:
        """Scrape a single page and return prioritized links to follow."""
        if page_url in visited:
            return []

        visited.add(page_url)
        links_to_follow: list[tuple[int, str]] = []

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
            return []

        # Parse HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script, style, and irrelevant elements
        for element in soup(["script", "style", "footer", "header"]):
            element.decompose()

        # Extract navigation links
        links_to_follow = extract_navigation_links(soup, page_url)

        # Convert to markdown
        try:
            result = md_converter.convert_stream(
                response.text.encode("utf-8"),
                file_extension=".html",
            )
            markdown = result.text_content
        except Exception:
            # Fallback to basic text extraction
            markdown = soup.get_text(separator="\n", strip=True)

        if markdown:
            markdown_parts.append(f"# Source: {page_url}\n\n{markdown}\n\n---\n")

            # Extract operations from this page
            page_operations = extract_operations_from_content(markdown, page_url)
            all_operations.extend(page_operations)

            # Collect auth content
            # If URL looks auth-related, include entire page
            if is_auth_related_url(page_url):
                auth_content_parts.append(f"# Auth Source: {page_url}\n\n{markdown}")
            else:
                # Otherwise, extract just auth sections
                auth_sections = extract_auth_sections(markdown)
                if auth_sections:
                    auth_content_parts.append(
                        f"# Auth Section from: {page_url}\n\n{auth_sections}"
                    )

        return links_to_follow

    # Start scraping with priority queue
    while pages_to_visit and len(visited) < max_pages:
        # Sort by priority and get the highest priority page
        pages_to_visit.sort(key=lambda x: x[0])
        _, current_url = pages_to_visit.pop(0)

        if current_url in visited:
            continue

        new_links = scrape_page(current_url)

        # Add new links to queue
        for priority, link in new_links:
            if link not in visited:
                pages_to_visit.append((priority, link))

    # Deduplicate operations by (method, path)
    seen: set[tuple[str, str]] = set()
    unique_operations: list[Operation] = []
    for op in all_operations:
        key = (op.method, op.path)
        if key not in seen:
            seen.add(key)
            unique_operations.append(op)

    # Sort operations by path for consistent ordering
    unique_operations.sort(key=lambda op: (op.path, op.method))

    # Detect base URL
    base_url = detect_api_base_url(markdown_parts, domain)

    # Combine markdown for fallback
    combined_markdown = "\n".join(markdown_parts)

    # Combine auth content
    combined_auth_content = "\n\n---\n\n".join(auth_content_parts)

    return ScrapingResult(
        operations=unique_operations,
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


def merge_operations(operations: list[Operation]) -> list[Operation]:
    """Merge duplicate operations, combining their information."""
    merged: dict[tuple[str, str], Operation] = {}

    for op in operations:
        key = (op.method, op.path)

        if key not in merged:
            merged[key] = op
        else:
            existing = merged[key]
            # Merge examples
            existing.examples.extend(op.examples)
            # Use longer description
            if len(op.description) > len(existing.description):
                existing.description = op.description
            # Combine parameters text
            if op.parameters_text and op.parameters_text not in existing.parameters_text:
                existing.parameters_text += "\n\n" + op.parameters_text

    return list(merged.values())
