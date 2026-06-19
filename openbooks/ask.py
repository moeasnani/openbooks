"""Natural-language question answering over an OpenBooks warehouse.

A grounded tool-calling layer: an LLM is given the
OpenBooks query methods as *function tools*. The model decides which
method(s) to call and with what arguments; this module executes them
read-only against the warehouse and feeds the real rows back. The model
composes its final answer **only** from data actually returned by the
query layer — it never invents numbers and never touches SQL or the DB.

Design notes
------------
* **Stdlib-only HTTP.** Uses ``urllib`` so OpenBooks keeps its
  zero-dependency posture. No ``openai``/``httpx`` import required.
* **Read-only.** Only the seven query methods are exposed as tools. The
  write path (``set_verdict``) is deliberately *not* a tool.
* **Auth flexibility.** Prefers a standalone API key (e.g. ``NOUS_API_KEY``,
  the clean production path). On a Hermes box it can fall back to an
  OAuth token in ``~/.hermes/auth.json`` so it works without a separate
  key for local development.

Run::

    from openbooks import OpenBooks
    from openbooks.ask import ask

    ob = OpenBooks("warehouse.duckdb")
    result = ask(ob, "Which agencies have the most tier-1 leads over $1M?")
    print(result["answer"])

``result`` also carries ``tool_calls`` (what was run, with arguments and
row counts) so the caller can show its work / audit the grounding.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger("openbooks.ask")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Configuration — provider-agnostic (Nous, xAI, or Anthropic direct)
# --------------------------------------------------------------------------
#
# The NL layer talks to any OpenAI-compatible chat-completions endpoint.
# Three providers are wired out of the box; pick one with
# OPENBOOKS_ASK_PROVIDER (default: "nous"). Each resolves its own base URL,
# model, and bearer token, with env overrides taking precedence so a
# production server can be configured without code changes.
#
#   OPENBOOKS_ASK_PROVIDER   xai | nous | anthropic   (default: nous)
#   OPENBOOKS_ASK_MODEL      override the model id for the chosen provider
#   OPENBOOKS_ASK_BASE_URL   override the endpoint base URL
#   <PROVIDER>_API_KEY       XAI_API_KEY / NOUS_API_KEY / ANTHROPIC_API_KEY
#
# Default models per provider (override via OPENBOOKS_ASK_MODEL):
DEFAULT_PROVIDER = os.environ.get("OPENBOOKS_ASK_PROVIDER", "nous").lower()

_PROVIDER_DEFAULTS = {
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "key_env": "XAI_API_KEY",
    },
    "nous": {
        "base_url": "https://inference-api.nousresearch.com/v1",
        "model": "z-ai/glm-5.2",
        "key_env": "NOUS_API_KEY",
    },
    "anthropic": {
        # Anthropic's OpenAI-compatible shim.
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-haiku-4-5",
        "key_env": "ANTHROPIC_API_KEY",
    },
}

MAX_ROUNDS = 6            # safety cap on tool-call/response loops
HTTP_TIMEOUT = 120        # seconds per LLM call
MAX_TOOL_ROWS = 60        # truncate big result sets before sending to the LLM


class AskError(RuntimeError):
    """Anything that prevents producing a grounded answer."""


# --------------------------------------------------------------------------
# Auth — env API key preferred, Hermes OAuth token as a local-dev fallback
# --------------------------------------------------------------------------

def _hermes_token(provider_key: str) -> str | None:
    """Best-effort read of a provider's bearer token from Hermes auth.json.

    ``provider_key`` is the key under ``providers`` in ~/.hermes/auth.json,
    e.g. "xai-oauth" or "nous". This is a *fallback* for running on the
    same box as Hermes; production should set the relevant API key env var.
    """
    path = os.path.expanduser("~/.hermes/auth.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    prov = (data.get("providers") or {}).get(provider_key) or {}

    # xai-oauth nests the token under "tokens.access_token"; nous keeps it
    # flat as "access_token". Try both shapes.
    tokens = prov.get("tokens") or {}
    tok = tokens.get("access_token") or prov.get("access_token")
    if not (isinstance(tok, str) and tok):
        return None

    # Warn (don't fail) if a short-lived token looks stale; the API will
    # reject an expired token and the caller surfaces that error.
    try:
        last = prov.get("last_refresh") or prov.get("obtained_at")
        ttl = int(tokens.get("expires_in", prov.get("expires_in", 0)) or 0)
        if last and ttl:
            from datetime import datetime, timezone

            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if age > ttl:
                log.warning(
                    "Hermes %s token appears expired (age %.0fs > ttl %ds); "
                    "refresh it in Hermes or set an API key env var.",
                    provider_key, age, ttl,
                )
    except (ValueError, TypeError):
        pass
    return tok


def _resolve_provider() -> dict:
    """Resolve the active provider config: base_url, model, token, source.

    Returns a dict with keys: provider, base_url, model, token, auth_source.
    Raises AskError if no credential can be found.
    """
    provider = DEFAULT_PROVIDER
    defaults = _PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise AskError(
            f"unknown OPENBOOKS_ASK_PROVIDER {provider!r}; "
            f"choose from {sorted(_PROVIDER_DEFAULTS)}"
        )

    base_url = os.environ.get("OPENBOOKS_ASK_BASE_URL") or defaults["base_url"]
    model = os.environ.get("OPENBOOKS_ASK_MODEL") or defaults["model"]

    # 1) explicit env API key wins (the production path)
    key = os.environ.get(defaults["key_env"])
    if key:
        return {"provider": provider, "base_url": base_url, "model": model,
                "token": key, "auth_source": defaults["key_env"]}

    # 2) fall back to a Hermes-stored token for this provider
    hermes_keys = {"xai": "xai-oauth", "nous": "nous", "anthropic": "anthropic"}
    tok = _hermes_token(hermes_keys.get(provider, provider))
    if tok:
        return {"provider": provider, "base_url": base_url, "model": model,
                "token": tok, "auth_source": f"hermes:{hermes_keys.get(provider, provider)}"}

    raise AskError(
        f"No credential for provider {provider!r}. Set {defaults['key_env']} "
        f"(recommended) or ensure ~/.hermes/auth.json has a valid "
        f"{hermes_keys.get(provider, provider)} token."
    )


# --------------------------------------------------------------------------
# Tool registry — the read-only query surface exposed to the model
# --------------------------------------------------------------------------

def _tool_specs() -> list[dict]:
    """OpenAI/xAI-compatible function-tool schemas for the query layer."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": (
                    "Fuzzy search across vendor entities, agencies, and "
                    "programs by name/marker. Use this first when the user "
                    "names something but you don't have an exact key."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "search text"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "entity",
                "description": (
                    "Full summary for ONE vendor entity: risk totals, top "
                    "transactions, merged names, primary-agency audit context. "
                    "Accepts an exact entity_key or a fuzzy name fragment."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name_or_key": {"type": "string"},
                    },
                    "required": ["name_or_key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "leads",
                "description": (
                    "The action queue: transactions at a given tier, "
                    "filterable by verification status, agency, and minimum "
                    "amount. Tier 1 = highest review priority."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
                        "status": {
                            "type": "string",
                            "description": (
                                "verification overlay filter, e.g. "
                                "genuine_review, screened_unreviewed, "
                                "explained_benign"
                            ),
                        },
                        "agency": {"type": "string"},
                        "min_amount": {"type": "number", "default": 0},
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agency_card",
                "description": (
                    "Scorecard + trend for ONE agency: exposure, tier "
                    "breakdown, AG-audit rollup. Accepts an agency name."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"agency": {"type": "string"}},
                    "required": ["agency"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "explain",
                "description": (
                    "Why a specific transaction got its tier: marker-family "
                    "breakdown and the score calculation. Needs a "
                    "transaction_id (get one from leads/entity first)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"transaction_id": {"type": "string"}},
                    "required": ["transaction_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "waterfall",
                "description": (
                    "Portfolio-wide tier distribution: counts, exposure, and "
                    "percentages per tier. Use for 'big picture' questions."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank_agencies",
                "description": (
                    "LEADERBOARD of agencies, sorted by a metric. Use this "
                    "for ANY 'which agency has the most/biggest/highest …' or "
                    "'top N agencies by …' question — do NOT try to infer "
                    "rankings from the leads sample. Returns agencies ordered "
                    "by the chosen metric, descending."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "usd_tier1", "tier12_exposure", "hv_exposure",
                                "n_tier1", "n_flagged", "tier12_pct_of_hv",
                                "avg_risk_score", "distinct_flagged_vendors",
                            ],
                            "description": (
                                "what to rank by: usd_tier1 = Tier-1 dollar "
                                "exposure (default), tier12_exposure = Tier-1+2 "
                                "dollars, hv_exposure = total high-value "
                                "dollars, n_tier1 = count of Tier-1 txns, "
                                "n_flagged = flagged txn count, "
                                "tier12_pct_of_hv = flagged share %, "
                                "avg_risk_score, distinct_flagged_vendors"
                            ),
                            "default": "usd_tier1",
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank_vendors",
                "description": (
                    "LEADERBOARD of vendor entities, sorted by a metric, "
                    "optionally restricted to one agency. Use this for 'which "
                    "vendors have the most …' or 'top N vendors (in agency X) "
                    "by …'. Do NOT infer vendor rankings from the leads sample."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "usd_tier1", "flagged_exposure", "hv_exposure",
                                "n_tier1", "n_flagged", "max_risk_score",
                            ],
                            "default": "usd_tier1",
                        },
                        "agency": {
                            "type": "string",
                            "description": "optional: restrict to vendors active in this agency",
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank_programs",
                "description": (
                    "LEADERBOARD of appropriations/programs, sorted by a "
                    "metric. Use for 'top programs/appropriations by …'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "tier12_exposure", "hv_exposure", "n_tier1",
                                "max_risk_score", "distinct_vendors",
                            ],
                            "default": "tier12_exposure",
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "spend",
                "description": (
                    "TOTAL SPENDING from the COMPLETE transaction ledger (every "
                    "transaction, all sizes — NOT just the >=$100K forensic "
                    "subset). Use this for ANY 'how much did <agency> spend "
                    "(on <category>) (in FY<year>)' question, budget totals, "
                    "category spend, or spend trends. This is the ONLY tool "
                    "that sees complete spend; the tier/leads/rank tools see "
                    "only high-value flagged transactions and will UNDERCOUNT "
                    "actual spending. Defaults to expenditures (money out)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agency": {
                            "type": "string",
                            "description": "agency name (fuzzy/long-form auto-resolved)",
                        },
                        "fiscal_year": {
                            "type": "integer",
                            "description": "one fiscal year, e.g. 2024; omit for all years",
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "spend-category keyword, contains-matched across "
                                "the category hierarchy + appropriation name, "
                                "e.g. 'information technology', 'travel', "
                                "'software', 'professional services'. Omit for "
                                "all categories (total spend)."
                            ),
                        },
                        "transaction_type": {
                            "type": "string",
                            "enum": ["EX", "RV", "ALL"],
                            "description": (
                                "EX = expenditures/spend (default), RV = "
                                "revenue/receipts, ALL = both"
                            ),
                            "default": "EX",
                        },
                        "breakdown": {
                            "type": "string",
                            "enum": ["category", "year", "vendor", "none"],
                            "description": (
                                "how to break down the total: category buckets "
                                "(default), year (trend), vendor (top payees), "
                                "or none (grand total only)"
                            ),
                            "default": "category",
                        },
                        "limit": {"type": "integer", "default": 25},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "verdicts_pending",
                "description": (
                    "Reviewer queue: entities at a tier still awaiting a "
                    "verdict."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "integer", "default": 1},
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_findings",
                "description": (
                    "Full-text search across Arizona Auditor-General (AG) "
                    "audit FINDINGS — the official performance-audit reports, "
                    "distinct from the warehouse's tier 'leads'. Searches "
                    "finding text, recommendations, and report titles. Use "
                    "for 'find audit findings about <topic>' or 'show "
                    "findings mentioning <keyword>' questions. Returns "
                    "matching findings with agency, fiscal year, questioned "
                    "cost, and confidence."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "search keyword, e.g. 'procurement', 'IT', 'payroll'",
                        },
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank_ag_findings",
                "description": (
                    "LEADERBOARD of agencies by Auditor-General (AG) audit "
                    "metrics — questioned costs, finding counts, adverse "
                    "findings, report counts. Use for 'which agencies had "
                    "the most audit findings / questioned costs / adverse "
                    "findings' or 'top agencies by audit severity'. These "
                    "are AUDITED findings, not the warehouse's tier 'leads'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "total_questioned_cost", "n_findings",
                                "n_findings_with_cost", "n_adverse",
                                "n_reports",
                            ],
                            "description": (
                                "what to rank by: total_questioned_cost = "
                                "sum of questioned costs (default), "
                                "n_findings = total finding count, "
                                "n_findings_with_cost = findings with a $ "
                                "figure, n_adverse = adverse finding count, "
                                "n_reports = audit report count"
                            ),
                            "default": "total_questioned_cost",
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "unattributed_spend",
                "description": (
                    "TRANSPARENCY metric: how much spending cannot be traced "
                    "to a named payee (payee is blank, 'N/A', redacted, or "
                    "confidential). Use for 'how much spending is "
                    "untraceable/dark/unattributed', 'which agencies hide the "
                    "most spending', 'redacted spending'. Statewide by default; "
                    "pass an agency to scope it. Returns a per-bucket split and "
                    "an agency leaderboard. NOTE: some redaction is statutory "
                    "(benefits to individuals) — this is a transparency metric, "
                    "NOT an allegation of wrongdoing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agency": {
                            "type": "string",
                            "description": "optional agency filter; omit for statewide",
                        },
                        "fiscal_year": {"type": "integer"},
                        "limit": {"type": "integer", "default": 25},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finding_transactions",
                "description": (
                    "TRIANGULATION drill-down: given an Auditor-General "
                    "finding id (e.g. '19-109-F01'), pull the checkbook "
                    "transactions implicated by that finding — same agency, "
                    "within the audit's fiscal-year window, optionally narrowed "
                    "by the finding's fund. Use AFTER search_findings or "
                    "rank_ag_findings to go from an audited finding to the "
                    "underlying spend ('show me the transactions behind that "
                    "finding', 'what spending does this finding cover'). The "
                    "transactions are CONTEXT for the finding, not themselves "
                    "flagged or accused."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "finding_id": {
                            "type": "string",
                            "description": "AG finding id, e.g. '19-109-F01'",
                        },
                        "limit": {"type": "integer", "default": 50},
                        "window_years": {
                            "type": "integer",
                            "default": 1,
                            "description": "fiscal-year window around the audit FY (+/-)",
                        },
                    },
                    "required": ["finding_id"],
                },
            },
        },
    ]


def _dispatch_tool(ob: Any, name: str, args: dict) -> Any:
    """Run a single named query method with validated kwargs."""
    table: dict[str, Callable[..., Any]] = {
        "search": lambda: ob.search(args.get("query", ""), limit=int(args.get("limit", 20))),
        "entity": lambda: ob.entity(args.get("name_or_key", "")),
        "leads": lambda: ob.leads(
            tier=int(args.get("tier", 1)),
            status=args.get("status") or None,
            agency=args.get("agency") or None,
            min_amount=float(args.get("min_amount", 0) or 0),
            limit=int(args.get("limit", 50)),
        ),
        "agency_card": lambda: ob.agency_card(args.get("agency", "")),
        "explain": lambda: ob.explain(args.get("transaction_id", "")),
        "waterfall": lambda: ob.waterfall(),
        "rank_agencies": lambda: ob.rank_agencies(
            metric=args.get("metric", "usd_tier1"),
            limit=int(args.get("limit", 10)),
        ),
        "rank_vendors": lambda: ob.rank_vendors(
            metric=args.get("metric", "usd_tier1"),
            agency=args.get("agency") or None,
            limit=int(args.get("limit", 10)),
        ),
        "rank_programs": lambda: ob.rank_programs(
            metric=args.get("metric", "tier12_exposure"),
            limit=int(args.get("limit", 10)),
        ),
        "spend": lambda: ob.spend(
            agency=args.get("agency") or None,
            fiscal_year=(int(args["fiscal_year"]) if args.get("fiscal_year") not in (None, "") else None),
            category=args.get("category") or None,
            transaction_type=args.get("transaction_type", "EX") or "EX",
            breakdown=args.get("breakdown", "category") or "category",
            limit=int(args.get("limit", 25)),
        ),
        "verdicts_pending": lambda: ob.verdicts_pending(
            tier=int(args.get("tier", 1)), limit=int(args.get("limit", 50))
        ),
        "search_findings": lambda: ob.search_findings(
            args.get("text", ""), limit=int(args.get("limit", 20))
        ),
        "rank_ag_findings": lambda: ob.rank_ag_findings(
            metric=args.get("metric", "total_questioned_cost"),
            limit=int(args.get("limit", 10)),
        ),
        "unattributed_spend": lambda: ob.unattributed_spend(
            args.get("agency") or None,
            fiscal_year=(int(args["fiscal_year"]) if args.get("fiscal_year") not in (None, "") else None),
            limit=int(args.get("limit", 25)),
        ),
        "finding_transactions": lambda: ob.finding_transactions(
            args.get("finding_id", ""),
            limit=int(args.get("limit", 50)),
            window_years=int(args.get("window_years", 1)),
        ),
    }
    fn = table.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    return fn()


def _truncate(result: Any) -> Any:
    """Cap list payloads so a huge result set can't blow the context window."""
    if isinstance(result, list) and len(result) > MAX_TOOL_ROWS:
        return {
            "_truncated": True,
            "_total_rows": len(result),
            "_showing": MAX_TOOL_ROWS,
            "rows": result[:MAX_TOOL_ROWS],
        }
    return result


# --------------------------------------------------------------------------
# LLM transport (stdlib urllib)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the OpenBooks analyst assistant for Arizona \
state fiscal oversight. You answer questions using ONLY the OpenBooks \
query tools provided.

──────── DATASET CONTEXT (what the warehouse actually holds) ────────
This is a three-way TRIANGULATION platform. Every answer should be
framed against these three data universes, because the analytical value
comes from cross-referencing them:

  BUDGET  — what the legislature AUTHORIZED.
    Source: SB 1847 General Appropriations Act (structured extraction).
    The authorized figures are the FY2025-26 current-year appropriation
    (NOT FY2026-27). 86 agencies; ~$47.4B total authorized. Each agency
    entry carries line items, fund sources, and FTE positions.

  CHECKBOOK — what was ACTUALLY SPENT.
    Source: Arizona state open checkbook (cash-basis, NOT audited GAAP).
    The `spend` tool reads the COMPLETE ledger — every transaction of
    every size. Actual-spend figures used for variance are FY2025 (the
    most recent completed fiscal year in the checkbook). Note: checkbook
    amounts are classified by the state's own category codes, not by
    audited budget lines; the State ACFR governs for GAAP figures.

  AG FINDINGS — what auditors FLAGGED.
    Source: Arizona Auditor-General (AG) performance-audit reports.
    Official, audited results: questioned costs, adverse findings,
    recommendations. Distinct from the warehouse's own tier "leads".

Because authorized = FY2025-26 budget and actual = FY2025 checkbook
(prior completed year), the variance between them is a YEAR-OVER-YEAR
DIRECTIONAL indicator — it is NOT an "overage", "deficit", or "overspend"
in the current year. 80 of 86 budget agencies match to a checkbook
agency; the apples-to-apples matched authorized total is ~$47.3B and
the statewide matched variance is roughly +35%. Always state the
fiscal-year basis when you cite a variance.

  FORENSIC TIERING (the `leads`/`rank_*`/`entity`/`agency_card` tools).
    The ≥ $100K expenditure population (≈306,600 transactions carrying
    ~83% of all state spending) is scored on ten forensic marker families
    and assigned Tier 1-4 review priority. Tier 1 = highest scrutiny.
    Tiers and markers are REVIEW-PRIORITIZATION LEADS, never findings of
    fraud or wrongdoing — no entity is accused. These tools see ONLY the
    high-value flagged subset and UNDERCOUNT real spending; use `spend`
    for any total-spending question.

When an answer touches more than one universe, connect them explicitly —
e.g. note when a high-variance agency also carries AG adverse findings,
or when a tier-1 lead sits inside a program the budget authorized. That
cross-referencing is the point of the platform.
──────── end dataset context ────────

Rules:

1. NEVER invent numbers, vendor names, agencies, or amounts. Every figure \
in your answer must come from a tool result you actually received.
2. Pick the right tool(s). For a named vendor/agency you don't have a key \
for, call `search` first, then drill in with `entity` or `agency_card`. \
For ANY ranking / superlative / comparison question ("which agency/vendor \
has the most…", "top N…", "biggest…", "highest…"), you MUST use \
`rank_agencies`, `rank_vendors`, or `rank_programs` with the appropriate \
metric. NEVER infer a ranking by eyeballing the `leads` or `waterfall` \
samples — those are not sorted leaderboards and will give wrong answers.
2a. SPEND vs FORENSIC — this distinction is critical. There are TWO data \
universes. (a) The COMPLETE ledger (tool: `spend`) covers every \
transaction of every size — use it for "how much did X spend", budget \
totals, spend on a category, or spend trends. (b) The FORENSIC/tier tables \
(`leads`, `entity`, `agency_card`, `rank_*`, `waterfall`) cover ONLY \
high-value flagged transactions (>= $100K selected for review) — use them \
for risk, tiers, flags, and leads. For ANY "how much was spent / total \
spending / spend on <category> / budget" question you MUST use `spend`; \
the tier/rank tools UNDERCOUNT real spending and must never be used to \
answer a spend-total question. `spend` defaults to expenditures (money \
out); mention the fiscal year and category scope in your answer.
3. The data contract forbids accusing any entity of wrongdoing. Tiers and \
risk markers are review-prioritization signals, NOT findings of fraud. \
Phrase answers accordingly (e.g. "flagged for review", "high review tier").
4. If the tools return nothing relevant, say so plainly — do not guess. \
For `spend`, note that figures are cash-basis checkbook data classified by \
the state's own category codes (not an audited budget line), and a \
category keyword matches the literal category names — if a result looks \
narrow, say what was matched.
5. Be concise. Lead with the direct answer, then supporting figures. \
Format multi-row answers as a compact table when helpful.
6. AG AUDIT FINDINGS are a distinct data universe from the warehouse's \
tier 'leads'. The Auditor-General (AG) performance-audit findings are \
official, audited results — use `search_findings` for 'find audit \
findings about <topic>' questions and `rank_ag_findings` for 'which \
agencies had the most audit findings / questioned costs / adverse \
findings' questions. Do NOT confuse AG findings (audited) with tier \
leads (review-prioritization signals). When citing a questioned cost, \
note its confidence level if it is medium/low or an estimate.
"""


def _chat_completion(messages: list[dict], tools: list[dict], cfg: dict) -> dict:
    """One POST to an OpenAI-compatible chat-completions endpoint.

    ``cfg`` is the dict returned by :func:`_resolve_provider`
    (base_url, model, token, provider). Returns the assistant message.
    """
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0,
    }
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    label = cfg["provider"]
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise AskError(f"{label} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AskError(f"{label} request failed: {exc.reason}") from exc
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise AskError(f"unexpected {label} response shape: {body}") from exc


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def ask(ob: Any, question: str, *, max_rounds: int = MAX_ROUNDS,
        lock: Any = None) -> dict:
    """Answer a natural-language question, grounded in OpenBooks queries.

    Returns a dict::

        {
          "question": str,
          "answer":   str,           # the model's final prose
          "tool_calls": [            # audit trail of what was actually run
              {"name": str, "arguments": dict, "rows": int|None},
              ...
          ],
          "model": str,
          "auth_source": str,
          "rounds": int,
        }

    ``lock`` is an optional ``threading.Lock`` (or any context manager).
    When provided it is acquired around *each* tool dispatch so concurrent
    requests against a shared DB connection are serialized — but the slow
    LLM HTTP call between dispatches stays unlocked. The HTTP server passes
    its ``db_lock``; the CLI path leaves it ``None`` (single-threaded).
    """
    if not question or not question.strip():
        raise AskError("question is empty")

    cfg = _resolve_provider()
    tools = _tool_specs()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question.strip()},
    ]
    audit: list[dict] = []

    for round_idx in range(1, max_rounds + 1):
        msg = _chat_completion(messages, tools, cfg)
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Model produced a final answer.
            return {
                "question": question.strip(),
                "answer": msg.get("content", "") or "",
                "tool_calls": audit,
                "model": cfg["model"],
                "provider": cfg["provider"],
                "auth_source": cfg["auth_source"],
                "rounds": round_idx,
            }

        # Echo the assistant turn (with its tool_calls) back into history.
        messages.append(
            {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        # Execute every requested tool call and append results.
        for call in tool_calls:
            fn_name = call["function"]["name"]
            raw_args = call["function"].get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed_args = {}
            try:
                # Serialize DB access per tool call. The LLM HTTP round-trip
                # above ran unlocked (it's the slow part); only the
                # warehouse read is fenced. ``lock`` is None on the
                # single-threaded CLI path.
                with lock or contextlib.nullcontext():
                    result = _dispatch_tool(ob, fn_name, parsed_args)
            except Exception as exc:  # noqa: BLE001 - surface to the model, not crash
                result = {"error": f"{type(exc).__name__}: {exc}"}

            rows = len(result) if isinstance(result, list) else None
            audit.append({"name": fn_name, "arguments": parsed_args, "rows": rows})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": fn_name,
                    "content": json.dumps(_truncate(result), default=str),
                }
            )

    raise AskError(
        f"exceeded {max_rounds} tool-call rounds without a final answer; "
        "the question may be too broad or the tools insufficient."
    )
