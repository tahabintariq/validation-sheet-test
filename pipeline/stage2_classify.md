# Stage 2 — Classification spec

This is the instruction the cloud routine follows. The routine (Claude) reads the
Stage 1 artifact and writes its verdicts to `output/02_verdicts_model.json`.
`stage2_validate.py` then checks that output and produces `02_classified.json`.

## Input

`output/01_raw_announcements.json` → for each announcement use only:
`announcement_id`, `SYMBOL`, `SHORT_DESC`.

Send the **whole list in one call**. Do not chunk.

## What we are collecting

The firm tracks the **quarterly results of listed companies** — the reported
figures (revenue, gross profit, net profit, and similar). An announcement is
only kept if it is a company reporting those results, or a correction to them.

## Task

Decide, from `SHORT_DESC` alone, whether the announcement is one we log.

### keep
- A company announcing its **interim financial results** (any interim period —
  three months, six months, nine months). Category `interim_results`.
- A company announcing its **annual financial results** — the full year.
  Category `annual_results`. Typical wording: "announces its Annual Financial
  results for the period ending on 2025-12-31". "Annual", "yearly", "full year"
  and "twelve months" all count.
- A **correction** or **addendum** that clearly refers to an interim **or
  annual** financial results announcement. These matter: a correction may change
  a figure we have already logged. Categories `correction_to_interim` and
  `correction_to_annual`.
- Wording varies — "interim", "preliminary", "consolidated interim" all count.

**Why annual results are kept.** Two figures in the sheet exist nowhere else.
Q4 is never announced on its own — it is the full year minus the first three
quarters. The second half for the companies that report every six months is the
full year minus the first half. Both are worked out in Stage 3. Without annual
announcements those columns can never be filled.

Do not confuse an annual **results** announcement with the annual **report**,
the annual general assembly, or the board's dividend recommendation for the
year. Those carry no results table and are drops.

### drop
- Anything else, **including announcements that merely contain a financial
  figure**: accumulated losses, dividend distributions, capital changes,
  credit facilities, contract awards, board/CEO changes, general assemblies,
  ratings, operational updates.
- A correction/addendum whose subject is *not* interim results (e.g. a
  correction to a credit facility announcement) is a drop.
- **Fund statements** — "Announcement by [X] Capital for providing the public
  with the interim financial statements of [Y] REIT/ETF Fund". These are fund
  disclosures by an asset manager, not a listed company's own results.
  Category `fund_statements`. CONFIRMED drop: the firm tracks company quarterly
  results only, not funds.
- **Inability to post** — "announces its inability to post interim financial
  results". Category `inability_to_post`. CONFIRMED drop: there is no figure in
  it, and only announcements carrying figures are logged.

### flag
Reserved for genuinely unclear text. Do **not** use it merely because a
financial figure is present. The one legitimate use:
- A correction/addendum that does not say what it is correcting, so it cannot
  be ruled in or out from the headline alone.

## Output — `output/02_verdicts_model.json`

```json
{ "verdicts": [ { "id": 97606, "verdict": "keep",
                  "category": "correction_to_interim",
                  "reason": "Correction to an interim financial results announcement" } ] }
```

- One entry per input announcement — **every id, no exceptions**.
- `verdict` ∈ `keep` | `drop` | `flag`.
- `category` — short slug; groups similar items so a rule can be reviewed or
  flipped wholesale (e.g. `interim_results`, `fund_statements`,
  `inability_to_post`, `dividends`, `board_changes`).
- `reason` — one short clause. This becomes the audit trail in the digest email.

Keyed by id, never by position. The validator rejects missing, duplicate, or
unknown ids rather than silently accepting a short list.

## Settled scope

- Funds (REIT / ETF interim statements) — **drop**. 33 of 125 in the 11–12 Aug
  sample, so this is the largest single exclusion. A fund's **annual** statement
  is a drop for the same reason.
- Inability to post — **drop**. No figures to log.
- Corrections and addenda to company results — **keep**. They can change a
  figure already written to the sheet, which is why no server-side type filter
  is used.
- Annual results — **keep** (added 16 Aug 2026). Q4 and the second half exist
  only here.
