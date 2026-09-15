# AI Governance Workshop

A hands-on workshop demonstrating how to govern AI workloads (starting with
Azure OpenAI behind Azure API Management) using policy-driven controls:
token limits and quotas, and more governance patterns to come.

The workshop is delivered as a set of Python Jupyter notebooks that create
everything they need on an existing APIM instance, apply governance
policies, and then demonstrate the resulting behavior live.

## Prerequisites

- An Azure subscription with an **existing Azure API Management (APIM)
  instance**. The notebooks do not create the APIM instance itself, only the
  APIs/policies/subscriptions on top of it.
- **Azure CLI**, logged in via `az login` before you start. The notebooks
  use `AzureCliCredential` (falling back to `DefaultAzureCredential`) and
  never open an interactive browser login.
- **Python 3.10+**
- An **Azure OpenAI** resource with a chat-completion model deployed (e.g.
  `gpt-4o-mini`), and either:
  - the APIM system-assigned managed identity granted the
    `Cognitive Services OpenAI User` role on the Azure OpenAI resource
    (preferred), or
  - an Azure OpenAI API key (used as a fallback).

### APIM SKU requirements

Demo 1 relies on the **`azure-openai-token-limit`** policy, which requires a
supported APIM tier. Classic **Standard**/**Premium** tiers and the newer
**StandardV2**/**PremiumV2** tiers support it; the **Consumption** and
**Developer** tiers may not, depending on current Azure documentation.
Confirm your APIM SKU supports `azure-openai-token-limit` before running
Demo 1 -- `00-setup-and-validation.ipynb` will print your instance's SKU as
part of its checks.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
az login
jupyter notebook
```

1. Open and run **`notebooks/00-setup-and-validation.ipynb`** first. It
   confirms your `az login` session, lets you pick/confirm a subscription,
   prompts for your resource group and APIM instance name, validates
   connectivity, and persists everything to a local `.env` file (never
   committed) so subsequent notebooks run non-interactively.
2. Open a demo notebook from the table below.

Configuration precedence in every notebook is: **environment variables**
&rarr; **`.env` file** &rarr; **interactive prompt** (with the answer
persisted back to `.env`). Secrets (API keys, subscription keys, tokens) are
always masked in notebook output and are never printed in full.

## The demos

| # | Notebook | Topic | Status |
| - | -------- | ----- | ------ |
| 1 | [`demo1-token-limits.ipynb`](notebooks/demo1-token-limits.ipynb) | Token limits & quota enforcement (TPM burst -> 429, daily budget -> 403) | **Complete** |
| 2 | [`demo2-placeholder.ipynb`](notebooks/demo2-placeholder.ipynb) | TBD | Coming soon |
| 3 | [`demo3-placeholder.ipynb`](notebooks/demo3-placeholder.ipynb) | TBD | Coming soon |
| 4 | [`demo4-placeholder.ipynb`](notebooks/demo4-placeholder.ipynb) | TBD | Coming soon |

Every demo notebook follows the same template, so new demos can be dropped
in without changing the `shared/` helpers:

`Scenario` -> `Isolation` -> `Configure (policy apply)` -> `Baseline` ->
`Demonstrate` -> `Observe` -> `Reset / Cleanup` -> `Talk track`

## Repository structure

```
README.md                      # this file
requirements.txt               # Python dependencies
.env.example                   # config template (copy to .env, or let notebooks prompt you)
.gitignore
shared/
  config.py                    # load/prompt config, persist to .env, validate
  apim.py                      # idempotent APIM control-plane helpers (ARM REST)
  auth.py                      # AzureCliCredential / DefaultAzureCredential helpers + ARM token
  display.py                   # tables, headers, banners, charts
policies/
  demo1-token-limit.xml        # API-scope policy for Demo 1
notebooks/
  00-setup-and-validation.ipynb  # shared prerequisite check
  demo1-token-limits.ipynb       # Demo 1 (complete)
  demo2-placeholder.ipynb        # stub
  demo3-placeholder.ipynb        # stub
  demo4-placeholder.ipynb        # stub
```

## Named value convention

APIM resolves `{{token}}` references in policy XML against a named value's
**display name**, and the match is case-sensitive. To avoid mismatches, this
repo requires the named value **id and display name to be identical,
lowercase, and equal to the `{{token}}` used in the policy XML** (for example
`demo1-tokens-per-minute`). `shared/apim.ensure_named_value` raises a
`ValueError` if the id and display name differ.

Named values interpolated into `condition` / `set-body` expressions must also
be created with `secret=False`; secret named values cannot be interpolated
there.

## Demo 1: Token limits & quota enforcement

Demo 1 shows a complete, idempotent, end-to-end flow:

1. Creates a dedicated APIM subscription and product for isolation, plus the
   Azure OpenAI-backed API, backend, named values, and operation -- all
   before any calls are made.
2. Applies a policy at **API scope** combining `azure-openai-token-limit`
   (tokens-per-minute) with a daily token-quota dimension.
3. Makes a baseline call and reads the `tokens-consumed` /
   `remaining-tokens` headers.
4. Bursts requests until a `429` with `Retry-After` is observed.
5. Continues (respecting `Retry-After`) until the daily token budget is
   exhausted and a `403` is returned.
6. Summarizes what happened, then demonstrates an **instant reset** by
   changing the `x-demo-run` suffix baked into the policy's counter key.
7. Leaves its APIM resources in place -- Demos 2-4 reuse the same APIM
   instance, so cleanup is covered at the end of the final demo.

Re-running `demo1-token-limits.ipynb` end to end, twice in a row, does not
fail or duplicate any Azure resources.
