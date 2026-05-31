from __future__ import annotations

from typing import Any, Iterable


Interval = dict[str, float]


def _coerce_interval(interval: Any) -> Interval:
    if isinstance(interval, dict):
        start = interval.get("start")
        end = interval.get("end")
    else:
        start, end = interval
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法时间区间：{interval}") from exc
    if start_f < 0:
        raise ValueError(f"时间区间 start 不能小于 0：{interval}")
    if start_f >= end_f:
        raise ValueError(f"时间区间必须满足 start < end：{interval}")
    return {"start": start_f, "end": end_f}


def normalize_intervals(intervals: Iterable[Any]) -> list[Interval]:
    normalized = sorted((_coerce_interval(interval) for interval in intervals), key=lambda item: (item["start"], item["end"]))
    if not normalized:
        return []
    merged: list[Interval] = [dict(normalized[0])]
    for interval in normalized[1:]:
        last = merged[-1]
        if interval["start"] <= last["end"]:
            last["end"] = max(last["end"], interval["end"])
        else:
            merged.append(dict(interval))
    return merged


def intervals_duration(intervals: Iterable[Any]) -> float:
    return round(sum(interval["end"] - interval["start"] for interval in normalize_intervals(intervals)), 6)


def intersect_intervals(left: Iterable[Any], right: Iterable[Any]) -> list[Interval]:
    left_norm = normalize_intervals(left)
    right_norm = normalize_intervals(right)
    intersections: list[Interval] = []
    i = 0
    j = 0
    while i < len(left_norm) and j < len(right_norm):
        start = max(left_norm[i]["start"], right_norm[j]["start"])
        end = min(left_norm[i]["end"], right_norm[j]["end"])
        if start < end:
            intersections.append({"start": start, "end": end})
        if left_norm[i]["end"] <= right_norm[j]["end"]:
            i += 1
        else:
            j += 1
    return intersections


def overlap_duration_between(left: Iterable[Any], right: Iterable[Any]) -> float:
    return intervals_duration(intersect_intervals(left, right))
