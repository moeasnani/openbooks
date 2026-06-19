#!/usr/bin/env python3
"""Grok-adjudicated context for untraceable (unattributed) spend.

For each top agency by unattributed dollars, gather the *evidence* — the
expenditure-category and fund mix of the unattributed portion — and ask
Grok (via the user's xAI OAuth) to classify WHY the spend is unattributed
and cite the governing Arizona statute / federal rule where one applies.

Output: a committed JSON the query layer + UI read, so every untraceable
figure ships with a verified, sourced reason and a statutory/operational/
anomalous label — turning a raw number into a defensible one.

Determinism contract (mirrors grok_enrichment.json): only the committed
adjudications are the source of truth; the evidence is regenerable from the
warehouse. Re-run only to refresh.

Usage:
    OPENBOOKS_ASK_PROVIDER=xai .venv/bin/python scripts/enrich_unattributed.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openbooks.ask import _resolve_provider  # noqa: E402

DB = os.environ.get("OPENBOOKS_DB", "warehouse.duckdb")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "unattributed_enrichment.json")
TOP_N = int(os.environ.get("ENRICH_TOP_N", "10"))

_BUCKET = """CASE
  WHEN payee_customer_vendor_name IS NULL OR trim(payee_customer_vendor_name)='' THEN 'blank'
  WHEN upper(payee_customer_vendor_name) IN ('N/A','NA','NONE','NOT APPLICABLE','UNKNOWN') THEN 'na'
  WHEN upper(payee_customer_vendor_name) LIKE '%REDACT%' THEN 'redacted'
  WHEN upper(payee_customer_vendor_name) LIKE '%CONFIDENTIAL%' THEN 'confidential'
  ELSE 'named' END"""

SYSTEM = (
    "You are a public-finance analyst verifying why Arizona state-checkbook "
    "spending is recorded without a named payee ('unattributed'). You are given "
    "REAL evidence: the agency, its unattributed dollars, and the expenditure "
    "categories and funds that spend sits in. Reason ONLY from this evidence plus "
    "well-established Arizona public-finance law. Common legitimate reasons: "
    "payments of public-assistance/benefits to individuals are redacted by statute "
    "(e.g. confidentiality of DES/AHCCCS/DCS client benefits); payroll (PERSONAL "
    "SERVICES, EMPLOYEE RELATED) is reported without individual payee names; "
    "intergovernmental TRANSFERS OUT often lack a vendor. Flag as 'anomalous' ONLY "
    "if the category mix does NOT fit a known statutory/operational reason. Do not "
    "accuse anyone of wrongdoing. If unsure, say so and lower confidence.\n\n"
    "Return STRICT JSON only, no prose, with keys: classification "
    "('statutory'|'operational'|'mixed'|'anomalous'), confidence ('high'|'medium'|"
    "'low'), reason (1-2 sentences, plain English), statutory_basis (citation or "
    "null), notes (caveats or null)."
)


def _chat(cfg: dict, system: str, user: str) -> str:
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 600,
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + cfg["token"],
                 "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=90))
    return r["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def main() -> int:
    con = duckdb.connect(DB, read_only=True)
    cfg = _resolve_provider()
    if cfg["provider"] != "xai":
        print(f"WARNING: provider is {cfg['provider']!r}, expected 'xai'. "
              "Set OPENBOOKS_ASK_PROVIDER=xai.", file=sys.stderr)

    top = con.execute(f"""
        SELECT organization_level_1_name agency,
               round(sum(amount),0) total,
               round(sum(CASE WHEN {_BUCKET}<>'named' THEN amount ELSE 0 END),0) un
        FROM transactions WHERE transaction_type='EX' AND amount IS NOT NULL
        GROUP BY 1 HAVING sum(amount)>0 ORDER BY un DESC LIMIT ?
    """, [TOP_N]).fetchall()

    agencies = {}
    for agency, total, un in top:
        cats = con.execute(f"""
            SELECT category_level_1_name c, round(sum(amount),0) usd
            FROM transactions WHERE transaction_type='EX' AND amount IS NOT NULL
              AND organization_level_1_name=? AND {_BUCKET}<>'named'
            GROUP BY 1 ORDER BY usd DESC LIMIT 6
        """, [agency]).fetchall()
        funds = con.execute(f"""
            SELECT fund_1_name f, round(sum(amount),0) usd
            FROM transactions WHERE transaction_type='EX' AND amount IS NOT NULL
              AND organization_level_1_name=? AND {_BUCKET}<>'named'
            GROUP BY 1 ORDER BY usd DESC LIMIT 6
        """, [agency]).fetchall()
        pct = round(100.0 * float(un) / float(total), 1) if total else 0.0
        evidence = {
            "agency": agency,
            "unattributed_usd": float(un),
            "unattributed_pct": pct,
            "top_categories": [{"category": c, "usd": float(u)} for c, u in cats],
            "top_funds": [{"fund": f, "usd": float(u)} for f, u in funds],
        }
        user = (
            "Evidence:\n" + json.dumps(evidence, indent=1) +
            "\n\nClassify why this agency's spend is unattributed. Strict JSON only."
        )
        print(f"  → {agency} ({pct}% unattributed)…", file=sys.stderr)
        try:
            adj = _parse_json(_chat(cfg, SYSTEM, user))
        except Exception as e:  # keep going; record the failure
            adj = {"classification": "error", "confidence": "low",
                   "reason": f"adjudication failed: {e}", "statutory_basis": None,
                   "notes": None}
        agencies[agency] = {
            "unattributed_usd": float(un),
            "unattributed_pct": pct,
            "top_categories": evidence["top_categories"],
            **adj,
        }

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": cfg["model"],
            "provider": cfg["provider"],
            "auth_source": cfg["auth_source"],
            "top_n": TOP_N,
            "method": (
                "Grok adjudication grounded in per-agency category/fund evidence "
                "from the checkbook warehouse; statutory basis is model-asserted "
                "and should be spot-checked against A.R.S. before publication."
            ),
        },
        "agencies": agencies,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {OUT} ({len(agencies)} agencies)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
