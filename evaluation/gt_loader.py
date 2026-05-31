from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TOP_LEVEL_REQUIRED_FIELDS = {
    "video_id",
    "video_path",
    "video_type",
    "duration_seconds",
    "video_summary",
    "semantic_segments",
}

TOP_LEVEL_KNOWN_FIELDS = TOP_LEVEL_REQUIRED_FIELDS

SEGMENT_REQUIRED_FIELDS = {
    "segment_id",
    "start",
    "end",
    "description",
    "default_highlight_score",
    "avoid_by_default",
}

SEGMENT_KNOWN_FIELDS = SEGMENT_REQUIRED_FIELDS


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _format_errors(gt_path: Path, errors: list[str]) -> str:
    return f"GT 标注文件校验失败：{gt_path}\n" + "\n".join(f"- {error}" for error in errors)


def resolve_gt_path(input_video: Path, gt_dir: Path) -> Path:
    expected_gt_path = Path(gt_dir) / f"{Path(input_video).stem}.json"
    if not expected_gt_path.exists():
        raise FileNotFoundError(
            f"找不到输入视频对应的 GT 文件：input_video={input_video} "
            f"expected_gt_path={expected_gt_path}"
        )
    return expected_gt_path


def resolve_gt_path_by_video_id(video_id: str, gt_dir: Path) -> Path:
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("video_id 不能为空")
    candidate = video_id.strip()
    candidate_path = Path(candidate)
    if (
        candidate_path.is_absolute()
        or ".." in candidate_path.parts
        or len(candidate_path.parts) != 1
        or "/" in candidate
        or "\\" in candidate
    ):
        raise ValueError(f"video_id 存在目录穿越风险：{video_id}")
    expected_gt_path = Path(gt_dir) / f"{candidate}.json"
    if not expected_gt_path.exists():
        raise FileNotFoundError(f"找不到 video_id 对应的 GT 文件：video_id={video_id} expected_gt_path={expected_gt_path}")
    return expected_gt_path


def validate_gt_annotation(annotation: dict[str, Any], gt_path: Path, *, strict_timeline: bool = True) -> list[str]:
    if not isinstance(annotation, dict):
        raise ValueError(f"GT 根节点必须是 JSON object：{gt_path}")

    errors: list[str] = []
    warnings: list[str] = []

    missing_top_fields = sorted(field for field in TOP_LEVEL_REQUIRED_FIELDS if field not in annotation)
    if missing_top_fields:
        errors.append(f"缺少顶层必填字段：{', '.join(missing_top_fields)}")

    video_id = annotation.get("video_id")
    video_path = annotation.get("video_path")
    video_type = annotation.get("video_type")
    duration_seconds = annotation.get("duration_seconds")
    video_summary = annotation.get("video_summary")
    semantic_segments = annotation.get("semantic_segments")

    if "video_id" in annotation and not _is_non_empty_string(video_id):
        errors.append("video_id 不能为空")
    if "video_path" in annotation and not _is_non_empty_string(video_path):
        errors.append("video_path 不能为空")
    if "video_type" in annotation and not _is_non_empty_string(video_type):
        errors.append("video_type 不能为空")
    if "duration_seconds" in annotation:
        if not _is_int(duration_seconds):
            errors.append("duration_seconds 必须是整数")
        elif duration_seconds <= 0:
            errors.append("duration_seconds 必须大于 0")
    if "video_summary" in annotation and not _is_non_empty_string(video_summary):
        errors.append("video_summary 不能为空")
    if "semantic_segments" in annotation:
        if not isinstance(semantic_segments, list):
            errors.append("semantic_segments 必须是列表")
        elif not semantic_segments:
            errors.append("semantic_segments 不能为空")

    if isinstance(video_type, str) and video_type == "other":
        warnings.append("video_type 为 other，请确认是否需要更具体的视频类型")

    extra_top_fields = sorted(set(annotation.keys()) - TOP_LEVEL_KNOWN_FIELDS)
    if extra_top_fields:
        warnings.append(f"存在额外顶层字段：{', '.join(extra_top_fields)}")

    if isinstance(semantic_segments, list):
        seen_segment_ids: set[str] = set()
        previous_end: int | None = None
        for index, segment in enumerate(semantic_segments):
            prefix = f"semantic_segments[{index}]"
            if not isinstance(segment, dict):
                errors.append(f"{prefix} 必须是 object")
                continue

            missing_segment_fields = sorted(field for field in SEGMENT_REQUIRED_FIELDS if field not in segment)
            if missing_segment_fields:
                errors.append(f"{prefix} 缺少必填字段：{', '.join(missing_segment_fields)}")

            segment_id = segment.get("segment_id")
            start = segment.get("start")
            end = segment.get("end")
            description = segment.get("description")
            score = segment.get("default_highlight_score")
            avoid_by_default = segment.get("avoid_by_default")

            if "segment_id" in segment:
                if not _is_non_empty_string(segment_id):
                    errors.append(f"{prefix}.segment_id 不能为空")
                elif segment_id in seen_segment_ids:
                    errors.append(f"{prefix}.segment_id 重复：{segment_id}")
                else:
                    seen_segment_ids.add(segment_id)

            start_is_int = _is_int(start)
            end_is_int = _is_int(end)
            if "start" in segment and not start_is_int:
                errors.append(f"{prefix}.start 必须是整数")
            if "end" in segment and not end_is_int:
                errors.append(f"{prefix}.end 必须是整数")
            if start_is_int and end_is_int:
                if start < 0:
                    errors.append(f"{prefix}.start 不能小于 0")
                if start >= end:
                    errors.append(f"{prefix}.start 必须小于 end")
                if _is_int(duration_seconds) and end > duration_seconds:
                    errors.append(f"{prefix}.end 不能大于 duration_seconds")
                if strict_timeline:
                    if index == 0 and start != 0:
                        errors.append(f"{prefix}.start 必须从 0 开始")
                    if previous_end is not None and start != previous_end:
                        errors.append(f"{prefix}.start 必须等于前一个片段 end，不能有空白或重叠")
                    if (
                        _is_int(duration_seconds)
                        and index == len(semantic_segments) - 1
                        and end != duration_seconds
                    ):
                        errors.append(f"{prefix}.end 必须等于 duration_seconds")
                else:
                    if previous_end is not None and start < previous_end:
                        warnings.append(f"{prefix} 与前一个 semantic segment 存在时间重叠")
                    if previous_end is not None and start > previous_end:
                        warnings.append(f"{prefix} 与前一个 semantic segment 存在空白区间")
                previous_end = end

            if "description" in segment and not _is_non_empty_string(description):
                errors.append(f"{prefix}.description 不能为空")
            if "default_highlight_score" in segment:
                if not _is_int(score):
                    errors.append(f"{prefix}.default_highlight_score 必须是整数")
                elif score < 1 or score > 5:
                    errors.append(f"{prefix}.default_highlight_score 必须在 [1, 5] 范围内")
            if "avoid_by_default" in segment and not isinstance(avoid_by_default, bool):
                errors.append(f"{prefix}.avoid_by_default 必须是布尔值")

            extra_segment_fields = sorted(set(segment.keys()) - SEGMENT_KNOWN_FIELDS)
            if extra_segment_fields:
                warnings.append(f"{prefix} 存在额外字段：{', '.join(extra_segment_fields)}")

    if errors:
        raise ValueError(_format_errors(gt_path, errors))
    return warnings


def load_gt_file(gt_path: Path) -> dict[str, Any]:
    path = Path(gt_path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"读取 GT 文件失败：{path}") from exc
    try:
        annotation = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GT 文件不是合法 JSON object：{path}；解析错误：{exc}") from exc
    if not isinstance(annotation, dict):
        raise ValueError(f"GT 文件根节点必须是 JSON object：{path}")
    validate_gt_annotation(annotation, path)
    return annotation


def load_gt_by_video_id(video_id: str, gt_dir: Path) -> dict[str, Any]:
    return load_gt_file(resolve_gt_path_by_video_id(video_id, gt_dir))


def load_gt_by_input_video(input_video: Path, gt_dir: Path) -> dict[str, Any]:
    return load_gt_file(resolve_gt_path(input_video, gt_dir))


def load_gt_dir(gt_dir: Path) -> dict[str, dict[str, Any]]:
    root = Path(gt_dir)
    annotations: dict[str, dict[str, Any]] = {}
    source_files: dict[str, Path] = {}
    for gt_path in sorted(root.glob("*.json")):
        annotation = load_gt_file(gt_path)
        video_id = str(annotation["video_id"])
        if video_id in annotations:
            raise ValueError(
                f"发现重复 video_id={video_id} 的 GT 文件："
                f"{source_files[video_id]} 与 {gt_path}"
            )
        if gt_path.stem != video_id:
            raise ValueError(
                f"GT 文件名 stem 与内部 video_id 不一致：file={gt_path} "
                f"file_stem={gt_path.stem} video_id={video_id}"
            )
        annotations[video_id] = annotation
        source_files[video_id] = gt_path
    return annotations
