"""Feature 1 - symbol verification across Windows builds.

What this package answers: *does the internal symbol this mod hooks still exist
in the Windows build the user runs?* That is the question the bench could not
answer before, and it is the reason a shell mod silently does nothing.

It answers it without Windows and without running anything:

    extract.py   the mod's source -> the symbol names and target modules it asks
                 Windows for (SYMBOL_HOOK tables, Wh_FindSymbolCompare, decorated
                 literals, Wh_SetFunctionHook on a public API)
    demangle.py  MSVC decorated-name decoder (x86 and x86-64), used to compare an
                 undecorated name a mod wrote with the decorated name in a PDB
    match.py     the decision, modelled on the Windhawk engine's own exact-string
                 comparison, with an explicit confidence for every verdict
    winbindex.py module + build -> PE signature (pinned table or live winbindex)
    symserver.py download PE/PDB by signature into .cache/ (never committed)
    matrix.py    configs/symbol-matrix.yaml -> the list of builds to check
    verify.py    the pipeline, and the `symbol_matrix` report section
    table.py     the same content as a terminal table

Outcome vocabulary, and what each word is allowed to mean:

    found      the symbol exists in the public PDB of that exact binary
    missing    the PDB was read and the symbol is not in it
    ambiguous  several symbols matched, or only case-insensitively (Windhawk
               would not resolve that at runtime)
    unknown    the check could not be performed - no network, no signature for
               that build, a module that does not exist there, an unreadable PDB.
               `unknown` is never reported as `found`.

A `found` verdict is evidence about *names*, not about behaviour: nothing was
executed, so it does not upgrade the bench's verdict ladder. The existing
verified / simulated / not-verifiable-without-real-Windows distinction is
untouched; this adds a fourth kind of statement - "the symbol this mod needs
exists (or does not) on build X" - which is checkable on Linux.
"""

from . import demangle, extract, match, matrix, symserver, table, verify, winbindex

__all__ = ["demangle", "extract", "match", "matrix", "symserver", "table",
           "verify", "winbindex", "verify_mod", "extract_file", "load_matrix",
 "render_table", "TOOLING"]

TOOLING = {
    "pdb_analyzer": "awt.pdb (pure-python MSF 7.0 / DBI / GSI reader)",
    "pe_reader": "awt.pe (pure-python PE/COFF + debug directory reader)",
    "demangler": "awt.symbols.demangle (partial MSVC x86/x86-64 decoder)",
    "index": "winbindex (m417z/winbindex@gh-pages) + pinned signatures",
    "symbol_server": symserver.DEFAULT_BASE,
    "optional_accelerators": ["llvm-undname", "llvm-pdbutil", "PyYAML"],
    "network_policy": ("downloads go to .cache/ (git-ignored); Microsoft binaries "
                       "and PDBs are never committed"),
}


def extract_file(path):
    return extract.extract_file(path)


def load_matrix(path=None):
    return matrix.load(path)


def verify_mod(mod_path, **kwargs):
    return verify.verify_mod(mod_path, **kwargs)


def render_table(section, **kwargs):
    return table.render(section, **kwargs)
