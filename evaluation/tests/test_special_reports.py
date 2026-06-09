from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation import batch_dispatch_openclaw_official as dispatch
from evaluation import prepare_fps_dispatch_cases as fps_prepare
from evaluation import prepare_stability_dispatch_cases as stability_prepare
from evaluation import run_abnormal_eval
from evaluation import run_official_eval_report_v2 as report_v2


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _official_case(tmp_path: Path) -> dict:
    return {
        "case_id": "generic__game_demo2",
        "video_id": "game_demo2",
        "video_filename": "game_demo2.MP4",
        "input_video": "/home/node/.openclaw/workspace/data/input/game_demo2.MP4",
        "skill_output_dir": "/home/node/.openclaw/workspace/outputs/openclaw_collection_v2/game_demo2/generic__game_demo2/run_01",
        "instruction": "帮我剪辑一下这个视频",
        "target_duration": None,
        "test_type": "baseline_generic",
        "primary_capability": "generic_selection",
        "tested_capability": "默认高光识别",
        "challenge_tags": ["high_dynamic"],
        "priority": "baseline",
        "include_in_official_score": True,
        "llm_video_url": "https://example.com/game_demo2.MP4",
    }


def _eval_result() -> dict:
    return {
        "evaluation_status": "scored_complete",
        "evaluation_scope": "official",
        "video_id": "game_demo2",
        "instruction": "帮我剪辑一下这个视频",
        "selection_score_v1": 80,
        "editing_experience_score_v1": 70,
        "final_score_v2": 77,
        "evaluation_elapsed_seconds": 1.0,
        "artifact_validation": {
            "artifact_validation_passed": True,
            "skill_backend_used": "ark",
            "fallback_used": False,
            "result_summary": {"skill_llm_total_tokens": 10},
        },
        "technical_quality": {"technical_quality_passed": True},
    }


def test_special_reports_overview_always_contains_four_items(tmp_path):
    overview = report_v2._load_special_reports_overview(tmp_path)
    assert [item["key"] for item in overview] == ["abnormal", "resolver", "fps", "stability"]
    assert all(item["status"] == "未执行" for item in overview)


def test_abnormal_overview_and_detail_html(tmp_path):
    output_dir = tmp_path / "special_reports" / "abnormal"
    cases = [
        {
            "case_id": f"abnormal__{index}",
            "abnormal_type": "missing_video_path",
            "description": "demo",
            "expected_error_type": "missing_input_video",
            "expected_behavior": "明确失败",
            "should_enter_official_scoring": False,
        }
        for index in range(10)
    ]
    results = [
        {
            "case_id": case["case_id"],
            "actual_error_type": "missing_input_video",
            "status": "failed",
            "result_summary_exists": True,
            "run_log_exists": True,
            "highlight_video_exists": False,
            "entered_official_scoring": False,
            "verification_mode": "real_input",
        }
        for case in cases
    ]
    summary = run_abnormal_eval.build_abnormal_summary(cases, results)
    run_abnormal_eval.write_abnormal_report(summary, cases, output_dir)
    overview = report_v2._load_special_reports_overview(tmp_path)
    abnormal = overview[0]
    assert abnormal["status"] == "已完成"
    assert "通过 10 / 10" in abnormal["summary"]
    assert abnormal["detail_path"] == "special_reports/abnormal/detail.html"
    assert (output_dir / "detail.html").exists()


def test_fps_dispatch_expands_fps_values_and_unique_case_ids(tmp_path):
    official = {"game_demo2": _official_case(tmp_path)}
    cases = [
        {
            "case_id": "fps__game_demo2__killstreak",
            "video_id": "game_demo2",
            "instruction": "剪出连续击杀高光",
            "fps_values": [1, 2, 4],
            "llm_video_url": "https://example.com/game_demo2.MP4",
        }
    ]
    rows = fps_prepare.expand_fps_cases(cases, official)
    assert {row["video_fps"] for row in rows} == {1, 2, 4}
    assert len({row["case_id"] for row in rows}) == 3
    assert all(row["source_case_id"] == "fps__game_demo2__killstreak" for row in rows)


def test_official_dispatch_transmits_video_fps(tmp_path):
    case = dispatch.OfficialCase.from_dict({
        **_official_case(tmp_path),
        "case_id": "fps__demo__fps_2",
        "test_type": "fps_sensitivity",
        "priority": "special",
        "source_case_id": "fps__demo",
        "video_fps": 2,
    })
    message = dispatch.render_message(case, "run_01")
    assert "video_fps:\n2" in message
    assert "必须使用 video_fps=2" in message
    manifest = dispatch.manifest_from_attempt(
        case=case,
        run_id="run_01",
        session_key="s",
        started_at="start",
        finished_at="finish",
        openclaw_exit_code=0,
        stdout_payload={"meta": {"transport": "gateway"}},
        run_dir=tmp_path,
        artifact_search=dispatch.ArtifactSearchResult("missing", None, None, "missing"),
        result_summary={},
        collection_status="failed",
        error_message="missing",
    )
    assert manifest["video_fps"] == 2


def test_official_case_without_video_fps_keeps_default_message(tmp_path):
    case = dispatch.OfficialCase.from_dict(_official_case(tmp_path))
    message = dispatch.render_message(case, "run_01")
    assert "video_fps:\n" not in message
    assert "未指定 video_fps 时使用 Skill 默认采样配置" in message


def test_stability_dispatch_expands_repeat_count(tmp_path):
    official = {"generic__game_demo2": _official_case(tmp_path)}
    rows = stability_prepare.expand_stability_cases(
        [
            {
                "case_id": "stability__generic__game_demo2",
                "source_case_id": "generic__game_demo2",
                "repeat_count": 3,
                "llm_video_url": "https://example.com/game_demo2.MP4",
            }
        ],
        official,
    )
    assert [row["repeat_index"] for row in rows] == [1, 2, 3]
    assert [row["case_id"] for row in rows] == [
        "generic__game_demo2__repeat_01",
        "generic__game_demo2__repeat_02",
        "generic__game_demo2__repeat_03",
    ]


def test_report_html_hides_special_results_and_shows_future_work(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    case = _official_case(tmp_path)
    _write_jsonl(cases_path, [case])
    run_dir = tmp_path / "out" / "runs" / case["case_id"]
    run_dir.mkdir(parents=True)
    (run_dir / "evaluation_result.json").write_text(
        json.dumps(_eval_result(), ensure_ascii=False),
        encoding="utf-8",
    )
    special_dir = tmp_path / "out" / "special_reports" / "abnormal"
    special_dir.mkdir(parents=True)
    (special_dir / "abnormal_summary.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "case_count": 10,
                "result_count": 10,
                "passed_result_count": 10,
                "failed_result_count": 0,
                "not_run_count": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (special_dir / "detail.html").write_text("detail", encoding="utf-8")
    args = argparse.Namespace(
        mode="report-only",
        cases=cases_path,
        gt_dir=tmp_path,
        output_dir=tmp_path / "out",
        path_map=[],
        resume=False,
        retry_failed=False,
        technical_quality_config=Path("evaluation/config/default.yaml"),
        auto_upload_judge_video=False,
        tos_bucket=None,
        tos_region=None,
        tos_endpoint=None,
        tos_key_prefix=None,
        tos_presign_expires_seconds=None,
    )
    report_v2.run(args)
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "专项测试概况" not in html
    assert "专项测试</th><th>状态</th><th>结果摘要</th><th>操作" not in html
    assert "查看详情" not in html
    assert "未来评测工作" in html
    assert "专项评测方案仍在完善中，当前不纳入正式剪辑效果结论。" in html
    assert "对照人工预期结果，检查不同类型指令" in html
    assert "分别使用 1 / 2 / 4 不同FPS" in html
    assert "{&quot;" not in html
