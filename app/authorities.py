from datetime import date
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LegalAuthority


SEED_AUTHORITIES = [
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第十条",
        "content": "建立劳动关系，应当订立书面劳动合同。已建立劳动关系，未同时订立书面劳动合同的，应当自用工之日起一个月内订立。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["未签合同", "书面劳动合同", "劳动关系", "双倍工资"],
    },
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第四十七条",
        "content": "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["经济补偿", "工资", "工作年限", "解除"],
    },
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第八十七条",
        "content": "用人单位违法解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["违法解除", "赔偿金", "二倍", "辞退"],
    },
    {
        "title": "中华人民共和国劳动争议调解仲裁法",
        "article": "第二十七条",
        "content": "劳动争议申请仲裁的时效期间为一年。仲裁时效期间从当事人知道或者应当知道其权利被侵害之日起计算。",
        "level": "法律",
        "effective_on": date(2008, 5, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["仲裁时效", "一年", "劳动争议", "拖欠工资"],
    },
    {
        "title": "中华人民共和国劳动法",
        "article": "第四十四条",
        "content": "安排劳动者延长工作时间、休息日工作且不能补休、法定休假日工作的，应分别依法支付不低于工资的百分之一百五十、百分之二百、百分之三百的报酬。",
        "level": "法律",
        "effective_on": date(1995, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["加班费", "延长工作时间", "休息日", "法定节假日"],
    },
]


def seed_authorities(db: Session) -> None:
    if db.scalar(select(LegalAuthority.id).limit(1)):
        return
    db.add_all([LegalAuthority(**item) for item in SEED_AUTHORITIES])
    db.commit()


LEVEL_WEIGHT = {"法律": 5, "行政法规": 4, "司法解释": 4, "部门规章": 3, "地方规定": 2}


def tokenize(text: str) -> set[str]:
    """Generate deterministic lexical features for Chinese legal hybrid retrieval."""
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", text.lower())
    return {normalized[i : i + size] for size in (2, 3, 4) for i in range(len(normalized) - size + 1)}


def search_authorities(db: Session, query: str, as_of: date | None = None, limit: int = 10, region: str = "中国大陆") -> list[LegalAuthority]:
    as_of = as_of or date.today()
    candidates = db.scalars(select(LegalAuthority)).all()
    query_tokens = tokenize(query)

    def score(authority: LegalAuthority) -> float:
        haystack = authority.title + authority.article + authority.content + " ".join(authority.keywords)
        keyword_hits = sum(8 for keyword in authority.keywords if keyword in query)
        overlap = len(query_tokens & tokenize(haystack)) / max(1, len(query_tokens))
        region_bonus = 2 if authority.region in ("全国", region, "中国大陆") else -10
        return keyword_hits + overlap * 10 + LEVEL_WEIGHT.get(authority.level, 1) + region_bonus

    valid = [a for a in candidates if a.effective_on <= as_of and (a.expired_on is None or a.expired_on > as_of)]
    ranked = sorted(valid, key=score, reverse=True)
    positive = [a for a in ranked if score(a) > 0]
    return (positive or ranked[:2])[:limit]
