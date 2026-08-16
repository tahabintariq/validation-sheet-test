# Stage 3 — which line on the page goes into which column

Every announcement page has a **quarter table** (first table, "Current Quarter")
and a **period table** ("Current Period", the cumulative 6/9 months).

**We always read the quarter table.** Verified: Al Rajhi's sheet value for
2Q2026 is 7012, which is its quarter figure — not the half-year figure.

The line names change depending on what kind of company it is. Below is what
each type reports, checked against numbers already in the sheet.

---

## 1. Normal companies

| Sheet column | Line on the page |
|---|---|
| Revenue | Sales/Revenue |
| Gross Profit | Gross Profit (Loss) |
| Operating Profit | Operational Profit (Loss) |
| Net Income | Net Profit (Loss) Attributable to Shareholders |

**Checked — Al Othaim (4001), 1Q2026:**
sheet 2965.191 / 645.306 / 74.170 / 53.661 → page 2,965.19 / 645.31 / 74.17 / 53.66 ✅

## 2. Banks and finance companies

These have no revenue or gross profit line at all. Two of the four are **sums
of two lines**.

| Sheet column | Line on the page |
|---|---|
| Revenue | Total Income From Special Commission of Financing **+** Total Income From Special Commission of Investment |
| Gross Profit | Net Income From Special Commission of Financing **+** Net Income From Special Commission of Investment |
| Operating Profit | Total Operations Profit (Loss) |
| Net Income | Net Profit (Loss) Attributable to Shareholders |

**Checked — Al Rajhi (1120), 2Q2026:**
12,130 + 2,354 = 14,484 = sheet Revenue ✅
7,290 + 1,017 = 8,307 = sheet Gross Profit ✅
Total Operations Profit 10,884 = sheet ✅ · Net Profit 7,012 = sheet ✅

**Checked again — Riyad Bank (1010), 2Q2026:**
5,998,038 + 940,809 = 6,938,847 → sheet 6938.847 ✅
3,190,108 + 186,081 = 3,376,189 → sheet 3376.189 ✅
4,735,866 → 4735.866 ✅ · 2,649,028 → 2649.028 ✅

Do **not** use "Net Profit before Zakat and Income Tax" — the sheet uses the
after-zakat, attributable-to-shareholders line.

## 3. Insurance companies

| Sheet column | Line on the page |
|---|---|
| Revenue | Insurance Revenues |
| Net Income | Net Profit (Loss), After Zakat, Attributable To Shareholders |
| Gross Profit | leave empty — never filled for insurers |
| Operating Profit | leave empty |

**Checked on the "Insurance" sheet, which is hand-filled from the announcements.
Three insurers, 2Q2026, every line exact:**

- Tawuniya (8010): revenue 6227.981, net profit 321.767 ✅
- Bupa (8210): revenue 5299.909, net profit 306.779 ✅
- Malath (8020): revenue 256.971, net profit 1.992 ✅

Do **not** compare against past-quarter cells in "Qtrly Results Updated" — those
are `VLOOKUP`s from the Universe data feed and use different definitions, so
they will not match the announcement.

---

## Units — must be read every time

The last row of the table says either **(Millions)** or **(Thousands)**.
The sheet is in **millions**, so thousands must be divided by 1000.

Both appear in practice: Al Rajhi reports in millions, Riyad Bank in thousands,
SVCP in millions, Bidaya Finance in thousands. Getting this wrong puts a number
in 1000x too large, so it is checked per announcement, never assumed.

## Precision

Companies reporting in millions publish 2 decimals (2,965.19). Existing sheet
values carry more (2965.191138) because they were taken from the full
statements. Announcement figures are therefore correct but slightly rounded.
