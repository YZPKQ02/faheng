"""Low-frequency acquisition and parsing for the official MOJ regulation database."""

import re
from datetime import date
from html.parser import HTMLParser

import httpx

from app.legal_ingestion import RawLegalChunk, RawLegalDocument

SEARCH_URL = "https://xzfg.moj.gov.cn/SearchFront"
DETAIL_URL = "https://xzfg.moj.gov.cn/front/law/detail?LawID={law_id}"
USER_AGENT = "AI-Legal-Advisor-Research/0.1 (low-frequency pilot corpus)"
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇两\d]+条)\s*(.*)$", re.S)
SUPPLEMENT_RE = re.compile(
    r"^(?P<label>附\s*[录件])"
    r"(?P<number>\s*(?:[一二三四五六七八九十百千零〇两\d]+|[（(][一二三四五六七八九十百千零〇两\d]+[）)]))?"
    r"(?:\s*[：:]\s*.*|\s*)$",
    re.S,
)
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


class _LawPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[set[str]] = []
        self._capture: tuple[str, list[str]] | None = None
        self.title = ""
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set(dict(attrs).get("class", "").split())
        self._stack.append(classes)
        ancestors = set().union(*self._stack) if self._stack else set()
        if "text-title" in classes:
            self._capture = ("title", [])
        elif "law-chapter" in ancestors and tag in {"p", "h1", "h2", "h3"}:
            self._capture = (tag, [])

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is not None:
            kind, parts = self._capture
            if (kind == "title" and tag == "div") or kind == tag:
                text = re.sub(r"\s+", " ", "".join(parts)).strip()
                if kind == "title":
                    self.title = text
                elif text:
                    self.blocks.append((kind, text))
                self._capture = None
        if self._stack:
            self._stack.pop()


def _parse_date(value: str) -> date:
    match = DATE_RE.search(value)
    if not match:
        raise ValueError(f"无法解析日期：{value}")
    return date(*(int(part) for part in match.groups()))


def _extract_dates(preamble: str) -> tuple[date | None, date]:
    matches = list(DATE_RE.finditer(preamble))
    if not matches:
        raise ValueError("官方正文未包含可解析的公布或施行日期")
    promulgated = _parse_date(matches[-1].group(0))
    effective_match = re.search(
        r"自\s*(\d{4}年\d{1,2}月\d{1,2}日)\s*起?施行", preamble
    )
    effective = _parse_date(effective_match.group(1)) if effective_match else promulgated
    return promulgated, effective


def _supplement_locator(block: str) -> str | None:
    match = SUPPLEMENT_RE.match(block)
    if not match:
        return None
    label = re.sub(r"\s+", "", match.group("label"))
    number = re.sub(r"\s+", "", match.group("number") or "")
    return f"{label}{number}"


def parse_law_page(html: str, law_id: str, *, keywords: list[str]) -> RawLegalDocument:
    parser = _LawPageParser()
    parser.feed(html)
    if not parser.title:
        raise ValueError(f"LawID={law_id} 缺少标题")
    chunks: list[RawLegalChunk] = []
    heading: str | None = None
    preamble: list[str] = []
    for kind, block in parser.blocks:
        supplement_locator = _supplement_locator(block)
        if supplement_locator:
            chunks.append(
                RawLegalChunk(
                    locator=supplement_locator,
                    content=block,
                    heading=heading,
                    keywords=keywords,
                    sequence=len(chunks),
                )
            )
            continue
        if kind in {"h1", "h2", "h3"}:
            heading = block
            continue
        match = ARTICLE_RE.match(block)
        if match:
            chunks.append(
                RawLegalChunk(
                    locator=match.group(1),
                    content=block,
                    heading=heading,
                    keywords=keywords,
                    sequence=len(chunks),
                )
            )
        elif chunks:
            chunks[-1].content = f"{chunks[-1].content}\n{block}"
        else:
            preamble.append(block)
    if not chunks:
        raise ValueError(f"LawID={law_id} 未解析出法条")
    promulgated, effective = _extract_dates(" ".join(preamble))
    return RawLegalDocument(
        title=parser.title,
        authority_type="administrative_regulation",
        level="行政法规",
        jurisdiction="全国",
        issuing_body="国务院",
        source_url=DETAIL_URL.format(law_id=law_id),
        version_label="国家行政法规库现行有效版本",
        status="active",
        promulgated_on=promulgated,
        effective_on=effective,
        review_status="pending",
        chunks=chunks,
    )


def search_law_ids(
    client: httpx.Client, query: str, *, max_pages: int = 20
) -> tuple[list[str], int]:
    ids: list[str] = []
    total = 0
    for page in range(1, max_pages + 1):
        response = client.get(
            SEARCH_URL,
            params={
                "SiteID": "122",
                "Query": query,
                "Type": "2",
                "QueryAll": f"{query}ZVING2",
                "PageIndex": page,
            },
        )
        response.raise_for_status()
        if page == 1:
            total_match = re.search(r'id="law-total"[^>]*value="(\d+)"', response.text)
            total = int(total_match.group(1)) if total_match else 0
        page_ids = re.findall(r"law/detail\?LawID=(\d+)", response.text)
        unique_page_ids = list(dict.fromkeys(page_ids))
        if not unique_page_ids:
            break
        ids.extend(law_id for law_id in unique_page_ids if law_id not in ids)
        if total and len(ids) >= total:
            break
    return ids, total


def official_client() -> httpx.Client:
    return httpx.Client(
        timeout=45,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
