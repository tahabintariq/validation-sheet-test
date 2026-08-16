"""
Stage 3a - open each kept announcement and save its contents for the AI to read.

This step does NO interpretation and produces NO figures of its own. It fetches
the page and writes it out in two forms:

  output/pages/<id>.txt   the announcement as readable text - this is what the
                          AI reads to pick the numbers
  output/03_tables.json   the same tables as JSON, for reference and for the
                          daily email

Reading the page and reporting the figures is the AI's job (see
stage3_extract.md). Code does not choose or copy any number.

Announcement pages carry up to two tables we care about:
  - "Current Quarter"  -> the quarter figures        (main market, quarterly reporters)
  - "Current Period"   -> the cumulative 6/9 months  (and the ONLY table for
                          Nomu half-year reporters, which have no quarter table)

Corrections and addenda have NO financial table. They carry an "Explanation"
table instead: what was wrong, what is correct, and a link to the original
announcement. Those are captured separately.

Units differ per announcement - Millions, Thousands, or Actual riyals - and are
read from the last row of each table. Never assumed.

PARSER: must be lxml, not html.parser. The site emits unbalanced </p> tags
inside table cells; html.parser silently drops every row after the first
malformed cell, which on a correction page loses the "Correct Statement" row -
i.e. the corrected number itself. lxml recovers like a browser does.

  input : output/02_classified.json   (verdict == keep)
  output: output/03_tables.json
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.saudiexchange.sa"
PAGE_URL = (BASE + "/wps/portal/saudiexchange/newsandreports/issuer-news/"
            "issuer-announcements?locale=en&page=1")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/153.0"

PKT = timezone(timedelta(hours=5))
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
PAGES_DIR = OUT_DIR / "pages"
CONFIG_FILE = HERE / "config.json"

# -> millions. Billions is rare but real: Saudi Aramco reports in it, and it is
# the largest company on the exchange. It was missing until 16 Aug 2026 because
# the 11-12 Aug test sample happened to contain only the other three (Actual 50,
# Thousands 36, Millions 26) - Aramco had announced on 4 Aug, just outside it.
UNIT_FACTOR = {"billions": 1000.0, "millions": 1.0,
               "thousands": 0.001, "actual": 0.000001}


def log(m):
    print(m, flush=True)


def load_skips():
    """Companies the client has asked us to leave alone for now (config.json).

    They are still reported - as 'skipped', with a reason - so that a company
    quietly disappearing is never mistaken for a company that did not report.
    """
    if not CONFIG_FILE.exists():
        return set(), None
    cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
    ins = (cfg.get("skip") or {}).get("insurance") or {}
    if not ins.get("enabled"):
        return set(), None
    return set(ins.get("tadawul_codes") or []), ins.get("reason")


def open_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})
    r = s.get(PAGE_URL, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"FATAL: cookie GET returned {r.status_code}. See stage1 notes on the WAF headers.")
    log(f"  session opened  http={r.status_code}")
    return s


def clean(t):
    return re.sub(r"\s+", " ", t).strip()


def to_number(txt):
    """'5,998,038' -> 5998038.0 ; '-' / '' -> None. Keeps sign, drops commas."""
    t = (txt or "").strip().replace(",", "")
    if t in ("", "-", "--", "N/A", "NA"):
        return None
    t = t.replace("(", "-").replace(")", "")     # (123) style negatives
    try:
        return float(t)
    except ValueError:
        return None


def parse_units(text):
    """'All figures are in (Thousands) Saudi Arabia, Riyals' -> ('thousands', 0.001)

    An unrecognised scale is returned NAMED, e.g. ('unrecognised: Trillions',
    None), rather than as a bare None. The difference matters: a bare None reads
    the same as "this table has no units row at all", and the two need different
    fixes. Billions was missing for months and showed up only as an empty units
    field on Saudi Aramco, which told nobody what was actually wrong.
    """
    m = re.search(r"\(\s*([A-Za-z]+)\s*\)", text)
    if not m:
        return None, None
    word = m.group(1)
    u = word.lower()
    if u in UNIT_FACTOR:
        return u, UNIT_FACTOR[u]
    return f"unrecognised: {word}", None


def parse_tables(html):
    """Return the financial tables and any correction table, as structured rows."""
    soup = BeautifulSoup(html, "lxml")     # see PARSER note in the docstring
    out, correction = [], None
    for t in soup.find_all("table"):
        trs = t.find_all("tr")
        rows = []
        for tr in trs:
            cells = [clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c != ""]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            continue
        header = rows[0]
        if not header or "element list" not in header[0].lower():
            continue

        joined = " ".join(header).lower()

        # correction / addendum page - key/value explanation table
        if "explanation" in joined and correction is None:
            fields, link = {}, None
            for tr in trs:
                cells = tr.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                k = clean(cells[0].get_text(" ", strip=True))
                v = clean(cells[1].get_text(" ", strip=True))
                a = cells[1].find("a", href=True)
                if a:
                    link = a["href"]
                if k and k.lower() != "element list":
                    fields[k] = v
            if fields:
                correction = {"fields": fields, "previous_announcement_url": link}
            continue

        if "current quarter" in joined:
            kind = "quarter"
        elif "current period" in joined:
            kind = "period"
        elif "current year" in joined:
            # Annual filing. Its table is headed "Element List | Current Year |
            # Previous Year | %Change" - no quarter column and no nine-month
            # column, so Q4 and 2H have to be worked out by subtraction later.
            # Verified on Hedab Alkhaleej (93400) and Amlak International
            # (93350), both FY2025. Before this branch existed the whole table
            # was skipped and the page was misread as a correction.
            kind = "annual"
        else:
            continue          # accumulated-losses / narrative tables

        units, factor = None, None
        elements = []
        for r in rows[1:]:
            if r[0].lower().startswith("all figures are in"):
                units, factor = parse_units(r[0])
                continue
            elements.append({
                "element": r[0],
                "current": to_number(r[1]) if len(r) > 1 else None,
                "prior_year": to_number(r[2]) if len(r) > 2 else None,
                "prev_quarter": to_number(r[4]) if len(r) > 4 else None,
                "raw": r[1:4],
            })
        out.append({"kind": kind, "columns": header, "units": units,
                    "to_millions": factor, "elements": elements})
    return out, correction


def page_as_text(html, headline):
    """The announcement's own tables, rendered as plain text for the AI to read.

    Site chrome (menus, tickers, watchlist) is left out - it is the same on every
    page and only makes the announcement harder to read. Nothing inside the
    announcement's own tables is dropped, altered or reformatted beyond
    whitespace, so what the AI reads is what the page says.
    """
    soup = BeautifulSoup(html, "lxml")
    lines = [f"ANNOUNCEMENT: {headline}", ""]
    for t in soup.find_all("table"):
        trs = t.find_all("tr")
        rows = []
        for tr in trs:
            cells = [clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c != ""]
            if cells:
                rows.append(cells)
        if len(rows) < 2 or "element list" not in rows[0][0].lower():
            continue
        lines.append("-" * 70)
        for r in rows:
            lines.append(" | ".join(r))
        lines.append("")
    return "\n".join(lines)


def parse_period_hint(desc):
    """Best-effort period end + length from the headline. Never guessed at silently -
    if these come out None the next stage has to ask."""
    end = None
    m = (re.search(r"(\d{4})-(\d{2})-(\d{2})", desc) or re.search(r"(\d{2})-(\d{2})-(\d{4})", desc))
    if m:
        g = m.groups()
        end = f"{g[0]}-{g[1]}-{g[2]}" if len(g[0]) == 4 else f"{g[2]}-{g[1]}-{g[0]}"
    else:
        MONTHS = ["january","february","march","april","may","june","july","august",
                  "september","october","november","december"]
        MN = "January|February|March|April|May|June|July|August|September|October|November|December"
        m = re.search(rf"({MN})\s+(\d{{1,2}}),?\s+(\d{{4}})", desc, re.I)          # June 30, 2026
        if m:
            end = f"{m.group(3)}-{MONTHS.index(m.group(1).lower())+1:02d}-{int(m.group(2)):02d}"
        else:
            m = re.search(rf"(\d{{1,2}})\s+({MN})\s+(\d{{4}})", desc, re.I)        # 30 June 2026
            if m:
                end = f"{m.group(3)}-{MONTHS.index(m.group(2).lower())+1:02d}-{int(m.group(1)):02d}"
    length = None
    m = re.search(r"\(\s*(three|six|nine|twelve)\s+months?\s*\)", desc, re.I)
    if m:
        length = m.group(1).lower()
    elif re.search(r"\bannual\b", desc, re.I):
        # Annual headlines do not carry a "(Twelve Months)" marker - they read
        # "announces its Annual Financial results for the period ending on
        # 2025-12-31". Without this the length came back None and the annual
        # filing looked like a period of unknown length.
        length = "twelve"
    return end, length


def build_prior_lookup(session):
    """Only annual filings need this, so it is built lazily the first time one
    turns up. It reads the sheet once, purely to learn whether a company's row
    reports quarterly or half-yearly - the figures themselves come from the
    company's own earlier announcement, never from the sheet."""
    from sheet_index import load_index
    from prior_announcement import find_prior_cumulative, bases_for_company
    idx = load_index()

    def prior_for(code, period_end):
        try:
            year = int(str(period_end).split("-")[0])
        except (ValueError, AttributeError):
            return {"found": False, "why": "could not tell which year from the period end"}
        bases = bases_for_company(idx, code)
        if not bases:
            return {"found": False, "why": "company is not in the sheet"}
        return [dict(basis=b,
                     **find_prior_cumulative(session, code, year, b, PAGES_DIR))
                for b in bases]

    return prior_for


def main():
    src = json.load(open(OUT_DIR / "02_classified.json", encoding="utf-8"))
    kept = [a for a in src["announcements"] if a["verdict"] == "keep"]
    skip_codes, skip_reason = load_skips()

    _prior = {"fn": None}

    def prior_for(code, period_end):
        if _prior["fn"] is None:
            _prior["fn"] = build_prior_lookup(session)
        return _prior["fn"](code, period_end)

    log(f"Stage 3a: {len(kept)} kept announcements")
    if skip_codes:
        log(f"  skip list active ({len(skip_codes)} codes): {skip_reason}")

    session = open_session()
    results, failures, skipped = [], [], []

    for i, a in enumerate(kept, 1):
        if a["tadawul_code"] in skip_codes:
            skipped.append({
                "announcement_id": a["announcement_id"],
                "tadawul_code": a["tadawul_code"],
                "company": a["company"],
                "headline": a["headline"],
                "news_date_ast": a["news_date_ast"],
                "url": a["url"],
                "skipped_reason": skip_reason,
            })
            log(f"  [{i:>2}/{len(kept)}] {a['tadawul_code']:>5} {a['company'][:22]:<22} SKIPPED")
            continue
        try:
            r = session.get(a["url"], timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"http {r.status_code}")
            tables, correction = parse_tables(r.text)
            if not tables and not correction:
                raise RuntimeError("no financial table and no correction table found")
            end, length = parse_period_hint(a["headline"])

            PAGES_DIR.mkdir(parents=True, exist_ok=True)
            txt_path = PAGES_DIR / f"{a['announcement_id']}.txt"
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(page_as_text(r.text, a["headline"]))
            results.append({
                "announcement_id": a["announcement_id"],
                "tadawul_code": a["tadawul_code"],
                "company": a["company"],
                "headline": a["headline"],
                "news_date_ast": a["news_date_ast"],
                "url": a["url"],
                "page_text_file": f"pages/{a['announcement_id']}.txt",
                "page_type": "correction" if correction and not tables else "financial",
                "period_end": end,
                "period_length": length,
                "has_quarter_table": any(t["kind"] == "quarter" for t in tables),
                "has_annual_table": any(t["kind"] == "annual" for t in tables),
                # For an annual filing only: the company's own earlier
                # announcement, whose cumulative the full year is measured
                # against. Never read from the sheet - see prior_announcement.py.
                "prior_announcement": prior_for(a["tadawul_code"], end)
                                      if any(t["kind"] == "annual" for t in tables) else None,
                "tables": tables,
                "correction": correction,
            })
            if correction and not tables:
                log(f"  [{i:>2}/{len(kept)}] {a['tadawul_code']:>5} {a['company'][:22]:<22} "
                    f"CORRECTION ({len(correction['fields'])} fields)")
            else:
                marks = "".join("Q" if t["kind"] == "quarter" else "P" for t in tables)
                log(f"  [{i:>2}/{len(kept)}] {a['tadawul_code']:>5} {a['company'][:22]:<22} "
                    f"tables={marks} units={tables[0]['units']} end={end} {length or ''}")
        except Exception as e:
            failures.append({"announcement_id": a["announcement_id"], "tadawul_code": a["tadawul_code"],
                             "company": a["company"], "url": a["url"], "error": f"{type(e).__name__}: {e}"})
            log(f"  [{i:>2}/{len(kept)}] {a['tadawul_code']:>5} {a['company'][:22]:<22} FAILED: {e}")
        time.sleep(0.3)      # be polite to the exchange

    artifact = {
        "_stage": "03_tables",
        "_note": "Announcement tables as printed. No interpretation yet - the element "
                 "names are verbatim. 'to_millions' is the factor to convert this "
                 "announcement's figures into millions (the sheet's unit).",
        "fetched_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "count": len(results),
        "failed": len(failures),
        "skipped": len(skipped),
        "failures": failures,
        "skipped_announcements": skipped,
        "announcements": results,
    }
    out = OUT_DIR / "03_tables.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    log(f"\n  fetched : {len(results)}   skipped : {len(skipped)}   failed : {len(failures)}")
    log(f"  written : {out}")
    if skipped:
        log("  NOTE: skipped companies go in the daily email - they are not silently dropped.")
    if failures:
        log("  NOTE: failures are listed in the artifact - they must be resolved, not ignored.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
