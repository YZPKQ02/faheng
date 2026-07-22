import hashlib
import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.authorities import retrieve_authority_hits, retrieve_hybrid_authority_hits
from app.database import SessionLocal
from app.embeddings import index_legal_chunks
from app.legal_governance import transition_legal_version
from app.legal_rag import build_rag_observations, validate_authority_ids
from app.legal_ingestion import RawLegalDocument, import_legal_jsonl, import_legal_records
from app.models import LegalAuthority, LegalChunk, LegalDocument, LegalDocumentVersion
from app.retrieval_evaluation import evaluate_retrieval, load_retrieval_cases


def _record(**overrides) -> RawLegalDocument:
    payload = {
        "title": "北京市劳动争议示例规定",
        "level": "地方规定",
        "jurisdiction": "北京市",
        "source_url": "https://www.gov.cn/",
        "version_label": "2026版",
        "effective_on": "2026-01-01",
        "chunks": [
            {
                "locator": "第三条",
                "content": "用人单位应当依法记录劳动者的加班时间。",
                "keywords": ["加班记录", "加班时间"],
            }
        ],
    }
    payload.update(overrides)
    return RawLegalDocument.model_validate(payload)


def _publish_pending_versions(db) -> None:
    versions = list(
        db.scalars(
            select(LegalDocumentVersion).where(
                LegalDocumentVersion.review_status == "pending"
            )
        ).all()
    )
    for version in versions:
        transition_legal_version(
            db,
            version,
            action="approve",
            actor_id="test-reviewer",
            roles={"admin"},
            notes="test corpus reviewed",
        )
        transition_legal_version(
            db,
            version,
            action="publish",
            actor_id="test-publisher",
            roles={"admin"},
            notes="test corpus published",
        )
    db.commit()


def test_seed_is_backfilled_into_versioned_knowledge(client):
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LegalDocument)) == 3
        assert db.scalar(select(func.count()).select_from(LegalDocumentVersion)) == 3
        assert db.scalar(select(func.count()).select_from(LegalChunk)) == 5


def test_legal_import_is_idempotent_and_auditable(client):
    with SessionLocal() as db:
        first = import_legal_records(db, [_record()])
        second = import_legal_records(db, [_record()])

        assert first.imported_documents == 1
        assert first.imported_versions == 1
        assert first.imported_chunks == 1
        assert second.duplicates == 1


def test_overlapping_active_version_requires_explicit_transition(client):
    with SessionLocal() as db:
        first = import_legal_records(db, [_record()])
        conflicting = import_legal_records(
            db,
            [_record(version_label="冲突版本", chunks=[{"locator": "第三条", "content": "冲突正文"}])],
        )

        assert first.imported_versions == 1
        assert conflicting.rejected == 1
        assert "未声明替换关系" in conflicting.errors[0]
        assert db.scalar(select(func.count()).select_from(LegalDocumentVersion)) == 4


def test_correction_supersedes_old_version_without_changing_effective_date(client):
    with SessionLocal() as db:
        import_legal_records(db, [_record()])
        document = db.scalar(
            select(LegalDocument).where(LegalDocument.title == "北京市劳动争议示例规定")
        )
        old_version = db.scalar(
            select(LegalDocumentVersion).where(LegalDocumentVersion.document_id == document.id)
        )
        old_authority = db.scalar(
            select(LegalChunk).where(LegalChunk.version_id == old_version.id)
        ).authority_id
        corrected = _record(
            version_label="2026勘误版",
            supersedes_content_hash=old_version.content_hash,
            transition_kind="correction",
            chunks=[
                {
                    "locator": "第三条",
                    "content": "用人单位应当准确记录劳动者的加班时间。",
                    "keywords": ["加班记录", "加班时间"],
                }
            ],
        )

        result = import_legal_records(db, [corrected])
        _publish_pending_versions(db)
        versions = db.scalars(
            select(LegalDocumentVersion)
            .where(LegalDocumentVersion.document_id == document.id)
            .order_by(LegalDocumentVersion.ingested_at)
        ).all()

        assert result.transitioned_versions == 1
        assert versions[0].status == "superseded"
        assert versions[0].expired_on == date(2026, 1, 1)
        assert versions[1].status == "active"
        assert versions[1].supersedes_id == versions[0].id
        hits = retrieve_authority_hits(db, "准确记录劳动者的加班时间", region="北京市")
        assert any(hit.authority.content == corrected.chunks[0].content for hit in hits)
        assert all(hit.authority.id != old_authority for hit in hits)


def test_amendment_expires_old_version_and_preserves_historical_retrieval(client):
    with SessionLocal() as db:
        import_legal_records(db, [_record()])
        document = db.scalar(
            select(LegalDocument).where(LegalDocument.title == "北京市劳动争议示例规定")
        )
        old_version = db.scalar(
            select(LegalDocumentVersion).where(LegalDocumentVersion.document_id == document.id)
        )
        amended = _record(
            version_label="2027修订版",
            effective_on="2027-01-01",
            supersedes_content_hash=old_version.content_hash,
            transition_kind="amendment",
            chunks=[
                {
                    "locator": "第三条",
                    "content": "用人单位应当保存劳动者的加班记录。",
                    "keywords": ["加班记录", "加班时间"],
                }
            ],
        )

        result = import_legal_records(db, [amended])
        _publish_pending_versions(db)
        db.refresh(old_version)
        old_authority = db.scalar(
            select(LegalChunk).where(LegalChunk.version_id == old_version.id)
        ).authority_id

        assert result.transitioned_versions == 1
        assert old_version.status == "expired"
        assert old_version.expired_on == date(2027, 1, 1)
        assert db.get(LegalAuthority, old_authority).expired_on == date(2027, 1, 1)
        historical = retrieve_authority_hits(
            db,
            "加班记录和加班时间",
            as_of=date(2026, 7, 16),
            region="北京市",
        )
        current = retrieve_authority_hits(
            db,
            "加班记录和加班时间",
            as_of=date(2027, 1, 1),
            region="北京市",
        )
        assert any(hit.authority.id == old_authority for hit in historical)
        assert all(hit.authority.id != old_authority for hit in current)


def test_transition_manifest_is_bound_to_corpus_hash(client, tmp_path):
    with SessionLocal() as db:
        import_legal_records(db, [_record()])
        document = db.scalar(
            select(LegalDocument).where(LegalDocument.title == "北京市劳动争议示例规定")
        )
        old_version = db.scalar(
            select(LegalDocumentVersion).where(LegalDocumentVersion.document_id == document.id)
        )
        corrected = _record(
            version_label="2026勘误版",
            chunks=[{"locator": "第三条", "content": "依法准确记录加班时间。"}],
        )
        corpus_bytes = (corrected.model_dump_json() + "\n").encode()
        corpus_path = tmp_path / "corpus.jsonl"
        corpus_path.write_bytes(corpus_bytes)
        manifest_path = tmp_path / "transition_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "corpus_version": "test_v2",
                    "target_corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
                    "transitions": [
                        {
                            "title": corrected.title,
                            "supersedes_content_hash": old_version.content_hash,
                            "transition_kind": "correction",
                            "reason": "测试解析勘误",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = import_legal_jsonl(
            db,
            corpus_path,
            transition_manifest_path=manifest_path,
        )

        assert result.transitioned_versions == 1
        db.refresh(old_version)
        assert old_version.status == "superseded"

        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                hashlib.sha256(corpus_bytes).hexdigest(),
                "0" * 64,
            ),
            encoding="utf-8",
        )
        try:
            import_legal_jsonl(
                db,
                corpus_path,
                transition_manifest_path=manifest_path,
            )
        except ValueError as exc:
            assert "SHA-256 不匹配" in str(exc)
        else:
            raise AssertionError("语料哈希不匹配时必须拒绝转换清单")


def test_version_and_region_are_hard_filters(client):
    with SessionLocal() as db:
        import_legal_records(
            db,
            [
                _record(),
                _record(
                    title="上海市劳动争议示例规定",
                    jurisdiction="上海市",
                ),
                _record(
                    title="北京市未来劳动规定",
                    effective_on="2030-01-01",
                ),
            ],
        )
        _publish_pending_versions(db)
        hits = retrieve_authority_hits(
            db,
            "加班记录和加班时间",
            as_of=date(2026, 7, 16),
            region="北京市",
        )

        assert any(hit.authority.title == "北京市劳动争议示例规定" for hit in hits)
        assert all(hit.authority.region in ("全国", "中国大陆", "北京市") for hit in hits)
        assert all(hit.authority.effective_on <= date(2026, 7, 16) for hit in hits)


def test_lexical_retrieval_baseline_has_no_scope_violations(client):
    with SessionLocal() as db:
        cases = load_retrieval_cases(Path("data/evaluation/legal_retrieval.json"))
        report = evaluate_retrieval(db, cases)

    assert report["aggregate"]["recall_at_10"] >= 0.75
    assert report["aggregate"]["version_violations"] == 0
    assert report["aggregate"]["region_violations"] == 0


def test_hybrid_retrieval_does_not_regress_lexical_recall(client):
    with SessionLocal() as db:
        cases = load_retrieval_cases(Path("data/evaluation/legal_retrieval.json"))
        lexical = evaluate_retrieval(db, cases, mode="lexical")
        hybrid = evaluate_retrieval(db, cases, mode="hybrid")

    assert hybrid["aggregate"]["recall_at_10"] >= lexical["aggregate"]["recall_at_10"]
    assert hybrid["aggregate"]["version_violations"] == 0
    assert hybrid["aggregate"]["region_violations"] == 0


def test_semantic_recall_can_add_a_lexically_missing_authority(client, monkeypatch):
    class FakeSemanticProvider:
        name = "test"
        model = "legal-semantic-test"
        dimensions = 2

        def embed(self, texts):
            return [
                [1.0, 0.0]
                if "经济补偿按劳动者" in text or "被裁后待遇" in text
                else [0.0, 1.0]
                for text in texts
            ]

    provider = FakeSemanticProvider()
    with SessionLocal() as db:
        index_legal_chunks(db, provider)
        db.commit()
        lexical = retrieve_authority_hits(db, "被裁后待遇怎么算")
        monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda: provider)
        hybrid = retrieve_hybrid_authority_hits(db, "被裁后待遇怎么算")

    assert all(hit.authority.article != "第四十七条" for hit in lexical)
    assert any(hit.authority.article == "第四十七条" for hit in hybrid)


def test_rag_observations_and_citations_use_versioned_chunks(client):
    with SessionLocal() as db:
        hits = retrieve_authority_hits(db, "违法解除赔偿金")
        ids = [hit.authority.id for hit in hits]
        valid_ids = validate_authority_ids(db, ids, region="北京市")
        observations = build_rag_observations(db, valid_ids)

    assert valid_ids == ids
    assert observations
    assert observations[0]["authority_id"] == ids[0]
    assert observations[0]["citation"].startswith("《中华人民共和国")
    assert observations[0]["effective_on"]
    assert observations[0]["source_url"].startswith("https://")


def test_pending_and_approved_legal_versions_are_not_retrievable(client):
    with SessionLocal() as db:
        import_legal_records(db, [_record()])
        version = db.scalar(
            select(LegalDocumentVersion)
            .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
            .where(LegalDocument.title == "北京市劳动争议示例规定")
        )

        assert not any(
            hit.authority.title == "北京市劳动争议示例规定"
            for hit in retrieve_authority_hits(db, "加班记录", region="北京市")
        )
        transition_legal_version(
            db,
            version,
            action="approve",
            actor_id="reviewer",
            roles={"reviewer"},
            notes="内容与官方来源一致",
        )
        assert not any(
            hit.authority.title == "北京市劳动争议示例规定"
            for hit in retrieve_authority_hits(db, "加班记录", region="北京市")
        )
        transition_legal_version(
            db,
            version,
            action="publish",
            actor_id="lawyer",
            roles={"lawyer"},
            notes="批准进入检索语料",
        )

        assert any(
            hit.authority.title == "北京市劳动争议示例规定"
            for hit in retrieve_authority_hits(db, "加班记录", region="北京市")
        )


def test_external_semantic_query_requires_case_consent(client, monkeypatch):
    case_id = client.post("/cases", json={"title": "向量授权测试"}).json()["id"]

    class ExternalProvider:
        name = "http"
        model = "external-test"
        dimensions = 2

        def embed(self, texts):
            raise AssertionError("未授权时不得调用外部 Embedding")

    monkeypatch.setattr(
        "app.embeddings.get_embedding_provider",
        lambda: ExternalProvider(),
    )
    with SessionLocal() as db:
        hits = retrieve_hybrid_authority_hits(
            db,
            "违法解除赔偿金",
            case_id=case_id,
            tenant_id="local",
        )

    assert hits
    assert hits[0].authority.article == "第八十七条"
