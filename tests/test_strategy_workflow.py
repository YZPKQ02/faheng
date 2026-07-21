from threading import Barrier, Lock

import pytest

from app.agent_contracts import JudicialAssessment, PartyArgument
from app.database import SessionLocal
from app.model_gateway import ModelGatewayError
from app.models import AgentTask, CaseFile
from app.observability import ModelCallTelemetry
from app.strategy_workflow import build_strategy_workflow


def _initial_state(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "protocol_version": "legal-debate-v2",
        "round_number": 1,
        "facts": [
            {
                "id": "fact-1",
                "content": "劳动者陈述公司口头解除劳动合同",
                "status": "user_stated",
                "source": "user",
            }
        ],
        "evidence": [],
        "authorities": [
            {
                "id": "law-1",
                "title": "中华人民共和国劳动合同法",
                "article": "第四十八条",
                "content": "用人单位违法解除或者终止劳动合同的法律责任。",
            }
        ],
    }


class ParallelGateway:
    def __init__(
        self,
        barrier: Barrier,
        events: list[tuple[str, str]],
        event_lock: Lock,
        instances: dict[str, set[int]],
        *,
        fail_roles: set[str] | None = None,
    ):
        self.enabled = True
        self.last_telemetry = None
        self._barrier = barrier
        self._events = events
        self._event_lock = event_lock
        self._instances = instances
        self._fail_roles = fail_roles or set()

    def structured(self, *, system, user, schema, authorization=None):
        if schema is PartyArgument:
            role = "worker" if "劳动者代理人" in system else "employer"
            if role == "employer":
                assert "劳动者观点" not in user
                assert "worker position" not in user
            with self._event_lock:
                self._events.append((role, "started"))
                self._instances.setdefault(role, set()).add(id(self))
            self._barrier.wait(timeout=3)
            with self._event_lock:
                self._events.append((role, "finished"))
            self.last_telemetry = ModelCallTelemetry("success", 10, 1, 0)
            if role in self._fail_roles:
                self.last_telemetry = ModelCallTelemetry(
                    "error", 10, 1, 0, error_type="forced_failure"
                )
                raise ModelGatewayError("forced failure")
            return PartyArgument(
                position=f"{role} position",
                arguments=[f"{role} argument"],
                evidence_needed=[],
                authority_ids=["law-1"],
            )
        if schema is JudicialAssessment:
            with self._event_lock:
                assert {role for role, event in self._events if event == "finished"} == {
                    "worker",
                    "employer",
                }
                self._events.append(("judge", "started"))
            if "worker" in self._fail_roles:
                assert "现有陈述显示劳动者" in user
            else:
                assert "worker position" in user
            if "employer" in self._fail_roles:
                assert "用人单位可能从劳动关系" in user
            else:
                assert "employer position" in user
            self.last_telemetry = ModelCallTelemetry("success", 8, 1, 0)
            return JudicialAssessment(
                issues=["解除是否合法"],
                assessment="需要结合双方主张和证据判断。",
                likely_outcome="信息仍需补充。",
                confidence=0.6,
                uncertainties=["解除事实尚待核实"],
                authority_ids=["law-1"],
            )
        raise AssertionError(f"unexpected schema: {schema}")


def _run_parallel_strategy(client, *, fail_roles: set[str] | None = None):
    barrier = Barrier(2)
    events: list[tuple[str, str]] = []
    event_lock = Lock()
    instances: dict[str, set[int]] = {}
    created_gateways: list[ParallelGateway] = []

    def gateway_factory():
        gateway = ParallelGateway(
            barrier,
            events,
            event_lock,
            instances,
            fail_roles=fail_roles,
        )
        created_gateways.append(gateway)
        return gateway

    with SessionLocal() as db:
        case = CaseFile(title="并行策略测试")
        db.add(case)
        db.commit()
        db.refresh(case)
        result = build_strategy_workflow(db, gateway_factory=gateway_factory).invoke(
            _initial_state(case.id)
        )
        tasks = list(db.query(AgentTask).filter(AgentTask.case_id == case.id).all())
    return result, events, instances, tasks, created_gateways


def test_party_agents_are_independent_parallel_branches_before_judging(client):
    result, events, instances, tasks, created_gateways = _run_parallel_strategy(client)

    assert result["protocol_version"] == "legal-debate-v2"
    assert result["worker_argument"]["position"] == "worker position"
    assert result["employer_argument"]["position"] == "employer position"
    assert result["worker_execution"]["status"] == "completed"
    assert result["employer_execution"]["status"] == "completed"
    assert result["safety_review"]["decision"] == "pass"
    assert result["safety_review"]["approved"] is True
    assert events.index(("judge", "started")) > events.index(("worker", "finished"))
    assert events.index(("judge", "started")) > events.index(("employer", "finished"))
    assert instances["worker"].isdisjoint(instances["employer"])
    assert len(created_gateways) == 3
    assert {task.agent for task in tasks} == {
        "worker_advocate",
        "employer_advocate",
        "neutral_adjudicator",
        "safety_reviewer",
    }
    assert all(task.output["strategy_protocol"] == "legal-debate-v2" for task in tasks)


@pytest.mark.parametrize("failed_role", ["worker", "employer"])
def test_one_party_failure_uses_local_fallback_without_blocking_the_other(
    client, failed_role
):
    result, _, _, tasks, _ = _run_parallel_strategy(
        client, fail_roles={failed_role}
    )

    other_role = "employer" if failed_role == "worker" else "worker"
    assert result[f"{failed_role}_execution"]["status"] == "fallback"
    assert result[f"{failed_role}_execution"]["error_type"] == "forced_failure"
    assert result[f"{other_role}_execution"]["status"] == "completed"
    assert result["assessment"]
    assert result["safety_review"]["decision"] == "escalate"
    assert result["safety_review"]["requires_human_lawyer"] is True
    assert "备用回答" in " ".join(result["safety_review"]["problems"])
    assert len(tasks) == 4
    party_tasks = {
        task.agent: task.output["execution"]
        for task in tasks
        if task.agent in {"worker_advocate", "employer_advocate"}
    }
    assert party_tasks[f"{failed_role}_advocate"]["status"] == "fallback"
    assert party_tasks[f"{other_role}_advocate"]["status"] == "completed"


def test_both_party_failures_still_join_and_reach_safety_review(client):
    result, events, _, tasks, created_gateways = _run_parallel_strategy(
        client, fail_roles={"worker", "employer"}
    )

    assert result["worker_execution"]["status"] == "fallback"
    assert result["employer_execution"]["status"] == "fallback"
    assert events[-1] == ("judge", "started")
    assert result["assessment"]
    assert result["safety_review"]
    assert result["safety_review"]["decision"] == "escalate"
    assert len(created_gateways) == 3
    assert len(tasks) == 4
