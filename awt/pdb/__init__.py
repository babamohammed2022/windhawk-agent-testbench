"""PDB analysis on Linux, without Windows and without external tools.

Public API:

    from awt import pdb
    f = pdb.open("/path/to/module.pdb")     # or pdb.from_bytes(data)
    f.info()                                # container + streams + warnings
    f.symbols()                             # list of decoded symbols with RVA
    f.by_name()                             # name -> [symbol]

`ANALYZER` describes the implementation for the JSON report, so a reader always
knows which tool produced a symbol verdict.
"""
import builtins

from . import msf, records
from .reader import PdbError, PdbFile, open  # noqa: A004 (module-level `open`)

ANALYZER = {
    "name": "awt-pdb",
    "version": 1,
    "implementation": "pure-python MSF 7.0 + DBI + GSI symbol reader",
    "sources": ["public symbol stream", "global symbol stream"],
    "dependencies": "python standard library only",
    "alternatives_evaluated": {
        "llvm-pdbutil": "most faithful reader, but not installed in the sandboxes "
                        "this bench runs in and not installable without package "
                        "network access; used as an optional cross-check "
                        "(`python3 -m awt.symbols --cross-check`) when present",
        "pdbparse": "unmaintained, does not decode the DBI/public-symbol streams "
                    "of modern public PDBs, requires `construct`",
    },
}


def from_bytes(data: bytes, source: str = "<memory>") -> PdbFile:
    return PdbFile(data, source=source)


def is_pdb(path: str) -> bool:
    try:
        # NOTE: `open` in this module is awt.pdb.open (a PDB reader), so the
        # builtin has to be named explicitly here.
        with builtins.open(path, "rb") as f:
            head = f.read(len(msf.MAGIC_700))
        return head.startswith(msf.MAGIC_700)
    except OSError:
        return False


__all__ = ["PdbFile", "PdbError", "open", "from_bytes", "is_pdb", "ANALYZER",
           "msf", "records"]
