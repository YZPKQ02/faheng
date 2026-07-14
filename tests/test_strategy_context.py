from app.authorities import search_authorities
from app.database import SessionLocal
from app.models import CaseFile, Fact
from app.strategy_workflow import MAX_STRATEGY_FACTS, _select_strategy_facts


def test_strategy_context_is_bounded_and_prioritizes_reviewed_facts():
    case = CaseFile(id="case-context")
    case.facts = [
        Fact(id=f"user-{index:02d}", content=f"用户陈述 {index}", status="user_stated")
        for index in range(MAX_STRATEGY_FACTS + 5)
    ]
    case.facts.append(
        Fact(id="confirmed", content="已确认的解除日期", status="confirmed")
    )

    selected = _select_strategy_facts(case)

    assert len(selected) == MAX_STRATEGY_FACTS
    assert selected[0]["id"] == "confirmed"
    assert all(len(item["content"]) <= 601 for item in selected)


def test_authority_retrieval_can_return_an_explicit_empty_result(client):
    with SessionLocal() as db:
        assert search_authorities(db, "今天午饭吃什么") == []
