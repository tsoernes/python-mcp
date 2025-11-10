# /// script
# dependencies = ["playwright", "beautifulsoup4", "requests<3", "rich"]
# requires-python = ">=3.12"
# ///
"""
Scrape MCP servers from mcpservers.org using Playwright.

Usage:
    uv run --script scripts/scrape_mcpservers.py --out servers.json

Outputs JSON with list of servers and key metadata.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from typing import Iterable

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from rich import print

BASE_URL = "https://mcpservers.org"

@dataclass
class Server:
    name: str
    url: str
    category: str | None
    repo: str | None
    tags: list[str]
    description: str | None


def fetch_html(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        html = page.content()
        browser.close()
        return html


def parse_listing(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[tuple[str, str]] = []
    for card in soup.select("a.card-link[href^='/servers/']"):
        name = card.select_one(".card-title").get_text(strip=True) if card.select_one(".card-title") else None
        href = card.get("href")
        if not href:
            continue
        url = BASE_URL + href
        if name:
            items.append((name, url))
    # Fallback: generic links under /servers/
    if not items:
        for a in soup.select("a[href^='/servers/']"):
            href = a.get("href")
            text = a.get_text(strip=True)
            if href and text:
                items.append((text, BASE_URL + href))
    return items


def parse_server(html: str, url: str) -> Server | None:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1, h2")
    name = title_el.get_text(strip=True) if title_el else url.rsplit("/", 1)[-1]

    # Try description paragraph under header
    desc_el = soup.select_one(".server-description, .lead, article p")
    description = desc_el.get_text(strip=True) if desc_el else None

    # Tags
    tags = [t.get_text(strip=True) for t in soup.select(".tags .tag, .tag-list .tag, .badge")]

    # Category
    breadcrumb = soup.select(".breadcrumb a")
    category = breadcrumb[-1].get_text(strip=True) if breadcrumb else None

    # Repo link heuristics
    repo = None
    for a in soup.select("a[href*='github.com']"):
        href = a.get("href")
        if href and "/servers/" not in href:
            repo = href
            break

    return Server(name=name, url=url, category=category, repo=repo, tags=tags, description=description)


def iter_listing_urls() -> Iterable[str]:
    yield BASE_URL + "/"
    yield BASE_URL + "/remote-mcp-servers"
    yield BASE_URL + "/category/search"
    yield BASE_URL + "/category/development"
    yield BASE_URL + "/category/data"
    yield BASE_URL + "/category/automation"


def main(argv: list[str]) -> int:
    out_path = "servers.json"
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out_path = argv[i + 1]
    print(f"[bold]Scraping MCP servers from[/] {BASE_URL}")

    # Collect listing pages
    server_links: dict[str, str] = {}
    for url in iter_listing_urls():
        html = fetch_html(url)
        for name, server_url in parse_listing(html):
            server_links[server_url] = name

    print(f"Found {len(server_links)} server detail pages")

    servers: list[Server] = []
    for detail_url, _ in sorted(server_links.items()):
        html = fetch_html(detail_url)
        server = parse_server(html, detail_url)
        if server:
            servers.append(server)

    data = [asdict(s) for s in servers]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(servers)} servers to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
