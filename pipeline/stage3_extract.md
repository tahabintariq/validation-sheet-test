# Stage 3 — reading the numbers off the announcement

The AI reads each announcement and reports the figures. Code does not pick or
copy any number; it only fetches the pages and checks the output afterwards.

## Input

- `output/pages/<announcement_id>.txt` — the announcement's tables as text
- `output/03_tables.json` — the same content as JSON, plus company and period info

## What we log

Four figures per company: **Revenue, Gross Profit, Operating Profit, Net Income**.

The line names differ by company type — decide from the table in front of you.
`stage3_mapping.md` describes what each type usually looks like; it is guidance,
not a fixed list. If a table does not fit any known shape, say so rather than
forcing a match.

## Which table

A page carries one or more of three financial tables. Report each one you find
into its own block — Stage 4 decides which block a given row needs, because the
same company can sit on a quarterly row and a half-year row at once.

| Table on the page | Goes into | What it is |
|---|---|---|
| `Element List \| Current Quarter \| …` | `quarter_metrics` | the three months |
| `Element List \| Current Period \| …` | `period_metrics` | the cumulative 6 or 9 months |
| `Element List \| Current Year \| …` | `annual_metrics` | the full year — see below |

- Report **every** table the page has. Do not choose between them.
- A block for a table that is not on the page is `null`.
- Ignore the accumulated-losses table (`Element List | Amount | Percentage of
  the capital (%)`) and the explanation table.

## Units

The last row of each table states the scale. The sheet is in millions, so report
both the figure as printed and the figure converted to millions:

| Printed | Convert |
|---|---|
| Billions | × 1,000 |
| Millions | as is |
| Thousands | ÷ 1,000 |
| Actual | ÷ 1,000,000 |

Read the units on every announcement. They vary between companies reporting on
the same day, and between the two tables on a single page.

**Billions is rare but real** — Saudi Aramco reports in it. If the units row
names a scale that is not one of these four, do not guess a factor: set
`needs_review` and say which word the page used.

## Lines that add up

Some types have no single revenue line. Banks and finance companies report
financing and investment separately, and the sheet adds them:

```
Revenue      = Total Income From Special Commission of Financing
             + Total Income From Special Commission of Investment
Gross Profit = Net Income From Special Commission of Financing
             + Net Income From Special Commission of Investment
```

When a figure is a sum, list every line used.

If one side of the pair is **absent or shown as a dash**, use what is there — a
missing line means that company has none of that income, not that the page is
incomplete. Bidaya Finance, for example, has financing income but no investment
income, so its revenue is the single financing line.

## Do NOT flag these — they are normal

Flagging costs a person a minute, so it should mean something. These are all
legitimate and must be reported as ordinary figures:

- **Negative revenue.** Refiners and holding companies report it (SARCO: revenue
  −8,142,305). Revenue equal to gross profit is also fine — it just means the
  company reports no cost of sales.
- **Decimals inside a Thousands or Actual table.** `4,170.927` in a Thousands
  table is 4,170,927 riyals exactly. The decimals add precision; they do not
  indicate the wrong unit.
- **A missing or dashed line in a bank/finance table**, as above.

## Do flag these

- The only plausible line is one you would not normally log (e.g. a "Financial
  Impact" line offered in place of net profit).
- The figures belong to a **different company** than the one announcing.
- A correction gives **period** figures where the sheet needs the **quarter**.
- The units row is missing entirely, or the table shape matches nothing known.

## Annual announcements — you do the subtraction

An annual announcement reports **the full year and nothing else**. Its table is
headed `Element List | Current Year | Previous Year | %Change`. There is no
quarter column and no nine-month column.

But the sheet does not store the full year. It stores the last period of it:

```
Q4  =  full year  −  (Q1 + Q2 + Q3)        for a quarterly row
2H  =  full year  −  1H                    for a half-year row
```

**You do this arithmetic.** The number to subtract is handed to you in
`prior_announcement` in `03_tables.json`: **the company's own earlier
announcement**, found and fetched by code.

- a quarterly row → its Q3 filing, the nine months to 30 September
- a half-year row → its first-half filing, the six months to 30 June

You get that announcement's "Current Period" table verbatim, plus its units and
its page. **Pick the same metric line on both pages.** If you took Revenue from
`Sales/Revenue` on the annual page, take it from `Sales/Revenue` on the earlier
one too. A subtraction between two different lines is meaningless, and the
result looks perfectly ordinary in the sheet.

The earlier figures are **not** taken from the spreadsheet. That was tried and
was wrong: the sheet's earlier quarters are almost all `VLOOKUP`s from the
`Universe` feed, and subtracting a feed figure from an announcement figure
subtracts two different conventions.

A company on both a quarterly row and a half-year row gets an entry for each,
with its own `basis`. Use the one matching the row you are reporting for.

### Report three things

1. `annual_metrics` — the full-year figures exactly as printed. Always.
2. `quarter_metrics` — your derived Q4, **only** for a quarterly row.
3. `period_metrics` — your derived second half, **only** for a half-year row.

`prior_announcement` tells you which kind of row it is, in its `basis` field. A
company on two rows gets an entry for each; if the two disagree in kind, report
both derived blocks.

### Show your working

Every derived figure carries a `derivation`:

```json
"net_income": {
  "lines": ["Net profit (Loss)"],
  "as_printed": "27,211,443", "millions": 16.287872,
  "derivation": {
    "from": "annual",
    "full_year": 27.211443,
    "prior_announcement_id": 89408,
    "prior_period_end": "2025-06-30",
    "prior_line": "Net profit (Loss)",
    "prior_as_printed": "10,923,571",
    "prior_millions": 10.923571,
    "result": 16.287872
  }
}
```

- `millions` for a derived figure is **the result of the subtraction**, not the
  full-year number. The full year stays in `annual_metrics`.
- `prior_as_printed` must be the figure exactly as it appears on the earlier
  announcement. The checker looks for it on that page, so an invented or
  half-remembered number is caught.
- `prior_line` is the element name used there — normally identical to `lines`.

### When NOT to compute — this matters more than computing

Set `needs_review: true`, explain in `note`, and leave the derived block `null`:

- **The company is on a quarterly row but filed only half-yearly that year**, or
  the other way round — the period you need was never published. Flag it.
- **`prior_announcement.found` is false** — the company published no earlier
  results announcement for that period, or its page carried no "Current Period"
  table. `why` says which. There is nothing to subtract, so there is no figure.
  **Never** substitute a number from the spreadsheet instead.
- **The earlier announcement does not report that line** — e.g. it has Revenue
  and Net Profit but no Gross Profit, which is normal for Nomu companies. Then
  that one metric is `null`; the others can still be derived.
- **`later_announcements_for_this_period` is not empty** — the company announced
  that same period more than once, so the cumulative may have been corrected.
  Derive from the figures given, but flag it so a human checks.
- **The units are missing on either page** — Aramco's nine-month filing is a
  real example. Without units the two figures cannot be put on the same scale.
- **The result looks wrong for the business** — e.g. a Q4 larger than the whole
  year, or a sign flip that the other three quarters do not explain. Report it
  with the flag rather than quietly passing it on.
- **The company is not in the sheet** — there is nothing to subtract from.

A flagged annual filing costs somebody a minute. A wrong Q4 is a plausible
number in a financial model and may never be caught.

### Units

Do the subtraction **in millions**, after converting **both** figures.

The two announcements can state different units — the annual page may be in
Actual while the earlier one is in Thousands. Read the units row on each page
separately and convert each before subtracting. Subtracting before converting
is the single easiest way to produce a wrong number here.

## Corrections and addenda

These have no financial table — only "Incorrect statements" / "Correct Statement".

- If the correction changes a **figure we log**, report the corrected figure and
  set `corrects_a_number` to true.
- If it only rewords an explanation (very common), set `corrects_a_number` to
  false and report no figures.

## Output — `output/03_extracted_model.json`

```json
{ "extractions": [
  { "announcement_id": 97580,
    "units_as_printed": "millions",
    "period_end": "2026-06-30",
    "quarter_metrics": {
      "revenue":          { "lines": ["Sales/Revenue"], "as_printed": "12.15", "millions": 12.15 },
      "gross_profit":     { "lines": ["Gross Profit (Loss)"], "as_printed": "-7.75", "millions": -7.75 },
      "operating_profit": { "lines": ["Operational Profit (Loss)"], "as_printed": "-18.4", "millions": -18.4 },
      "net_income":       { "lines": ["Net Profit (Loss) Attributable to Shareholders of the Issuer"],
                            "as_printed": "-19.29", "millions": -19.29 }
    },
    "period_metrics": null,
    "annual_metrics": null,
    "correction_metrics": null,
    "corrects_a_number": false,
    "needs_review": false,
    "note": ""
  } ] }
```

- One entry per announcement, keyed by `announcement_id`.
- The four blocks are `quarter_metrics`, `period_metrics`, `annual_metrics` and
  `correction_metrics`. A block for a table the page does not have is `null`.
- Inside a block, a figure the announcement does not report → `null`. Do not
  substitute a similar line.
- `as_printed` keeps the figure exactly as it appears, including the `+` when it
  is a sum of two lines (`"3776 + 801"`). The checker re-does that arithmetic
  and looks for each part on the page, so an invented digit is caught.
- `millions` is the figure converted to millions — and for a derived Q4 or
  second half, the **result of the subtraction**.
- `needs_review: true` with a short `note` whenever the right line is unclear,
  the units are missing, the table has an unfamiliar shape, or an annual
  subtraction is not safe to make. Flagging costs a human one minute; a wrong
  figure in the sheet may never be noticed.
- `lines` records the exact element name(s) used, so any mapping decision can be
  checked later against the page.
