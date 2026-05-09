"""
Config — loads and validates sdd.config.yml
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG_FILENAME = "sdd.config.yml"


class ProjectMode(str, Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD_WITH_SPECS = "brownfield-with-specs"
    BROWNFIELD_NO_SPECS = "brownfield-no-specs"


class VectorDBProvider(str, Enum):
    SUPABASE = "supabase"
    QDRANT = "qdrant"
    NONE = "none"


class TestingConfig(BaseModel):
    backend_runner: str = "pytest"
    frontend_runner: str = "vitest"
    api_test_tool: str = "curl"
    coverage_threshold: int = 80


class AgentConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    judge_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_judge_iterations: int = Field(default=5, ge=1, le=10)
    max_spec_read_files: int = Field(default=5, ge=1, le=20)


class VectorDBConfig(BaseModel):
    provider: VectorDBProvider = VectorDBProvider.NONE
    index_name: str = ""
    connection_url: str = ""


class GitHubConfig(BaseModel):
    default_branch: str = "main"
    require_spec_update: bool = True
    bug_labels: list[str] = Field(default_factory=lambda: ["bug", "fix-needed"])
    feature_labels: list[str] = Field(default_factory=lambda: ["feature", "enhancement"])


class PathsConfig(BaseModel):
    specs: str = "./specs"
    runs: str = "./runs"
    src: str = "./src"
    tests: str = "./tests"


class NotificationsConfig(BaseModel):
    slack_channel: str = "#engineering"
    slack_webhook_url: str = ""
    notify_on: list[str] = Field(
        default_factory=lambda: ["pr_opened", "judge_failed", "tests_failed"]
    )


class SpeckitConfig(BaseModel):
    # Project
    project_name: str
    repo: str = ""
    mode: ProjectMode
    primary_language: str = "python"
    description: str = ""

    # Sub-configs
    paths: PathsConfig = Field(default_factory=PathsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    vector_db: VectorDBConfig = Field(default_factory=VectorDBConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, v: str) -> str:
        if v and "/" not in v:
            raise ValueError("repo must be in format org/repo-name")
        return v


def load_config(project_root: Path) -> SpeckitConfig:
    """Load and validate sdd.config.yml from the project root."""
    config_path = project_root / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {project_root}. "
            "Run 'speckit init' first."
        )
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return SpeckitConfig(**raw)


def save_config(config: SpeckitConfig, project_root: Path) -> Path:
    """Write config to sdd.config.yml."""
    config_path = project_root / CONFIG_FILENAME
    data = config.model_dump()
    # Convert enums to values for clean YAML
    data["mode"] = config.mode.value
    data["vector_db"]["provider"] = config.vector_db.provider.value
    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return config_path


def config_exists(project_root: Path) -> bool:
    return (project_root / CONFIG_FILENAME).exists()
