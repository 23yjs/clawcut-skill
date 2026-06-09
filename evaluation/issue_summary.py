from __future__ import annotations

from typing import Any


ISSUE_LABELS = {
    "execution_issue": "执行链路异常",
    "ark_fallback": "Ark 调用失败，已触发兜底",
    "content_selection_issue": "内容选择得分低于 60 分",
    "forbidden_content_issue": "混入用户明确禁止内容",
    "technical_quality_issue": "成片存在技术质量问题",
    "editing_experience_issue": "成片观看体验需要优化",
}


def build_issue_summary(
    result: dict[str, Any],
    *,
    content_selection_score_warning_below: float = 60,
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
        issues.append(
            _issue(
                "content_selection_issue",
                f"selection_score_v1={selection_score}，低于阈值 {content_selection_score_warning_below}。",
                display_text=_content_selection_display_text(
                    result,
                    score=float(selection_score),
                    threshold=float(content_selection_score_warning_below),
                ),
                reasons=_content_selection_reasons(result),
            )
        )
    forbidden_ratio = _forbidden_duration_ratio(result)
    if isinstance(forbidden_ratio, (int, float)) and forbidden_ratio > 0:
        display_text = f"混入用户明确禁止内容：明确禁止内容混入比例 {_format_percent(forbidden_ratio)}"
        issues.append(
            _issue(
                "forbidden_content_issue",
                f"forbidden_duration_ratio={forbidden_ratio}",
                display_text=display_text,
                reasons=[
                    {
                        "label": "明确禁止内容混入比例",
                        "value": forbidden_ratio,
                        "display_value": _format_percent(forbidden_ratio),
                    }
                ],
            )
        )

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


def _issue(
    issue_type: str,
    detail: Any,
    *,
    display_text: str | None = None,
    reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issue = {
        "issue_type": issue_type,
        "label": ISSUE_LABELS[issue_type],
        "detail": "" if detail is None else str(detail),
    }
    if display_text:
        issue["display_text"] = display_text
    if reasons:
        issue["reasons"] = reasons
    return issue


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _forbidden_duration_ratio(result: dict[str, Any]) -> float | None:
    metrics = result.get("time_metrics") if isinstance(result.get("time_metrics"), dict) else {}
    return _as_float(metrics.get("forbidden_duration_ratio"))


def _format_score(value: float) -> str:
    return f"{value:.2f}"


def _format_threshold(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def _format_percent(value: float) -> str:
    return f"{(value * 100 if value <= 1 else value):.1f}%"


def _content_selection_reasons(result: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = result.get("time_metrics") if isinstance(result.get("time_metrics"), dict) else {}
    duration = result.get("duration_context") if isinstance(result.get("duration_context"), dict) else {}
    duration_score = duration.get("duration_score") if duration.get("duration_score") is not None else metrics.get("duration_score")
    specs = [
        ("目标内容覆盖率", metrics.get("relevant_duration_coverage"), True),
        ("合理内容占比", metrics.get("acceptable_precision"), True),
        ("默认高光命中率", metrics.get("default_highlight_precision"), True),
        ("明确禁止内容混入比例", metrics.get("forbidden_duration_ratio"), True),
        ("默认规避内容混入比例", metrics.get("avoid_by_default_overlap_ratio"), True),
        ("时长约束得分", duration_score, True),
    ]
    reasons: list[dict[str, Any]] = []
    for label, raw_value, is_percent in specs:
        value = _as_float(raw_value)
        if value is None:
            continue
        reasons.append(
            {
                "label": label,
                "value": value,
                "display_value": _format_percent(value) if is_percent else _format_score(value),
            }
        )
    return reasons


def _content_selection_display_text(
    result: dict[str, Any],
    *,
    score: float,
    threshold: float,
) -> str:
    lines = [
        f"内容选择得分偏低：{_format_score(score)} 分，低于告警阈值 {_format_threshold(threshold)} 分"
    ]
    reasons = _content_selection_reasons(result)
    if reasons:
        lines.append("主要原因：")
        lines.extend(f"- {item['label']}：{item['display_value']}" for item in reasons)
    return "\n".join(lines)
