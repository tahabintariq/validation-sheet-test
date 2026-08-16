"""
Stage 5b - the morning meeting email.

WHAT IT REPLACES
The intern typed a company's ticker into A2 of the "Morning meeting sheet" (or
A24 for the six-month companies), waited for the table to fill in, took a
screenshot, and repeated it for every company that reported that day. Then
emailed the screenshots.

This builds the same tables from the same sheets and puts them in one email.
No Excel, no screenshots - see morning_layout.py for why that is possible.

WHICH COMPANIES
Exactly the ones Stage 4 has figures for today. That is the same list the intern
worked from: the companies that announced.

  input : output/04_write_preview.json, the workbook
  output: output/05b_morning.html, output/05b_morning_payload.json
"""

import html
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from morning_layout import load_layout, source_tables, LOOKUP_LABELS
from sheet_index import WORKBOOK, load_index

PKT = timezone(timedelta(hours=5))
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
CONFIG_FILE = HERE / "config.json"

# Row order of the rebuilt table, and how each line is produced.
#  lookup  - read from 'Qtrly Results Updated'
#  derived - arithmetic on the lines above, exactly as the sheet does it
LINES = [
    ("Revenue", "lookup", None),
    ("YoY", "derived", "yoy"),
    ("QoQ", "derived", "qoq"),
    ("COGS", "derived", "cogs"),
    ("Gross Profit", "lookup", None),
    ("Gross Margin", "derived", "gross_margin"),
    ("Operating Expenditure", "derived", "opex"),
    ("Operating Income", "lookup", None),
    ("Net Income", "derived_lookup", None),
    ("Market Cap (current)", "derived", "mktcap"),
    ("PE T12m", "derived", "pe_trailing"),
    ("PE Annualised", "derived", "pe_annualised"),
]

NAVY = "#002060"
FONT = "Arial,Helvetica,sans-serif"


def esc(x):
    return html.escape("" if x is None else str(x))


class _Error:
    """A cell that holds an Excel error rather than a figure.

    Empty and errored are NOT the same thing and the table treats them
    differently. Derayah genuinely reports no gross profit, so its blank cell
    counts as zero and COGS comes out as the whole revenue - which is what the
    sheet shows. But where the lookup itself failed (#N/A, #VALUE!), the figure
    is unknown, and anything computed from it has to stay blank rather than
    quietly pretend the missing part was zero.
    """
    def __bool__(self):
        return False


ERROR = _Error()


def known(x):
    return isinstance(x, (int, float))


# Excel's own conventions, which the table depends on:
#
#   an EMPTY cell counts as 0 in arithmetic. Derayah reports no gross profit, so
#   the sheet shows COGS = revenue - (blank) = the whole revenue, and a gross
#   margin of 0.0%. Treating blank as "unknown" instead would blank those lines
#   and quietly differ from what the analyst has always seen.
#
#   dividing by an empty or zero cell gives an error, which every ratio here
#   wraps in IFERROR and shows as blank.


def div(a, b):
    if a is ERROR or b is ERROR or not known(b) or b == 0:
        return None
    return (a if known(a) else 0) / b


def sub(a, b):
    if a is ERROR or b is ERROR:
        return None
    if a is None and b is None:
        return None
    return (a if known(a) else 0) - (b if known(b) else 0)


def build_table(ticker, block, src, idx_col, row_no):
    """Rebuild one company's table. Returns {line label: [value per period]}."""
    cols = [c for c, _ in block["cols"]]
    ppy = block["periods_per_year"]

    raw = {}
    for label in LOOKUP_LABELS:
        per_col = block["lookups"][label]
        series = []
        for c in cols:
            helper = per_col.get(c)
            if not helper:
                series.append(None)
                continue
            hcol, hrow = helper
            n = block["_mm"][hrow - 1][hcol - 1] if hrow - 1 < len(block["_mm"]) else None
            target = idx_col.get(n)
            v = None
            if target and row_no:
                srow = src[row_no - 1] if row_no - 1 < len(src) else []
                v = srow[target - 1] if target - 1 < len(srow) else None
            # Only a genuinely ABSENT cell counts as zero. Everything else that
            # is not a number is unknown, and that includes the empty STRING -
            # which is not the same as an empty cell. Most blanks in the source
            # are `IFERROR(...,"")`, and Excel treats that "" as text: dividing
            # by it gives #VALUE!, not zero. Derayah's gross margin is #VALUE!
            # in the sheet for exactly this reason. Reading "" as zero would
            # have invented a 0.0% margin and a COGS equal to the whole revenue.
            if known(v):
                series.append(v)
            elif v is None:
                series.append(None)
            else:
                series.append(ERROR)
        raw[label] = series

    n = len(cols)
    rev, gp = raw["Revenue"], raw["Gross Profit"]
    oi, ni = raw["Operating Income"], raw["Net Income"]

    out = {"Revenue": rev, "Gross Profit": gp,
           "Operating Income": oi, "Net Income": ni}
    def change(i, lag):
        if i < lag:
            return None
        r = div(rev[i], rev[i - lag])
        return None if r is None else r - 1

    out["YoY"] = [change(i, ppy) for i in range(n)]
    out["QoQ"] = [change(i, 1) for i in range(n)]
    out["COGS"] = [sub(rev[i], gp[i]) for i in range(n)]
    out["Gross Margin"] = [div(gp[i], rev[i]) for i in range(n)]
    out["Operating Expenditure"] = [sub(gp[i], oi[i]) for i in range(n)]

    mc = block["_mktcap"].get(ticker)
    out["Market Cap (current)"] = [mc] * n

    trailing = []
    for i in range(n):
        window = [ni[j] for j in range(max(0, i - ppy + 1), i + 1)]
        trailing.append(None if any(w is ERROR for w in window)
                        else (sum(w for w in window if known(w))
                              if any(known(w) for w in window) else None))
    out["PE T12m"] = [div(mc, trailing[i]) for i in range(n)]
    out["PE Annualised"] = [div(mc, ni[i] * ppy) if known(ni[i]) else None
                            for i in range(n)]

    # Blank out whatever the sheet itself leaves blank, so the email shows the
    # same coverage the analyst is used to rather than filling in gaps they have
    # never seen. Matched by label, and by the alias the sheet uses for the
    # annualised line ("PE Q*2 Annualized" / "PE H*2 Annualized").
    for label in list(out):
        pop = block["populated"].get(label)
        if pop is None and label == "PE Annualised":
            pop = next((v for k, v in block["populated"].items()
                        if k.startswith("PE") and "Annual" in k), None)
        if pop is None:
            continue
        out[label] = [v if cols[i] in pop else None for i, v in enumerate(out[label])]
    return out


def fmt(label, v):
    if v is None:
        return "-"
    if label in ("YoY", "QoQ", "Gross Margin"):
        return f"{v * 100:.1f}%"
    if label.startswith("PE"):
        return f"{v:.1f}"
    try:
        return f"{v:,.0f}"
    except (TypeError, ValueError):
        return esc(v)


# How many periods of history to show. The sheet keeps twelve, but the email
# has a hard size budget (see MAX_BYTES) and the oldest columns are the least
# useful in a morning meeting - the sheet itself leaves its first four blank on
# every derived row. Eight keeps two years of quarters, or four years of halves.
HISTORY = 8

# Gmail hides anything past roughly 102KB behind a "view entire message" link.
MAX_BYTES = 95_000


def render_company(name, block, table):
    """One company's table.

    Written to be small as well as legible: the font, size and right-alignment
    live on the <table>, not on each of the ~100 cells, because this markup is
    repeated once per company and forty copies of a long style string is what
    pushes the email over Gmail's clipping limit.
    """
    cols = block["cols"][-HISTORY - 1:]
    periods = [p for _, p in cols]
    hist, actual = periods[:-1], periods[-1]
    keep = len(cols)

    th = f"background:{NAVY};color:#fff;padding:3px 5px;font-weight:600"
    td = "padding:2px 5px;border-bottom:1px solid #eee"
    act = "background:#f6dedb;border-left:1px solid #bbb"

    p = [f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;'
         f'margin:0 0 18px;font-family:{FONT};font-size:11px;text-align:right;'
         f'width:100%">',
         f'<tr><td colspan="{keep + 1}" style="{th};text-align:center;font-size:12px">'
         f'{esc(name)}</td></tr>',
         f'<tr><td colspan="{len(hist) + 1}" style="{th};text-align:center">Historical</td>'
         f'<td style="{th};background:#7b1f1f;text-align:center">Actual</td></tr>',
         f'<tr><td style="{th}"></td>'
         + "".join(f'<td style="{th}">{esc(x)}</td>' for x in hist)
         + f'<td style="{th};background:#7b1f1f">{esc(actual)}</td></tr>']

    for label, _, _ in LINES:
        vals = table.get(label)
        if vals is None:
            continue
        vals = vals[-keep:]
        # the ratio lines are the sheet's own lighter blue
        soft = ";color:#4a7ebb" if label in ("YoY", "QoQ", "Gross Margin") \
            or label.startswith("PE") else ""
        cells = [f'<td style="{td};text-align:left{soft}">{esc(label)}</td>']
        cells += [f'<td style="{td}{soft}">{fmt(label, v)}</td>' for v in vals[:-1]]
        cells.append(f'<td style="{td};{act}{soft}">{fmt(label, vals[-1])}</td>')
        p.append("<tr>" + "".join(cells) + "</tr>")

    p.append("</table>")
    return "".join(p)


def main():
    plan_path = OUT_DIR / "04_write_preview.json"
    if not plan_path.exists():
        raise SystemExit(f"FATAL: {plan_path} not found. Run stage4_plan.py first.")
    plan = json.load(open(plan_path, encoding="utf-8"))

    idx = load_index()
    lay = load_layout(WORKBOOK)
    tabs = source_tables(WORKBOOK)
    for b in lay["blocks"]:
        b["_mm"] = lay["values"]
        b["_mktcap"] = tabs["mktcap"]
    quarterly = next(b for b in lay["blocks"] if not b["halfyear"])
    halfyear = next(b for b in lay["blocks"] if b["halfyear"])

    # the companies that reported today, in the order Stage 4 planned them
    wanted, order = {}, []
    for row in plan["plan"]:
        if row["action"] not in ("write", "overwrite") or row["metric"] == "Updated On":
            continue
        key = (row["tadawul_code"], row["row"])
        if key not in wanted:
            order.append(key)
            wanted[key] = {"code": row["tadawul_code"], "sheet_row": row["row"],
                           "company": row["company"],
                           "section": row.get("section") or ""}

    built, skipped = [], []
    for key in order:
        info = wanted[key]
        srow = tabs["src"][info["sheet_row"] - 1] if info["sheet_row"] - 1 < len(tabs["src"]) else []
        ticker = (srow[0] or "").strip() if srow and isinstance(srow[0], str) else ""
        if not ticker:
            skipped.append({**info, "why": "no Bloomberg ticker in column A of that row"})
            continue
        # Which of the two tables this company belongs in is decided by how it
        # reports, not by its market - the same rule Stage 4 uses. The half-year
        # table's columns ARE half-years, so a quarterly company put in it would
        # be read against the wrong periods.
        is_half = "halfyear" in info["section"].lower().replace(" ", "")
        block = halfyear if is_half else quarterly
        table = build_table(ticker, block, tabs["src"], tabs["index_to_col"],
                            info["sheet_row"])
        if not any(v is not None for v in table["Revenue"] + table["Net Income"]):
            skipped.append({**info, "ticker": ticker,
                            "why": "the morning sheet finds no history for this ticker"})
            continue
        name = tabs["name"].get(ticker) or info["company"]
        # Is today's figure actually in the sheet yet? This table is built FROM
        # the sheet, so until stage4_write has run the newest column is empty -
        # and the newest column is the entire point of the email.
        actual_empty = not any(known(table[k][-1]) for k in
                               ("Revenue", "Net Income", "Operating Income"))
        built.append({**info, "ticker": ticker, "name": str(name),
                      "block": block["name"], "actual_empty": actual_empty,
                      "html": render_company(name, block, table)})

    run_date = datetime.now(PKT).strftime("%d %B %Y")

    # A full table is about 11KB of HTML and Gmail hides anything past ~102KB,
    # so roughly eight companies fit in one message. On a quiet day that is the
    # whole list and there is one email; in earnings week - the 11-12 Aug sample
    # has 39 - it splits. Splitting beats the alternatives: an attachment makes
    # the reader open a file before seeing a number, and letting Gmail clip it
    # would silently hide whichever companies fell off the end.
    parts, current, used = [], [], 0
    for b in built:
        size = len(b["html"])
        if current and used + size > MAX_BYTES:
            parts.append(current)
            current, used = [], 0
        current.append(b)
        used += size
    if current:
        parts.append(current)

    def page_for(group, n, of):
        head = f"Morning meeting — {esc(run_date)}"
        if of > 1:
            head += f' <span style="color:#888;font-size:14px">part {n} of {of}</span>'
        out = [f'<div style="font-family:{FONT};font-size:13px;color:#1a1a1a;'
               f'max-width:1000px;margin:0 auto;padding:14px">',
               f'<h1 style="font-size:18px;margin:0 0 2px">{head}</h1>',
               f'<p style="color:#666;font-size:12px;margin:0 0 16px">'
               f'{len(built)} companies reported'
               + (f", {len(group)} in this part" if of > 1 else "")
               + '. Figures in millions of SAR, straight from the sheet.</p>']
        for kind, title in (("quarterly", "Quarterly reporters"),
                            ("half-year", "Six-month reporters")):
            sub_group = [b for b in group if b["block"] == kind]
            if not sub_group:
                continue
            out.append(f'<h2 style="font-size:14px;margin:22px 0 10px;color:{NAVY};'
                       f'border-bottom:2px solid {NAVY};padding-bottom:3px">'
                       f'{title} ({len(sub_group)})</h2>')
            out.extend(b["html"] for b in sub_group)
        if n == of and skipped:
            out.append('<h2 style="font-size:14px;margin:22px 0 8px;color:#8a5a00">'
                       f'No table for these ({len(skipped)})</h2><ul style="font-size:12px">')
            out.extend(f'<li>{esc(s["company"])} ({esc(s["code"])}) — {esc(s["why"])}</li>'
                       for s in skipped)
            out.append("</ul>")
        out.append('<div style="margin-top:20px;padding-top:8px;border-top:1px solid #ddd;'
                   'color:#888;font-size:11px">Rebuilt from the "Morning meeting sheet" '
                   'formulas. Concensus and Hilal are left for the analyst.</div></div>')
        return "".join(out)

    pages = [page_for(g, i + 1, len(parts)) for i, g in enumerate(parts)]

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "05b_morning.html").write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Morning meeting {esc(run_date)}</title></head><body>"
        + "<hr style='margin:40px 0;border:0;border-top:3px dashed #bbb'>".join(pages)
        + "</body></html>", encoding="utf-8")

    cfg = json.load(open(CONFIG_FILE, encoding="utf-8")) if CONFIG_FILE.exists() else {}
    ecfg = cfg.get("email") or {}
    of = len(parts)
    payload = {
        "_note": "Morning meeting tables. This script does not send. One entry "
                 "per email - more than one only when the day is too big for a "
                 "single message without Gmail clipping it.",
        "prepared_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "to": ecfg.get("morning_recipients") or ecfg.get("recipients") or [],
        "emails": [
            {"subject": f"Morning meeting — {run_date} — {len(built)} companies"
                        + (f" (part {i + 1} of {of})" if of > 1 else ""),
             "html_body": pages[i],
             "companies": [b["company"] for b in parts[i]],
             "bytes": len(pages[i])}
            for i in range(of)],
        "counts": {"companies": len(built), "skipped": len(skipped), "emails": of},
    }
    json.dump(payload, open(OUT_DIR / "05b_morning_payload.json", "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)

    print("Stage 5b - morning meeting tables (draft)\n")
    blank = [b for b in built if b["actual_empty"]]
    if blank and len(blank) > len(built) / 2:
        print("  " + "!" * 66)
        print(f"  WARNING: {len(blank)} of {len(built)} companies have an EMPTY "
              "newest column.")
        print("  These tables are built FROM the workbook, so today's figures only")
        print("  appear once they have been written to it. Run")
        print("      python stage4_write.py --confirm")
        print("  first, then re-run this. Otherwise the email shows history only.")
        print("  " + "!" * 66 + "\n")
    print(f"  companies with a table : {len(built)}")
    for kind in ("quarterly", "half-year"):
        n = len([b for b in built if b["block"] == kind])
        print(f"     {kind:<12}        : {n}")
    print(f"  no table               : {len(skipped)}")
    print(f"  emails                 : {of}" + ("" if of == 1 else "  (split so Gmail does not clip them)"))
    for i, pg in enumerate(pages):
        print(f"     part {i+1}: {len(parts[i]):>2} companies, {len(pg):,} bytes")
    for s in skipped[:6]:
        print(f"     {s['code']:<6} {str(s['company'])[:26]:<28} {s['why']}")
    print(f"\n  page    : {OUT_DIR / '05b_morning.html'}")
    print(f"  payload : {OUT_DIR / '05b_morning_payload.json'}")
    print("\n  NOT SENT - same as 5a, sending happens from the payload.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
