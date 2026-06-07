from __future__ import annotations

from typing import Any


ISSUE_LABELS = {
    "execution_issue": "执行链路异常",
    "ark_fallback": "Ark 调用失败，已触发兜底",
    "content_selection_issue": "内容选择存在问题",
    "technical_quality_issue": "成片存在技术质量问题",
    "editing_experience_issue": "成片观看体验需要优化",
}


def build_issue_summary(
    result: dict[str, Any],
    *,
    content_selection_score_warning_below: float = 70,
    editing_experience_score_warning_below: float = 70,
    max_display_issue_count: int = 3,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    status = str(result.get("evaluation_status") or "")
    if status in {"batch_case_failed", "resolver_failed", "invalid_artifact", "judge_video_upload_failed", "judge_failed"}:
        issues.append(_issue("execution_issue", result.get("error_message") or status))

    artifact = result.get("artifact_validation") if isinstance(result.get("artifact_validation"), dict) else {}
    if artifact.get("fallback_used") or result.get("fallback_used"):
        issues.append(_issue("ark_fallback", "Skill 使用 fallback，不能进入正式评分。"))

    selection_score = result.get("selection_score_v1")
    if isinstance(selection_score, (int, float)) and selection_score < content_selection_score_warning_below:
        issues.append(_issue("content_selection_issue", f"selection_score_v1={selection_score}，低于阈值 {content_selection_score_warning_below}。"))

    technical = result.get("technical_quality") if isinstance(result.get("technical_quality"), dict) else {}
    if technical and technical.get("technical_quality_passed") is False:
        detail = "; ".join(str(item) for item in technical.get("technical_quality_errors", []) or [])
        issues.append(_issue("technical_quality_issue", detail or "technical_quality_passed=false。"))

    editing_score = result.get("editing_experience_score_v1")
    editing = result.get("editing_experience") if isinstance(result.get("editing_experience"), dict) else {}
    aesthetic = result.get("aesthetic_judge") if isinstance(result.get("aesthetic_judge"), dict) else {}
    judge_issues = aesthetic.get("judge_issues") or editing.get("issues")
    if (
        isinstance(editing_score, (int, float))
        and editing_score < editing_experience_score_warning_below
    ) or judge_issues:
        issues.append(_issue("editing_experience_issue", f"editing_experience_score_v1={editing_score}。"))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        if issue["issue_type"] in seen:
            continue
        seen.add(issue["issue_type"])
        deduped.append(issue)
    return deduped[:max_display_issue_count]


def _issue(issue_type: str, detail: Any) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "label": ISSUE_LABELS[issue_type],
        "detail": "" if detail is None else str(detail),
    }
