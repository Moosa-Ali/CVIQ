import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path(os.environ.get("CVMOD_DATA_DIR", str(BASE_DIR / ".cvmod")))
DEFAULT_TEMPLATES_DIR = Path(os.environ.get("CVMOD_TEMPLATES_DIR", str(BASE_DIR / "Templates")))


def _resolve_templates_dir(path: Path) -> Path:
    """Case-tolerant resolution of the templates directory.

    The repo ships ``templates/`` (lowercase) while the default config says
    ``Templates`` — fine on Windows, broken on case-sensitive filesystems. If
    the exact path is missing, look for a sibling directory with the same name
    in any casing under the same parent.
    """
    path = Path(path)
    if path.exists():
        return path
    parent = path.parent
    if parent.exists():
        target = path.name.lower()
        for child in parent.iterdir():
            if child.is_dir() and child.name.lower() == target:
                return child
    return path


class Settings(BaseSettings):
    data_dir: Path = DEFAULT_DATA_DIR
    templates_dir: Path = DEFAULT_TEMPLATES_DIR
    default_template: str = "modern"
    default_accent: str = "#2563eb"
    openrouter_default_model: str = "anthropic/claude-sonnet-4-6"
    bedrock_default_model: str = "anthropic.claude-sonnet-4-5-v2-0"
    openrouter_endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    cors_origins: list[str] = ["*"]

    model_config = SettingsConfigDict(env_prefix="CVMOD_", extra="ignore")

    @field_validator("templates_dir", mode="after")
    @classmethod
    def _templates_dir_case_tolerant(cls, v: Path) -> Path:
        return _resolve_templates_dir(Path(v))


settings = Settings()
