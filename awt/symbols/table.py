"""Human-readable rendering of the `symbol_matrix` section.

The JSON report stays the machine-readable artifact; this is the same content
for a terminal. Cells are abbreviated but never ambiguous:

    found*      symbol exists, name matched byte for byte (what Windhawk needs)
    found~      symbol exists, matched structurally (decoded decorated name)
    found?      symbol probably exists, only the qualified name could be decoded
    ambiguous   several symbols matched, or they differ only in case
    MISSING     the PDB was read and this name is not in it
    unknown     the check could not be performed (reason printed below)
"""

CELL = {
    ("found", "exact"): "found*",
    ("found", "structural"): "found~",
    ("found", "partial"): "found?",
    ("found", "weak"): "ambiguous",
    ("found", None): "found",
    ("missing", None): "MISSING",
    ("ambiguous", None): "ambiguous",
    ("unknown", None): "unknown",
}


def cell_text(cell):
    outcome = cell.get("outcome")
    confidence = cell.get("confidence")
    if outcome == "found" and confidence not in ("exact", "structural", "partial"):
        return "found?"
    return CELL.get((outcome, confidence)) or CELL.get((outcome, None)) or str(outcome)


def _shorten(text, width):
    text = text or ""
    if len(text) <= width:
        return text
    return text[:max(1, width - 1)] + "\u2026"


def render(section, max_rows=0, width=None, name_width=52):
    lines = []
    builds = (section.get("matrix") or {}).get("builds") or []
    build_ids = [b["id"] for b in builds]
    symbols = section.get("symbols") or []
    summary = section.get("summary") or {}

    lines.append("=== symbol matrix: %s ===" % (section.get("mod_file") or "?"))
    lines.append("mod id      : %s" % (section.get("mod_id") or "?"))
    lines.append("arch        : %s" % section.get("arch"))
    lines.append("generated   : %s  (%.1fs)" % (section.get("generated"),
                                                section.get("seconds") or 0))
    extraction = section.get("extraction") or {}
    lines.append("extracted   : %d symbol request(s), %d checked against the matrix"
                 % (extraction.get("requests") or 0, extraction.get("checked") or 0))
    lines.append("modules     : %s" % (", ".join(extraction.get("modules") or []) or "-"))
    server = (section.get("network") or {}).get("symbol_server") or {}
    lines.append("symbol srv  : %s%s" % (server.get("base_url"),
                                         "  [OFFLINE]" if server.get("offline") else ""))

    # module availability, because that is what most 'unknown' cells mean
    lines.append("")
    lines.append("module availability")
    lines.append("  %-24s %-14s %-9s %s" % ("module", "build", "state", "detail"))
    for state in section.get("modules") or []:
        detail = ""
        if state.get("available"):
            pdb = state.get("pdb") or {}
            detail = "%s, %s symbol(s)" % (pdb.get("name") or "?",
                                           state.get("symbol_count"))
        else:
            detail = _shorten(state.get("reason") or "-", 90)
        lines.append("  %-24s %-14s %-9s %s"
                     % (_shorten(state.get("module"), 24),
                        _shorten(state.get("build"), 14),
                        "ready" if state.get("available") else "unavailable",
                        detail))

    if not symbols:
        lines.append("")
        lines.append("no checkable symbol requests were extracted from this mod")
        return "\n".join(lines)

    lines.append("")
    col_w = max(9, max([len(b) for b in build_ids] or [9]) + 1)
    header = "  %-*s %s" % (name_width, "symbol",
                            "".join("%-*s" % (col_w, b) for b in build_ids))
    lines.append(header)
    lines.append("  %s" % ("-" * (len(header) - 2)))
    shown = symbols[:max_rows] if max_rows else symbols
    for row in shown:
        cells = "".join("%-*s" % (col_w, cell_text((row.get("builds") or {}).get(b)
                                                   or {"outcome": "unknown"}))
                        for b in build_ids)
        name = row.get("symbol") or "?"
        if row.get("module"):
            name = "%s  [%s]" % (name, row["module"])
        flag = " (optional)" if row.get("optional") else ""
        lines.append("  %-*s %s%s" % (name_width, _shorten(name, name_width),
                                      cells, flag))
    if len(symbols) > len(shown):
        lines.append("  ... %d more symbol(s); see the JSON report"
                     % (len(symbols) - len(shown)))

    lines.append("")
    lines.append("summary     : %s" % _summary_text(summary))
    lines.append("legend      : found* exact name | found~ structural | found? name only"
                 " | ambiguous | MISSING | unknown (not checked)")
    lines.append("")
    lines.append(_wrap(section.get("disclaimer") or "", 78, indent="note        : "))
    return "\n".join(lines)


def _summary_text(summary):
    rows = summary.get("row_outcomes") or {}
    per_build = summary.get("per_build") or {}
    parts = ["%d symbol(s) x %d build(s)" % (summary.get("symbols") or 0,
                                             summary.get("builds") or 0)]
    parts.append("rows: " + ", ".join("%s=%d" % (k, rows[k])
                                      for k in ("found", "ambiguous", "missing",
                                                "unknown", "mixed") if rows.get(k)))
    for build_id, counts in per_build.items():
        parts.append("%s: found %d / missing %d / ambiguous %d / unknown %d"
                     % (build_id, counts.get("found", 0), counts.get("missing", 0),
                        counts.get("ambiguous", 0), counts.get("unknown", 0)))
    if summary.get("optional_symbols_missing"):
        parts.append("%d of the missing symbols are marked optional by the mod"
                     % summary["optional_symbols_missing"])
    return " | ".join(parts)


def _wrap(text, width, indent=""):
    words = (text or "").split()
    out, line = [], indent
    for word in words:
        if len(line) + len(word) + 1 > width + len(indent):
            out.append(line.rstrip())
            line = " " * len(indent)
        line += word + " "
    if line.strip():
        out.append(line.rstrip())
    return "\n".join(out)
