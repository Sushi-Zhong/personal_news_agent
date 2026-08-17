from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "personal_news_agent" / "static" / "shared.js"
WEB = ROOT / "personal_news_agent" / "static" / "web.js"
MOBILE = ROOT / "personal_news_agent" / "static" / "mobile.js"


def test_slash_menu_loads_public_skills_without_static_business_catalog() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert 'request("/api/skills")' in source
    assert "async function loadAssistantSkills" in source
    assert "let assistantSlashCommands = []" in source
    assert "assistantSlashCommands = []" in source
    assert 'name: "factcheck"' not in source
    assert 'name: "report"' not in source
    assert 'name: "schedule"' not in source


def test_public_skill_catalog_drives_desktop_and_mobile_submission() -> None:
    shared = SHARED.read_text(encoding="utf-8")
    web = WEB.read_text(encoding="utf-8")
    mobile = MOBILE.read_text(encoding="utf-8")

    assert "let assistantSkillsReady" in shared
    assert "async function isPublicAssistantSkillCommand" in shared
    assert "await assistantSkillsReady" in shared
    assert 'input.addEventListener("input", async () =>' in shared
    assert "await isPublicAssistantSkillCommand(command.name)" in web
    assert "return sendChatIntoTurn(message, assistantNode);" in web
    assert "await isPublicAssistantSkillCommand(command.name)" in mobile
    assert "return sendChatIntoTurn(message, assistantNode);" in mobile


def test_slash_menu_searches_aliases_and_descriptions() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert 'command.aliases.join(" ")' in source
    assert "command.description" in source


def test_unknown_output_kind_has_default_markdown_renderer() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "const outputRenderers" in source
    assert "default_markdown" in source
    assert "outputRenderers[data.output_kind] || outputRenderers.default_markdown" in source


def test_schedule_confirmation_card_is_actionable_only_with_ephemeral_token() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "schedule_confirmation: renderScheduleConfirmationOutput" in source
    assert "function renderScheduleConfirmationOutput" in source
    assert 'data-schedule-action="confirm"' in source
    assert 'data-schedule-action="cancel"' in source
    assert "if (!confirmation.token)" in source
    assert "buttons.forEach((item) => { item.disabled = true; })" in source
    assert "confirmation_token: confirmation.token" in source
    assert "confirmation_id: confirmation.confirmation_id" in source
    assert "conversation_id: card.dataset.conversationId" in source
    assert "error.status = response.status" in source
    assert "const terminalStatuses = new Set([403, 404, 409, 410])" in source
    assert "buttons.forEach((item) => { item.disabled = false; })" in source
    assert "localStorage.setItem" not in source.split("async function handleScheduleConfirmationAction", 1)[1].split("function", 1)[0]


def test_change_digest_renderer_keeps_execution_and_business_status_separate() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "change_digest: renderChangeDigestOutput" in source
    assert "function renderChangeDigestOutput" in source
    assert "payload.change_status" in source
    assert 'renderChangeItems("新增事实", payload.new_facts, evidenceAnchorPrefix)' in source
    assert 'renderChangeItems("状态变化", payload.status_changes, evidenceAnchorPrefix)' in source
    assert 'renderChangeItems("数字变化", payload.number_changes, evidenceAnchorPrefix)' in source
    assert 'renderChangeItems("纠正与反转", payload.corrections, evidenceAnchorPrefix)' in source
    assert "payload.repeated_reports" in source
    assert 'data.status === "degraded"' in source
    assert "renderSkillEvidenceLinks" in source
    assert "renderSkillEvidenceList" in source
    assert 'skillEvidenceAnchorPrefix(data, "change")' in source


def test_coverage_compare_renderer_separates_conflicts_from_framing() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "coverage_compare: renderCoverageCompareOutput" in source
    assert "function renderCoverageCompareOutput" in source
    assert "payload.comparison_status" in source
    assert 'renderComparisonItems("共同确认", payload.common_facts, evidenceAnchorPrefix)' in source
    assert 'renderComparisonItems("独有说法", payload.unique_claims, evidenceAnchorPrefix)' in source
    assert 'renderComparisonItems("事实冲突", payload.conflicts, evidenceAnchorPrefix)' in source
    assert 'renderComparisonItems("叙事侧重", payload.framing_differences, evidenceAnchorPrefix)' in source
    assert "payload.source_groups" in source
    assert 'data.status === "degraded"' in source
    assert "renderSkillEvidenceLinks" in source
    assert "renderSkillEvidenceList" in source
    assert 'skillEvidenceAnchorPrefix(data, "compare")' in source


def test_structured_skill_evidence_anchors_are_scoped_per_card() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "data.turn_id" in source.split("function skillEvidenceAnchorPrefix", 1)[1].split("function", 1)[0]
    assert "renderSkillEvidenceLinks(item.evidence_indices, evidenceAnchorPrefix)" in source
    assert "renderSkillEvidenceList(payload.evidence || data.evidence || [], evidenceAnchorPrefix)" in source
    assert 'href="#skill-evidence-${escapeAttr(index)}"' not in source


def test_structured_skill_results_hide_internal_status_and_group_codes() -> None:
    source = SHARED.read_text(encoding="utf-8")

    assert "function skillFallbackMessage" in source
    assert "function sourceGroupReasonLabel" in source
    assert "skillFallbackMessage(data.fallback_reason)" in source
    assert "sourceGroupReasonLabel(group.reason)" in source
    assert '${escapeHtml(data.fallback_reason' not in source
    assert '${escapeHtml(group.reason' not in source
