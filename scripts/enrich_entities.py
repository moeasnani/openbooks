#!/usr/bin/env python3
"""Grok-grounded real-world context + verdict for flagged vendor entities.

For each flagged entity (red-flag markers but no human-reviewed context), feed
Grok the *flag evidence* (markers, agencies, exposure, years) AND let it run
live web_search + x_search via xAI's Agent Tools API to verify what the entity
actually IS in the real world. Grok returns a structured verdict + a plain
plain-English explanation + REAL source citations.

This turns a raw red flag ("FNF CONSTRUCTION: $963M, offcontract, whole_dollar")
into a defensible line ("FNF is a long-established AZ highway contractor; the
'offcontract' flag reflects ADOT design-build progress payments, not anomaly —
[sources]"). Reviewers keep the final call; this is decision support.

Determinism contract (mirrors unattributed_enrichment.json): the committed
adjudications are the source of truth; flag evidence regenerates from the
warehouse. Resumable + incremental — safe to Ctrl-C and re-run.

Usage:
    .venv/bin/python scripts/enrich_entities.py             # default: tier-1, no ctx/verdict
    ENRICH_MAX=300 ENRICH_MIN_TIER=2 .venv/bin/python scripts/enrich_entities.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import duckdb

os.environ.setdefault("OPENBOOKS_ASK_PROVIDER", "xai")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openbooks.ask as ask  # noqa: E402

ask.DEFAULT_PROVIDER = "xai"  # force xai even if module imported with another default

DB = os.environ.get("OPENBOOKS_DB", "warehouse.duckdb")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "entity_enrichment.json")
MAX = int(os.environ.get("ENRICH_MAX", "166"))
MIN_TIER = int(os.environ.get("ENRICH_MIN_TIER", "1"))   # 1 = tier-1 only
SLEEP = float(os.environ.get("ENRICH_SLEEP", "0.5"))

# Plain-English glossary so Grok understands the warehouse's flag tokens.
MARKER_GLOSSARY = {
    "whole_dollar": "payment is an exact whole-dollar amount (no cents) — common for progress/lump-sum payments but also a structuring tell",
    "offcontract": "payment not tied to a named procurement contract in the data",
    "no_contract_named": "contract number field is blank",
    "new_vendor_large": "large payments to a vendor newly appearing in the data",
    "new_vendor_dominant": "a new vendor quickly dominates an agency's spend",
    "round_100k": "payment is a round multiple of $100k",
    "round_1m": "payment is a round multiple of $1M",
    "peer_outlier": "amount is a statistical outlier vs peers in the same agency/category",
    "true_duplicate": "same amount/payee/date appears more than once (possible double-pay)",
    "short_tenure_vendor": "vendor active for only a short window",
    "june_round": "round payment clustered at fiscal year-end (June) — possible budget-dumping",
    "person_name_payee": "payee looks like an individual's name",
    "sole_source": "awarded without competitive bidding",
    "vendor_dependency": "agency is heavily dependent on this one vendor",
    "masked_payee": "payee name is redacted/masked",
    "manual_rail_disc": "flagged by a manual review rail",
    "manual_rail": "flagged by a manual review rail",
}

SYSTEM = (
    "You are a public-finance oversight analyst. You are given a vendor/entity that "
    "received Arizona state-government payments and was auto-FLAGGED by anomaly markers. "
    "Your job: use web_search and x_search to verify WHAT THIS ENTITY ACTUALLY IS in the "
    "real world (industry, ownership, what it does, its relationship to Arizona agencies), "
    "then judge whether the flag pattern most likely reflects a LEGITIMATE business reason "
    "or genuinely WARRANTS SCRUTINY. The markers are heuristics, not proof of wrongdoing — "
    "e.g. highway contractors legitimately get large whole-dollar progress payments; the "
    "'offcontract' flag often just means the contract id wasn't in the dataset. Do NOT "
    "accuse anyone of wrongdoing. Prefer authoritative sources (state records, SEC, "
    "reputable news). If you cannot verify the entity, say so and lower confidence.\n\n"
    "After researching, return your FINAL answer as STRICT JSON only (no prose around it), "
    "with keys:\n"
    "  identity: 1 sentence on what the entity is (who/what/where).\n"
    "  arizona_role: 1 sentence on why it plausibly receives AZ state money.\n"
    "  verdict: one of 'explained_benign' | 'mostly_benign' | 'mixed' | 'warrants_review' | 'unverifiable'.\n"
    "  reason: 1-2 sentences tying the flag pattern to the verified reality.\n"
    "  confidence: 'high' | 'medium' | 'low'.\n"
    "  notes: caveats or null."
)


def _responses_call(cfg: dict, system: str, user: str) -> tuple[str, list[str], dict]:
    """Call xAI /v1/responses with web+x search; return (text, citations, usage)."""
    body = {
        "model": cfg["model"],
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": [{"type": "web_search"}, {"type": "x_search"}],
        "max_output_tokens": 1500,
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + cfg["token"],
                 "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=240))
    text = r.get("output_text") or ""
    cites: list[str] = []
    if not text:
        parts = []
        for item in r.get("output", []):
            if isinstance(item, dict) and item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        parts.append(c.get("text", ""))
                    for a in (c.get("annotations") or []):
                        if a.get("url"):
                            cites.append(a["url"])
        text = "\n".join(parts)
    # citations may also be top-level
    for u in (r.get("citations") or []):
        if u not in cites:
            cites.append(u)
    return text, cites, r.get("usage", {})


def _parse_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        # pull the fenced block
        seg = text.split("```", 2)
        text = seg[1] if len(seg) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    # find the outermost JSON object
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text.strip())


def _load_existing() -> dict:
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"_meta": {}, "entities": {}}


def main() -> int:
    con = duckdb.connect(DB, read_only=True)
    cfg = ask._resolve_provider()
    if cfg["provider"] != "xai":
        print(f"ERROR: provider is {cfg['provider']!r}, need 'xai'.", file=sys.stderr)
        return 2
    print(f"provider={cfg['provider']} model={cfg['model']} auth={cfg['auth_source']}",
          file=sys.stderr)

    payload = _load_existing()
    entities = payload.setdefault("entities", {})

    rows = con.execute("""
        SELECT entity_key, entity_name, flagged_exposure, n_flagged, top_tier,
               max_risk_score, n_agencies, agencies, top_markers, first_year, last_year
        FROM tier_entities
        WHERE top_tier <= ? AND (public_context IS NULL OR verify_verdict IS NULL)
        ORDER BY flagged_exposure DESC
        LIMIT ?
    """, [MIN_TIER, MAX]).fetchall()

    todo = [r for r in rows if r[0] not in entities]
    print(f"{len(rows)} candidates, {len(todo)} not yet enriched", file=sys.stderr)

    done = 0
    for (ek, name, exp, nflag, tier, z, nag, agencies, markers, fy0, fy1) in todo:
        markers = markers or []
        gloss = "; ".join(f"{m} ({MARKER_GLOSSARY.get(m, 'heuristic flag')})"
                          for m in markers)
        ag_list = agencies if isinstance(agencies, list) else [agencies]
        evidence = (
            f"Entity: {name}\n"
            f"Flagged exposure: ${float(exp):,.0f}\n"
            f"Number of flagged transactions: {int(nflag) if nflag else 'n/a'}\n"
            f"Risk tier: {tier} (1=highest)  max risk z-score: {z}\n"
            f"Active years in data: {fy0}–{fy1}\n"
            f"Paid by {nag} Arizona agency(ies); top: {', '.join(map(str, ag_list[:5]))}\n"
            f"Anomaly markers fired: {gloss}\n\n"
            "Verify what this entity is via web/X search, then return the STRICT JSON verdict."
        )
        print(f"  → {name[:42]:42s} ${float(exp):>13,.0f} t{tier}", file=sys.stderr, end="")
        try:
            text, cites, usage = _responses_call(cfg, SYSTEM, evidence)
            adj = _parse_json(text)
            adj["citations"] = cites[:8]
            adj["_web_calls"] = (usage.get("server_side_tool_usage_details") or {}).get("web_search_calls")
            adj["_x_calls"] = (usage.get("server_side_tool_usage_details") or {}).get("x_search_calls")
            print(f"  ✓ {adj.get('verdict','?')} ({len(cites)} cites)", file=sys.stderr)
        except urllib.error.HTTPError as e:
            adj = {"verdict": "error", "confidence": "low",
                   "reason": f"HTTP {e.code}: {e.read().decode()[:200]}",
                   "citations": []}
            print(f"  ✗ HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            adj = {"verdict": "error", "confidence": "low",
                   "reason": f"{type(e).__name__}: {e}", "citations": []}
            print(f"  ✗ {type(e).__name__}", file=sys.stderr)

        entities[ek] = {
            "entity_name": name,
            "flagged_exposure": float(exp),
            "top_tier": int(tier) if tier is not None else None,
            "markers": markers,
            "agencies": ag_list[:5],
            **adj,
        }
        done += 1
        # incremental save every entity (resumable)
        payload["_meta"] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": cfg["model"], "provider": cfg["provider"],
            "auth_source": cfg["auth_source"],
            "method": ("Grok (xAI Agent Tools API) grounded each verdict in live "
                       "web_search + x_search; citations are real source URLs. "
                       "Verdicts are decision-support, not findings of wrongdoing; "
                       "a human reviewer keeps the final call."),
            "tool": "web_search+x_search", "count": len(entities),
        }
        with open(OUT, "w") as f:
            json.dump(payload, f, indent=2)
        time.sleep(SLEEP)

    print(f"\nwrote {OUT} — {len(entities)} entities total ({done} new this run)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
