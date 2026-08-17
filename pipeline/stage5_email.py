"""
Stage 5a - the daily summary email.

WHY THIS EXISTS
Everything the pipeline knows ends up in JSON files that nobody will open. This
turns one run into a page a person can read in a minute, and - more importantly -
tells them what it could NOT do. A pipeline that silently drops figures is worse
than no pipeline, because the sheet looks finished either way.

WHAT IT DOES NOT DO
It does not send. It writes a draft to output/05_email.html and stops. Sending
is an outward-facing action and the recipients have not been agreed yet (see
OPEN_QUESTIONS.md B3). send_email() is the single seam where that gets added.

ORDER OF THE PAGE
Deliberate: what needs a human comes FIRST, before the list of successes. A
person reading this at 08:30 should hit the exceptions before they lose
attention, not after forty rows of things that went fine.

  input : output/02_classified.json, output/03_extracted.json,
          output/04_write_preview.json, and output/05_write_result.json if the
          write has run
  output: output/05_email.html
"""

import html
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKT = timezone(timedelta(hours=5))
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
CONFIG_FILE = HERE / "config.json"

METRIC_ORDER = ["Revenue", "Gross Profit", "Operating Profit", "Net Income"]

# Plain English for the machine-readable reasons in the plan. The email is read
# by someone who does not know the code, so nothing should reach them as a slug.
BLOCKED_WORDING = {
    "formula_in_cell": ("The cell is linked to the data feed",
                        "Overwriting it would cut the company's link to Universe. "
                        "Left alone deliberately."),
    "cell_has_value":  ("The cell already has a number in it",
                        "Someone has filled this in by hand. Left alone."),
    "missing_column":  ("There is no column for this period yet",
                        "Someone needs to add it in Excel before this can be written."),
}


def load(name, required=True):
    p = OUT_DIR / name
    if not p.exists():
        if required:
            raise SystemExit(f"FATAL: {p} not found. Run the earlier stages first.")
        return None
    return json.load(open(p, encoding="utf-8"))


def load_email_config():
    cfg = json.load(open(CONFIG_FILE, encoding="utf-8")) if CONFIG_FILE.exists() else {}
    return cfg.get("email") or {}


def esc(x):
    return html.escape("" if x is None else str(x))


def num(x, dp=3):
    if x is None or x == "":
        return "-"
    try:
        return f"{float(x):,.{dp}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return esc(x)


# --------------------------------------------------------------------------
# gathering
# --------------------------------------------------------------------------

def gather(classified, extracted, plan, result):
    """Everything the page needs, worked out once so the rendering stays dumb."""
    by_id = {e["announcement_id"]: e for e in extracted["extractions"]}
    rows = plan["plan"]

    written = [p for p in rows if p["action"] in ("write", "overwrite")
               and p["metric"] != "Updated On"]
    blocked = [p for p in rows if p["action"] == "blocked"]
    superseded = [p for p in rows if p["action"] == "superseded"]
    overwrites = [p for p in rows if p["action"] == "overwrite"
                  and p["metric"] != "Updated On"]

    # Figures the AI was not confident about. These never reach the sheet, so
    # if nobody reads this section they are simply lost.
    flagged = [e for e in extracted["extractions"] if e.get("needs_review")]

    # Announcements that produced nothing. Split, because "not in the sheet" is
    # a different action for the reader than "we could not read it".
    skipped = plan.get("skipped") or []
    not_in_sheet, other_skips = [], []
    for s in skipped:
        reason = s.get("reason") or ""
        if "not in the sheet" in reason:
            not_in_sheet.append(s)
        elif reason.startswith("flagged for review in Stage 3"):
            # Already shown, with the same wording, under "not confident about
            # these figures". Listing them twice makes the reader check the same
            # announcement twice and pads the section that should be short.
            continue
        else:
            other_skips.append(s)

    # Derived Q4 / second-half figures, with the working, so the subtraction can
    # be checked without opening a JSON file.
    #
    # Blocked ones are included ON PURPOSE. A derived figure that could not be
    # written is the case where someone has to type it in themselves, so they
    # need the working more than anyone - showing it only for the successes
    # would hide it from exactly the reader who needs it.
    derived = [{**p, "d": p["derivation"], "landed": p["action"] in ("write", "overwrite")}
               for p in rows
               if p.get("derivation") and p["action"] != "superseded"]

    # Anything whose units the parser did not recognise. parse_units returns
    # these named ("unrecognised: Trillions") precisely so they can surface here.
    odd_units = [e for e in extracted["extractions"]
                 if str(e.get("units_as_printed") or "").startswith("unrecognised")]

    dropped = Counter()
    for a in classified["announcements"]:
        if a["verdict"] == "drop":
            dropped[a.get("category") or "other"] += 1

    return {
        "written": written, "blocked": blocked, "superseded": superseded,
        "overwrites": overwrites, "flagged": flagged, "not_in_sheet": not_in_sheet,
        "other_skips": other_skips, "derived": derived, "odd_units": odd_units,
        "dropped": dropped, "by_id": by_id,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

# EVERY style is written onto the element itself. Email clients are not
# browsers: Gmail and Outlook strip <style> blocks and drop the <head> entirely,
# so a stylesheet would arrive as unstyled text. That also rules out class
# selectors and :nth-child, which is why the zebra striping below is applied per
# row by hand rather than by a rule.
NAVY = "#002060"
# A short font stack on purpose. It is repeated on every cell, and Gmail CLIPS a
# message over roughly 102KB - it shows "[Message clipped]" and hides the rest
# behind a link. With the long -apple-system/Segoe UI/Roboto stack this page came
# to 93KB on an ordinary day and would have tipped over on a busy one, hiding the
# very sections the email exists for. Arial is present everywhere that matters.
FONT = "Arial,Helvetica,sans-serif"

S_BODY = (f"font-family:{FONT};font-size:14px;color:#1a1a1a;line-height:1.5;"
          "max-width:900px;margin:0 auto;padding:16px")
S_H1 = f"font-family:{FONT};font-size:19px;margin:0 0 2px;color:#1a1a1a"
S_H2 = (f"font-family:{FONT};font-size:15px;margin:26px 0 8px;padding-bottom:4px;"
        f"border-bottom:2px solid {NAVY};color:{NAVY}")
S_H3 = f"font-family:{FONT};font-size:13px;margin:16px 0 6px;color:#444"
S_P = f"font-family:{FONT};font-size:13px;color:#1a1a1a;margin:6px 0"
S_MUTED = f"font-family:{FONT};font-size:13px;color:#777;margin:6px 0"
S_SUB = f"font-family:{FONT};font-size:13px;color:#666;margin:0 0 18px"
# The font is set once on the table and inherited by its cells, rather than
# repeated on every one of them. On a day like 11-12 Aug that is several hundred
# repetitions of the same declaration, and the size matters (see FONT above).
S_TABLE = (f"border-collapse:collapse;width:100%;margin:8px 0 14px;"
           f"font-family:{FONT};font-size:13px")
S_TH = (f"background-color:{NAVY};color:#ffffff;text-align:left;padding:5px 8px;"
        "font-size:12px;font-weight:600")
S_TD = "padding:4px 8px;border-bottom:1px solid #e6e6e6;vertical-align:top"
S_TD_N = S_TD + ";text-align:right;white-space:nowrap"
ZEBRA = "background-color:#fafafa"
S_WORK = "font-family:Consolas,Menlo,monospace;font-size:12px;color:#333;white-space:nowrap"
S_FOOT = (f"font-family:{FONT};margin-top:26px;padding-top:10px;"
          "border-top:1px solid #ddd;color:#888;font-size:12px")

# background, text, left-border
TONE = {"ok":   ("#e6f4ea", "#136c2e", "#2e8b52"),
        "warn": ("#fdf1dc", "#8a5a00", "#e0a53a"),
        "stop": ("#fdecea", "#a3231a", "#c5392c")}


def tag(text, kind="ok"):
    bg, fg, _ = TONE[kind]
    return (f'<span style="background-color:{bg};color:{fg};padding:1px 7px;'
            f'border-radius:9px;font-family:{FONT};font-size:12px;'
            f'font-weight:600">{esc(text)}</span>')


def box(inner, kind="ok"):
    edge = TONE[kind][2]
    return (f'<div style="border-left:4px solid {edge};padding:9px 13px;margin:10px 0;'
            f'background-color:#fafafa;font-family:{FONT};font-size:13px">{inner}</div>')


def h2(t):
    return f'<h2 style="{S_H2}">{esc(t)}</h2>'


def h3(t):
    return f'<h3 style="{S_H3}">{esc(t)}</h3>'


def para(inner, muted=False):
    return f'<p style="{S_MUTED if muted else S_P}">{inner}</p>'


def table(headers, rows, aligns=None):
    if not rows:
        return ""
    aligns = aligns or [""] * len(headers)
    # bgcolor as well as the CSS. It is a plain HTML attribute, so a client that
    # strips or rewrites style rules cannot remove it - and without a background
    # these header cells are white text on white, which is how they arrived in
    # Gmail the first time: invisible until you selected them.
    out = [f'<table cellpadding="0" cellspacing="0" style="{S_TABLE}"><tr>'
           + "".join(f'<th bgcolor="{NAVY}" style="{S_TH}">{esc(h)}</th>'
                     for h in headers) + "</tr>"]
    for i, r in enumerate(rows):
        z = f";{ZEBRA}" if i % 2 else ""
        cells = "".join(
            f'<td style="{(S_TD_N if a == "n" else S_TD)}{z}">{c}</td>'
            for c, a in zip(r, aligns))
        out.append(f"<tr>{cells}</tr>")
    out.append("</table>")
    return "\n".join(out)


def render(g, classified, plan, result, meta):
    W, B = len(g["written"]), len(g["blocked"])
    needs_human = (len(g["flagged"]) + B + len(g["not_in_sheet"])
                   + len(g["other_skips"]) + len(g["odd_units"]))

    p = []
    p.append(f'<h1 style="{S_H1}">Saudi results — {esc(meta["run_date"])}</h1>')
    p.append(f'<p style="{S_SUB}">Announcements checked {esc(meta["window"])}<br>'
             f'{classified["total"]} announcements → {classified["counts"].get("keep",0)} kept '
             f'→ <b>{W} figures</b> for {plan["companies"]} companies</p>')

    # ---- status line ------------------------------------------------------
    if meta["written_for_real"]:
        p.append(box(f'{tag("WRITTEN", "ok")} {result["cells_written"]} cells written to '
                     f'<b>{esc(Path(result["workbook"]).name)}</b>. '
                     f'All {result["charts_intact"]} charts intact. Backup saved.', "ok"))
    else:
        p.append(box(f'{tag("NOT WRITTEN YET", "warn")} This is the plan only — nothing '
                     'has been written to the workbook. Run '
                     '<code>stage4_write.py --confirm</code> to apply it.', "warn"))

    # ---- 1. NEEDS A HUMAN -------------------------------------------------
    p.append(h2("Needs a person"))
    if not needs_human:
        p.append(para(f'{tag("Nothing", "ok")} Everything read cleanly and every '
                      'figure found a home.'))

    if g["flagged"]:
        p.append(h3(f"Not confident about these figures ({len(g['flagged'])})"))
        p.append(para("These were read but <b>not written</b>. They need checking "
                      "against the announcement by hand.", muted=True))
        p.append(table(
            ["Company", "Code", "What is unclear", "Announcement"],
            [[esc(e["company"]), esc(e["tadawul_code"]), esc(e.get("note") or "—"),
              f'<a href="{esc(e.get("url"))}">open</a>' if e.get("url") else "—"]
             for e in g["flagged"]]))

    if g["blocked"]:
        p.append(h3(f"Could not be written ({B})"))
        groups = defaultdict(list)
        for b in g["blocked"]:
            groups[b.get("blocked_reason") or "other"].append(b)
        for reason, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            title, why = BLOCKED_WORDING.get(reason, (reason, ""))
            p.append(para(f'<b>{esc(title)}</b> — {len(items)} figure(s). '
                          f'<span style="color:#777">{esc(why)}</span>'))
            p.append(table(
                ["Company", "Figure", "Cell", "Value we had"],
                [[esc(b["company"]), esc(b["metric"]),
                  esc(b.get("cell") or "—"), num(b.get("value"))]
                 for b in items[:40]],
                ["", "", "", "n"]))
            if len(items) > 40:
                p.append(para(f"… and {len(items)-40} more.", muted=True))

    if g["not_in_sheet"]:
        p.append(h3(f"Reported results but are not in the sheet ({len(g['not_in_sheet'])})"))
        p.append(para("Nothing was written for these. If they should be tracked, "
                      "a row needs adding.", muted=True))
        p.append(table(["Company", "Code"],
                       [[esc(s["company"]), esc(s["tadawul_code"])]
                        for s in g["not_in_sheet"]]))

    if g["odd_units"]:
        p.append(h3(f"Unrecognised units ({len(g['odd_units'])})"))
        p.append(box("The scale on these pages is not one the pipeline knows. "
                     "<b>No figure was taken from them.</b> The code needs the new "
                     "unit adding before these can be read.", "stop"))
        p.append(table(["Company", "Code", "What the page said"],
                       [[esc(e["company"]), esc(e["tadawul_code"]),
                         esc(e.get("units_as_printed"))] for e in g["odd_units"]]))

    if g["other_skips"]:
        p.append(h3(f"Announcements that produced no figures ({len(g['other_skips'])})"))
        p.append(table(["Company", "Code", "Why"],
                       [[esc(s["company"]), esc(s["tadawul_code"]),
                         esc(s.get("reason"))] for s in g["other_skips"]]))

    # ---- 2. DERIVED FIGURES ----------------------------------------------
    if g["derived"]:
        p.append(h2(f"Worked-out figures ({len(g['derived'])})"))
        p.append(para("Q4 and second-half figures are never announced on their own. "
                      "They are the full year minus the company&rsquo;s own earlier "
                      "cumulative figure. The working is shown so it can be checked.",
                      muted=True))
        stuck = [d for d in g["derived"] if not d["landed"]]
        if stuck:
            p.append(box(f'{tag(f"{len(stuck)} NEED TYPING IN", "warn")} These were '
                         'worked out correctly but the cell could not be written, so the '
                         'figure is not in the sheet. The working below is what to enter.',
                         "warn"))
        rows = []
        for d in g["derived"]:
            w = d["d"]
            mark = "" if d["landed"] else " " + tag("not written", "warn")
            rows.append([
                esc(d["company"]) + mark, esc(d["metric"]),
                esc(d.get("cell") or "—"),
                f'<span style="{S_WORK}">{num(w.get("full_year"))} − '
                f'{num(w.get("prior_millions"))} = <b>{num(w.get("result"))}</b></span>',
                esc(w.get("prior_period_end") or "—"),
            ])
        p.append(table(["Company", "Figure", "Cell", "Working (millions)",
                        "Earlier filing"], rows, ["", "", "", "", ""]))

    # ---- 3. CORRECTIONS ---------------------------------------------------
    if g["overwrites"]:
        p.append(h2(f"Values replaced ({len(g['overwrites'])})"))
        p.append(para("A correction, or a newer announcement, replaced something "
                      "already in the cell.", muted=True))
        p.append(table(
            ["Company", "Figure", "Cell", "Was", "Now", "Why"],
            [[esc(o["company"]), esc(o["metric"]), esc(o["cell"]),
              num(o.get("existing")), num(o.get("value")), esc(o.get("note"))]
             for o in g["overwrites"]],
            ["", "", "", "n", "n", ""]))

    # ---- 4. WHAT WAS WRITTEN ---------------------------------------------
    p.append(h2(f"Figures {'written' if meta['written_for_real'] else 'to write'} ({W})"))
    by_company = defaultdict(dict)
    order, cells = [], defaultdict(dict)
    for w in g["written"]:
        key = (w["company"], w["tadawul_code"], w.get("period"))
        if key not in by_company:
            order.append(key)
        by_company[key][w["metric"]] = w.get("value")
        cells[key][w["metric"]] = w.get("cell")
    rows = []
    for key in order:
        comp, code, period = key
        vals = by_company[key]
        rows.append([esc(comp), esc(code), esc(period)] +
                    [num(vals.get(m)) for m in METRIC_ORDER])
    p.append(table(["Company", "Code", "Period"] + METRIC_ORDER, rows,
                   ["", "", ""] + ["n"] * 4))

    # ---- 5. AUDIT TRAIL ---------------------------------------------------
    p.append(h2("Not results — ignored"))
    p.append(para("Every announcement is looked at; these were not company results, "
                  "so nothing was logged.", muted=True))
    p.append(table(["Kind", "Count"],
                   [[esc(k.replace("_", " ")), str(v)]
                    for k, v in g["dropped"].most_common()], ["", "n"]))

    p.append(f'<div style="{S_FOOT}">Generated {esc(meta["generated"])} PKT · '
             f'sheet &ldquo;{esc(plan["sheet"])}&rdquo; · '
             f'produced automatically from the day&rsquo;s announcements.</div>')

    inner = "".join(p)
    # The wrapper is for opening the file in a browser. Gmail throws away
    # everything outside <body>, which is exactly why the styles are inline.
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Saudi results {esc(meta['run_date'])}</title></head>"
            f'<body style="{S_BODY}">{inner}</body></html>')


def render_text(g, classified, plan, meta):
    """Plain-text alternative.

    Not decoration: a multipart email needs a text part for clients that refuse
    HTML, and some spam filters treat an HTML-only message as a small negative
    signal. It is a summary, not a transcription - anyone wanting the detail has
    the HTML right next to it.
    """
    L = [f"SAUDI RESULTS - {meta['run_date']}",
         f"Announcements checked {meta['window']}",
         f"{classified['total']} announcements -> "
         f"{classified['counts'].get('keep', 0)} kept -> {len(g['written'])} figures "
         f"for {plan['companies']} companies", ""]

    L.append("WRITTEN TO THE SHEET" if meta["written_for_real"]
             else "NOT WRITTEN YET - this is the plan only.")
    L.append("")

    needs = [("figures we were not confident about", g["flagged"]),
             ("figures that could not be written", g["blocked"]),
             ("companies not in the sheet", g["not_in_sheet"]),
             ("announcements with no figures", g["other_skips"]),
             ("announcements with unrecognised units", g["odd_units"])]
    live = [(label, items) for label, items in needs if items]
    if live:
        L.append("NEEDS A PERSON")
        for label, items in live:
            L.append(f"  {len(items):>4}  {label}")
    else:
        L.append("NEEDS A PERSON: nothing.")
    L.append("")

    if g["derived"]:
        L.append(f"WORKED-OUT FIGURES ({len(g['derived'])})")
        for d in g["derived"]:
            w = d["d"]
            flag = "" if d["landed"] else "   [NOT WRITTEN - type this in]"
            L.append(f"  {d['company'][:28]:<30}{d['metric']:<18}"
                     f"{num(w.get('full_year'))} - {num(w.get('prior_millions'))} "
                     f"= {num(w.get('result'))}{flag}")
        L.append("")

    L.append("Full detail is in the formatted version of this email.")
    return "\n".join(L)


def send_email(payload, cfg):
    """The one seam where sending gets added. Deliberately not implemented here.

    This script cannot send by itself, and that is a fact about the architecture
    rather than an omission. The Gmail connector lives in the Claude session, not
    in Python - it has no credentials of its own. So the split is:

        this script   ->  works out WHAT to send, and writes 05_email_payload.json
        whoever sends ->  reads that file and creates the draft

    Today the sender is a Claude session using the Gmail connector's create_draft.
    Later it is expected to be Microsoft Graph's sendMail, once the app
    registration for the OneDrive work exists. Neither of those changes anything
    above this line - the payload is the contract.
    """
    raise NotImplementedError(
        "This script does not send. It writes output/05_email_payload.json.\n"
        "  Create the draft from that payload (Gmail connector today, Graph later).\n"
        "  Recipients and auto-send vs draft are still open (OPEN_QUESTIONS.md B3)."
    )


def main():
    classified = load("02_classified.json")
    extracted = load("03_extracted.json")
    plan = load("04_write_preview.json")
    result = load("05_write_result.json", required=False)

    # The per-announcement list is the source of truth; the summary fields on
    # top of it are a convenience. Deriving them rather than trusting them keeps
    # the email working against any artifact that carries the announcements -
    # including the annual test fixture, which is the only place the derived-
    # figures section can be exercised until real annual filings appear.
    anns = classified.get("announcements") or []
    classified["total"] = len(anns)
    classified["counts"] = dict(Counter(a.get("verdict") for a in anns))

    # The result file survives from earlier runs, so it is only trusted when it
    # was produced AFTER the plan it claims to have applied. Otherwise the email
    # would report a stale success against today's figures.
    written_for_real = False
    if result:
        fmt = "%d/%m/%Y %H:%M:%S"
        try:
            written_for_real = (datetime.strptime(result["written_at_pkt"], fmt)
                                >= datetime.strptime(plan["planned_at_pkt"], fmt))
        except (KeyError, ValueError):
            written_for_real = False

    g = gather(classified, extracted, plan, result)
    # The end of the window is open when the run took "everything since X", so
    # it comes through as null. Printing "None" makes it look like a fault.
    window = classified.get("source_window_ast") or [None, None]
    frm, to = window[0] or "the start", window[1] or "now"
    meta = {
        "run_date": datetime.now(PKT).strftime("%d %B %Y"),
        "generated": datetime.now(PKT).strftime("%d/%m/%Y %H:%M"),
        "window": f"{frm} → {to} (Riyadh time)",
        "written_for_real": written_for_real,
    }

    cfg = load_email_config()
    page = render(g, classified, plan, result, meta)
    text = render_text(g, classified, plan, meta)
    out = OUT_DIR / "05_email.html"
    out.write_text(page, encoding="utf-8")

    # The subject carries the date and the one number that decides whether this
    # needs opening now. A subject that is identical every morning is both easy
    # to skim past and a small spam signal.
    needs_human = (len(g["flagged"]) + len(g["blocked"]) + len(g["not_in_sheet"])
                   + len(g["other_skips"]) + len(g["odd_units"]))
    subject = (f'{cfg.get("subject_prefix", "Saudi results")} — {meta["run_date"]} — '
               f'{len(g["written"])} figures'
               + (f", {needs_human} need a look" if needs_human else ""))

    payload = {
        "_note": "What to send. This script does not send it - see send_email().",
        "prepared_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "mode": cfg.get("mode", "draft"),
        "to": cfg.get("recipients") or [],
        "subject": subject,
        "html_body": page,
        "text_body": text,
        "counts": {"figures": len(g["written"]), "needs_human": needs_human,
                   "derived": len(g["derived"]), "written_for_real": written_for_real},
    }
    payload_path = OUT_DIR / "05_email_payload.json"
    json.dump(payload, open(payload_path, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print("Stage 5a - daily summary email (draft)\n")
    print(f"  subject : {subject}")
    print(f"  figures {'written' if written_for_real else 'planned'} : {len(g['written'])}")
    print(f"  needs a person             : {needs_human}")
    print(f"     not confident           : {len(g['flagged'])}")
    print(f"     could not be written    : {len(g['blocked'])}")
    print(f"     not in the sheet        : {len(g['not_in_sheet'])}")
    print(f"     no figures              : {len(g['other_skips'])}")
    print(f"     unrecognised units      : {len(g['odd_units'])}")
    print(f"  worked-out (Q4 / 2H)       : {len(g['derived'])}")
    print(f"  values replaced            : {len(g['overwrites'])}")
    print(f"\n  page    : {out}")
    print(f"  payload : {payload_path}")
    if cfg.get("recipients"):
        print(f"  to      : {', '.join(cfg['recipients'])} "
              f"(mode: {cfg.get('mode', 'draft')})")
    else:
        print("  to      : nobody configured yet (config.json -> email.recipients)")
    print("\n  NOT SENT. This script only prepares the email; creating the draft")
    print("  is done from the payload by whoever holds the mail credentials.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
