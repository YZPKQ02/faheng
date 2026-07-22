from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from unittest.mock import patch

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent_contracts import JudicialAssessment, PartyArgument
from app.authorities import SEED_AUTHORITIES, seed_authorities
from app.config import Settings
from app.database import Base
from app.evaluation import EvalCase, evaluate_trace, load_gold_cases
from app.legal_governance import transition_legal_version
from app.model_gateway import ModelGateway, ModelGatewayError, ModelRequestBudget
from app.models import (
    AgentTask,
    AuditEvent,
    CaseFile,
    EvidenceItem,
    Fact,
    LegalAuthority,
    LegalDocumentVersion,
)
from app.observability import ModelCallTelemetry
from app.privacy import ModelCallAuthorization
from app.reasoning import build_reasoning_trace
from app.retrieval_evaluation import evaluate_retrieval, load_retrieval_cases
from app.strategy_workflow import (
    MAX_STRATEGY_AUTHORITIES,
    MAX_STRATEGY_EVIDENCE,
    _select_strategy_facts,
    build_strategy_workflow,
)


HARNESS_SCHEMA_VERSION = "harness-report-v1"
BASELINE_SCHEMA_VERSION = "harness-baseline-v1"
WORKFLOW_VERSION = "legal-debate-v2"
FIXED_AS_OF = date(2026, 7, 21)
EXPECTED_AGENTS = {
    "worker_advocate",
    "employer_advocate",
    "neutral_adjudicator",
    "safety_reviewer",
}
DEFAULT_BASELINE = Path("data/harness/baselines/offline.json")
DEFAULT_OFFLINE_PACK = Path("data/harness/cases/offline.json")
DEFAULT_LIVE_PACK = Path("data/harness/cases/live.json")


class HarnessFact(BaseModel):
    id: str
    content: str
    status: str = "confirmed"
    source: str = "harness"


class HarnessEvidence(BaseModel):
    id: str
    name: str
    purpose: str
    authenticity: str = "unverified"


class HarnessCase(BaseModel):
    id: str
    title: str
    region: str = "中国大陆"
    data_classification: Literal["synthetic", "public"]
    external_model_allowed: bool = False
    facts: list[HarnessFact]
    evidence: list[HarnessEvidence] = Field(default_factory=list)


class HarnessCasePack(BaseModel):
    schema_version: Literal["harness-case-pack-v1"]
    version: str
    as_of: date
    cases: list[HarnessCase] = Field(min_length=1)


class HarnessAuthorizationManifest(BaseModel):
    schema_version: Literal["harness-authorization-v1"]
    status: Literal["active", "template"]
    consent_id: str
    consent_version: int = Field(ge=1)
    tenant_id: str
    provider: Literal["deepseek"]
    purpose: Literal["harness_evaluation"]
    allowed_case_pack_version: str
    allowed_data_classifications: set[Literal["synthetic", "public"]]
    expires_on: date


class JudgeDimension(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str = Field(max_length=400)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class HarnessJudgeResult(BaseModel):
    factual_fidelity: JudgeDimension
    legal_grounding: JudgeDimension
    worker_argument_quality: JudgeDimension
    employer_argument_quality: JudgeDimension
    neutrality: JudgeDimension
    safety_expression: JudgeDimension
    warnings: list[str] = Field(default_factory=list, max_length=8)


class HarnessJudge(Protocol):
    source: dict

    def evaluate(
        self,
        case: HarnessCase,
        state: dict,
        authorization: ModelCallAuthorization,
    ) -> HarnessJudgeResult: ...


class ModelHarnessJudge:
    def __init__(self, gateway_factory: Callable[[], ModelGateway], settings: Settings):
        self.gateway_factory = gateway_factory
        self.source = {
            "provider": settings.model_provider,
            "model": settings.deepseek_model,
            "same_source_as_subject": True,
            "policy": "advisory",
        }

    def evaluate(
        self,
        case: HarnessCase,
        state: dict,
        authorization: ModelCallAuthorization,
    ) -> HarnessJudgeResult:
        gateway = self.gateway_factory()
        payload = {
            "case_id": case.id,
            "facts": [item.model_dump() for item in case.facts],
            "evidence": [item.model_dump() for item in case.evidence],
            "allowed_authority_ids": [item["id"] for item in state["authorities"]],
            "worker_argument": state["worker_argument"],
            "employer_argument": state["employer_argument"],
            "assessment": state["assessment"],
            "safety_review": state["safety_review"],
        }
        return gateway.structured(
            system=(
                "你是独立的法律工作流质量评估者。只评价给定轨迹，不补充外部事实或法律。"
                "按六个维度给出1到5分，理由必须指向输入中的事实、证据、authority id或输出字段。"
                "评分仅用于告警，不代表律师意见或案件胜率。"
            ),
            user=json.dumps(payload, ensure_ascii=False),
            schema=HarnessJudgeResult,
            authorization=authorization,
        )


class _DeterministicGateway:
    def __init__(
        self,
        trace: list[dict],
        *,
        failures: set[str] | None = None,
        retry_roles: set[str] | None = None,
    ):
        self.trace = trace
        self.failures = failures or set()
        self.retry_roles = retry_roles or set()
        self.last_telemetry: ModelCallTelemetry | None = None

    def structured(self, *, system: str, user: str, schema, authorization=None):
        del authorization
        if "劳动者代理人" in system:
            role = "worker"
        elif "用人单位代理人" in system:
            role = "employer"
        else:
            role = "adjudicator"
        self.trace.append({"role": role, "user": user})
        retries = 1 if role in self.retry_roles else 0
        if role in self.failures:
            self.last_telemetry = ModelCallTelemetry(
                "error", 1.0, 1, 0, error_type="injected_failure"
            )
            raise ModelGatewayError(f"injected {role} failure")
        self.last_telemetry = ModelCallTelemetry(
            "success", 1.0, retries + 1, retries, status_code=200
        )
        allowed = list(json.loads(user.split("\n双方观点：", 1)[0]).get("authorities", []))
        allowed_ids = [item["id"] for item in allowed]
        if schema is PartyArgument:
            return PartyArgument(
                position=(
                    "HARNESS_WORKER_SENTINEL 劳动者主张"
                    if role == "worker"
                    else "单位基于相同材料独立抗辩"
                ),
                arguments=["仅依据已确认事实逐项论证"],
                evidence_needed=["核验原始证据"],
                authority_ids=allowed_ids,
            )
        if schema is JudicialAssessment:
            return JudicialAssessment(
                issues=["劳动争议请求是否成立"],
                assessment="双方观点均需结合证据和候选依据审慎评估。",
                likely_outcome="需人工结合完整证据判断。",
                confidence=0.6,
                uncertainties=["证据真实性待核验"],
                authority_ids=allowed_ids,
            )
        raise AssertionError(f"unsupported deterministic schema: {schema}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_case_pack(path: Path) -> HarnessCasePack:
    return HarnessCasePack.model_validate(_read_json(path))


@contextmanager
def isolated_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with maker() as db:
            yield db
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _publish_fixture_legal_versions(db: Session) -> None:
    versions = list(db.scalars(select(LegalDocumentVersion)).all())
    for version in versions:
        transition_legal_version(
            db,
            version,
            action="approve",
            actor_id="harness-legal-reviewer",
            roles={"admin"},
            notes="isolated harness corpus",
        )
        transition_legal_version(
            db,
            version,
            action="publish",
            actor_id="harness-legal-publisher",
            roles={"admin"},
            notes="isolated harness corpus",
        )
    db.commit()


@contextmanager
def _deterministic_embedding_settings():
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        model_provider="deterministic",
        embedding_provider="deterministic",
        embedding_model="legal-hash-v1",
        embedding_dimensions=128,
        embedding_consent_required=False,
    )
    with (
        patch("app.authorities.get_settings", return_value=settings),
        patch("app.embeddings.get_settings", return_value=settings),
    ):
        yield settings


def _seed_case(db: Session, item: HarnessCase) -> CaseFile:
    case = CaseFile(
        id=item.id,
        title=item.title,
        tenant_id="harness",
        owner_id="harness",
        region=item.region,
    )
    case.facts = [Fact(**fact.model_dump(), case_id=item.id) for fact in item.facts]
    case.evidence = [
        EvidenceItem(
            **evidence.model_dump(),
            case_id=item.id,
            evidence_type="harness",
        )
        for evidence in item.evidence
    ]
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def _initial_state(case: CaseFile, authorities: list[LegalAuthority]) -> dict:
    return {
        "case_id": case.id,
        "protocol_version": WORKFLOW_VERSION,
        "round_number": 1,
        "facts": _select_strategy_facts(case),
        "evidence": [
            {
                "id": item.id,
                "name": item.name[:200],
                "purpose": item.purpose[:500],
                "authenticity": item.authenticity,
            }
            for item in case.evidence[:MAX_STRATEGY_EVIDENCE]
        ],
        "authorities": [
            {
                "id": item.id,
                "title": item.title[:300],
                "article": item.article,
                "content": item.content[:1200],
            }
            for item in authorities[:MAX_STRATEGY_AUTHORITIES]
        ],
    }


def _assert_strategy(
    db: Session,
    case: HarnessCase,
    state: dict,
    trace: list[dict] | None,
    *,
    allow_fallback: bool,
) -> list[str]:
    failures: list[str] = []
    if state.get("protocol_version") != WORKFLOW_VERSION:
        failures.append("workflow_protocol_mismatch")
    tasks = list(db.scalars(select(AgentTask).where(AgentTask.case_id == case.id)).all())
    agents = {item.agent for item in tasks}
    if len(tasks) != 4 or agents != EXPECTED_AGENTS:
        failures.append("agent_task_protocol_invalid")
    if any(item.output.get("strategy_protocol") != WORKFLOW_VERSION for item in tasks):
        failures.append("agent_task_workflow_version_invalid")
    allowed_ids = {item["id"] for item in state.get("authorities", [])}
    for key in ("worker_argument", "employer_argument", "assessment"):
        if not set(state.get(key, {}).get("authority_ids", [])) <= allowed_ids:
            failures.append(f"{key}_authority_out_of_bounds")
    if not state.get("assessment", {}).get("authority_ids"):
        failures.append("assessment_missing_authority")
    safety_decision = state.get("safety_review", {}).get("decision")
    if (not allow_fallback and safety_decision != "pass") or (
        allow_fallback and safety_decision == "block"
    ):
        failures.append("safety_review_rejected")
    source_fact_ids = {item.id for item in case.facts}
    if not {item["id"] for item in state.get("facts", [])} <= source_fact_ids:
        failures.append("fact_boundary_violation")
    if not allow_fallback and any(
        state.get(key, {}).get("fallback_used")
        for key in ("worker_execution", "employer_execution")
    ):
        failures.append("party_model_fallback")
    if trace:
        employer_prompts = [item["user"] for item in trace if item["role"] == "employer"]
        if not employer_prompts or any("HARNESS_WORKER_SENTINEL" in item for item in employer_prompts):
            failures.append("employer_prompt_not_independent")
    return failures


def _run_reasoning_metrics(dataset: Path) -> dict:
    gold_cases = load_gold_cases(dataset)
    authorities = [
        LegalAuthority(id=f"authority-{index}", **item)
        for index, item in enumerate(SEED_AUTHORITIES)
    ]
    authority_articles = {item.id: item.article for item in authorities}
    rows = []
    for gold in gold_cases:
        case = CaseFile(id=gold.id, title=gold.id)
        case.facts = [
            Fact(id=f"{gold.id}-fact-{index}", **item.model_dump())
            for index, item in enumerate(gold.facts)
        ]
        case.evidence = [
            EvidenceItem(
                id=f"{gold.id}-evidence-{index}",
                name=item.name,
                purpose=item.purpose,
                evidence_type="benchmark",
            )
            for index, item in enumerate(gold.evidence)
        ]
        trace = build_reasoning_trace(case, authorities, FIXED_AS_OF)
        rows.append(
            evaluate_trace(
                trace,
                authority_articles,
                EvalCase(gold.expected_issues, gold.expected_authority_articles),
            )
        )
    names = ("issue_recall", "authority_recall", "grounded_element_rate", "citation_validity")
    return {
        "case_count": len(rows),
        "aggregate": {
            name: round(sum(row[name] for row in rows) / max(1, len(rows)), 3)
            for name in names
        },
    }


def _compatibility(pack_path: Path, pack: HarnessCasePack) -> dict:
    paths = {
        "case_pack": pack_path,
        "reasoning_demo": Path("data/evaluation/gold_cases.json"),
        "reasoning_official": Path("data/evaluation/official_model_labeled_cases.json"),
        "retrieval": Path("data/evaluation/legal_retrieval.json"),
    }
    inputs = {name: _sha256(path) for name, path in paths.items()}
    payload = {
        "case_pack_version": pack.version,
        "workflow_version": WORKFLOW_VERSION,
        "embedding": "deterministic:legal-hash-v1:128",
        "inputs": inputs,
    }
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return payload


def _metric_rules(metrics: dict) -> dict[str, tuple[float, Literal["min", "max"]]]:
    rules: dict[str, tuple[float, Literal["min", "max"]]] = {}
    for dataset in ("reasoning_demo", "reasoning_official"):
        for name, value in metrics[dataset]["aggregate"].items():
            rules[f"{dataset}.aggregate.{name}"] = (float(value), "min")
    for name, value in metrics["retrieval"]["aggregate"].items():
        direction: Literal["min", "max"] = (
            "max" if name in {"version_violations", "region_violations", "empty_result_rate"} else "min"
        )
        rules[f"retrieval.aggregate.{name}"] = (float(value), direction)
    rules["strategy.pass_rate"] = (float(metrics["strategy"]["pass_rate"]), "min")
    rules["fault_injection.pass_rate"] = (
        float(metrics["fault_injection"]["pass_rate"]),
        "min",
    )
    return rules


def _get_metric(metrics: dict, path: str) -> float:
    value = metrics
    for item in path.split("."):
        value = value[item]
    return float(value)


def compare_baseline(metrics: dict, compatibility: dict, baseline_path: Path) -> dict:
    if not baseline_path.exists():
        return {"status": "missing", "path": str(baseline_path), "deltas": []}
    baseline = _read_json(baseline_path)
    if baseline.get("schema_version") != BASELINE_SCHEMA_VERSION:
        return {"status": "incompatible", "reason": "baseline_schema", "deltas": []}
    if baseline.get("compatibility_fingerprint") != compatibility["fingerprint"]:
        return {"status": "incompatible", "reason": "input_fingerprint", "deltas": []}
    deltas = []
    for path, rule in baseline["metric_rules"].items():
        expected = float(rule["value"])
        actual = _get_metric(metrics, path)
        direction = rule["direction"]
        passed = actual >= expected if direction == "min" else actual <= expected
        deltas.append(
            {
                "metric": path,
                "baseline": expected,
                "actual": actual,
                "direction": direction,
                "passed": passed,
            }
        )
    return {
        "status": "passed" if all(item["passed"] for item in deltas) else "regressed",
        "path": str(baseline_path),
        "deltas": deltas,
    }


def _environment(profile: str, settings: Settings | None = None) -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "profile": profile,
        "provider": settings.model_provider if settings else "deterministic",
        "model": settings.deepseek_model if settings else None,
    }


def run_offline(
    *,
    pack_path: Path = DEFAULT_OFFLINE_PACK,
    baseline_path: Path = DEFAULT_BASELINE,
) -> dict:
    started = time.perf_counter()
    pack = load_case_pack(pack_path)
    compatibility = _compatibility(pack_path, pack)
    gate_failures: list[str] = []
    case_rows: list[dict] = []
    fault_rows: list[dict] = []
    with _deterministic_embedding_settings():
        with isolated_session() as db:
            seed_authorities(db)
            _publish_fixture_legal_versions(db)
            authorities = list(db.scalars(select(LegalAuthority)).all())
            for item in pack.cases:
                case = _seed_case(db, item)
                trace: list[dict] = []
                def factory(trace=trace):
                    return _DeterministicGateway(trace)

                state = build_strategy_workflow(db, gateway_factory=factory).invoke(
                    _initial_state(case, authorities)
                )
                failures = _assert_strategy(db, item, state, trace, allow_fallback=False)
                gate_failures.extend(f"{item.id}:{failure}" for failure in failures)
                case_rows.append({"id": item.id, "passed": not failures, "failures": failures})

            scenarios = (
                ("429_then_success", set(), {"worker"}),
                ("timeout_worker", {"worker"}, set()),
                ("invalid_json_employer", {"employer"}, set()),
                ("one_party_failure", {"worker"}, set()),
                ("both_party_failure", {"worker", "employer"}, set()),
            )
            for index, (name, failures_to_inject, retry_roles) in enumerate(scenarios):
                original = pack.cases[index % len(pack.cases)]
                source = original.model_copy(
                    update={
                        "id": f"fault-{index}-{name}",
                        "facts": [
                            fact.model_copy(update={"id": f"fault-{index}-{fact.id}"})
                            for fact in original.facts
                        ],
                        "evidence": [
                            evidence.model_copy(update={"id": f"fault-{index}-{evidence.id}"})
                            for evidence in original.evidence
                        ],
                    }
                )
                case = _seed_case(db, source)
                trace = []
                def factory(
                    trace=trace,
                    failures_to_inject=failures_to_inject,
                    retry_roles=retry_roles,
                ):
                    return _DeterministicGateway(
                        trace,
                        failures=failures_to_inject,
                        retry_roles=retry_roles,
                    )

                state = build_strategy_workflow(db, gateway_factory=factory).invoke(
                    _initial_state(case, authorities)
                )
                failures = _assert_strategy(db, source, state, trace, allow_fallback=True)
                expected_fallbacks = failures_to_inject & {"worker", "employer"}
                actual_fallbacks = {
                    role
                    for role, key in (
                        ("worker", "worker_execution"),
                        ("employer", "employer_execution"),
                    )
                    if state[key]["fallback_used"]
                }
                if actual_fallbacks != expected_fallbacks:
                    failures.append("fallback_behavior_mismatch")
                fault_rows.append({"id": name, "passed": not failures, "failures": failures})
                gate_failures.extend(f"fault:{name}:{failure}" for failure in failures)

            retrieval_cases = load_retrieval_cases(Path("data/evaluation/legal_retrieval.json"))
            retrieval_cases = [
                item.model_copy(update={"as_of": item.as_of or FIXED_AS_OF})
                for item in retrieval_cases
            ]
            retrieval = evaluate_retrieval(db, retrieval_cases, mode="hybrid")

    metrics = {
        "reasoning_demo": _run_reasoning_metrics(Path("data/evaluation/gold_cases.json")),
        "reasoning_official": _run_reasoning_metrics(
            Path("data/evaluation/official_model_labeled_cases.json")
        ),
        "retrieval": retrieval,
        "strategy": {
            "case_count": len(case_rows),
            "pass_rate": round(sum(item["passed"] for item in case_rows) / len(case_rows), 3),
        },
        "fault_injection": {
            "scenario_count": len(fault_rows),
            "pass_rate": round(sum(item["passed"] for item in fault_rows) / len(fault_rows), 3),
        },
    }
    aggregate = retrieval["aggregate"]
    if aggregate["citation_validity"] != 1.0:
        gate_failures.append("retrieval:citation_validity_below_one")
    if aggregate["version_violations"] != 0:
        gate_failures.append("retrieval:version_violation")
    if aggregate["region_violations"] != 0:
        gate_failures.append("retrieval:region_violation")
    baseline = compare_baseline(metrics, compatibility, baseline_path)
    quality_passed = not gate_failures
    passed = quality_passed and baseline["status"] == "passed"
    return {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "generated_at": _utc_now(),
        "profile": "offline",
        "complete": True,
        "quality_passed": quality_passed,
        "passed": passed,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "environment": _environment("offline"),
        "compatibility": compatibility,
        "metrics": metrics,
        "cases": case_rows,
        "fault_injection": fault_rows,
        "gate_failures": gate_failures,
        "warnings": ([] if baseline["status"] == "passed" else [f"baseline_{baseline['status']}"]),
        "baseline": baseline,
        "judge": None,
        "budget": None,
    }


def _validate_live_authorization(
    pack: HarnessCasePack,
    manifest: HarnessAuthorizationManifest,
) -> None:
    if manifest.status != "active":
        raise ValueError("授权清单不是 active 状态")
    if manifest.expires_on < date.today():
        raise ValueError("授权清单已过期")
    if manifest.allowed_case_pack_version != pack.version:
        raise ValueError("授权清单与 Case Pack 版本不匹配")
    for item in pack.cases:
        if not item.external_model_allowed:
            raise ValueError(f"案例 {item.id} 未允许发送至外部模型")
        if item.data_classification not in manifest.allowed_data_classifications:
            raise ValueError(f"案例 {item.id} 的数据分类不在授权范围")


def run_live(
    *,
    pack_path: Path,
    authorization_path: Path,
    allow_paid_model: bool,
    max_model_calls: int = 15,
    max_http_requests: int = 30,
    settings: Settings | None = None,
    judge_factory: Callable[[Callable[[], ModelGateway], Settings], HarnessJudge] = ModelHarnessJudge,
) -> dict:
    if not allow_paid_model:
        raise ValueError("live profile 必须显式传入 --allow-paid-model")
    if max_model_calls <= 0 or max_http_requests <= 0:
        raise ValueError("模型调用预算必须为正数")
    pack = load_case_pack(pack_path)
    if len(pack.cases) != 3:
        raise ValueError("live Case Pack 第一版必须恰好包含 3 个案例")
    manifest = HarnessAuthorizationManifest.model_validate(_read_json(authorization_path))
    _validate_live_authorization(pack, manifest)
    settings = settings or Settings()
    if settings.model_provider != "deepseek" or not settings.deepseek_api_key:
        raise ValueError("live profile 需要已配置的 DeepSeek provider 和 API key")

    started = time.perf_counter()
    budget = ModelRequestBudget(max_model_calls, max_http_requests)
    gateways: list[ModelGateway] = []
    gateway_lock = threading.Lock()

    def gateway_factory() -> ModelGateway:
        gateway = ModelGateway(settings, request_budget=budget)
        with gateway_lock:
            gateways.append(gateway)
        return gateway

    judge = judge_factory(gateway_factory, settings)
    rows: list[dict] = []
    gate_failures: list[str] = []
    warnings: list[str] = []
    complete = True
    with _deterministic_embedding_settings():
        with isolated_session() as db:
            seed_authorities(db)
            _publish_fixture_legal_versions(db)
            authorities = list(db.scalars(select(LegalAuthority)).all())
            for item in pack.cases:
                case = _seed_case(db, item)
                authorization = ModelCallAuthorization(
                    consent_id=manifest.consent_id,
                    consent_version=manifest.consent_version,
                    case_id=item.id,
                    tenant_id=manifest.tenant_id,
                    purpose=manifest.purpose,
                )
                try:
                    state = build_strategy_workflow(
                        db,
                        gateway_factory=gateway_factory,
                        authorization=authorization,
                    ).invoke(_initial_state(case, authorities))
                    failures = _assert_strategy(db, item, state, None, allow_fallback=False)
                    judge_result = judge.evaluate(item, state, authorization)
                    judge_payload = judge_result.model_dump()
                    low_dimensions = [
                        name
                        for name, value in judge_payload.items()
                        if isinstance(value, dict) and value.get("score", 5) < 3
                    ]
                    warnings.extend(f"{item.id}:judge_low:{name}" for name in low_dimensions)
                    timing_events = list(
                        db.scalars(
                            select(AuditEvent).where(
                                AuditEvent.case_id == item.id,
                                AuditEvent.event_type == "agent_task_completed",
                            )
                        ).all()
                    )
                    node_timings = {
                        event.agent: event.duration_ms for event in timing_events
                    }
                except Exception as exc:
                    complete = False
                    failures = [f"execution_incomplete:{type(exc).__name__}"]
                    judge_payload = None
                    node_timings = {}
                gate_failures.extend(f"{item.id}:{failure}" for failure in failures)
                rows.append(
                    {
                        "id": item.id,
                        "passed": not failures,
                        "failures": failures,
                        "judge": judge_payload,
                        "node_duration_ms": node_timings,
                    }
                )

    telemetry = [asdict(item.last_telemetry) for item in gateways if item.last_telemetry]
    usage = {
        "prompt_tokens": sum(item.get("prompt_tokens") or 0 for item in telemetry),
        "completion_tokens": sum(item.get("completion_tokens") or 0 for item in telemetry),
        "total_tokens": sum(item.get("total_tokens") or 0 for item in telemetry),
        "fallbacks": sum(item.get("outcome") != "success" for item in telemetry),
        "retries": sum(item.get("retries") or 0 for item in telemetry),
        "call_duration_ms": [item.get("duration_ms") for item in telemetry],
    }
    return {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "generated_at": _utc_now(),
        "profile": "live",
        "complete": complete,
        "quality_passed": not gate_failures,
        "passed": complete and not gate_failures,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "environment": _environment("live", settings),
        "compatibility": {
            "case_pack_version": pack.version,
            "workflow_version": WORKFLOW_VERSION,
            "case_pack_sha256": _sha256(pack_path),
        },
        "metrics": {"model": usage},
        "cases": rows,
        "fault_injection": None,
        "gate_failures": gate_failures,
        "warnings": warnings,
        "baseline": None,
        "judge": judge.source,
        "budget": budget.snapshot(),
    }


def render_markdown(report: dict) -> str:
    status = "PASS" if report["passed"] else "FAIL"
    lines = [
        f"# Harness Report: {status}",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Run ID: `{report['run_id']}`",
        f"- Complete: `{str(report['complete']).lower()}`",
        f"- Duration: `{report['duration_ms']} ms`",
        "",
        "## Gate failures",
        "",
    ]
    failures = report.get("gate_failures") or []
    lines.extend([f"- {item}" for item in failures] or ["- None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {item}" for item in report.get("warnings") or []] or ["- None"])
    lines.extend(["", "## Cases", "", "| Case | Status |", "|---|---|"])
    lines.extend(
        f"| {item['id']} | {'PASS' if item['passed'] else 'FAIL'} |"
        for item in report.get("cases") or []
    )
    return "\n".join(lines) + "\n"


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def accept_baseline(report_path: Path, baseline_path: Path = DEFAULT_BASELINE) -> dict:
    report = _read_json(report_path)
    if report.get("schema_version") != HARNESS_SCHEMA_VERSION:
        raise ValueError("报告 schema 不受支持")
    if report.get("profile") != "offline" or not report.get("complete"):
        raise ValueError("只能接受完整的 offline 报告")
    if not report.get("quality_passed"):
        raise ValueError("不能接受质量门禁失败的报告")
    rules = _metric_rules(report["metrics"])
    baseline = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "accepted_at": _utc_now(),
        "accepted_from_run_id": report["run_id"],
        "compatibility_fingerprint": report["compatibility"]["fingerprint"],
        "compatibility": report["compatibility"],
        "metric_rules": {
            path: {"value": value, "direction": direction}
            for path, (value, direction) in rules.items()
        },
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return baseline
