from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only without optional dependency.
    yaml = None


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config" / "default.yaml"


class SkillError(RuntimeError):
    """用于抛出可预期、可展示给用户的 Skill 错误。"""


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_scalar(value: str) -> Any:
    if value in ('""', "''"):
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section_name = line[:-1].strip()
            current_section = {}
            result[section_name] = current_section
            continue
        if current_section is not None and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            current_section[key.strip()] = _parse_scalar(value.strip())
            continue
        raise SkillError(f"配置文件语法暂不支持：{path}: {raw_line}")
    return result


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or DEFAULT_CONFIG_PATH
    if yaml is None:
        return _load_simple_yaml(path)
    with path.open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def setup_logger(log_path: Path) -> logging.Logger:
    ensure_dir(log_path.parent)
    logger = logging.getLogger("clawcut-video-highlight")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def run_command(args: list[str], logger: logging.Logger | None = None) -> subprocess.CompletedProcess[str]:
    if logger:
        logger.info("执行外部命令：%s", " ".join(args))
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SkillError(f"找不到外部命令：{args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise SkillError(f"外部命令执行失败：{' '.join(args)}\n{details}") from exc
    if logger:
        logger.info("外部命令执行完成：returncode=%s", completed.returncode)
    return completed


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def seconds(value: float) -> str:
    return f"{value:.3f}"


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise SkillError(f"{label} 不存在：{path}")
    if not path.is_file():
        raise SkillError(f"{label} 不是文件：{path}")
