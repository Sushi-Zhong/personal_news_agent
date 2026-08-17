from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personal_news_agent.core.models import ChatResponse, EvidenceRef, SearchResult
from personal_news_agent.services.evidence import EvidenceLedger
from personal_news_agent.skills.base import SkillResult
from personal_news_agent.skills.result_models import ChangeDigestData, CoverageCompareData


def test_skill_result_and_chat_response_keep_compatible_defaults() -> None:
    skill = SkillResult(command="/brief", title="brief", message="done")
    chat = ChatResponse(
        conversation_id="conv",
        answer="done",
        context_relation="skill",
    )

    assert skill.skill_id == "brief"
    assert skill.status == "success"
    assert skill.output_kind == "default_markdown"
    assert skill.evidence == ()
    assert skill.fallback_reason is None
    assert chat.status == "success"
    assert chat.output_kind == "default_markdown"
    assert chat.fallback_reason is None


def test_evidence_ref_has_the_unified_public_fields() -> None:
    evidence = EvidenceRef(
        index=1,
        title="Source",
        url="https://example.com/news",
        source_id="example",
        published_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        origin="local",
        claim_role="supporting",
    )

    assert set(evidence.model_dump()) == {
        "index",
        "title",
        "url",
        "source_id",
        "published_at",
        "origin",
        "claim_role",
    }


def test_evidence_ledger_deduplicates_and_rejects_unobserved_references() -> None:
    ledger = EvidenceLedger()
    first = ledger.add(
        SearchResult(
            article_id="a1",
            source_id="example",
            title="Observed",
            url="https://example.com/news?utm_source=test",
            category="tech",
            origin="local",
        ),
        claim_role="current",
    )
    duplicate = ledger.add(
        {
            "source_id": "example",
            "title": "Observed duplicate",
            "url": "https://example.com/news",
            "origin": "local",
        }
    )

    assert first.index == 1
    assert duplicate.index == 1
    assert len(ledger.references) == 1
    assert ledger.validate_model_references([{"index": 1, "url": "https://example.com/news"}]) == [first]
    assert ledger.validate_model_references([{"index": 99, "url": "https://invented.example/x"}]) == []
    assert ledger.validate_model_references([{"index": 1, "url": "https://invented.example/x"}]) == []


def test_evidence_ledger_rejects_non_http_urls() -> None:
    ledger = EvidenceLedger()

    with pytest.raises(ValueError, match="HTTP"):
        ledger.add({"title": "invalid", "url": "invented-without-host"})


def test_changed_and_compare_use_distinct_business_status_names() -> None:
    changed = ChangeDigestData(topic="OpenAI", change_status="no_material_change")
    compared = CoverageCompareData(topic="OpenAI", comparison_status="insufficient")

    assert changed.change_status == "no_material_change"
    assert compared.comparison_status == "insufficient"
    assert "status" not in changed.model_dump()
    assert "status" not in compared.model_dump()

    with pytest.raises(Exception):
        ChangeDigestData.model_validate({"topic": "OpenAI", "status": "changed"})
    with pytest.raises(Exception):
        CoverageCompareData.model_validate({"topic": "OpenAI", "status": "sufficient"})
