#!/usr/bin/env python3
"""Refresh the offline data snapshot used by `python3 -m awt.prgate`.

The official PR validator fetches live data from the Windhawk catalog, the
SPDX license list and winbindex. This tool records the data that can be
recorded (GitHub API, authenticated) into `third_party/windhawk-pr-validation/data`
so that the same checks can be replayed without network access. It stores
metadata only: no mod source code, binaries or personal tokens.

Usage:
    GH_TOKEN=... python3 tools/refresh_prgate_snapshot.py

GH_TOKEN is read from the environment (or from `gh auth token`) and is never
written to disk. Requires network access to api.github.com.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "third_party" / "windhawk-pr-validation" / "data"
MODS_REPO = "ramensoftware/windhawk-mods"
SPDX_REPO = "spdx/license-list-data"
API = "https://api.github.com"

METADATA_KEYS = {
    "id", "version", "github", "twitter", "homepage", "compilerOptions",
    "license", "donateUrl", "name", "description", "author", "include",
    "exclude", "architecture",
}
METADATA_BLOCK = re.compile(
    r"//\s*==WindhawkMod==\s*\n(.*?)//\s*==/WindhawkMod==", re.S
)
METADATA_LINE = re.compile(r"^\s*//\s*@([A-Za-z]+)\s+(.*?)\s*$")


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def get(self, path: str, tries: int = 4) -> object:
        url = path if path.startswith("https://") else API + path
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "awt-prgate-snapshot",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code not in (403, 429, 500, 502, 503, 504) or attempt == tries - 1:
                    raise
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == tries - 1:
                    raise
                print(f"retrying {url}: {exc!r}", file=sys.stderr)
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def blob(self, sha: str, repo: str = MODS_REPO) -> bytes:
        data = self.get(f"/repos/{repo}/git/blobs/{sha}")
        if data.get("encoding") != "base64":
            raise RuntimeError(f"unexpected blob encoding for {sha}")
        return base64.b64decode(data["content"])


def token_from_environment() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("A GitHub token is required: set GH_TOKEN or use `gh auth login`.") from exc


def metadata_lines(source: str) -> list[str]:
    block = METADATA_BLOCK.search(source)
    if not block:
        return []
    result = []
    for line in block.group(1).splitlines():
        match = METADATA_LINE.match(line)
        if match and match.group(1) in METADATA_KEYS:
            result.append(f"// @{match.group(1)} {match.group(2)}")
    return result


def synthetic_source(lines: list[str]) -> str:
    """The validator only reads metadata from deployed sources; keep it small."""
    return "// ==WindhawkMod==\n" + "\n".join(lines) + "\n// ==/WindhawkMod==\n"


def write_json(name: str, value: object) -> str:
    path = OUT / name
    text = json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="only refresh the first N catalog mods (smoke test)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    gh = GitHub(token_from_environment())
    OUT.mkdir(parents=True, exist_ok=True)

    upstream = gh.get(f"/repos/{MODS_REPO}/commits/main")
    commit = upstream["sha"]
    print(f"windhawk-mods main: {commit}")

    author_raw = gh.blob(gh.get(f"/repos/{MODS_REPO}/contents/mod_author_data.json?ref=pages")["sha"])
    author_data = json.loads(author_raw)
    author_hash = write_json("mod_author_data.json", author_data)
    print(f"mod_author_data.json: {len(author_data)} authors, sha256={author_hash[:12]}")

    spdx = gh.get(f"/repos/{SPDX_REPO}/contents/json/licenses.json")
    licenses = json.loads(gh.blob(spdx["sha"], SPDX_REPO))
    ids = sorted(license_id["licenseId"].lower() for license_id in licenses["licenses"])
    license_hash = write_json("spdx_license_ids.json", ids)
    print(f"spdx_license_ids.json: {len(ids)} identifiers, sha256={license_hash[:12]}")

    tree = gh.get(f"/repos/{MODS_REPO}/git/trees/pages?recursive=1")
    if tree.get("truncated"):
        raise SystemExit("the pages tree was truncated; cannot build a complete snapshot")
    sources = {
        entry["path"].split("/")[1].removesuffix(".wh.cpp"): entry
        for entry in tree["tree"]
        if re.fullmatch(r"mods/[^/]+\.wh\.cpp", entry["path"])
    }
    version_files = {
        entry["path"].split("/")[1]: entry
        for entry in tree["tree"]
        if re.fullmatch(r"mods/[^/]+/versions\.json", entry["path"])
    }
    if args.limit:
        sources = dict(list(sorted(sources.items()))[: args.limit])
    print(f"pages catalog: {len(sources)} deployed mods, {len(version_files)} version histories")

    catalog: dict[str, dict] = {}
    filenames: set[str] = set()

    def load(item: tuple[str, dict]) -> tuple[str, dict, set[str]]:
        mod_id, entry = item
        source = gh.blob(entry["sha"]).decode("utf-8", errors="replace")
        lines = metadata_lines(source)
        block = METADATA_BLOCK.search(source)
        if block:
            for line in block.group(1).splitlines():
                match = METADATA_LINE.match(line)
                if match and match.group(1) == "include":
                    name = match.group(2).strip().split("\\")[0]
                    if name and "*" not in name and "." in name:
                        filenames.add(name.lower())
        versions: list[str] = []
        if mod_id in version_files:
            try:
                versions = [v["version"] for v in json.loads(gh.blob(version_files[mod_id]["sha"]))]
            except (ValueError, KeyError, TypeError) as exc:
                print(f"warning: unreadable versions.json for {mod_id}: {exc}", file=sys.stderr)
        values = {}
        for line in lines:
            match = METADATA_LINE.match(line)
            if match:
                values.setdefault(match.group(1), match.group(2))
        return mod_id, {"metadata_lines": lines, "versions": versions, "values": values}, filenames

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(load, item) for item in sources.items()]
        for index, future in enumerate(as_completed(futures), 1):
            mod_id, record, found_names = future.result()
            catalog[mod_id] = record
            filenames.update(found_names)
            if index % 100 == 0:
                print(f"  read {index}/{len(futures)} deployed mods")

    catalog_hash = write_json("catalog.json", {"commit": commit, "mods": catalog})
    filenames_hash = write_json("windows_filenames.json", sorted(filenames))
    print(f"catalog.json: {len(catalog)} mods, sha256={catalog_hash[:12]}")
    print(f"windows_filenames.json: {len(filenames)} names (derived, not winbindex), sha256={filenames_hash[:12]}")

    # Pin the exact copies of the upstream scripts checked into this repository.
    vendor = OUT.parent
    script_hashes = {
        name: hashlib.sha256((vendor / name).read_bytes()).hexdigest()
        for name in ("pr_validation.py", "preprocessor.py", "extract_mod_symbols.py",
                     "pull_request_template.md")
    }
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "upstream": {
            "repository": f"https://github.com/{MODS_REPO}",
            "commit": commit,
            "workflow": ".github/workflows/pr_validation.yml",
            "compatibility_workflow": ".github/workflows/mod_compatibility_check.yml",
            "pr_checks": [
                "Test changed files",
                "Manual review",
                "run (1.6.1)",
                "run (1.7.3)",
                "run (2.0.0-alpha.8)",
                "run",
            ],
            "other_pr_workflows": [
                ".github/workflows/pr_labels.yml",
                ".github/workflows/pr_commands.yml",
                ".github/workflows/pr_queued_ai_review.yml",
            ],
        },
        "files": {
            "pr_validation.py": {"sha256": script_hashes["pr_validation.py"], "exact": True},
            "preprocessor.py": {"sha256": script_hashes["preprocessor.py"], "exact": True},
            "extract_mod_symbols.py": {"sha256": script_hashes["extract_mod_symbols.py"], "exact": True},
            "pull_request_template.md": {"sha256": script_hashes["pull_request_template.md"], "exact": True},
            "mod_author_data.json": {
                "url": f"https://raw.githubusercontent.com/{MODS_REPO}/pages/mod_author_data.json",
                "sha256": author_hash, "exact": True,
            },
            "spdx_license_ids.json": {
                "url": f"https://github.com/{SPDX_REPO}/blob/main/json/licenses.json",
                "sha256": license_hash, "exact": True,
            },
            "catalog.json": {
                "source": f"https://github.com/{MODS_REPO}/tree/pages/mods",
                "sha256": catalog_hash, "exact": False,
                "note": "Metadata-only projection of deployed mod sources; raw sources are not vendored.",
            },
            "windows_filenames.json": {
                "source": "include directives of the deployed mods, not winbindex.m417z.com",
                "sha256": filenames_hash, "exact": False,
                "note": "Subset used only for symbol-hook review items; a miss is reported as unavailable.",
            },
        },
    }
    write_json("manifest.json", manifest)
    print("wrote data/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
