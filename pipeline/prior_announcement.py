"""
Find the earlier announcement an annual filing has to be measured against.

WHY
An annual announcement reports the full year and nothing else. The sheet stores
the last period of the year, so it has to be worked out:

    Q4 = full year  -  the nine months to 30 September
    2H = full year  -  the six months to 30 June

The subtrahend used to be read out of the spreadsheet. That was wrong in a way
that was easy to miss: the sheet's earlier quarters are almost all `VLOOKUP`s
into the `Universe` feed, and measured across the sheet, 250+ of the 277
quarterly rows have a feed formula in Q1 of the year. Subtracting a feed figure
from an announcement figure is subtracting two different conventions, and the
result is not a quarter.

So we ask the company instead. Its own Q3 (or H1) announcement carries the
cumulative figure in its "Current Period" table - same issuer, same convention,
same source as the annual figure. One subtraction, nothing to reconcile.

WHAT THIS RETURNS
The earlier announcement's period table **verbatim**, not figures picked out of
it. Choosing which line is Revenue is the AI's job on every run - that is a
settled decision - and the AI must pick the SAME line on both pages for the
subtraction to mean anything. Handing it pre-chosen numbers would take that
choice away and hide the mismatch.

The list endpoint's `symbol` field really does filter to one company (verified
16 Aug 2026 against Amlak 1182, Hedab 9631 and Aramco 2222 - each returned only
its own announcements), so this costs one small request per company plus one
page fetch.
"""

import time

from stage1_fetch import (LIST_ENDPOINT, BASE, PAGE_URL, ANNOUNCEMENT_TYPE)
from stage3_fetch_tables import parse_tables, page_as_text, parse_period_hint

HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
    "Referer": PAGE_URL,
}

# What we need, by how the row reports.
TARGET = {
    "quarterly": {"month_day": "09-30", "length": "nine months to 30 September"},
    "half-year": {"month_day": "06-30", "length": "six months to 30 June"},
}

RESULT_WORDS = ("financial results", "financial statements", "interim financial")


def _list_for_symbol(session, symbol, page, size=50):
    body = {
        "annoucmentType": ANNOUNCEMENT_TYPE,   # sic - misspelled server-side
        "symbol": str(symbol).strip(),
        "sectorDpId": "", "searchType": "", "fromDate": "", "toDate": "",
        "datePeriod": "", "productType": "", "advisorsList": "-1", "textSearch": "",
        "pageNumberDb": str(page), "pageSize": str(size),
    }
    r = session.post(LIST_ENDPOINT, headers=HEADERS, data=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _candidates(session, symbol, want_end, max_pages=6):
    """Every results announcement from this company for the period we want.

    Newest first, because a company can announce the same period more than once
    - a correction, or a re-issue - and the later one supersedes.
    """
    found, page = [], 1
    while page <= max_pages:
        data = _list_for_symbol(session, symbol, page)
        rows = data.get("announcementList") or []
        if not rows:
            break
        for r in rows:
            desc = r.get("SHORT_DESC") or ""
            if not any(w in desc.lower() for w in RESULT_WORDS):
                continue
            end, _ = parse_period_hint(desc)
            if end == want_end:
                found.append(r)
        total = int(data.get("totalCount") or 0)
        if page * 50 >= total:
            break
        page += 1
        time.sleep(0.3)
    return found


def find_prior_cumulative(session, symbol, year, basis, pages_dir=None):
    """The cumulative figures an annual filing must be measured against.

    basis: 'quarterly' -> the nine months to 30 Sept
           'half-year' -> the six months to 30 June

    Never falls back to the spreadsheet. If the earlier announcement cannot be
    found, that is reported and the figure is flagged - quietly substituting a
    feed value would reintroduce exactly the problem this replaces.
    """
    spec = TARGET.get(basis)
    if not spec:
        return {"found": False, "why": f"unknown reporting basis {basis!r}"}
    want_end = f"{year}-{spec['month_day']}"

    try:
        cands = _candidates(session, symbol, want_end)
    except Exception as e:
        return {"found": False, "why": f"could not search this company's announcements: "
                                       f"{type(e).__name__}: {e}",
                "wanted_period_end": want_end}

    if not cands:
        return {"found": False,
                "wanted_period_end": want_end,
                "why": f"{symbol} published no results announcement for the "
                       f"{spec['length']} {year}. Without it there is nothing to "
                       f"subtract the full year from."}

    superseded = [c.get("PRESS_REL_ID") for c in cands[1:]]
    problems = []
    for cand in cands:
        aid = cand.get("PRESS_REL_ID") or cand.get("announcement_id")
        # The list gives announcementUrl as a RELATIVE path ("/wps/portal/..."),
        # which requests rejects outright. Build the absolute URL from the id -
        # the same form Stage 1 uses - and only fall back to the supplied one.
        url = (f"{BASE}/wps/portal/saudiexchange/newsandreports/issuer-news/"
               f"issuer-announcements/issuer-announcements-details/"
               f"?anId={aid}&anCat=1&locale=en")
        if not aid:
            raw = cand.get("url") or cand.get("announcementUrl") or ""
            url = raw if raw.startswith("http") else BASE + raw
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
        except Exception as e:
            # Never swallow this. A fetch that failed and a page with no table
            # are different problems, and reporting them as one cost real time.
            problems.append(f"{aid}: fetch failed ({type(e).__name__}: {e})")
            continue
        tables, _correction = parse_tables(r.text)
        period = next((t for t in tables if t["kind"] == "period"), None)
        if not period:
            # A correction page carries no financial table. Keep looking down the
            # list for the announcement that actually holds the figures, and say
            # so - a corrected cumulative changes the answer.
            problems.append(f"{aid}: page has no 'Current Period' table "
                            f"(kinds present: {[t['kind'] for t in tables] or 'none'})")
            continue

        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            (pages_dir / f"{aid}.txt").write_text(
                page_as_text(r.text, cand.get("SHORT_DESC") or ""), encoding="utf-8")

        return {
            "found": True,
            "announcement_id": aid,
            "url": url,
            "headline": cand.get("SHORT_DESC"),
            "news_date_ast": cand.get("newsDateStr"),
            "period_end": want_end,
            "period_is": spec["length"],
            "units": period.get("units"),
            "to_millions": period.get("to_millions"),
            "page_text_file": f"pages/{aid}.txt" if pages_dir else None,
            "table": period,
            "later_announcements_for_this_period": [s for s in superseded if s],
            "note": ("a later announcement exists for this same period - check it is "
                     "not a correction to these figures"
                     if superseded else ""),
        }

    return {"found": False,
            "wanted_period_end": want_end,
            "why": f"found {len(cands)} announcement(s) from {symbol} for the "
                   f"{spec['length']} {year}, but none yielded a 'Current Period' "
                   f"table. Details: " + "; ".join(problems),
            "announcement_ids_seen": [c.get("PRESS_REL_ID") for c in cands]}


def bases_for_company(idx, code):
    """Which reporting bases this company's row(s) use.

    A company can sit on a quarterly row and a half-year row at once, and then
    both cumulatives are needed - the two rows want different periods.
    """
    out = []
    for e in idx["by_code"].get(str(code).strip(), []):
        section = (e["section"] or "").lower().replace(" ", "")
        b = "half-year" if "halfyear" in section else "quarterly"
        if b not in out:
            out.append(b)
    return out
