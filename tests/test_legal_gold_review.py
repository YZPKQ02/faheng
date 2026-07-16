from app.legal_gold_review import _review_cross_query


def test_cross_question_is_reframed_as_two_independent_issues():
    query = "在同一项争议中，如果同时涉及“问题甲”以及“问题乙”，如何处理？"
    reviewed = _review_cross_query(query)
    assert "两个独立问题" in reviewed
    assert "问题甲" in reviewed
    assert "问题乙" in reviewed
    assert "同一项争议" not in reviewed
