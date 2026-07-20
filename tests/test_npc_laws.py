import io
import zipfile

import httpx

from app.npc_laws import extract_docx_paragraphs, find_active_law, parse_npc_law


def _docx(*paragraphs: str) -> bytes:
    body = "".join(
        f'<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>' for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_extract_and_parse_npc_docx_keeps_article_boundaries():
    docx = _docx(
        "测试法",
        "目录",
        "第一章 总则",
        "第二章 其他规定",
        "第一章 总则",
        "第一条 第一款。",
        "第二款。",
        "第二章 其他规定",
        "第二条 第二条内容。",
    )
    detail = {
        "bbbs": "law-1",
        "title": "测试法",
        "flxz": "法律",
        "sxx": 3,
        "zdjgName": "全国人民代表大会常务委员会",
        "gbrq": "2026-01-01",
        "sxrq": "2026-02-01",
    }

    assert extract_docx_paragraphs(docx)[0] == "测试法"
    document = parse_npc_law(
        detail,
        docx,
        expected_title="测试法",
        expected_last_locator="第二条",
        expected_article_count=2,
    )

    assert [chunk.locator for chunk in document.chunks] == ["第一条", "第二条"]
    assert document.chunks[0].content == "第一条 第一款。\n第二款。"
    assert document.chunks[0].heading == "第一章 总则"
    assert document.chunks[1].heading == "第二章 其他规定"


def test_find_active_law_requires_one_exact_effective_law():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "bbbs": "active",
                        "title": "<em>测试法</em>",
                        "flxz": "法律",
                        "sxx": 3,
                    },
                    {
                        "bbbs": "expired",
                        "title": "<em>测试法</em>",
                        "flxz": "法律",
                        "sxx": 1,
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_active_law(client, "测试法")

    assert result["bbbs"] == "active"
