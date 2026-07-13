from app.evaluation import ConversationEvalTurn, aggregate_conversation_metrics, evaluate_conversation_turn


def test_conversation_turn_metrics_are_auditable():
    turn = ConversationEvalTurn(
        question_focus="公司是否应当支付赔偿金",
        question_focus_hit=True,
        follow_up_questions=["1. 是否有解除通知？", "是否有解除通知？", "是否签订劳动合同？"],
        previously_asked_questions=["是否签订劳动合同？"],
        answered_questions=["是否签订劳动合同？"],
        retrieved_authority_ids={"authority-1"},
        cited_authority_ids={"authority-1", "authority-unknown"},
        context_fact_conflicts=["解除日期与已确认事实不一致"],
    )

    metrics = evaluate_conversation_turn(turn)

    assert metrics["question_focus_hit"] == 1.0
    assert metrics["follow_up_duplicate_rate"] == 0.667
    assert metrics["answered_information_repeat_rate"] == 0.333
    assert metrics["unsupported_authority_rate"] == 0.5
    assert metrics["context_fact_conflict_rate"] == 1.0
    assert metrics["unsupported_authority_ids"] == ["authority-unknown"]


def test_conversation_metric_aggregation_handles_empty_and_multiple_turns():
    assert aggregate_conversation_metrics([])["turn_count"] == 0
    aggregate = aggregate_conversation_metrics(
        [
            ConversationEvalTurn(question_focus="焦点一", question_focus_hit=True),
            ConversationEvalTurn(question_focus="焦点二", question_focus_hit=False),
        ]
    )

    assert aggregate["turn_count"] == 2
    assert aggregate["question_focus_hit"] == 0.5
    assert aggregate["follow_up_duplicate_rate"] == 0.0
