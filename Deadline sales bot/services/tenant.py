"""
Tenant config loader (Phase 1, 2026-05-26).

A "tenant" = one customer of the Deadline Sales Bot skeleton.
Right now we have exactly one tenant: ourselves (slug = "deadline-corp").
When the skeleton is extracted for resale, each new client gets their own
tenants/<slug>/ directory with their own config.yaml, system_prompt.md and
kb/. The code path is identical — only TENANT_SLUG env var differs.

Layout expected (relative to project root = "Deadline sales bot/"):

    tenants/
    └── <slug>/
        ├── config.yaml          ← all tunable behaviour
        ├── system_prompt.md     ← bot tone-of-voice (path inside config.yaml)
        └── kb/                  ← optional, default = project_root/kb/

Loader is intentionally minimal: parse YAML, read prompt file, return a
Tenant dataclass with a `.get(*keys)` walker for nested access. No pydantic
schema validation — the tenant config is operator-authored, not user input.
If a key is missing we either fall back to a default in code or fail loudly
at startup (which is exactly the right time to fail).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Project root = parent of services/ (i.e. "Deadline sales bot/")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Tenant:
    """A loaded tenant configuration.

    Fields prefixed with raw_ are direct YAML reads — use .get() for safe
    nested access (returns None or default if key path missing).
    """
    slug: str
    display_name: str
    languages: list[str]
    system_prompt: str          # full text of system_prompt.md, with {placeholder} markers intact
    raw_config: dict[str, Any]  # everything else — funnel, scoring, temperature, etc.
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)

    def get(self, *keys: str, default: Any = None) -> Any:
        """Walk nested config: tenant.get('crm', 'pipeline_name').

        Returns `default` if any key in the path is missing or any
        intermediate node isn't a dict.
        """
        node: Any = self.raw_config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def kb_path(self) -> Path:
        """Absolute path to the tenant's knowledge base directory.

        Defaults to <project_root>/kb if not specified in config — this
        preserves backwards compatibility with the pre-tenant layout.
        """
        kb_rel = self.get("kb", "path", default="kb")
        return self.project_root / kb_rel

    @property
    def crm_pipeline_name(self) -> str:
        return self.get("crm", "pipeline_name", default="Deadline Sales")

    @property
    def funnel_stages(self) -> list[str]:
        return self.get("funnel", "stages", default=[])

    @property
    def lost_reasons(self) -> list[str]:
        return self.get("funnel", "lost_reasons", default=[])


def load_tenant(slug: str, project_root: Optional[Path] = None) -> Tenant:
    """Load a tenant from tenants/<slug>/.

    Raises FileNotFoundError if the directory, config.yaml, or referenced
    system_prompt.md is missing. We want startup to fail loudly rather than
    run with a half-loaded tenant.

    Example:
        tenant = load_tenant("deadline-corp")
        prompt = tenant.system_prompt
        provider = tenant.get("crm", "provider_env")  # 'CRM_PROVIDER'
    """
    root = project_root or _PROJECT_ROOT

    tenant_dir = root / "tenants" / slug
    if not tenant_dir.is_dir():
        raise FileNotFoundError(
            f"Tenant directory not found: {tenant_dir}. "
            f"Did you forget to create tenants/{slug}/?"
        )

    config_path = tenant_dir / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Tenant config.yaml not found: {config_path}")

    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError(
            f"Tenant config.yaml must be a YAML mapping at top level: {config_path}"
        )

    # Resolve system_prompt — config gives a path relative to project root
    prompt_path_rel = raw.get("brand", {}).get("system_prompt_path")
    if not prompt_path_rel:
        raise ValueError(
            f"config.yaml at {config_path} missing brand.system_prompt_path"
        )
    prompt_path = root / prompt_path_rel
    if not prompt_path.is_file():
        raise FileNotFoundError(
            f"System prompt referenced by config not found: {prompt_path}"
        )

    system_prompt = prompt_path.read_text(encoding="utf-8")

    return Tenant(
        slug=raw.get("tenant_slug", slug),
        display_name=raw.get("display_name", slug),
        languages=raw.get("languages", ["en"]),
        system_prompt=system_prompt,
        raw_config=raw,
        project_root=root,
    )
