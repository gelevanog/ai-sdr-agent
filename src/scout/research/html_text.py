"""HTML to the text Scout reasons about: visible text blocks with their dates, the page date, the links, and the
hidden text (kept apart: it is never shown to a model, only scanned for injection attempts).

A block is an article, a job card or a paragraph-like element. Each block keeps the date printed next to it
(`<time datetime>` or a date in the text), so a signal's age comes from the page, not from a model's guess.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, Tag
from pydantic import BaseModel, Field

from scout.research.dates import first_date, parse_iso

_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px)?\b|opacity\s*:\s*0(?:\.0+)?\b", re.I
)
_CONTAINERS = ("article",)
_CONTAINER_CLASSES = {"job", "post", "news-item", "person", "press-release"}
_LEAVES = ("p", "li", "h1", "h2", "h3", "h4", "td", "dd", "dt", "blockquote", "figcaption")
_SPACE = re.compile(r"\s+")


class Block(BaseModel):
    text: str
    date: dt.date | None = None
    kind: str = "text"


class ParsedPage(BaseModel):
    url: str
    title: str = ""
    blocks: list[Block] = Field(default_factory=list)
    hidden_text: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    page_date: dt.date | None = None
    """The page's own "last updated" date (meta tag or footer), if it has one."""

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


def clean(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
        return True
    style = tag.get("style")
    return isinstance(style, str) and bool(_HIDDEN_STYLE.search(style))


def _block_date(tag: Tag, text: str) -> dt.date | None:
    time_tag = tag.find("time")
    if isinstance(time_tag, Tag):
        value = time_tag.get("datetime")
        parsed = parse_iso(value if isinstance(value, str) else None) or first_date(time_tag.get_text(" "))
        if parsed:
            return parsed
    return first_date(text)


def parse_html(url: str, raw: str) -> ParsedPage:
    soup = BeautifulSoup(raw, "html.parser")
    hidden: list[str] = []
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        if clean(str(comment)):
            hidden.append(clean(str(comment)))
        comment.extract()
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if isinstance(tag, Tag) and not tag.decomposed and _is_hidden(tag):
            text = clean(tag.get_text(" "))
            if text:
                hidden.append(text)
            tag.decompose()

    title_tag = soup.find("title")
    title = clean(title_tag.get_text(" ")) if isinstance(title_tag, Tag) else ""
    page_date = None
    meta = soup.find("meta", attrs={"name": re.compile(r"last-modified|article:modified_time|date", re.I)})
    if isinstance(meta, Tag):
        content = meta.get("content")
        page_date = parse_iso(content if isinstance(content, str) else None)
    footer = soup.find("footer")
    if page_date is None and isinstance(footer, Tag):
        match = re.search(r"(?:last updated|updated)[:\s]+(.{6,40})", footer.get_text(" "), re.I)
        if match:
            page_date = first_date(match.group(1))

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if isinstance(href, str) and not href.startswith(("mailto:", "tel:", "javascript:", "#")):
            absolute = urljoin(url, href).split("#")[0]
            if absolute not in links:
                links.append(absolute)

    body = soup.find("main") or soup.body or soup
    blocks: list[Block] = []
    consumed: set[int] = set()
    for tag in body.find_all(True):
        if not isinstance(tag, Tag) or any(id(parent) in consumed for parent in tag.parents):
            continue
        classes = set(tag.get("class") or [])
        if tag.name in _CONTAINERS or _CONTAINER_CLASSES & classes:
            text = clean(tag.get_text(" "))
            if text:
                kind = "job" if "job" in classes else ("person" if "person" in classes else "item")
                blocks.append(Block(text=text, date=_block_date(tag, text), kind=kind))
            consumed.add(id(tag))
        elif tag.name in _LEAVES and not tag.find(_LEAVES):
            text = clean(tag.get_text(" "))
            if text:
                blocks.append(Block(text=text, date=first_date(text)))
    # Footer (address, "last updated") is useful context for firmographics.
    if isinstance(footer, Tag) and footer is not body:
        for p in footer.find_all(["p", "address"]) or [footer]:
            text = clean(p.get_text(" "))
            if text:
                blocks.append(Block(text=text, date=None, kind="footer"))
    return ParsedPage(url=url, title=title, blocks=blocks, hidden_text=hidden, links=links, page_date=page_date)


def same_site(url: str, other: str) -> bool:
    a, b = urlsplit(url).hostname or "", urlsplit(other).hostname or ""
    return a.removeprefix("www.") == b.removeprefix("www.")
