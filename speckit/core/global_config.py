"""
Global speckit config — multi-service orchestrator.

Reads global.sdd.config.yml from the monorepo root.
Each service entry must have its own sdd.config.yml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from speckit.core.config import SpeckitConfig

GLOBAL_CONFIG_FILENAME = "global.sdd.config.yml"


@dataclass
class ServiceEntry:
    name: str
    path: Path              # resolved absolute path to service root
    depends_on: list[str]
    config: "SpeckitConfig"  # loaded per-service sdd.config.yml


@dataclass
class OrchestratorAgentConfig:
    model: str = "claude-sonnet-4-6"
    judge_threshold: float = 0.85
    max_judge_iterations: int = 3


@dataclass
class GlobalSpeckitConfig:
    project_name: str
    root: Path
    global_specs: Path
    contracts_dir: Path
    runs_dir: Path
    agent: OrchestratorAgentConfig
    services: list[ServiceEntry] = field(default_factory=list)


def load_global_config(root: Path) -> GlobalSpeckitConfig:
    """
    Load global.sdd.config.yml from root and return a GlobalSpeckitConfig.

    Resolves all paths relative to root.
    Loads each service's sdd.config.yml via load_config().
    Raises FileNotFoundError if the global config or any service config is missing.
    """
    from speckit.core.config import load_config

    config_path = root / GLOBAL_CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {GLOBAL_CONFIG_FILENAME} found in {root}.\n"
            "Create one with the template from speckit/templates/files.py GLOBAL_CONFIG_YML,\n"
            "or copy the example:\n\n"
            "  global.sdd.config.yml at your monorepo root\n\n"
            "Then run: speckit orchestrate --name 'feature name'"
        )

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    agent_raw = raw.get("agent", {})
    agent_cfg = OrchestratorAgentConfig(
        model=agent_raw.get("model", "claude-sonnet-4-6"),
        judge_threshold=float(agent_raw.get("judge_threshold", 0.85)),
        max_judge_iterations=int(agent_raw.get("max_judge_iterations", 3)),
    )

    global_specs = (root / raw.get("global_specs", "./global-specs")).resolve()
    contracts_dir = (root / raw.get("contracts_dir", "./global-specs/contracts")).resolve()
    runs_dir = (root / raw.get("runs_dir", "./runs")).resolve()

    services: list[ServiceEntry] = []
    for svc in raw.get("services", []):
        name = svc["name"]
        svc_path = (root / svc["path"]).resolve()
        depends_on = svc.get("depends_on") or []

        try:
            svc_config = load_config(svc_path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Service '{name}' at {svc_path} has no sdd.config.yml.\n"
                f"Run 'speckit init' inside {svc_path} first."
            ) from None

        services.append(ServiceEntry(
            name=name,
            path=svc_path,
            depends_on=depends_on,
            config=svc_config,
        ))

    # Validate depends_on references at load time so typos surface immediately
    service_names = {s.name for s in services}
    for svc in services:
        for dep in svc.depends_on:
            if dep not in service_names:
                raise ValueError(
                    f"Service '{svc.name}' depends_on '{dep}', "
                    f"but '{dep}' is not registered in global.sdd.config.yml.\n"
                    f"Registered services: {sorted(service_names)}"
                )

    return GlobalSpeckitConfig(
        project_name=raw.get("project_name", root.name),
        root=root,
        global_specs=global_specs,
        contracts_dir=contracts_dir,
        runs_dir=runs_dir,
        agent=agent_cfg,
        services=services,
    )


def save_global_config(global_config: GlobalSpeckitConfig) -> None:
    """Re-write global.sdd.config.yml after services list is modified."""
    config_path = global_config.root / GLOBAL_CONFIG_FILENAME
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Rebuild services list from current ServiceEntry objects
    raw["services"] = [
        {
            "name": svc.name,
            "path": str(svc.path.relative_to(global_config.root)),
            "depends_on": svc.depends_on,
        }
        for svc in global_config.services
    ]

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def topological_sort(services: list[ServiceEntry]) -> list[ServiceEntry]:
    """
    Return services in dependency order (dependencies first).
    Raises ValueError on circular dependencies.
    """
    name_map = {s.name: s for s in services}
    visited: set[str] = set()
    in_progress: set[str] = set()
    order: list[ServiceEntry] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in in_progress:
            raise ValueError(
                f"Circular dependency detected involving service '{name}'.\n"
                "Check depends_on in global.sdd.config.yml."
            )
        in_progress.add(name)
        svc = name_map.get(name)
        if svc:
            for dep in svc.depends_on:
                if dep in name_map:
                    visit(dep)
        in_progress.discard(name)
        visited.add(name)
        if svc:
            order.append(svc)

    for svc in services:
        visit(svc.name)

    return order
