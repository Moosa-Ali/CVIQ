import json
from pathlib import Path

from .base import apply_env_overrides, LLMConfig


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "config.json"


def load_config(data_dir: Path) -> LLMConfig:
    path = _path(data_dir)
    if path.exists():
        try:
            return LLMConfig(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return LLMConfig()


def save_config(cfg: LLMConfig, data_dir: Path) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")


def redact(cfg: LLMConfig) -> LLMConfig:
    return cfg.redacted()


def effective(cfg: LLMConfig) -> LLMConfig:
    return apply_env_overrides(cfg.model_copy(deep=True))
