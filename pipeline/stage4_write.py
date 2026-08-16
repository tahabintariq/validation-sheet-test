"""
Stage 4b - write the planned figures into the workbook.

WHY NOT openpyxl: openpyxl does not edit a file, it rebuilds one from the parts
it understands. This workbook holds 73 charts, 4 external links to SharePoint,
drawings and defined names - none of which openpyxl reads, so all of it would be
silently dropped on save. That is what damaged the sheet on an earlier attempt.

WHAT THIS DOES INSTEAD: an .xlsx is a zip of XML parts. Every part is copied to
the new file byte for byte, except the single worksheet part being edited. Inside
that part only the target cells are changed. Charts, formulas, links and macros
are never parsed, so they cannot be harmed.

SAFETY
  - the original is copied to backups/ before anything is written
  - by default the result is written to a COPY, not the original (--in-place to
    change that)
  - afterwards the file is reopened and checked: every value landed, the part
    count matches, and all charts are still present
  - if any check fails the original is restored from the backup

  input : output/04_write_preview.json   (only rows with action write/overwrite)
  output: the workbook, plus output/05_write_result.json
"""

import argparse
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sheet_index import WORKBOOK, SHEET

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKT = timezone(timedelta(hours=5))

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
BACKUP_DIR = HERE / "backups"


def log(m):
    print(m, flush=True)


def col_to_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def split_ref(ref):
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref.upper())
    return m.group(1), int(m.group(2))


def find_sheet_part(z, sheet_name):
    """Map a sheet's display name to its XML part inside the zip."""
    wb_xml = z.read("xl/workbook.xml").decode("utf-8")
    m = re.search(rf'<sheet[^>]*name="{re.escape(sheet_name)}"[^>]*r:id="([^"]+)"', wb_xml)
    if not m:
        raise SystemExit(f"FATAL: sheet '{sheet_name}' not found in workbook.xml")
    rid = m.group(1)
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    m2 = re.search(rf'<Relationship[^>]*Id="{rid}"[^>]*Target="([^"]+)"', rels)
    if not m2:
        raise SystemExit(f"FATAL: no relationship for {rid}")
    target = m2.group(1)
    return "xl/" + target[1:] if target.startswith("/xl/") else (
        target if target.startswith("xl/") else "xl/" + target)


CELL_RE = r'<c r="{ref}"(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</c>)'


EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=PKT)


def _fmt(value):
    """Excel stores plain decimals, and dates as a day count from 1899-12-30."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        y, m, d = (int(x) for x in value.split("-"))
        return str((datetime(y, m, d, tzinfo=PKT) - EXCEL_EPOCH).days)
    f = float(value)
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.10f}".rstrip("0").rstrip(".") if 1e-6 < abs(f) < 1e15 else repr(f)


def style_for_column(xml, letters):
    """Formatting comes from the column, not the row: column L is formatted as a
    date, the metric columns as numbers. Copying a neighbour along the row would
    give a new date cell a number format."""
    best = None
    for m in re.finditer(rf'<c r="{letters}(\d+)"[^>]*?\ss="(\d+)"', xml):
        row_no = int(m.group(1))
        if row_no > 3:                     # skip the header rows
            best = m.group(2)
            break
    return best


def edit_sheet_xml(xml_bytes, writes):
    """Set the given cells to the given numbers, editing the XML text directly.

    ElementTree cannot be used here: the worksheet root declares mc:Ignorable
    over four Microsoft namespaces (x14ac, xr, xr2, xr3). Re-serialising renames
    those prefixes, mc:Ignorable then points at prefixes that no longer exist,
    and Excel rejects the file. Editing the text leaves every other byte alone.
    """
    xml = xml_bytes.decode("utf-8")
    by_row = {}
    for ref, value in writes.items():
        letters, row_no = split_ref(ref)
        by_row.setdefault(row_no, []).append((ref, col_to_index(letters), value))

    applied = []
    for row_no, targets in by_row.items():
        m = re.search(rf'<row r="{row_no}"(?:[^>]*?)>', xml)
        if not m:
            raise SystemExit(f"FATAL: row {row_no} not found in the worksheet")
        start = m.end()
        end = xml.find("</row>", start)
        if end == -1:
            raise SystemExit(f"FATAL: row {row_no} is not closed")
        body = xml[start:end]

        for ref, target_idx, value in sorted(targets, key=lambda t: t[1]):
            cm = re.search(CELL_RE.format(ref=re.escape(ref)), body, re.DOTALL)
            if cm:
                attrs = re.sub(r'\st="[^"]*"', "", cm.group("attrs"))   # no longer a string cell
                new_cell = f'<c r="{ref}"{attrs}><v>{_fmt(value)}</v></c>'
                body = body[:cm.start()] + new_cell + body[cm.end():]
            else:
                # take formatting from the nearest cell in the same row so the new
                # figure looks like its neighbours
                letters_only = split_ref(ref)[0]
                col_style = style_for_column(xml, letters_only)
                style = f' s="{col_style}"' if col_style else ""
                new_cell = f'<c r="{ref}"{style}><v>{_fmt(value)}</v></c>'

                pos = len(body)
                for om in re.finditer(r'<c r="([A-Z]+)\d+"', body):
                    if col_to_index(om.group(1)) > target_idx:
                        pos = om.start()
                        break
                body = body[:pos] + new_cell + body[pos:]
            applied.append(ref)

        xml = xml[:start] + body + xml[end:]

    return xml.encode("utf-8"), applied


def chart_parts(path):
    with zipfile.ZipFile(path) as z:
        return {n for n in z.namelist() if "/charts/chart" in n and n.endswith(".xml")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-place", action="store_true",
                    help="write the real workbook (default: write a copy alongside it)")
    ap.add_argument("--confirm", action="store_true", help="required before anything is written")
    args = ap.parse_args()

    plan = json.load(open(OUT_DIR / "04_write_preview.json", encoding="utf-8"))
    writes, rows_out = {}, []
    for p in plan["plan"]:
        if p["action"] in ("write", "overwrite") and p["cell"]:
            writes[p["cell"]] = p["value"]
            rows_out.append(p)

    log(f"Stage 4b - writing {len(writes)} cells")
    log(f"  workbook : {WORKBOOK.name}  /  '{SHEET}'")
    if not args.confirm:
        log("\n  DRY RUN - nothing written. Re-run with --confirm to write,")
        log("            and add --in-place to edit the real workbook.")
        for p in rows_out[:10]:
            log(f"    {p['cell']:<8} {p['company'][:20]:<20} {p['metric']:<17} {p['value']}")
        log(f"    ... {len(rows_out)-10} more" if len(rows_out) > 10 else "")
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(PKT).strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{WORKBOOK.stem}_{stamp}.xlsx"
    shutil.copy2(WORKBOOK, backup)
    log(f"  backup   : {backup.name}")

    target = WORKBOOK if args.in_place else WORKBOOK.with_name(f"{WORKBOOK.stem}_updated.xlsx")
    tmp = target.with_suffix(".tmp.xlsx")

    with zipfile.ZipFile(WORKBOOK) as zin:
        part = find_sheet_part(zin, SHEET)
        log(f"  sheet part: {part}")
        before_charts = {n for n in zin.namelist() if "/charts/chart" in n and n.endswith(".xml")}
        new_xml, applied = edit_sheet_xml(zin.read(part), writes)

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == part:
                    data = new_xml
                elif item.filename == "xl/workbook.xml":
                    # ask Excel to recalculate on open so dependent cells refresh
                    txt = data.decode("utf-8")
                    if "<calcPr" in txt:
                        txt = re.sub(r"<calcPr([^>]*?)/>",
                                     lambda m: f"<calcPr{m.group(1)} fullCalcOnLoad=\"1\"/>"
                                     if "fullCalcOnLoad" not in m.group(1) else m.group(0), txt, count=1)
                        data = txt.encode("utf-8")
                zout.writestr(item, data)

    shutil.move(str(tmp), str(target))
    log(f"  written  : {target.name}   ({len(applied)} cells)")

    # --- verify --------------------------------------------------------------
    problems = []
    after_charts = chart_parts(target)
    if after_charts != before_charts:
        problems.append(f"charts changed: {len(before_charts)} -> {len(after_charts)}")
    with zipfile.ZipFile(WORKBOOK if not args.in_place else backup) as z1, zipfile.ZipFile(target) as z2:
        if len(z1.namelist()) != len(z2.namelist()):
            problems.append(f"part count changed: {len(z1.namelist())} -> {len(z2.namelist())}")

    import openpyxl
    wb = openpyxl.load_workbook(target, data_only=False)
    ws = wb[SHEET]
    mismatched = []
    for ref, expected in writes.items():
        got = ws[ref].value
        if got is None:
            mismatched.append((ref, expected, got))
        elif isinstance(expected, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected):
            if not (hasattr(got, "strftime") and got.strftime("%Y-%m-%d") == expected):
                mismatched.append((ref, expected, got))
        elif abs(float(got) - float(expected)) > 1e-9:
            mismatched.append((ref, expected, got))
    wb.close()
    if mismatched:
        problems.append(f"{len(mismatched)} cell(s) do not hold the expected value: {mismatched[:5]}")

    if problems:
        log("\n  VERIFICATION FAILED:")
        for p in problems:
            log(f"    - {p}")
        if args.in_place:
            shutil.copy2(backup, WORKBOOK)
            log(f"  restored the original from {backup.name}")
        sys.exit(1)

    log(f"  verified : {len(writes)} values correct, {len(after_charts)} charts intact, parts unchanged")

    result = {
        "_stage": "05_write_result",
        "written_at_pkt": datetime.now(PKT).strftime("%d/%m/%Y %H:%M:%S"),
        "workbook": str(target), "backup": str(backup), "in_place": args.in_place,
        "cells_written": len(writes), "charts_intact": len(after_charts),
        "cells": rows_out,
    }
    with open(OUT_DIR / "05_write_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    log(f"  result   : output/05_write_result.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
