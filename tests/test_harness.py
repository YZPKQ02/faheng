import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.harness import (
    DEFAULT_OFFLINE_PACK,
    accept_baseline,
    compare_baseline,
    load_case_pack,
    run_live,
    run_offline,
    write_report,
)


def test_offline_harness_is_complete_and_uses_isolated_gates(tmp_path):
    report = run_offline(baseline_path=tmp_path / "missing-baseline.json")

    assert report["complete"] is True
    assert report["quality_passed"] is True
    assert report["passed"] is False
    assert report["baseline"]["status"] == "missing"
    assert report["metrics"]["strategy"]["pass_rate"] == 1.0
    assert report["metrics"]["fault_injection"]["pass_rate"] == 1.0
    assert report["metrics"]["retrieval"]["aggregate"]["citation_validity"] == 1.0
    assert report["metrics"]["retrieval"]["aggregate"]["version_violations"] == 0
    assert report["metrics"]["retrieval"]["aggregate"]["region_violations"] == 0


def test_baseline_requires_explicit_accept_and_detects_regression(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    report = run_offline(baseline_path=baseline_path)
    report_path, _ = write_report(report, tmp_path / "report")

    accept_baseline(report_path, baseline_path)
    assert baseline_path.exists()
    assert compare_baseline(
        report["metrics"], report["compatibility"], baseline_path
    )["status"] == "passed"

    regressed = json.loads(json.dumps(report["metrics"]))
    regressed["retrieval"]["aggregate"]["recall_at_10"] = 0.0
    comparison = compare_baseline(regressed, report["compatibility"], baseline_path)
    assert comparison["status"] == "regressed"
    assert any(
        item["metric"] == "retrieval.aggregate.recall_at_10" and not item["passed"]
        for item in comparison["deltas"]
    )
    incompatible = dict(report["compatibility"])
    incompatible["fingerprint"] = "different-inputs"
    assert compare_baseline(report["metrics"], incompatible, baseline_path)["status"] == (
        "incompatible"
    )


def test_baseline_rejects_failed_report(tmp_path):
    report = run_offline(baseline_path=tmp_path / "missing.json")
    report["quality_passed"] = False
    report_path, _ = write_report(report, tmp_path / "report")

    with pytest.raises(ValueError, match="质量门禁失败"):
        accept_baseline(report_path, tmp_path / "baseline.json")


def test_live_harness_fails_before_transport_without_explicit_permission(tmp_path):
    with pytest.raises(ValueError, match="allow-paid-model"):
        run_live(
            pack_path=Path("data/harness/cases/live.json"),
            authorization_path=Path("data/harness/live_authorization.example.json"),
            allow_paid_model=False,
        )


def test_live_harness_rejects_template_authorization_before_model_configuration():
    with pytest.raises(ValueError, match="不是 active"):
        run_live(
            pack_path=Path("data/harness/cases/live.json"),
            authorization_path=Path("data/harness/live_authorization.example.json"),
            allow_paid_model=True,
        )


def test_offline_case_pack_is_never_allowed_for_external_model():
    pack = load_case_pack(DEFAULT_OFFLINE_PACK)
    assert all(not item.external_model_allowed for item in pack.cases)


def test_live_harness_runs_three_cases_with_advisory_judge(monkeypatch, tmp_path):
    authorization = {
        "schema_version": "harness-authorization-v1",
        "status": "active",
        "consent_id": "approved-test-consent",
        "consent_version": 1,
        "tenant_id": "harness",
        "provider": "deepseek",
        "purpose": "harness_evaluation",
        "allowed_case_pack_version": "live-strategy-v1",
        "allowed_data_classifications": ["synthetic"],
        "expires_on": "2099-12-31",
    }
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")

    def fake_post(self, url, headers, json):
        system = json["messages"][0]["content"]
        user = json["messages"][1]["content"]
        authority_id = json_module.loads(user.split("\n双方观点：", 1)[0]).get(
            "authorities", [{"id": "authority"}]
        )[0]["id"] if "authorities" in user else "authority"
        if "六个维度" in system:
            dimension = {"score": 4, "reason": "轨迹与输入一致", "evidence_refs": []}
            content = json_module.dumps(
                {
                    "factual_fidelity": dimension,
                    "legal_grounding": dimension,
                    "worker_argument_quality": dimension,
                    "employer_argument_quality": dimension,
                    "neutrality": dimension,
                    "safety_expression": dimension,
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        elif "中立的劳动争议裁判" in system:
            content = json_module.dumps(
                {
                    "issues": ["争议焦点"],
                    "assessment": "需要结合证据审慎判断",
                    "likely_outcome": "信息有限",
                    "confidence": 0.6,
                    "uncertainties": ["证据待核验"],
                    "authority_ids": [authority_id],
                },
                ensure_ascii=False,
            )
        else:
            content = json_module.dumps(
                {
                    "position": "基于现有材料形成观点",
                    "arguments": ["逐项举证"],
                    "evidence_needed": ["原始证据"],
                    "authority_ids": [authority_id],
                },
                ensure_ascii=False,
            )
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    json_module = json
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    report = run_live(
        pack_path=Path("data/harness/cases/live.json"),
        authorization_path=authorization_path,
        allow_paid_model=True,
        settings=Settings(
            _env_file=None,
            model_provider="deepseek",
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.invalid",
            deepseek_max_retries=0,
        ),
    )

    assert report["complete"] is True
    assert report["passed"] is True
    assert report["budget"]["logical_calls"] == 12
    assert report["metrics"]["model"]["total_tokens"] == 180
    assert report["judge"]["policy"] == "advisory"
    assert all(len(item["node_duration_ms"]) == 4 for item in report["cases"])
