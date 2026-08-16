"""
Stage 1 - Fetch Saudi Exchange issuer announcements.

Two-step fetch (no browser, no login):
  1. GET the announcements page with a cookie-keeping session, purely to collect
     the firewall / load-balancer cookies (TS..., BIGipServer...). Body discarded.
  2. POST to the getAnnouncementListData endpoint with those cookies -> JSON.

WAF NOTE: the edge returns 403 unless a plausible minimal header set is sent.
User-Agent alone is NOT enough. Accept / Accept-Encoding / Connection are all
required. `requests` sends those three by default, so a plain Session with a
User-Agent override works -- do not "optimise" them away.

TIME WINDOW: rows carry newsDateStr in Riyadh local time (UTC+3, dd/MM/yyyy
HH:mm:ss). We select announcements by timestamp window. Neither Riyadh (UTC+3)
nor Pakistan (UTC+5) observes DST, so the offset is a fixed 2 hours and we use
fixed offsets rather than a tz database (no tzdata dependency, no DST edges).
11:00 PKT == 09:00 AST, always.

Rows come newest-first, so paging stops as soon as we fall below the window.
announcement_id (PRESS_REL_ID) is sequential and is used ONLY as a tiebreaker
at the exact boundary second, so a same-second announcement can neither be
double-logged nor skipped.

Output: output/01_raw_announcements.json
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://www.saudiexchange.sa"
PAGE_URL = (
    BASE + "/wps/portal/saudiexchange/newsandreports/issuer-news/"
    "issuer-announcements?locale=en&page=1"
)
LIST_ENDPOINT = (
    BASE + "/wps/portal/saudiexchange/newsandreports/issuer-news/issuer-announcements/"
    "!ut/p/z1/lY9NDoIwFITPwgk6Qnity6fGggEBoYrdGFakiaIL4_kl7sCf4Owm-SYzI6yohe2ah2ubu7t2zbn3R0unkAl-pJAhqpYglJpMpWYrn8RhCKhUE4otF5kvQ-g9hP0rjzIPUazzNEiwgwZNy-OLeEK_HSIZz2W_YLPQccKBIjkGPlwclbx_eAE_Rt4uxtRwccue9wRUSy-z/"
    "p0/IZ7_5A602H80O0HTC060SG6UT81DI1=CZ6_5A602H80O0HTC060SG6UT81D26=NJgetAnnouncementListData=/"
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/153.0"

# 1_-1 = All Forms. We deliberately do NOT filter by announcement type server-side:
# corrections and addenda to interim results carry their own type codes and would
# be silently dropped. Relevance is decided in Stage 2 by reading SHORT_DESC.
ANNOUNCEMENT_TYPE = "1_-1"

AST = timezone(timedelta(hours=3))   # Riyadh / Saudi Exchange - no DST
PKT = timezone(timedelta(hours=5))   # Pakistan - no DST

SITE_FMT = "%d/%m/%Y %H:%M:%S"

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
STATE_DIR = HERE / "state"
WATERMARK_FILE = STATE_DIR / "watermark.json"


def log(msg):
    print(msg, flush=True)


def parse_site_dt(s):
    """'12/08/2026 16:07:17' (Riyadh local) -> aware datetime."""
    return datetime.strptime(s.strip(), SITE_FMT).replace(tzinfo=AST)


def fmt(dt):
    """Show a moment in both market and local time, so windows are unambiguous."""
    return (f"{dt.astimezone(AST).strftime(SITE_FMT)} AST"
            f"  /  {dt.astimezone(PKT).strftime(SITE_FMT)} PKT")


def parse_user_dt(s, tz, label):
    """Accept 'dd/MM/yyyy HH:mm:ss' or 'dd/MM/yyyy HH:mm' or 'dd/MM/yyyy'."""
    s = s.strip()
    for f in (SITE_FMT, "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=tz)
        except ValueError:
            continue
    raise SystemExit(f"FATAL: could not parse {label} '{s}'. "
                     "Use dd/MM/yyyy [HH:mm[:ss]]")


def open_session():
    """Step 1: mint the firewall cookies."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})
    r = s.get(PAGE_URL, timeout=30)
    if r.status_code != 200:
        raise SystemExit(
            f"FATAL: cookie GET returned {r.status_code} (expected 200).\n"
            "  If 403: the WAF rejected the request. Check that Accept / Accept-Encoding /\n"
            "  Connection headers are being sent, and see cloud-IP smoke test notes."
        )
    cookies = list(s.cookies.keys())
    log(f"  session opened  http={r.status_code}  cookies={cookies}")
    if not any(c.startswith("TS") for c in cookies):
        log("  WARNING: no TS... firewall cookie was set; POSTs may be rejected.")
    return s


def fetch_page(session, page_number, page_size, attempts=3):
    """Step 2: POST one page of the announcement list."""
    body = {
        "annoucmentType": ANNOUNCEMENT_TYPE,  # sic - misspelled server-side, match exactly
        "symbol": "",
        "sectorDpId": "",
        "searchType": "",
        "fromDate": "",
        "toDate": "",
        "datePeriod": "",
        "productType": "",
        "advisorsList": "-1",
        "textSearch": "",
        "pageNumberDb": str(page_number),
        "pageSize": str(page_size),  # honoured only when the FULL body is sent
    }
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE,
        "Referer": PAGE_URL,
    }
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            r = session.post(LIST_ENDPOINT, headers=headers, data=body, timeout=30)
            if r.status_code == 200:
                return r.json()
            last_err = f"http {r.status_code}"
        except Exception as e:  # network hiccup / bad json
            last_err = f"{type(e).__name__}: {e}"
        if attempt < attempts:
            wait = 2 * attempt
            log(f"    page {page_number} failed ({last_err}); retry in {wait}s")
            time.sleep(wait)
    raise SystemExit(f"FATAL: page {page_number} failed after {attempts} attempts: {last_err}")


def load_state():
    if not WATERMARK_FILE.exists():
        return None, None
    with open(WATERMARK_FILE, encoding="utf-8") as f:
        st = json.load(f)
    ts = st.get("last_announcement_time")
    return (parse_site_dt(ts) if ts else None), st.get("last_announcement_id")


def save_state(last_dt, last_id, count):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(WATERMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_announcement_time": last_dt.astimezone(AST).strftime(SITE_FMT),
                "last_announcement_time_note": "Riyadh local time (UTC+3), as published by the site",
                "last_announcement_id": last_id,
                "updated_at": datetime.now(PKT).strftime(SITE_FMT) + " PKT",
                "announcements_in_last_run": count,
            },
            f,
            indent=2,
        )


def main():
    ap = argparse.ArgumentParser(
        description="Fetch announcements in a time window (times are Riyadh/AST unless --pkt).")
    ap.add_argument("--since", help="window start, dd/MM/yyyy [HH:mm[:ss]] (exclusive)")
    ap.add_argument("--until", help="window end, dd/MM/yyyy [HH:mm[:ss]] (inclusive)")
    ap.add_argument("--pkt", action="store_true",
                    help="interpret --since/--until as Pakistan time instead of Riyadh time")
    ap.add_argument("--days", type=int,
                    help="shorthand: window = the last N days up to now")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--max-pages", type=int, default=20,
                    help="safety cap; stop and flag rather than paging forever")
    ap.add_argument("--commit", action="store_true",
                    help="advance the stored watermark (omit for a dry run)")
    args = ap.parse_args()

    user_tz = PKT if args.pkt else AST
    state_dt, state_id = load_state()

    # ---- resolve the window -------------------------------------------------
    boundary_id = None
    if args.since:
        since_dt = parse_user_dt(args.since, user_tz, "--since")
        window_src = "--since"
    elif args.days:
        since_dt = datetime.now(AST) - timedelta(days=args.days)
        window_src = f"--days {args.days}"
    elif state_dt:
        since_dt = state_dt
        boundary_id = state_id  # tiebreaker for same-second rows at the boundary
        window_src = "stored watermark"
    else:
        since_dt = datetime.now(AST) - timedelta(days=1)
        window_src = "default (last 24h) - no watermark yet"

    until_dt = parse_user_dt(args.until, user_tz, "--until") if args.until else None

    log("Stage 1: fetching announcements")
    log(f"  window from : {fmt(since_dt)}   [{window_src}]")
    log(f"  window to   : {fmt(until_dt) if until_dt else 'now (no upper bound)'}")

    session = open_session()

    collected = []
    seen_ids = set()
    stop_reason = None
    pages_fetched = 0
    skipped_future = 0

    for page in range(1, args.max_pages + 1):
        data = fetch_page(session, page, args.page_size)
        rows = data.get("announcementList") or []
        pages_fetched = page
        if not rows:
            stop_reason = "empty page"
            break

        below_window = False
        for row in rows:
            aid = int(row["announcementNumber"])
            dt = parse_site_dt(row["newsDateStr"])

            if dt < since_dt:
                below_window = True
                break
            # exact-boundary tiebreaker: same second as the watermark -> only take
            # rows the previous run had not already seen
            if dt == since_dt and boundary_id is not None and aid <= boundary_id:
                below_window = True
                break
            if dt == since_dt and boundary_id is None:
                below_window = True  # --since is exclusive
                break
            if until_dt and dt > until_dt:
                skipped_future += 1
                continue  # newer than the window; keep scanning downward
            if aid in seen_ids:
                continue  # defensive: a row shifting between pages
            seen_ids.add(aid)
            row["announcement_id"] = aid
            row["url"] = BASE + row["announcementUrl"]
            collected.append(row)

        log(f"  page {page}: {len(rows)} rows, kept {len(collected)} so far")

        if below_window:
            stop_reason = "reached start of window"
            break
    else:
        stop_reason = "hit max-pages cap"

    if stop_reason == "hit max-pages cap":
        log(f"  WARNING: stopped at the {args.max_pages}-page cap without reaching the "
            "start of the window. Unusual volume or a gap - review before committing.")
    if skipped_future:
        log(f"  note: skipped {skipped_future} announcement(s) newer than --until")

    newest = collected[0] if collected else None
    newest_dt = parse_site_dt(newest["newsDateStr"]) if newest else since_dt
    newest_id = newest["announcement_id"] if newest else boundary_id

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "_stage": "01_raw_announcements",
        "_note": "Raw rows exactly as returned by getAnnouncementListData, plus "
                 "announcement_id (int) and absolute url. Stage 2 reads SHORT_DESC. "
                 "All site times (newsDateStr) are Riyadh local, UTC+3.",
        "fetched_at_pkt": datetime.now(PKT).strftime(SITE_FMT),
        "window_from_ast": since_dt.astimezone(AST).strftime(SITE_FMT),
        "window_from_pkt": since_dt.astimezone(PKT).strftime(SITE_FMT),
        "window_to_ast": until_dt.astimezone(AST).strftime(SITE_FMT) if until_dt else None,
        "window_to_pkt": until_dt.astimezone(PKT).strftime(SITE_FMT) if until_dt else None,
        "window_source": window_src,
        "watermark_new_time_ast": newest_dt.astimezone(AST).strftime(SITE_FMT),
        "watermark_new_id": newest_id,
        "watermark_committed": bool(args.commit),
        "pages_fetched": pages_fetched,
        "page_size": args.page_size,
        "announcement_type_filter": ANNOUNCEMENT_TYPE,
        "stop_reason": stop_reason,
        "skipped_newer_than_until": skipped_future,
        "count": len(collected),
        "announcements": collected,
    }
    out_path = OUT_DIR / "01_raw_announcements.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    log("")
    log(f"  fetched   : {len(collected)} announcements over {pages_fetched} page(s)")
    log(f"  stop      : {stop_reason}")
    log(f"  newest    : {fmt(newest_dt)}" + ("" if args.commit else "   (dry run, watermark not saved)"))
    log(f"  written   : {out_path}")

    if args.commit and collected:
        save_state(newest_dt, newest_id, len(collected))
        log(f"  committed : watermark now {newest_dt.astimezone(AST).strftime(SITE_FMT)} AST")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
