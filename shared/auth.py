"""Authentication helpers for the AI Governance workshop.

The workshop assumes the user is already authenticated with ``az login``.
We therefore prefer :class:`azure.identity.AzureCliCredential` and only fall
back to :class:`azure.identity.DefaultAzureCredential` if the CLI credential
is unavailable (e.g. running in a different environment). No interactive
browser auth flow is used.
"""

from __future__ import annotations

import functools
import subprocess
from typing import Optional

ARM_SCOPE = "https://management.azure.com/.default"


@functools.lru_cache(maxsize=1)
def get_credential():
    """Return a credential object, preferring the Azure CLI session.

    Cached so repeated calls within a notebook session reuse the same
    credential object (and its internal token cache).
    """
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    try:
        cred = AzureCliCredential()
        # Force a token fetch to make sure `az login` has actually happened.
        cred.get_token(ARM_SCOPE)
        return cred
    except Exception:
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def get_arm_token() -> str:
    """Return a bearer token for the Azure Resource Manager control plane."""
    credential = get_credential()
    token = credential.get_token(ARM_SCOPE)
    return token.token


def get_current_subscription_id() -> Optional[str]:
    """Best-effort lookup of the current `az` default subscription id."""
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        sub_id = result.stdout.strip()
        return sub_id or None
    except Exception:
        return None


def mask_secret(value: Optional[str], keep: int = 4) -> str:
    """Mask a secret value for safe display/logging, keeping only a hint."""
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * (len(value) - keep)}{value[-keep:]}"
