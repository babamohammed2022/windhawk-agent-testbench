# Windows 7 SP1 x64 — Microsoft Symbol Server Links Archive

Metadata-only index of **2495 verified Windows 7 SP1 x64 binaries** (**1735 unique DLL/CPL/OCX files**) hosted directly on Microsoft's official Symbol Server (`https://msdl.microsoft.com/download/symbols/...`).

No Microsoft binaries are hosted in this repository — only metadata (`filename`, `version`, `size`, `sha256`, `url`) extracted from official Microsoft Update Catalog packages:
- Windows 7 Service Pack 1 x64 (`KB976932`)
- Windows 7 SP1 Convenience Rollup May 2016 x64 (`KB3125574`)
- Windows 7 SP1 Final Security Monthly Quality Rollup Jan 2023 x64 (`KB5022338`)

## Sibling Archives
- Windows 7 SP1 x64 (`6.1.7601`): `../win7-msdl-links/README.md`
- Windows 8.0 x64 (`6.2.9200`): `../win8-msdl-links/README.md`
- Windows 8.1 x64 (`6.3.9600`): `../win81-msdl-links/README.md`

## How to fetch with an AI / Scraper (Raw URLs)

Use `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win7-msdl-links/<PATH>`:

### Best for AI scrapers (small files, never truncated)
- **By initial letter (CSV, ~5–40 KB each):** `by_letter/<a..z|0-9>.csv`
  - Example for `PerfCenterCPL.dll` → `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win7-msdl-links/by_letter/p.csv`
  - Example for `themecpl.dll` → `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win7-msdl-links/by_letter/t.csv`
- **By Windhawk-relevant category (Markdown tables):**
  - `by_category/control_panel.md` (55 entries)
  - `by_category/shell_explorer.md` (47 entries)
  - `by_category/themes_ui.md` (42 entries)
  - `by_category/graphics_media.md` (20 entries)
  - `by_category/core_system.md` (59 entries)

### Full index (for scripts / local tools)
- `win7_x64_msdl.csv` — complete CSV (528 KB)
- `win7_x64_msdl.min.json` — minified JSON keyed by lowercase filename (606 KB)

## CSV Columns
`name,version,size,sha256,url,source`

All URLs were verified live against `msdl.microsoft.com` (HTTP 200/302). Note: `msdl.microsoft.com` occasionally returns transient HTTP 503 responses, so downloaders should retry 3+ times.
