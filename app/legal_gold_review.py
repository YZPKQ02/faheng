"""AI legal-evidence review for the pilot retrieval benchmark."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


AUDIT_DATE = date(2026, 7, 16)
OFFICIAL_HOSTS = {"xzfg.moj.gov.cn", "flk.npc.gov.cn", "www.court.gov.cn"}
EXTERNAL_AUTHORITIES = {
    "中华人民共和国劳动合同法": {
        "source_url": "https://flk.npc.gov.cn/detail?fileId=&id=2c909fdd678bf17901678bf74d7106b3",
        "status": "有效",
    },
    "中华人民共和国劳动争议调解仲裁法": {
        "source_url": "https://flk.npc.gov.cn/detail?id=2c909fdd678bf17901678bf64f28039d",
        "status": "有效",
    },
    "中华人民共和国船员条例": {
        "source_url": "https://xzfg.moj.gov.cn/front/law/detail?LawID=1691",
        "status": "现行有效",
    },
    "对外承包工程管理条例": {
        "source_url": "https://xzfg.moj.gov.cn/front/law/detail?LawID=1061",
        "status": "现行有效",
    },
}
REFERENCE_RE = re.compile(r"《([^》]+)》")
QUOTED_RE = re.compile(r"“([^”]+)”")

REJECTED: dict[str, str] = {
    "pilot-49268c89e61e": "附录被采集器并入第十六条，法条定位不准确，不能作为金标准",
    "pilot-a815c9ceafc0": "村提留用途不属于本项目劳动争议检索核心范围",
    "pilot-e800f25f999c": "船员条例目的条款与对外劳务经营资格缺少实质共同法律问题",
    "pilot-94f20a1a6550": "女职工保护目的条款不是高毒作业场景的直接裁判或合规依据",
}

QUERY_REVISIONS: dict[str, str] = {
    "pilot-a470a8bb1f79": "劳动能力再次鉴定和复查鉴定应当在多长期限内完成，适用哪项期限规则？",
    "pilot-03298442f033": "劳动能力鉴定中，劳动功能障碍和生活自理障碍分别如何分级？",
    "pilot-38e2559c096a": "哪些社会保险费的征收缴纳适用该条例，哪些单位和个人属于缴费主体？",
    "pilot-9f77f6007653": "因劳动合同订立、履行、变更、解除或者终止发生争议，应当通过什么法律程序处理？",
    "pilot-fac62036e246": "用人单位抗拒劳动监察、不报送材料、拒不改正或者报复举报人，会承担什么行政责任？",
    "pilot-2cedaff77323": "征缴的社会保险费应当如何管理，任何单位或者个人能否挪用？",
}


def _corpus_citations(corpus_path: Path) -> dict[str, dict]:
    citations = {}
    for line in corpus_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        document = json.loads(line)
        for chunk in document["chunks"]:
            key = f"《{document['title']}》{chunk['locator']}"
            citations[key] = {
                "content": chunk["content"],
                "source_url": document["source_url"],
                "effective_on": document["effective_on"],
            }
    return citations


def _review_cross_query(query: str) -> str:
    fragments = QUOTED_RE.findall(query)
    if len(fragments) != 2:
        raise ValueError("跨法规题缺少两个明确问题片段")
    return (
        f"一次综合法律咨询包含两个独立问题：第一，{fragments[0]}；"
        f"第二，{fragments[1]}。请分别说明适用规则。"
    )


def review_candidates(candidate_path: Path, corpus_path: Path) -> tuple[list[dict], dict]:
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    citations = _corpus_citations(corpus_path)
    reviewed = []
    for candidate in candidates:
        item = dict(candidate)
        item.pop("split", None)
        rejected_reason = REJECTED.get(item["id"])
        if rejected_reason:
            item["review_status"] = "ai_reviewed"
            item["review_disposition"] = "rejected"
            item["review_confidence"] = "high"
            item["review_rationale"] = rejected_reason
            item["reviewed_on"] = AUDIT_DATE.isoformat()
            reviewed.append(item)
            continue

        original_query = item["query"]
        if item["difficulty"] == "hard":
            item["query"] = _review_cross_query(original_query)
        elif item["id"] in QUERY_REVISIONS:
            item["query"] = QUERY_REVISIONS[item["id"]]

        referenced_authorities = set()
        official_sources_verified = True
        effective_versions_verified = True
        for expected in item["expected_citations"]:
            source = citations.get(expected)
            if source is None:
                raise ValueError(f"审核题引用不在试点语料：{expected}")
            official_sources_verified &= urlparse(source["source_url"]).hostname in OFFICIAL_HOSTS
            effective_versions_verified &= date.fromisoformat(source["effective_on"]) <= AUDIT_DATE
            referenced_authorities.update(REFERENCE_RE.findall(source["content"][:1000]))
        cited_titles = {
            match.group(1)
            for expected in item["expected_citations"]
            if (match := re.match(r"《([^》]+)》", expected))
        }
        referenced_authorities -= cited_titles
        verified_external = [
            {"title": title, **EXTERNAL_AUTHORITIES[title], "verified_on": AUDIT_DATE.isoformat()}
            for title in sorted(referenced_authorities)
            if title in EXTERNAL_AUTHORITIES
        ]
        unverified_external = sorted(referenced_authorities - EXTERNAL_AUTHORITIES.keys())
        revised = item["query"] != original_query
        item["review_status"] = "ai_reviewed"
        item["review_disposition"] = "approved_after_revision" if revised else "approved"
        item["review_confidence"] = (
            "medium" if item["difficulty"] == "hard" or referenced_authorities else "high"
        )
        item["review_rationale"] = (
            "问题与最低预期依据直接对应；跨法规题已改为两个独立问题的多意图检索，"
            "不再暗示条文属于同一争议。"
            if item["difficulty"] == "hard"
            else "问题与法条规范对象、行为或法律后果直接对应，可作为最低预期引用。"
        )
        item["legal_review"] = {
            "official_source_verified": official_sources_verified,
            "effective_on_or_before_review_date": effective_versions_verified,
            "citation_scope": "minimum_expected_not_exhaustive",
            "verified_external_authorities": verified_external,
            "referenced_authorities_requiring_completeness_review": unverified_external,
            "completeness_limitation": (
                "当前试点主要覆盖行政法规；上位法、司法解释、部门规章和地方规则"
                "未被穷尽，因此不得把最低预期引用解释为完整法律意见。"
            ),
            "reviewer": "Codex AI legal evidence audit",
        }
        item["reviewed_on"] = AUDIT_DATE.isoformat()
        reviewed.append(item)

    approved = [item for item in reviewed if item["review_disposition"].startswith("approved")]
    ordered = sorted(
        approved,
        key=lambda item: hashlib.sha256(item["id"].encode("utf-8")).hexdigest(),
    )
    for index, item in enumerate(ordered):
        item["split"] = "test" if index < 40 else "dev"
    disposition_counts = {}
    confidence_counts = {}
    for item in reviewed:
        disposition_counts[item["review_disposition"]] = (
            disposition_counts.get(item["review_disposition"], 0) + 1
        )
        confidence_counts[item["review_confidence"]] = (
            confidence_counts.get(item["review_confidence"], 0) + 1
        )
    report = {
        "schema_version": "pilot_retrieval_gold_ai_reviewed_v2",
        "reviewed_count": len(reviewed),
        "approved_count": len(approved),
        "rejected_count": len(reviewed) - len(approved),
        "disposition_counts": disposition_counts,
        "confidence_counts": confidence_counts,
        "dev_count": sum(item.get("split") == "dev" for item in reviewed),
        "test_count": sum(item.get("split") == "test" for item in reviewed),
        "official_source_check_passed": all(
            item.get("legal_review", {}).get("official_source_verified", True)
            for item in approved
        ),
        "version_check_passed": all(
            item.get("legal_review", {}).get("effective_on_or_before_review_date", True)
            for item in approved
        ),
        "reviewer_type": "AI evidence and legal-consistency review; not lawyer sign-off",
        "reviewed_on": AUDIT_DATE.isoformat(),
    }
    return reviewed, report
