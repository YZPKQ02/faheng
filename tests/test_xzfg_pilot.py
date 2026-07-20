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


def test_parse_law_page_keeps_appendix_separate_from_last_article():
    html = """
    <div class="text-title">女职工劳动保护特别规定</div>
    <div class="law-chapter"><div>
      <p>（2012年4月28日公布 自公布之日起施行）</p>
      <p><span>第十六条　</span><span>本规定自公布之日起施行。</span></p>
      <p><span>附录：</span></p>
      <p>女职工禁忌从事的劳动范围</p>
      <p>一、矿山井下作业。</p>
    </div></div>
    """

    document = parse_law_page(html, "343", keywords=["女职工"])

    assert [chunk.locator for chunk in document.chunks] == ["第十六条", "附录"]
    assert document.chunks[0].content == "第十六条 本规定自公布之日起施行。"
    assert document.chunks[1].content == (
        "附录：\n女职工禁忌从事的劳动范围\n一、矿山井下作业。"
    )
    assert document.chunks[1].sequence == 1


def test_parse_law_page_recognizes_numbered_attachment_heading():
    html = """
    <div class="text-title">测试条例</div>
    <div class="law-chapter"><div>
      <p>（2020年1月1日公布）</p>
      <p>第一条 正文。</p>
      <h2>附件（一）：</h2>
      <p>附件内容。</p>
    </div></div>
    """

    document = parse_law_page(html, "2", keywords=[])

    assert [chunk.locator for chunk in document.chunks] == ["第一条", "附件（一）"]
    assert document.chunks[0].content == "第一条 正文。"
    assert document.chunks[1].content == "附件（一）：\n附件内容。"
