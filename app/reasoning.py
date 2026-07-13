from __future__ import annotations

from datetime import date

from app.models import CaseFile, FactStatus, LegalAuthority


ISSUE_RULES = {
    "违法解除": {
        "triggers": ("解除", "辞退", "开除"),
        "elements": (
            ("劳动关系成立", ("劳动合同", "工资", "社保", "工作")),
            ("用人单位作出解除", ("解除", "辞退", "开除", "通知")),
            ("解除理由缺乏合法依据", ("违法", "无理由", "突然")),
        ),
    },
    "未签劳动合同": {
        "triggers": ("未签", "没签", "书面合同"),
        "elements": (
            ("劳动关系成立", ("工资", "入职", "工作")),
            ("超过一个月未订立书面合同", ("未签", "没签", "一个月")),
        ),
    },
    "加班费": {
        "triggers": ("加班", "996", "调休"),
        "elements": (
            ("存在加班事实", ("加班", "考勤", "打卡")),
            ("加班由单位安排或认可", ("安排", "审批", "通知")),
            ("未依法支付报酬或补休", ("未支付", "加班费", "调休")),
        ),
    },
    "拖欠工资": {
        "triggers": ("欠薪", "拖欠工资", "工资没发"),
        "elements": (
            ("劳动关系成立", ("劳动合同", "入职", "工作")),
            ("工资标准和支付周期明确", ("工资", "流水", "工资条")),
            ("单位未按期足额支付", ("欠薪", "拖欠", "没发")),
        ),
    },
    "经济补偿": {
        "triggers": ("经济补偿", "补偿金"),
        "elements": (
            ("劳动关系及工作年限明确", ("入职", "年限", "劳动合同")),
            ("解除或终止原因符合补偿情形", ("解除", "终止", "离职")),
            ("离职前十二个月平均工资明确", ("平均工资", "工资流水", "工资条")),
        ),
    },
    "仲裁时效": {
        "triggers": ("仲裁时效", "超过一年", "时效"),
        "elements": (
            ("权利受侵害日期明确", ("日期", "解除", "欠薪")),
            ("申请仲裁日期明确", ("申请仲裁", "立案")),
            ("不存在时效中止或中断争议", ("催讨", "承诺支付", "不可抗力")),
        ),
    },
}


def detect_timeline_conflicts(case: CaseFile) -> list[str]:
    dated = sorted((fact.occurred_on, fact.content) for fact in case.facts if fact.occurred_on)
    conflicts: list[str] = []
    for index, (day, content) in enumerate(dated):
        if "入职" in content:
            earlier_exit = [c for d, c in dated[:index] if any(k in c for k in ("离职", "解除", "辞退"))]
            if earlier_exit:
                conflicts.append(f"时间线冲突：入职日期 {day} 晚于已记录的离职或解除事件")
    return conflicts


def build_reasoning_trace(case: CaseFile, authorities: list[LegalAuthority], as_of: date) -> list[dict]:
    text = " ".join(f.content for f in case.facts)
    trace: list[dict] = []
    for issue, rule in ISSUE_RULES.items():
        if not any(word in text for word in rule["triggers"]):
            continue
        elements = []
        for name, keywords in rule["elements"]:
            fact_ids = [f.id for f in case.facts if any(k in f.content for k in keywords)]
            evidence_ids = [e.id for e in case.evidence if any(k in f"{e.name}{e.purpose}" for k in keywords)]
            if evidence_ids:
                status = "supported"
            elif fact_ids:
                status = "claimed"
            else:
                status = "missing"
            elements.append({"element": name, "status": status, "fact_ids": fact_ids, "evidence_ids": evidence_ids})
        authority_ids = [a.id for a in authorities if any(k in f"{a.title}{a.content}{a.keywords}" for k in rule["triggers"])]
        trace.append({"issue": issue, "as_of": as_of.isoformat(), "elements": elements, "authority_ids": authority_ids})
    return trace


def quality_metrics(case: CaseFile, trace: list[dict]) -> dict:
    elements = [element for item in trace for element in item["elements"]]
    cited = [item for item in trace if item["authority_ids"]]
    supported = [element for element in elements if element["status"] == "supported"]
    non_inferred = [f for f in case.facts if f.status != FactStatus.INFERRED]
    return {
        "issue_coverage": round(len(trace) / max(1, len(ISSUE_RULES)), 3),
        "element_evidence_coverage": round(len(supported) / max(1, len(elements)), 3),
        "citation_coverage": round(len(cited) / max(1, len(trace)), 3),
        "fact_grounding": round(len(non_inferred) / max(1, len(case.facts)), 3),
        "timeline_conflicts": detect_timeline_conflicts(case),
        "missing_elements": [
            {"issue": item["issue"], "element": element["element"]}
            for item in trace
            for element in item["elements"]
            if element["status"] == "missing"
        ],
    }


def validate_citation_support(trace: list[dict], authority_ids: list[str]) -> tuple[list[str], list[str]]:
    trace_ids = {authority_id for item in trace for authority_id in item["authority_ids"]}
    supported = [authority_id for authority_id in authority_ids if authority_id in trace_ids]
    rejected = [authority_id for authority_id in authority_ids if authority_id not in trace_ids]
    return supported, rejected


def decision_gate(metrics: dict, authority_ids: list[str]) -> list[str]:
    reasons: list[str] = []
    if not authority_ids:
        reasons.append("没有通过支持性校验的法律依据")
    if metrics["citation_coverage"] < 1:
        reasons.append("部分争议焦点缺少可核验法律依据")
    if metrics["element_evidence_coverage"] < 0.5:
        reasons.append("关键构成要件的证据覆盖不足50%")
    if metrics["timeline_conflicts"]:
        reasons.append("案件时间线存在冲突")
    return reasons


def calibrated_confidence(metrics: dict) -> float:
    """Conservative, auditable calibration; replace weights after labelled-case fitting."""
    score = 0.2 + 0.25 * metrics["element_evidence_coverage"] + 0.25 * metrics["citation_coverage"] + 0.15 * metrics["fact_grounding"]
    if metrics["timeline_conflicts"]:
        score -= 0.2
    return round(max(0.1, min(0.85, score)), 2)
