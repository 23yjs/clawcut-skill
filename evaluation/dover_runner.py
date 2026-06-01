from __future__ import annotations

import argparse
import json
import pickle as pkl
import sys
import time
from pathlib import Path
from typing import Any


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("dover_status") == "success" else 1


def _fuse_results(results: list[float]) -> float:
    import numpy as np

    fused_logit = (results[0] - 0.1107) / 0.07355 * 0.6104 + (results[1] + 0.08285) / 0.03774 * 0.3896
    return float(1 / (1 + np.exp(-fused_logit)))


def _gaussian_rescale(values: Any) -> Any:
    import numpy as np

    return (values - np.mean(values)) / np.std(values)


def _uniform_rescale(values: Any) -> Any:
    import numpy as np

    return np.arange(len(values))[np.argsort(values).argsort()] / len(values)


def _reference_percentiles(repo_dir: Path, raw_results: list[float]) -> dict[str, dict[str, int]] | None:
    import numpy as np

    dbs = {
        "livevqc": "LIVE_VQC",
        "kv1k": "KoNViD-1k",
        "ltest": "LSVQ_Test",
        "l1080p": "LSVQ_1080P",
        "ytugc": "YouTube_UGC",
    }
    predictions_dir = repo_dir / "dover_predictions"
    if not predictions_dir.exists():
        return None
    percentiles: dict[str, dict[str, int]] = {}
    for abbr, full_name in dbs.items():
        path = predictions_dir / f"val-{abbr}.pkl"
        if not path.exists():
            return None
        with path.open("rb") as handle:
            labels = pkl.load(handle)
        aqe_score_set = labels["resize"]
        tqe_score_set = labels["fragments"]
        tqe_score_set_p = np.concatenate((np.array([raw_results[0]]), tqe_score_set), 0)
        aqe_score_set_p = np.concatenate((np.array([raw_results[1]]), aqe_score_set), 0)
        # Compute gaussian values to match official script side effects and validate arrays.
        _gaussian_rescale(tqe_score_set_p)[0]
        _gaussian_rescale(aqe_score_set_p)[0]
        percentiles[full_name] = {
            "technical_percentile": int(_uniform_rescale(tqe_score_set_p)[0] * 100),
            "visual_aesthetic_percentile": int(_uniform_rescale(aqe_score_set_p)[0] * 100),
        }
    return percentiles


def _opencv_spatial_temporal_view_decomposition(
    *,
    video_path: Path,
    sample_types: dict[str, Any],
    samplers: dict[str, Any],
    is_train: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    import cv2
    import numpy as np
    import torch
    from dover.datasets.dover_datasets import get_single_view

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV 无法打开视频：{video_path}")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        raise RuntimeError(f"OpenCV 无法读取视频帧数：{video_path}")

    frame_inds: dict[str, Any] = {}
    all_frame_inds = []
    for stype in samplers:
        frame_inds[stype] = samplers[stype](frame_count, is_train)
        all_frame_inds.append(frame_inds[stype])
    all_frame_inds = np.concatenate(all_frame_inds, 0)

    frame_dict: dict[int, Any] = {}
    for raw_idx in np.unique(all_frame_inds):
        idx = int(raw_idx)
        capture.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame_bgr = capture.read()
        if not ok or frame_bgr is None:
            raise RuntimeError(f"OpenCV 读取视频帧失败：frame={idx} video={video_path}")
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_dict[idx] = torch.from_numpy(frame_rgb.copy())
    capture.release()

    video = {}
    for stype in samplers:
        imgs = [frame_dict[int(idx)] for idx in frame_inds[stype]]
        video[stype] = torch.stack(imgs, 0).permute(3, 0, 1, 2)

    sampled_video = {}
    for stype, sopt in sample_types.items():
        sampled_video[stype] = get_single_view(video[stype], stype, **sopt)
    return sampled_video, frame_inds


def _run_dover(video_path: Path, repo_dir: Path, opt_path: Path, device: str) -> dict[str, Any]:
    import numpy as np
    import torch
    import yaml
    from dover.datasets import UnifiedFrameSampler
    from dover.models import DOVER

    mean, std = (
        torch.FloatTensor([123.675, 116.28, 103.53]),
        torch.FloatTensor([58.395, 57.12, 57.375]),
    )

    with opt_path.open("r", encoding="utf-8") as handle:
        opt = yaml.safe_load(handle)

    evaluator = DOVER(**opt["model"]["args"]).to(device)
    load_path = Path(opt["test_load_path"])
    if not load_path.is_absolute():
        load_path = repo_dir / load_path
    evaluator.load_state_dict(torch.load(load_path, map_location=device))
    evaluator.eval()

    dopt = opt["data"]["val-l1080p"]["args"]
    temporal_samplers = {}
    for stype, sopt in dopt["sample_types"].items():
        if "t_frag" not in sopt:
            temporal_samplers[stype] = UnifiedFrameSampler(
                sopt["clip_len"], sopt["num_clips"], sopt["frame_interval"]
            )
        else:
            temporal_samplers[stype] = UnifiedFrameSampler(
                sopt["clip_len"] // sopt["t_frag"],
                sopt["t_frag"],
                sopt["frame_interval"],
                sopt["num_clips"],
            )

    views, _ = _opencv_spatial_temporal_view_decomposition(
        video_path=video_path,
        sample_types=dopt["sample_types"],
        samplers=temporal_samplers,
    )

    for key, value in views.items():
        num_clips = dopt["sample_types"][key].get("num_clips", 1)
        views[key] = (
            ((value.permute(1, 2, 3, 0) - mean) / std)
            .permute(3, 0, 1, 2)
            .reshape(value.shape[0], num_clips, -1, *value.shape[2:])
            .transpose(0, 1)
            .to(device)
        )

    with torch.no_grad():
        raw_results = [float(result.mean().item()) for result in evaluator(views)]

    return {
        "dover_status": "success",
        "dover_model": str(opt.get("name", "dover")),
        "dover_device": device,
        "dover_fused_overall_score": _fuse_results(raw_results),
        "dover_raw_technical_score": raw_results[0],
        "dover_raw_visual_aesthetic_score": raw_results[1],
        "dover_reference_percentiles": _reference_percentiles(repo_dir, raw_results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DOVER quality inference and emit JSON.")
    parser.add_argument("--video_path", type=Path, required=True)
    parser.add_argument("--repo_dir", type=Path, required=True)
    parser.add_argument("--opt_path", type=Path)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    if not args.video_path.exists():
        return _emit({"dover_status": "failed", "dover_error": f"视频不存在：{args.video_path}"})
    if not args.repo_dir.exists():
        return _emit({"dover_status": "unavailable", "dover_error": f"DOVER repo 不存在：{args.repo_dir}"})
    opt_path = args.opt_path or (args.repo_dir / "dover-mobile.yml")
    if not opt_path.exists():
        return _emit({"dover_status": "unavailable", "dover_error": f"DOVER opt 文件不存在：{opt_path}"})

    sys.path.insert(0, str(args.repo_dir))

    try:
        import cv2  # noqa: F401
        import torch  # noqa: F401
        import yaml  # noqa: F401
    except Exception as exc:
        return _emit({"dover_status": "unavailable", "dover_error": f"DOVER 依赖不可用：{exc}"})

    started = time.monotonic()
    try:
        result = _run_dover(args.video_path, args.repo_dir, opt_path, args.device)
    except Exception as exc:
        return _emit({"dover_status": "failed", "dover_error": f"DOVER 推理失败：{exc}"})
    result["dover_runtime_seconds"] = round(time.monotonic() - started, 3)
    return _emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
