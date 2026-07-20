"""Low-frequency acquisition and deterministic parsing for NPC national laws."""

import io
import re
import zipfile
from datetime import date
from html import unescape
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from app.legal_ingestion import RawLegalChunk, RawLegalDocument

BASE_URL = "https://flk.npc.gov.cn"
SEARCH_URL = f"{BASE_URL}/law-search/search/list"
DETAIL_API_URL = f"{BASE_URL}/law-search/search/flfgDetails"
DOWNLOAD_URL = f"{BASE_URL}/law-search/download/mobile"
DETAIL_URL = f"{BASE_URL}/detail?id={{law_id}}&title={{title}}"
USER_AGENT = "AI-Legal-Advisor-Research/0.1 (low-frequency official corpus)"

ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇两\d]+条)\s*(.*)$", re.S)
HEADING_RE = re.compile(
    r"^第[一二三四五六七八九十百千零〇两\d]+(?:章|节)(?:\s+.*)?$"
)
TAG_RE = re.compile(r"<[^>]+>")
WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def official_client() -> httpx.Client:
    return httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _plain_title(value: str) -> str:
    return unescape(TAG_RE.sub("", value)).strip()


def find_active_law(client: httpx.Client, title: str) -> dict:
    response = client.post(
        SEARCH_URL,
        json={
            "searchRange": 1,
            "sxrq": [],
            "gbrq": [],
            "sxx": [3],
            "searchType": 1,
            "xgzlSearch": False,
            "searchContent": title,
            "pageNum": 1,
            "pageSize": 20,
        },
    )
    response.raise_for_status()
    payload = response.json()
    matches = [
        row
        for row in payload.get("rows", [])
        if _plain_title(row.get("title", "")) == title
        and row.get("flxz") == "法律"
        and row.get("sxx") == 3
    ]
    if len(matches) != 1:
        raise ValueError(f"《{title}》现行法律精确匹配数量为 {len(matches)}，需人工复核")
    return matches[0]


def fetch_law_detail(client: httpx.Client, law_id: str) -> dict:
    response = client.get(DETAIL_API_URL, params={"bbbs": law_id})
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise ValueError(f"全国人大详情 API 未返回有效数据：{law_id}")
    return payload["data"]


def download_law_docx(client: httpx.Client, law_id: str) -> bytes:
    response = client.get(
        DOWNLOAD_URL,
        params={"format": "docx", "bbbs": law_id, "fileId": ""},
    )
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        raise ValueError(f"全国人大下载接口未返回 DOCX：{law_id}")
    return response.content


def extract_docx_paragraphs(docx: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("官方文件不是有效 DOCX") from exc
    root = ElementTree.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:body//w:p", WORD_NAMESPACE):
        text = "".join(
            node.text or "" for node in paragraph.findall(".//w:t", WORD_NAMESPACE)
        )
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def parse_npc_law(
    detail: dict,
    docx: bytes,
    *,
    expected_title: str,
    expected_last_locator: str,
    expected_article_count: int,
) -> RawLegalDocument:
    if detail.get("title") != expected_title:
        raise ValueError(f"官方详情标题不匹配：《{detail.get('title')}》")
    if detail.get("flxz") != "法律" or detail.get("sxx") != 3:
        raise ValueError(f"《{expected_title}》不是现行有效法律")
    paragraphs = extract_docx_paragraphs(docx)
    if expected_title not in paragraphs[:20]:
        raise ValueError(f"《{expected_title}》DOCX 标题校验失败")

    chunks: list[RawLegalChunk] = []
    heading: str | None = None
    for paragraph in paragraphs:
        if HEADING_RE.match(paragraph):
            heading = paragraph
            continue
        match = ARTICLE_RE.match(paragraph)
        if match:
            chunks.append(
                RawLegalChunk(
                    locator=match.group(1),
                    content=paragraph,
                    heading=heading,
                    keywords=[],
                    sequence=len(chunks),
                )
            )
        elif chunks:
            chunks[-1].content = f"{chunks[-1].content}\n{paragraph}"
    if not chunks:
        raise ValueError(f"《{expected_title}》未解析出法条")
    if chunks[0].locator != "第一条" or chunks[-1].locator != expected_last_locator:
        raise ValueError(
            f"《{expected_title}》法条范围异常："
            f"{chunks[0].locator} 至 {chunks[-1].locator}"
        )
    if len(chunks) != expected_article_count:
        raise ValueError(
            f"《{expected_title}》法条数量异常："
            f"{len(chunks)}，预期 {expected_article_count}"
        )

    law_id = str(detail["bbbs"])
    return RawLegalDocument(
        title=expected_title,
        authority_type="statute",
        level="法律",
        jurisdiction="全国",
        issuing_body=str(detail["zdjgName"]),
        source_url=DETAIL_URL.format(law_id=law_id, title=quote(expected_title)),
        version_label=f"国家法律法规数据库现行有效版本（{detail['gbrq']}）",
        status="active",
        promulgated_on=date.fromisoformat(detail["gbrq"]),
        effective_on=date.fromisoformat(detail["sxrq"]),
        review_status="pending",
        chunks=chunks,
    )
