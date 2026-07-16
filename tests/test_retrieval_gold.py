import json

from app.retrieval_gold import build_gold_candidates, validate_gold_candidates


def _corpus(tmp_path):
    rows = []
    for topic_index, topic in enumerate(
        ("劳动合同", "工资", "工伤", "社会保险", "劳动保护", "就业", "监察", "船员")
    ):
        for index in range(24):
            rows.append(
                {
                    "title": f"{topic}测试条例{index % 4}",
                    "source_url": f"https://xzfg.moj.gov.cn/front/law/detail?LawID={topic_index}",
                    "effective_on": "2020-01-01",
                    "chunks": [
                        {
                            "locator": f"第{topic_index * 24 + index + 1}条",
                            "content": (
                                f"第{topic_index * 24 + index + 1}条 "
                                f"公司处理{topic}事项中的第{index + 1000}种情形时应当依法办理。"
                            ),
                        }
                    ],
                }
            )
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_gold_builder_creates_balanced_leak_free_splits(tmp_path):
    corpus = _corpus(tmp_path)
    candidates = build_gold_candidates(corpus)
    report = validate_gold_candidates(candidates, corpus)
    assert len(candidates) == 120
    assert report["passed_count"] == 120
    assert report["split_counts"] == {"dev": 80, "test": 40}
    assert report["difficulty_counts"] == {"medium": 96, "hard": 24}
