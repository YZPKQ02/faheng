"""Build an auditable, review-pending retrieval gold-set draft from official corpus."""

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from app.retrieval_evaluation import citation_key


TOPICS: dict[str, tuple[str, ...]] = {
    "劳动合同": ("劳动合同", "用工", "解除", "终止", "经济补偿"),
    "工资支付": ("工资", "劳动报酬", "欠薪", "农民工", "支付"),
    "工伤保险": ("工伤", "伤残", "职业病", "劳动能力鉴定"),
    "社会保险": ("社会保险", "保险费", "失业保险", "缴费", "基金"),
    "劳动保护": ("劳动保护", "安全生产", "有毒", "女职工", "童工", "矿山"),
    "就业安置": ("就业", "安置", "职业培训", "残疾人", "退役军人"),
    "监督执法": ("监察", "监督", "检查", "处罚", "投诉", "举报"),
    "特殊用工": ("劳务", "船员", "集体企业", "保安", "勤工俭学"),
}
ARTICLE_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千零〇两\d]+条\s*")
LEGAL_REFERENCE_RE = re.compile(r"《[^》]+》(?:第[一二三四五六七八九十百千零〇两\d]+条)?")
ARTICLE_MENTION_RE = re.compile(r"第[一二三四五六七八九十百千零〇两\d]+条")
SENTENCE_SPLIT_RE = re.compile(r"[。；;]", re.U)
REPLACEMENTS = (
    ("用人单位", "公司"),
    ("劳动者", "员工"),
    ("职工", "员工"),
    ("应当", "需要"),
    ("不得", "不能"),
    ("可以", "能否"),
    ("有关部门", "主管部门"),
)


class EvidenceAnchor(BaseModel):
    citation: str
    source_url: str
    effective_on: str
    excerpt: str


class GoldCandidate(BaseModel):
    id: str
    split: str = Field(pattern=r"^(dev|test)$")
    topic: str
    difficulty: str = Field(pattern=r"^(medium|hard)$")
    query: str
    expected_citations: list[str] = Field(min_length=1)
    as_of: str = "2026-07-16"
    region: str = "中国大陆"
    evidence: list[EvidenceAnchor] = Field(min_length=1)
    source_kind: str = "official_moj_regulation"
    generation_method: str
    review_status: str = "pending"


def _load_chunks(corpus_path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chunks = []
    for document in rows:
        for chunk in document["chunks"]:
            chunks.append(
                {
                    "title": document["title"],
                    "source_url": document["source_url"],
                    "effective_on": document["effective_on"],
                    "locator": chunk["locator"],
                    "content": chunk["content"],
                }
            )
    return chunks


def _topic_score(chunk: dict, keywords: tuple[str, ...]) -> int:
    haystack = f"{chunk['title']} {chunk['content']}"
    return sum((3 if word in chunk["title"] else 1) for word in keywords if word in haystack)


def _query_fragment(content: str) -> str:
    content = ARTICLE_PREFIX_RE.sub("", content)
    content = LEGAL_REFERENCE_RE.sub("相关规定", content)
    candidates = [part.strip() for part in SENTENCE_SPLIT_RE.split(content) if part.strip()]
    fragment = max(candidates[:3], key=len, default=content)
    for source, replacement in REPLACEMENTS:
        fragment = fragment.replace(source, replacement)
    fragment = ARTICLE_MENTION_RE.sub("相关条款", fragment)
    fragment = re.sub(r"\s+", "", fragment)
    return fragment[:72].rstrip("，、：:")


def _single_query(chunk: dict, topic: str) -> str:
    fragment = _query_fragment(chunk["content"])
    if "不能" in fragment:
        return f"在{topic}纠纷中，如果有人主张“{fragment}”，这种做法是否合法、限制是什么？"
    if "需要" in fragment:
        return f"处理{topic}问题时，遇到“{fragment}”的情形，相关主体需要履行什么义务？"
    if "能否" in fragment:
        return f"发生{topic}争议时，针对“{fragment}”的情况，可以采取哪些措施？"
    return f"现实中遇到与“{fragment}”有关的{topic}争议，应当适用什么处理规则？"


def _anchor(chunk: dict) -> EvidenceAnchor:
    return EvidenceAnchor(
        citation=citation_key(chunk["title"], chunk["locator"]),
        source_url=chunk["source_url"],
        effective_on=chunk["effective_on"],
        excerpt=chunk["content"][:220],
    )


def _candidate_id(topic: str, anchors: list[EvidenceAnchor]) -> str:
    material = topic + "|" + "|".join(anchor.citation for anchor in anchors)
    return "pilot-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _ranked_topic_chunks(chunks: list[dict], topic: str) -> list[dict]:
    keywords = TOPICS[topic]
    candidates = [chunk for chunk in chunks if _topic_score(chunk, keywords) > 0]
    return sorted(
        candidates,
        key=lambda chunk: (
            -_topic_score(chunk, keywords),
            hashlib.sha256(
                f"{topic}|{chunk['title']}|{chunk['locator']}".encode("utf-8")
            ).hexdigest(),
        ),
    )


def build_gold_candidates(corpus_path: Path) -> list[GoldCandidate]:
    chunks = _load_chunks(corpus_path)
    drafts: list[dict] = []
    for topic in TOPICS:
        ranked = _ranked_topic_chunks(chunks, topic)
        if len(ranked) < 18:
            raise ValueError(f"主题“{topic}”只有 {len(ranked)} 个候选法条")
        for chunk in ranked[:12]:
            anchor = _anchor(chunk)
            drafts.append(
                {
                    "id": _candidate_id(topic, [anchor]),
                    "topic": topic,
                    "difficulty": "medium",
                    "query": _single_query(chunk, topic),
                    "expected_citations": [anchor.citation],
                    "evidence": [anchor],
                    "generation_method": "rule_based_scenario_paraphrase_v1",
                }
            )
        by_title: dict[str, list[dict]] = defaultdict(list)
        for chunk in ranked[12:]:
            by_title[chunk["title"]].append(chunk)
        title_groups = sorted(by_title.values(), key=lambda group: (-len(group), group[0]["title"]))
        if len(title_groups) < 2:
            raise ValueError(f"主题“{topic}”无法组成 3 个跨法规问题")
        cross_pairs = [
            (
                title_groups[0][index % len(title_groups[0])],
                title_groups[1 + index % (len(title_groups) - 1)][
                    index % len(title_groups[1 + index % (len(title_groups) - 1)])
                ],
            )
            for index in range(3)
        ]
        for left, right in cross_pairs:
            anchors = [_anchor(left), _anchor(right)]
            query = (
                f"在同一项{topic}争议中，如果同时涉及“{_query_fragment(left['content'])}”"
                f"以及“{_query_fragment(right['content'])}”，两方面应分别适用什么规则？"
            )
            drafts.append(
                {
                    "id": _candidate_id(topic, anchors),
                    "topic": topic,
                    "difficulty": "hard",
                    "query": query,
                    "expected_citations": [anchor.citation for anchor in anchors],
                    "evidence": anchors,
                    "generation_method": "rule_based_cross_regulation_v1",
                }
            )

    ordered = sorted(
        drafts,
        key=lambda item: hashlib.sha256(item["id"].encode("utf-8")).hexdigest(),
    )
    candidates = []
    for index, draft in enumerate(ordered):
        candidates.append(GoldCandidate(split="dev" if index < 80 else "test", **draft))
    return candidates


def validate_gold_candidates(candidates: list[GoldCandidate], corpus_path: Path) -> dict:
    chunks = _load_chunks(corpus_path)
    valid_citations = {
        citation_key(chunk["title"], chunk["locator"]): chunk for chunk in chunks
    }
    errors: list[dict] = []
    seen_queries: set[str] = set()
    seen_ids: set[str] = set()
    for candidate in candidates:
        case_errors = []
        if candidate.id in seen_ids:
            case_errors.append("duplicate_id")
        if candidate.query in seen_queries:
            case_errors.append("duplicate_query")
        seen_ids.add(candidate.id)
        seen_queries.add(candidate.query)
        if ARTICLE_MENTION_RE.search(candidate.query):
            case_errors.append("query_leaks_article_number")
        for anchor in candidate.evidence:
            source = valid_citations.get(anchor.citation)
            if source is None:
                case_errors.append(f"missing_citation:{anchor.citation}")
                continue
            if source["title"] in candidate.query:
                case_errors.append(f"query_leaks_title:{source['title']}")
            if source["source_url"] != anchor.source_url:
                case_errors.append(f"source_mismatch:{anchor.citation}")
        if set(candidate.expected_citations) != {
            anchor.citation for anchor in candidate.evidence
        }:
            case_errors.append("expected_evidence_mismatch")
        if case_errors:
            errors.append({"id": candidate.id, "errors": case_errors})
    topic_counts: dict[str, int] = defaultdict(int)
    split_counts: dict[str, int] = defaultdict(int)
    difficulty_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        topic_counts[candidate.topic] += 1
        split_counts[candidate.split] += 1
        difficulty_counts[candidate.difficulty] += 1
    return {
        "candidate_count": len(candidates),
        "passed_count": len(candidates) - len(errors),
        "failed_count": len(errors),
        "citation_count": sum(len(item.expected_citations) for item in candidates),
        "citation_validity": round(
            sum(
                citation in valid_citations
                for item in candidates
                for citation in item.expected_citations
            )
            / max(1, sum(len(item.expected_citations) for item in candidates)),
            4,
        ),
        "topic_counts": dict(topic_counts),
        "split_counts": dict(split_counts),
        "difficulty_counts": dict(difficulty_counts),
        "review_status": "pending",
        "errors": errors,
    }
