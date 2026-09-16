"""Configuration loading for the AI Governance workshop notebooks.

Precedence: environment variables -> `.env` file -> interactive `input()`
prompt. Any value collected interactively is persisted back to `.env` so
subsequent runs are non-interactive. Secrets are never printed.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv, set_key

from .auth import get_current_subscription_id, mask_secret

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# Fields that should never be echoed back in plain text.
SECRET_FIELDS = {"aoai_key", "app_insights_connection_string"}

# Supported Azure OpenAI request surfaces:
#   "v1"      -> Microsoft Foundry / v1 surface: POST /openai/v1/chat/completions
#                with the deployment passed in the body as "model" and no
#                api-version query parameter.
#   "classic" -> Azure OpenAI surface: POST /openai/deployments/{deployment}
#                /chat/completions?api-version=...
API_STYLES = ("v1", "classic")


@dataclass
class WorkshopConfig:
    subscription_id: Optional[str] = None
    resource_group: str = ""
    apim_name: str = ""
    aoai_endpoint: str = ""
    aoai_deployment: str = ""
    aoai_key: Optional[str] = None
    aoai_api_version: str = "2024-10-21"
    aoai_api_style: str = "v1"
    app_insights_name: Optional[str] = None
    app_insights_resource_id: Optional[str] = None
    app_insights_connection_string: Optional[str] = None
    demo_run: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def as_display_dict(self) -> Dict[str, str]:
        """Return a dict safe for printing (secrets masked)."""
        data = asdict(self)
        for field_name in SECRET_FIELDS:
            if data.get(field_name):
                data[field_name] = mask_secret(data[field_name])
        return data


_ENV_KEYS = {
    "subscription_id": "AZURE_SUBSCRIPTION_ID",
    "resource_group": "APIM_RESOURCE_GROUP",
    "apim_name": "APIM_NAME",
    "aoai_endpoint": "AOAI_ENDPOINT",
    "aoai_deployment": "AOAI_DEPLOYMENT",
    "aoai_key": "AOAI_KEY",
    "aoai_api_version": "AOAI_API_VERSION",
    "aoai_api_style": "AOAI_API_STYLE",
    "app_insights_name": "APP_INSIGHTS_NAME",
    "app_insights_resource_id": "APP_INSIGHTS_RESOURCE_ID",
    "app_insights_connection_string": "APP_INSIGHTS_CONNECTION_STRING",
    "demo_run": "DEMO_RUN",
}


def _ensure_env_file() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.touch()


def _persist(key: str, value: str) -> None:
    _ensure_env_file()
    set_key(str(ENV_PATH), _ENV_KEYS[key], value, quote_mode="never")


def _prompt(field_name: str, label: str, default: str = "", secret: bool = False) -> str:
    prompt_default = f" [{default}]" if default else ""
    if secret:
        import getpass

        value = getpass.getpass(f"{label}{prompt_default} (input hidden): ")
    else:
        value = input(f"{label}{prompt_default}: ")
    value = value.strip() or default
    return value


def load_config(interactive: bool = True) -> WorkshopConfig:
    """Load configuration using env vars -> .env -> interactive prompts.

    Values obtained interactively are persisted to `.env` for future runs.
    """
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=False)

    cfg = WorkshopConfig()
    cfg.subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID") or get_current_subscription_id()
    cfg.resource_group = os.environ.get("APIM_RESOURCE_GROUP", "")
    cfg.apim_name = os.environ.get("APIM_NAME", "")
    cfg.aoai_endpoint = os.environ.get("AOAI_ENDPOINT", "")
    cfg.aoai_deployment = os.environ.get("AOAI_DEPLOYMENT", "")
    cfg.aoai_key = os.environ.get("AOAI_KEY") or None
    cfg.aoai_api_version = os.environ.get("AOAI_API_VERSION", cfg.aoai_api_version)
    cfg.aoai_api_style = (
        os.environ.get("AOAI_API_STYLE", "").strip().lower() or cfg.aoai_api_style
    )
    cfg.app_insights_name = os.environ.get("APP_INSIGHTS_NAME") or None
    cfg.app_insights_resource_id = os.environ.get("APP_INSIGHTS_RESOURCE_ID") or None
    cfg.app_insights_connection_string = (
        os.environ.get("APP_INSIGHTS_CONNECTION_STRING") or None
    )
    cfg.demo_run = os.environ.get("DEMO_RUN", "") or cfg.demo_run

    if not interactive:
        return cfg

    if not cfg.resource_group:
        cfg.resource_group = _prompt("resource_group", "Resource group containing the APIM instance")
        _persist("resource_group", cfg.resource_group)

    if not cfg.apim_name:
        cfg.apim_name = _prompt("apim_name", "APIM instance name")
        _persist("apim_name", cfg.apim_name)

    if not cfg.aoai_endpoint:
        cfg.aoai_endpoint = _prompt(
            "aoai_endpoint", "Azure OpenAI endpoint (e.g. https://<name>.openai.azure.com/)"
        )
        _persist("aoai_endpoint", cfg.aoai_endpoint)

    if not cfg.aoai_deployment:
        cfg.aoai_deployment = _prompt("aoai_deployment", "Azure OpenAI chat deployment name")
        _persist("aoai_deployment", cfg.aoai_deployment)

    # AOAI key is optional (managed identity preferred); only ask once so
    # reruns don't nag if the user deliberately left it blank for MI.
    if cfg.aoai_key is None and "AOAI_KEY" not in _dotenv_keys():
        answer = _prompt(
            "aoai_key",
            "Azure OpenAI API key (leave blank to use managed identity)",
            secret=True,
        )
        cfg.aoai_key = answer or None
        _persist("aoai_key", cfg.aoai_key or "")

    if "AOAI_API_STYLE" not in _dotenv_keys():
        answer = _prompt(
            "aoai_api_style",
            "Azure OpenAI API style -- 'v1' for Microsoft Foundry "
            "(/openai/v1/chat/completions) or 'classic' for Azure OpenAI "
            "(/openai/deployments/...)",
            default=cfg.aoai_api_style,
        )
        answer = answer.strip().lower()
        cfg.aoai_api_style = answer if answer in API_STYLES else cfg.aoai_api_style
        _persist("aoai_api_style", cfg.aoai_api_style)

    if cfg.subscription_id:
        _persist("subscription_id", cfg.subscription_id)
    _persist("demo_run", cfg.demo_run)

    return cfg


def _dotenv_keys() -> set:
    if not ENV_PATH.exists():
        return set()
    keys = set()
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0])
    return keys


def persist_demo_run(demo_run: str) -> None:
    """Persist a (regenerated) DEMO_RUN value, used by the demo reset cell."""
    _persist("demo_run", demo_run)


def validate_config(cfg: WorkshopConfig) -> None:
    """Raise a clear error if required fields are missing."""
    missing = [
        name
        for name, value in (
            ("resource_group", cfg.resource_group),
            ("apim_name", cfg.apim_name),
            ("aoai_endpoint", cfg.aoai_endpoint),
            ("aoai_deployment", cfg.aoai_deployment),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required configuration values: {', '.join(missing)}")

    if cfg.aoai_api_style not in API_STYLES:
        raise ValueError(
            f"AOAI_API_STYLE must be one of {', '.join(API_STYLES)} "
            f"(got '{cfg.aoai_api_style}')."
        )

    parsed = urlparse(cfg.aoai_endpoint)
    if parsed.path not in ("", "/"):
        raise ValueError(
            "AOAI_ENDPOINT must be the resource root (e.g. "
            "https://<name>.services.ai.azure.com or https://<name>.openai.azure.com) "
            f"without a path -- got '{cfg.aoai_endpoint}'. Remove the "
            f"'{parsed.path}' path segment; the notebooks append the correct "
            "request path themselves."
        )
