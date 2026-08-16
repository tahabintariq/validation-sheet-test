"""
Stage 2 (validate) - check the routine's classification, then merge it into
output/02_classified.json.

The model's output is never trusted as-is. A classifier handed a long list can
silently drop rows, duplicate them, or invent ids - and a silently short list on
a financial pipeline looks exactly like a quiet day. So every id is reconciled
against Stage 1 and any mismatch is a hard failure, not a warning.

  input : output/01_raw_announcements.json   (source of truth for ids)
          output/02_verdicts_model.json      (what the routine produced)
  output: output/02_classified.json
"""

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sheet_index import load_index

VALID_VERDICTS = {"keep", "drop", "flag"}
PKT = timezone(timedelta(hours=5))

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"


def fail(msg):
    print(f"\nFAILED: {msg}", flush=True)
    sys.exit(1)


def main():
    raw = json.load(open(OUT_DIR / "01_raw_announcements.json", encoding="utf-8"))
    model = json.load(open(OUT_DIR / "02_verdicts_model.json", encoding="utf-8"))

    rows = {r["announcement_id"]: r for r in raw["announcements"]}
    verdicts = model.get("verdicts") or []

    print(f"Stage 2 validation: {len(rows)} announcements in, {len(verdicts)} verdicts out")

    # --- reconcile ids -------------------------------------------------------
    seen = Counter(v["id"] for v in verdicts)
    dupes = [i for i, n in seen.items() if n > 1]
    missing = [i for i in rows if i not in seen]
    unknown = [i for i in seen if i not in rows]

    if dupes:
        fail(f"{len(dupes)} id(s) classified more than once: {sorted(dupes)[:10]}")
    if missing:
        fail(f"{len(missing)} announcement(s) got no verdict: {sorted(missing, reverse=True)[:10]}\n"
             "  Re-run classification for these ids - do not proceed with a partial list.")
    if unknown:
        fail(f"{len(unknown)} verdict(s) reference ids not in Stage 1: {sorted(unknown)[:10]}")

    bad = [v for v in verdicts if v.get("verdict") not in VALID_VERDICTS]
    if bad:
        fail(f"{len(bad)} verdict(s) outside {sorted(VALID_VERDICTS)}: "
             f"{[(v['id'], v.get('verdict')) for v in bad][:5]}")
    noreason = [v["id"] for v in verdicts if not (v.get("reason") or "").strip()]
    if noreason:
        fail(f"{len(noreason)} verdict(s) have no reason: {noreason[:10]}")

    print("  id reconciliation : OK (no missing, duplicate, or unknown ids)")
    print("  verdict values    : OK")
    print("  reasons present   : OK")

    # --- merge, then apply scope from the sheet ------------------------------
    # The sheet is the authority on which companies are tracked and which sector
    # each sits in, so scope is decided here in code rather than by the AI.
    idx = load_index()
    merged, out_of_scope, not_in_sheet = [], 0, 0
    for v in verdicts:
        r = rows[v["id"]]
        code = str(r["SYMBOL"]).strip()
        entries = idx["by_code"].get(code, [])
        ok, why = idx["in_scope"](code)

        verdict, category, reason = v["verdict"], v.get("category", ""), v["reason"]
        if verdict != "drop" and not ok:
            verdict, category = "drop", "out_of_scope"
            reason = f"Skipped: {why}"
            out_of_scope += 1
        if verdict == "keep" and not entries:
            not_in_sheet += 1          # still kept - reported in the daily email

        merged.append({
            "announcement_id": v["id"],
            "verdict": verdict,
            "verdict_ai": v["verdict"],          # what the AI said, before scope rules
            "category": category,
            "reason": reason,
            "tadawul_code": code,
            "company": r["indexName"],
            "headline": r["SHORT_DESC"],
            "news_date_ast": r["newsDateStr"],
            "url": r["url"],
            "in_sheet": bool(entries),
            "sheet_rows": [e["row"] for e in entries],
            "sheet_section": entries[0]["section"] if entries else None,
        })
    merged.sort(key=lambda x: x["announcement_id"], reverse=True)

    if out_of_scope:
        print(f"  scope filter      : {out_of_scope} dropped (insurance / out of scope)")
    if not_in_sheet:
        print(f"  not in the sheet  : {not_in_sheet} kept announcements have no row - "
              "these go in the email, not the sheet")

    counts = Counter(m["verdict"] for m in merged)
    cats = Counter(m["category"] for m in merged)

    artifact = {
        "_stage": "02_classified",
        "_note": "Every Stage 1 announcement with its verdict and reason. "
                 "kept + flagged go on to Stage 3; drops are retained for audit.",
        "validated_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "source_window_ast": [raw["window_from_ast"], raw["window_to_ast"]],
        "total": len(merged),
        "counts": dict(counts),
        "categories": dict(cats.most_common()),
        "out_of_scope_dropped": out_of_scope,
        "kept_not_in_sheet": [m["announcement_id"] for m in merged if m["verdict"]=="keep" and not m["in_sheet"]],
        "kept_ids": [m["announcement_id"] for m in merged if m["verdict"] == "keep"],
        "flagged_ids": [m["announcement_id"] for m in merged if m["verdict"] == "flag"],
        "announcements": merged,
    }
    out = OUT_DIR / "02_classified.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    print(f"\n  keep {counts['keep']}   flag {counts['flag']}   drop {counts['drop']}")
    print(f"  written : {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
