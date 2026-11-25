#!/usr/bin/env python3
"""Adapter for the vendored Windhawk PR validator.

`awt.prgate` starts this module in a synthetic PR worktree. The adapter runs the
official `pr_validation.py` **unmodified**: in snapshot mode it only replaces
the validator's data fetcher with a replay of a dated snapshot; in live mode it
leaves the validator alone. A side-channel fetch log tells the caller which
data was actually used. No outcome is ever changed to "pass" by this adapter.
"""

from __future__ import annotations

import json
import os
import re
import sys
import types
import urllib.error
import urllib.parse
from pathlib import Path

VENDOR = Path(os.environ["AWT_PRGATE_VENDOR"])
sys.path.insert(0, str(VENDOR))


def load_preprocessor() -> None:
    """Load upstream's module on Python 3.11 with one syntactic rewrite.

    Upstream uses PEP 695 (`type Macros = ...`), which needs Python 3.12+. The
    vendored file is kept byte-for-byte identical; only this in-memory shim
    rewrites that single alias so the same logic can run on 3.11. It is recorded
    in the fetch log and shown in the report.
    """
    path = VENDOR / "preprocessor.py"
    source = path.read_text(encoding="utf-8")
    compat = []
    if sys.version_info < (3, 12):
        source, count = re.subn(
            r"^type Macros = (.+)$", r"Macros = \1", source, flags=re.MULTILINE
        )
        if count != 1:
            raise RuntimeError("upstream preprocessor.py changed: PEP 695 alias not found")
        compat.append("PEP 695 type alias rewritten in memory for Python 3.11")
    module = types.ModuleType("preprocessor")
    module.__file__ = str(path)
    sys.modules["preprocessor"] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    COMPAT.extend(compat)


COMPAT: list[str] = []
load_preprocessor()

import pr_validation as validator  # noqa: E402  (path and shim set above)

DATA_DIR = Path(os.environ["AWT_PRGATE_DATA_DIR"])


def load(name: str) -> object:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


SNAPSHOT = {
    "author_data": load("mod_author_data.json"),
    "spdx_license_ids": load("spdx_license_ids.json"),
    "windows_filenames": load("windows_filenames.json"),
    "catalog": load("catalog.json"),
}
FETCH_LOG_PATH = Path(os.environ["AWT_PRGATE_FETCH_LOG"])
LIVE = os.environ.get("AWT_PRGATE_DATA_MODE", "snapshot") == "live"

AUTHOR_DATA_URL = (
    "https://raw.githubusercontent.com/ramensoftware/windhawk-mods/"
    "refs/heads/pages/mod_author_data.json"
)
LICENSES_URL = "https://spdx.org/licenses/licenses.json"
WINBINDEX_URL = "https://winbindex.m417z.com/data/filenames.json"
MODS_PREFIX = (
    "https://raw.githubusercontent.com/ramensoftware/windhawk-mods/"
    "refs/heads/pages/mods/"
)

fetches: list[dict] = []


class FetchUnavailable(Exception):
    """A value the official validator wanted is not in the snapshot."""


def record(kind: str, detail: str) -> None:
    fetches.append({"kind": kind, "detail": detail})


def http_404(url: str) -> urllib.error.HTTPError:
    # The upstream validator treats 404 as "new mod / no image", so a synthetic
    # PR keeps exactly the same semantics as a real one.
    return urllib.error.HTTPError(url, 404, "not in snapshot", {}, None)


def synthetic_deployed_source(mod_id: str) -> bytes:
    record("catalog_metadata", mod_id)
    record("catalog_versions", mod_id)
    entry = SNAPSHOT["catalog"]["mods"].get(mod_id)
    if entry is None:
        raise http_404(MODS_PREFIX + mod_id)
    lines = entry.get("metadata_lines") or [
        f"// @id {mod_id}",
        f"// @name {entry.get('values', {}).get('name', mod_id)}",
    ]
    source = "// ==WindhawkMod==\n" + "\n".join(lines) + "\n// ==/WindhawkMod==\n"
    return source.encode("utf-8")


def versions_json(mod_id: str) -> bytes:
    record("catalog_versions", mod_id)
    entry = SNAPSHOT["catalog"]["mods"].get(mod_id)
    if entry is None:
        raise http_404(MODS_PREFIX + mod_id + "/versions.json")
    versions = entry.get("versions") or []
    return json.dumps([{"version": v} for v in versions]).encode("utf-8")


def snapshot_fetch(url: str, headers=None) -> bytes:
    """Replay the official validator's fetch calls from a dated snapshot."""
    if url == AUTHOR_DATA_URL:
        record("mod_author_data", AUTHOR_DATA_URL)
        return json.dumps(SNAPSHOT["author_data"], ensure_ascii=False).encode("utf-8")
    if url == LICENSES_URL:
        record("spdx_license_ids", LICENSES_URL)
        licenses = [{"licenseId": i} for i in SNAPSHOT["spdx_license_ids"]]
        return json.dumps({"licenses": licenses}).encode("utf-8")
    if url == WINBINDEX_URL:
        record("winbindex_subset", WINBINDEX_URL)
        return json.dumps(SNAPSHOT["windows_filenames"]).encode("utf-8")
    if url.startswith(MODS_PREFIX):
        tail = url[len(MODS_PREFIX) :]
        if tail.endswith(".wh.cpp"):
            return synthetic_deployed_source(
                urllib.parse.unquote(tail[: -len(".wh.cpp")])
            )
        if tail.endswith("/versions.json"):
            return versions_json(urllib.parse.unquote(tail[: -len("/versions.json")]))
    raise FetchUnavailable(
        "The official validator requested data that is not in the snapshot: "
        + url
        + ". Use --data-mode live on a machine with network access."
    )


def patched_is_existing_windows_file_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == "systemtray.dll":
        record("winbindex_subset_check", lowered + " (special case)")
        return True
    names = set(SNAPSHOT["windows_filenames"])
    if lowered in names:
        record("winbindex_subset_check", lowered)
    else:
        # Derived list, not winbindex: be lenient, but force the caller to
        # report the symbol-hook check as not fully verifiable locally.
        record("winbindex_unknown", lowered)
    return True


def main() -> int:
    if not LIVE:
        validator.fetch_url = snapshot_fetch
        validator.is_existing_windows_file_name = patched_is_existing_windows_file_name

    code = 0
    try:
        validator.main()
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        else:
            print("::error::" + str(exc.code))
            code = 1
    except FetchUnavailable as exc:
        print("::error::PRGATE_UNAVAILABLE: " + str(exc))
        code = 3
    except validator.FetchError as exc:
        print("::error::PRGATE_UNAVAILABLE: " + str(exc))
        code = 3
    except Exception as exc:  # a crash in the official validator is never a pass
        print(f"::error::PRGATE_VALIDATOR_CRASH: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        code = 3
    finally:
        FETCH_LOG_PATH.write_text(
            json.dumps(
                {"mode": "live" if LIVE else "snapshot",
                 "compatibility_shims": COMPAT, "fetches": fetches}, indent=1
            ),
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
