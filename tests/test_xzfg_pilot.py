from app.xzfg_pilot import parse_law_page


def test_parse_law_page_keeps_article_boundaries_and_followup_paragraphs():
    html = """
    <div class="text-title">测试条例</div>
    <div class="law-chapter"><div>
      <p>（2020年1月1日公布 自2020年2月1日起施行）</p>
      <h2><span>第一章 总则</span></h2>
      <p><span>第一条　</span><span>第一款。</span></p>
      <p>第二款。</p>
      <p><span>第二条　</span><span>第二条内容。</span></p>
    </div></div>
    """
    document = parse_law_page(html, "1", keywords=["劳动"])
    assert document.title == "测试条例"
    assert document.effective_on.isoformat() == "2020-02-01"
    assert [chunk.locator for chunk in document.chunks] == ["第一条", "第二条"]
    assert document.chunks[0].content == "第一条 第一款。\n第二款。"
    assert document.chunks[0].heading == "第一章 总则"
