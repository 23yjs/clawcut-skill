from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluation.gt_loader import (
    load_gt_by_input_video,
    load_gt_by_video_id,
    load_gt_dir,
    load_gt_file,
    resolve_gt_path,
    validate_gt_annotation,
)


def _valid_annotation(**overrides: Any) -> dict[str, Any]:
    annotation: dict[str, Any] = {
        "video_id": "demo",
        "video_path": "data/input/demo.MP4",
        "video_type": "ecommerce_product",
        "duration_seconds": 20,
        "video_summary": "一个用于测试的电商视频。",
        "semantic_segments": [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 8,
                "description": "展示商品外观。",
                "default_highlight_score": 4,
                "avoid_by_default": False,
            }
        ],
    }
    annotation.update(overrides)
    return annotation


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class GTLoaderTests(unittest.TestCase):
    def test_load_gt_by_input_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_dir = Path(tmp)
            _write_json(gt_dir / "demo.json", _valid_annotation())
            annotation = load_gt_by_input_video(Path("data/input/demo.MP4"), gt_dir)
            self.assertEqual(annotation["video_id"], "demo")

    def test_load_gt_by_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_dir = Path(tmp)
            _write_json(gt_dir / "demo.json", _valid_annotation())
            annotation = load_gt_by_video_id("demo", gt_dir)
            self.assertEqual(annotation["video_path"], "data/input/demo.MP4")

    def test_missing_gt_file_mentions_input_and_expected_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_dir = Path(tmp)
            with self.assertRaises(FileNotFoundError) as context:
                resolve_gt_path(Path("data/input/demo.MP4"), gt_dir)
            message = str(context.exception)
            self.assertIn("input_video", message)
            self.assertIn("expected_gt_path", message)

    def test_rejects_unsafe_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for video_id in ["", "../demo", "/tmp/demo", "group/demo", "group\\demo"]:
                with self.subTest(video_id=video_id):
                    with self.assertRaises(ValueError):
                        load_gt_by_video_id(video_id, Path(tmp))

    def test_filename_must_match_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_dir = Path(tmp)
            _write_json(gt_dir / "demo.json", _valid_annotation(video_id="another_demo"))
            with self.assertRaises(ValueError) as context:
                load_gt_dir(gt_dir)
            self.assertIn("文件名 stem 与内部 video_id 不一致", str(context.exception))

    def test_duplicate_video_id_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_dir = Path(tmp)
            _write_json(gt_dir / "demo.json", _valid_annotation(video_id="demo"))
            _write_json(gt_dir / "demo_copy.json", _valid_annotation(video_id="demo"))
            with self.assertRaises(ValueError) as context:
                load_gt_dir(gt_dir)
            self.assertIn("重复 video_id=demo", str(context.exception))

    def test_invalid_timestamps_error(self) -> None:
        cases = [
            {"start": 1.5, "end": 8},
            {"start": 1, "end": 8.5},
            {"start": 8, "end": 8},
            {"start": -1, "end": 8},
            {"start": 1, "end": 21},
        ]
        for segment_patch in cases:
            with self.subTest(segment_patch=segment_patch):
                annotation = _valid_annotation()
                annotation["semantic_segments"][0].update(segment_patch)
                with self.assertRaises(ValueError):
                    validate_gt_annotation(annotation, Path("demo.json"))

    def test_invalid_default_highlight_score_error(self) -> None:
        for score in [0, 6, 4.5]:
            with self.subTest(score=score):
                annotation = _valid_annotation()
                annotation["semantic_segments"][0]["default_highlight_score"] = score
                with self.assertRaises(ValueError):
                    validate_gt_annotation(annotation, Path("demo.json"))

    def test_allows_extra_fields(self) -> None:
        annotation = _valid_annotation(
            review_notes=["人工复核备注"],
            semantic_segments=[
                {
                    "segment_id": "seg_001",
                    "start": 0,
                    "end": 8,
                    "description": "展示商品外观。",
                    "default_highlight_score": 4,
                    "avoid_by_default": False,
                    "tags": ["product_appearance"],
                    "attribute_notes": {"objects": ["商品"]},
                    "review_notes": ["保留旧字段"],
                }
            ],
        )
        warnings = validate_gt_annotation(annotation, Path("demo.json"))
        self.assertTrue(any("额外顶层字段" in warning for warning in warnings))
        self.assertTrue(any("额外字段" in warning for warning in warnings))

    def test_gap_and_small_overlap_only_warn(self) -> None:
        annotation = _valid_annotation(
            semantic_segments=[
                {
                    "segment_id": "seg_001",
                    "start": 0,
                    "end": 8,
                    "description": "第一段。",
                    "default_highlight_score": 4,
                    "avoid_by_default": False,
                },
                {
                    "segment_id": "seg_002",
                    "start": 10,
                    "end": 15,
                    "description": "中间有空白区间。",
                    "default_highlight_score": 3,
                    "avoid_by_default": False,
                },
                {
                    "segment_id": "seg_003",
                    "start": 14,
                    "end": 18,
                    "description": "与上一段少量重叠。",
                    "default_highlight_score": 3,
                    "avoid_by_default": False,
                },
            ]
        )
        warnings = validate_gt_annotation(annotation, Path("demo.json"))
        self.assertTrue(any("时间重叠" in warning for warning in warnings))

    def test_load_gt_file_rejects_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt_path = Path(tmp) / "demo.json"
            gt_path.write_text("{}\n{}\n", encoding="utf-8")
            with self.assertRaises(ValueError) as context:
                load_gt_file(gt_path)
            self.assertIn(str(gt_path), str(context.exception))


if __name__ == "__main__":
    unittest.main()
