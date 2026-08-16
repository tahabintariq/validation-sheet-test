"""
Stage 3 (validate) - check what the AI read off the pages, then write 03_extracted.json.

The AI reads each announcement and reports the figures. This step does not
second-guess which line it chose - that is the AI's job. It checks the things
code can check without judgement:

  1. every kept announcement got an entry, exactly once
  2. the units are one we know, and the conversion to millions is right
  3. the figure it reported actually appears in the page text
  4. the element name it cited actually appears in the page text

(3) and (4) are the important ones. They catch a mistyped or half-remembered
number - the failure that is invisible once it sits in a spreadsheet. They do
not constrain which line the AI picks.

  input : output/02_classified.json, output/03_tables.json,
          output/pages/*.txt, output/03_extracted_model.json
  output: output/03_extracted.json
"""

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKT = timezone(timedelta(hours=5))
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
PAGES = OUT_DIR / "pages"

METRICS = ("revenue", "gross_profit", "operating_profit", "net_income")
# Must stay in step with UNIT_FACTOR in stage3_fetch_tables.py. Billions is real:
# Saudi Aramco reports in it. Until 16 Aug 2026 it was missing here, and because
# an unknown unit fails the whole validation, a single Aramco announcement would
# have stopped the entire morning's run.
FACTOR = {"billions": 1000.0, "millions": 1.0,
          "thousands": 0.001, "actual": 0.000001}


def fail(msg):
    print(f"\nFAILED: {msg}", flush=True)
    sys.exit(1)


def normalise(s):
    return (s or "").replace(",", "").replace(" ", "").lstrip("-").strip()


def main():
    tables = json.load(open(OUT_DIR / "03_tables.json", encoding="utf-8"))
    model = json.load(open(OUT_DIR / "03_extracted_model.json", encoding="utf-8"))
    meta = {a["announcement_id"]: a for a in tables["announcements"]}
    rows = model.get("extractions") or []

    print(f"Stage 3 validation: {len(meta)} announcements, {len(rows)} extractions")

    seen = Counter(r["announcement_id"] for r in rows)
    missing = [i for i in meta if i not in seen]
    dupes = [i for i, n in seen.items() if n > 1]
    unknown = [i for i in seen if i not in meta]
    if missing:
        fail(f"{len(missing)} announcement(s) not extracted: {sorted(missing, reverse=True)[:10]}")
    if dupes:
        fail(f"extracted more than once: {sorted(dupes)[:10]}")
    if unknown:
        fail(f"extraction(s) for unknown ids: {sorted(unknown)[:10]}")
    print("  coverage        : OK")

    problems, checked_vals, checked_lines = [], 0, 0
    checked_derivations = [0]      # list so the inner loop can bump it
    for r in rows:
        aid = r["announcement_id"]
        units = r.get("units_as_printed")
        page = (PAGES / f"{aid}.txt").read_text(encoding="utf-8") if (PAGES / f"{aid}.txt").exists() else ""
        flat = page.replace(",", "").replace(" ", "")

        if r.get("quarter_metrics") or r.get("period_metrics") or r.get("annual_metrics"):
            if units not in FACTOR:
                problems.append(f"{aid}: unknown units {units!r}")
                continue

        # every set is checked - each figure must be on the page, the arithmetic
        # right, and the cited line really present
        for which in ("quarter_metrics", "period_metrics", "annual_metrics",
                      "correction_metrics"):
            block = r.get(which) or {}
            for k in METRICS:
                m = block.get(k)
                if not m:
                    continue
                printed, millions = m.get("as_printed"), m.get("millions")
                parts = [x.strip() for x in str(printed).split("+")] if printed else []
                deriv = m.get("derivation")

                if units in FACTOR and parts and which != "correction_metrics":
                    try:
                        printed_m = round(sum(float(x.replace(",", "")) for x in parts)
                                          * FACTOR[units], 6)
                    except ValueError:
                        problems.append(f"{aid} {which} {k}: cannot read figure {printed!r}")
                        printed_m = None

                    if printed_m is None:
                        pass
                    elif deriv:
                        # A derived Q4 or second half. 'as_printed' is the full
                        # year off the annual page; 'millions' is what is left
                        # once the company's own earlier cumulative is taken off
                        # it. Every part of that is re-done here - the AI does
                        # the subtraction, but nothing goes to the sheet on its
                        # word alone.
                        full = deriv.get("full_year")
                        prior_m = deriv.get("prior_millions")
                        result = deriv.get("result")
                        prior_printed = deriv.get("prior_as_printed")
                        prior_id = deriv.get("prior_announcement_id")

                        if full is None or abs(printed_m - full) > 1e-4:
                            problems.append(
                                f"{aid} {which} {k}: derivation.full_year {full} does not "
                                f"match the printed figure {printed} in {units} ({printed_m})")
                        elif prior_m is None:
                            problems.append(
                                f"{aid} {which} {k}: derived but no prior figure was given")
                        elif result is None or abs(round(full - prior_m, 6) - result) > 1e-4:
                            problems.append(
                                f"{aid} {which} {k}: {full} - {prior_m} = "
                                f"{round(full - prior_m, 6)}, but derivation.result "
                                f"says {result}")
                        elif millions is None or abs(result - millions) > 1e-4:
                            problems.append(
                                f"{aid} {which} {k}: derived {result} but millions "
                                f"reports {millions} - these must agree")
                        else:
                            # The subtracted figure must really appear on the
                            # earlier announcement's own page. This is the same
                            # check applied to every other figure, and it is what
                            # catches a half-remembered number.
                            ppath = PAGES / f"{prior_id}.txt"
                            if prior_printed and ppath.exists():
                                pflat = ppath.read_text(encoding="utf-8").replace(
                                    ",", "").replace(" ", "")
                                if normalise(prior_printed) not in pflat:
                                    problems.append(
                                        f"{aid} {which} {k}: subtracted {prior_printed!r} "
                                        f"but it does not appear on announcement "
                                        f"{prior_id}'s page")
                                else:
                                    checked_derivations[0] += 1
                            elif prior_printed and not ppath.exists():
                                problems.append(
                                    f"{aid} {which} {k}: cannot check the subtracted figure "
                                    f"- page for announcement {prior_id} was not saved")
                            else:
                                checked_derivations[0] += 1
                    elif millions is None or abs(printed_m - millions) > 1e-6:
                        problems.append(f"{aid} {which} {k}: {printed} in {units} -> "
                                        f"expected {printed_m}, got {millions}")

                for part in parts:
                    if normalise(part) and normalise(part) not in flat:
                        problems.append(f"{aid} {which} {k}: figure {part!r} does not appear on the page")
                    else:
                        checked_vals += 1

                for line in m.get("lines", []):
                    if line[:28] not in page:
                        problems.append(f"{aid} {which} {k}: cited line {line[:40]!r} not on the page")
                    else:
                        checked_lines += 1

    if problems:
        print(f"\n  {len(problems)} problem(s) found:")
        for p in problems[:25]:
            print(f"    - {p}")
        fail("extraction did not pass validation - fix before writing to the sheet")

    print(f"  figures on page : OK ({checked_vals} checked)")
    print(f"  lines on page   : OK ({checked_lines} checked)")
    if checked_derivations[0]:
        print(f"  subtractions    : OK ({checked_derivations[0]} re-computed - "
              "Q4 / second-half figures derived from an annual filing)")

    merged = []
    for r in sorted(rows, key=lambda x: -x["announcement_id"]):
        a = meta[r["announcement_id"]]
        merged.append({**r,
                       "tadawul_code": a["tadawul_code"], "company": a["company"],
                       "headline": a["headline"], "news_date_ast": a["news_date_ast"],
                       "url": a["url"], "page_type": a["page_type"]})

    review = [m for m in merged if m["needs_review"]]
    artifact = {
        "_stage": "03_extracted",
        "_note": "Figures as read off each announcement by the AI, converted to millions. "
                 "'lines' records which element name was used, so any mapping decision "
                 "can be checked against the page later.",
        "validated_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "count": len(merged),
        "needs_review_count": len(review),
        "needs_review_ids": [m["announcement_id"] for m in review],
        "extractions": merged,
    }
    with open(OUT_DIR / "03_extracted.json", "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    print(f"\n  extracted : {len(merged)}   needs review : {len(review)}")
    print(f"  written   : {OUT_DIR / '03_extracted.json'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
