"""Build a formatted Excel workbook from the Discord cross-check CSV."""
import csv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SRC = r"A:\Discord Mod\IM8\discord_crosscheck_results.csv"
OUT = r"A:\Discord Mod\IM8\IM8 Discord Crosscheck 16-Jun.xlsx"

with open(SRC, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

FONT = "Arial"
BRAND = "00C9A7"      # IM8 teal
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=11)
HDR_FILL = PatternFill("solid", fgColor=BRAND)
TITLE_FONT = Font(name=FONT, bold=True, size=16, color="0E5C4F")
SUB_FONT = Font(name=FONT, italic=True, size=10, color="666666")
BASE_FONT = Font(name=FONT, size=10)
GREEN = Font(name=FONT, size=10, color="0A7D34")
AMBER = Font(name=FONT, size=10, color="B26A00")
thin = Side(style="thin", color="D9D9D9")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def name(r):
    return f"{r['affiliate_first']} {r['affiliate_last']}".strip()


def style_header(ws, ncols, row=1):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HDR_FONT
        cell.fill = HDR_FILL
        cell.alignment = Alignment(vertical="center", horizontal="left")
        cell.border = BORDER
    ws.row_dimensions[row].height = 22


def finish(ws, ncols, first_data_row=2):
    last = ws.max_row
    for r in range(first_data_row, last + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            if cell.font is BASE_FONT or cell.font.name != FONT:
                if cell.font not in (GREEN, AMBER):
                    cell.font = BASE_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=False)
    ws.freeze_panes = ws.cell(row=first_data_row, column=1)
    if last >= first_data_row:
        ws.auto_filter.ref = f"A{first_data_row-1}:{get_column_letter(ncols)}{last}"


def autosize(ws, ncols, maxw=55):
    for c in range(1, ncols + 1):
        col = get_column_letter(c)
        width = 8
        for cell in ws[col]:
            if cell.value is not None:
                width = max(width, len(str(cell.value)) + 2)
        ws.column_dimensions[col].width = min(width, maxw)


wb = Workbook()

# ── Sheet: In Server ──────────────────────────────────────────────
in_server = [r for r in rows if r["in_server"] == "YES"]
# handle matches first, then display/nick; then by affiliate name
in_server.sort(key=lambda r: (0 if r["match_confidence"] == "handle" else 1, name(r).lower()))

ws = wb.active
ws.title = "In Server"
HEADERS = ["Affiliate Name", "Email", "Discord Handle", "Display Name",
           "Match Confidence", "Value in Export", "Discord ID", "Multiple Matches?"]
ws.append(HEADERS)
style_header(ws, len(HEADERS))
for r in in_server:
    conf = "Handle (exact)" if r["match_confidence"] == "handle" else "Display name / nickname"
    ws.append([
        name(r), r["affiliate_email"], "@" + r["matched_username"], r["matched_display"],
        conf, r["discord_raw"], r["matched_id"], "Yes" if r["ambiguous"] == "YES" else "",
    ])
    cell = ws.cell(row=ws.max_row, column=5)
    cell.font = GREEN if r["match_confidence"] == "handle" else AMBER
finish(ws, len(HEADERS))
autosize(ws, len(HEADERS))

# ── Sheet: Not in Server ──────────────────────────────────────────
not_in = [r for r in rows if r["in_server"] == "NO" and r["unusable_value"] != "YES"]
not_in.sort(key=lambda r: name(r).lower())
ws = wb.create_sheet("Not in Server")
H2 = ["Affiliate Name", "Email", "Discord Username (from export)"]
ws.append(H2)
style_header(ws, len(H2))
for r in not_in:
    ws.append([name(r), r["affiliate_email"], r["discord_raw"]])
finish(ws, len(H2))
autosize(ws, len(H2))

# ── Sheet: Unusable Values ────────────────────────────────────────
junk = [r for r in rows if r["unusable_value"] == "YES"]
junk.sort(key=lambda r: name(r).lower())
ws = wb.create_sheet("Unusable Values")
H3 = ["Affiliate Name", "Email", "Value in Export (not a handle)"]
ws.append(H3)
style_header(ws, len(H3))
for r in junk:
    ws.append([name(r), r["affiliate_email"], r["discord_raw"]])
finish(ws, len(H3))
autosize(ws, len(H3))

# ── Sheet: All Results ────────────────────────────────────────────
ws = wb.create_sheet("All Results")
H4 = ["Value in Export", "In Server", "Match Confidence", "Matched Username",
      "Matched Display Name", "Discord ID", "Multiple Matches?",
      "Affiliate First", "Affiliate Last", "Affiliate Email", "Unusable Value"]
ws.append(H4)
style_header(ws, len(H4))
for r in rows:
    ws.append([
        r["discord_raw"], r["in_server"], r["match_confidence"], r["matched_username"],
        r["matched_display"], r["matched_id"], r["ambiguous"],
        r["affiliate_first"], r["affiliate_last"], r["affiliate_email"], r["unusable_value"],
    ])
finish(ws, len(H4))
autosize(ws, len(H4), maxw=40)

# ── Sheet: Summary (dynamic COUNTIF formulas over All Results) ────
ws = wb.create_sheet("Summary", 0)
ws.sheet_view.showGridLines = False
ws["A1"] = "IM8 — Affiliate Discord Cross-Check"
ws["A1"].font = TITLE_FONT
ws["A2"] = "Source: SS Export 16-Jun.xlsx  ·  matched against live IM8 server roster (665 members)"
ws["A2"].font = SUB_FONT

n_total = len(rows)
n_in = sum(1 for r in rows if r["in_server"] == "YES")
n_handle = sum(1 for r in rows if r["match_confidence"] == "handle")
n_disp = sum(1 for r in rows if r["match_confidence"] == "display/nick")
n_no = sum(1 for r in rows if r["in_server"] == "NO" and r["unusable_value"] != "YES")
n_junk = sum(1 for r in rows if r["unusable_value"] == "YES")
metrics = [
    ("Affiliate rows with a Discord-username value", n_total),
    ("Already in server — total", n_in),
    ("    • matched by exact handle", n_handle),
    ("    • matched by display name / nickname", n_disp),
    ("Not in server", n_no),
    ("Unusable values (email / URL / sentence / \"none\")", n_junk),
]
row = 4
ws[f"A{row}"] = "Metric"; ws[f"B{row}"] = "Count"
style_header(ws, 2, row=row)
row += 1
for label, formula in metrics:
    ws[f"A{row}"] = label
    ws[f"B{row}"] = formula
    ws[f"A{row}"].font = Font(name=FONT, size=11, bold=label.startswith(("Already", "Affiliate", "Not", "Unusable")))
    ws[f"B{row}"].font = Font(name=FONT, size=11, bold=True)
    ws[f"A{row}"].border = BORDER; ws[f"B{row}"].border = BORDER
    ws[f"B{row}"].alignment = Alignment(horizontal="center")
    row += 1
ws[f"A{row+1}"] = "Tabs: In Server  ·  Not in Server  ·  Unusable Values  ·  All Results"
ws[f"A{row+1}"].font = SUB_FONT
ws.column_dimensions["A"].width = 48
ws.column_dimensions["B"].width = 12

wb.save(OUT)
print("Saved:", OUT)
print(f"In Server={len(in_server)}  Not in Server={len(not_in)}  Unusable={len(junk)}  Total={len(rows)}")
