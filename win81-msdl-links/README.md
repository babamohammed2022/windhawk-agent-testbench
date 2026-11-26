# Windows 8.1 x64 (6.3.9600) — Microsoft Symbol Server Links Archive

Metadata-only index of **3551 verified Windows 8.1 x64 (6.3.9600) binaries** (**2677 unique DLL/CPL/OCX files**) hosted directly on Microsoft's official Symbol Server (`https://msdl.microsoft.com/download/symbols/...`).

No Microsoft binaries are hosted in this repository — only metadata (`filename`, `version`, `size`, `sha256`, `url`) extracted from official Microsoft Update Catalog packages:
- Windows 8.1 Update 1 x64 (`KB2919355`, Apr 2014)
- Windows 8.1 August 2014 Rollup x64 (`KB2975719`)
- Windows 8.1 November 2014 Update Rollup x64 (`KB3000850`)
- Windows 8.1 Final Security Monthly Quality Rollup Jan 2023 x64 (`KB5022352`)

## Sibling Archives
- Windows 7 SP1 x64 (`6.1.7601`): `../win7-msdl-links/README.md`
- Windows 8.0 x64 (`6.2.9200`): `../win8-msdl-links/README.md`
- Windows 8.1 x64 (`6.3.9600`): `../win81-msdl-links/README.md`

## How to fetch with an AI / Scraper (Raw URLs)

Use `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win81-msdl-links/<PATH>`:

### Best for AI scrapers (small files, never truncated)
- **By initial letter (CSV, small per-letter shards):** `by_letter/<a..z|0-9>.csv`
  - Example for `shell32.dll` → `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win81-msdl-links/by_letter/s.csv`
  - Example for `uxtheme.dll` → `https://raw.githubusercontent.com/babamohammed2022/windhawk-agent-testbench/main/win81-msdl-links/by_letter/u.csv`
- **By Windhawk-relevant category (Markdown tables):**
  - `by_category/control_panel.md` (40 entries)
  - `by_category/shell_explorer.md` (30 entries)
  - `by_category/themes_ui.md` (21 entries)
  - `by_category/graphics_media.md` (20 entries)
  - `by_category/core_system.md` (38 entries)

### Full index (for scripts / local tools)
- `win81_x64_msdl.csv` — complete CSV (826 KB)
- `win81_x64_msdl.min.json` — minified JSON keyed by lowercase filename (934 KB)

## CSV Columns
`name,version,size,sha256,url,source`

All URLs were verified live against `msdl.microsoft.com` (HTTP 200/302). Note: `msdl.microsoft.com` occasionally returns transient HTTP 503 responses, so downloaders should retry 3+ times.
