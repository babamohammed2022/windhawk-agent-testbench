#!/usr/bin/env python3
"""Offline replay of Windhawk's PR validators, exercised end to end.

The upstream validator itself is the oracle: these tests check that the bench
sets up the synthetic PR correctly, that errors and review items reach the
report with their original annotations, and that the report never claims an
acceptance that the bench cannot verify.
"""

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from tests.unit.harness import optional_import

optional_import("yaml")  # the vendored upstream validator requires PyYAML

from awt import prgate  # noqa: E402  (skip above must run first)

ROOT = prgate.ROOT
FIXTURES = ROOT / "tests" / "fixtures" / "prgate"
AUTHOR = "awt-prgate-fixture"
AUTHOR_ID = 1234567


def run(mod: Path, body: Path, *extra: str):
    sandbox = ROOT / ".sandbox"
    sandbox.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="prgate-test-", dir=sandbox)) / "report.json"
    argv = [
        str(mod), "--author", AUTHOR, "--author-id", str(AUTHOR_ID),
        "--pr-body", str(body), "--json", str(out), "--quiet", *extra,
    ]
    code = prgate.main(argv)
    report = json.loads(out.read_text(encoding="utf-8"))
    return code, report


def cleanup(report: dict) -> None:
    worktree = report.get("worktree")
    if worktree:
        shutil.rmtree(worktree, ignore_errors=True)


def gate(report: dict, gate_id: str) -> dict:
    return next(g for g in report["gates"] if g["id"] == gate_id)


def test_snapshot_integrity():
    """Vendored upstream code and cached data are checked, not trusted blindly."""
    data = prgate.load_snapshot()
    manifest = data["manifest"]
    assert len(manifest["upstream"]["commit"]) == 40
    assert manifest["files"]["pr_validation.py"]["exact"] is True
    for name, meta in manifest["files"].items():
        path = prgate.DATA / name if name.endswith(".json") else prgate.VENDOR / name
        assert path.is_file(), name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == meta["sha256"], f"stale snapshot for {name}"
    assert len(data["catalog"]["mods"]) > 900
    assert len(data["spdx_license_ids"]) > 700
    assert data["author_data"]


def test_new_mod_ready_for_ci():
    code, report = run(FIXTURES / "new-mod.wh.cpp", FIXTURES / "new-pr-body.md")
    try:
        assert code == 0, report.get("validator_output")
        assert report["input"]["change"] == "added"
        assert report["verdict"]["submission_readiness"] == "ready_for_ci"
        assert report["verdict"]["blocking_errors"] == 0
        assert gate(report, "pr_mod_validation.run")["status"] == "pass"
        assert gate(report, "pr_mod_validation.manual_review")["status"] == "pass"
        assert gate(report, "mod_compatibility_check")["status"] == "unavailable"
        assert "not_verifiable_here" in report["verdict"]["immediate_acceptance"]
        assert gate(report, "human_review")["status"] == "not_verifiable_here"
        assert report["data_quality"]["mode"] == "snapshot"
        if sys.version_info < (3, 12):
            assert report["data_quality"]["compatibility_shims"]
    finally:
        cleanup(report)


def test_invalid_version_blocks():
    code, report = run(FIXTURES / "invalid-version.wh.cpp", FIXTURES / "new-pr-body.md")
    try:
        assert code == 1
        assert report["verdict"]["submission_readiness"] == "blocked"
        assert report["verdict"]["blocking_errors"] == 1
        messages = " ".join(a["message"] for a in gate(report, "pr_mod_validation.run")["annotations"])
        assert "version" in messages
    finally:
        cleanup(report)


def test_review_item_requires_review():
    code, report = run(FIXTURES / "review-item.wh.cpp", FIXTURES / "new-pr-body.md")
    try:
        assert code == 1
        assert report["verdict"]["submission_readiness"] == "needs_manual_review"
        assert report["verdict"]["review_items"] == 1
        assert gate(report, "pr_mod_validation.manual_review")["status"] == "needs_review"
        assert gate(report, "x_account_verification")["status"] == "needs_maintainer"
    finally:
        cleanup(report)


def test_maintainer_verification_is_passed_through():
    sandbox = ROOT / ".sandbox"
    comments = Path(tempfile.mkdtemp(prefix="prgate-comments-", dir=sandbox)) / "comments.md"
    comments.write_text(
        "Thanks!\nVerified https://x.com/awt-prgate-fixture\n", encoding="utf-8"
    )
    code, report = run(
        FIXTURES / "review-item.wh.cpp", FIXTURES / "new-pr-body.md",
        "--maintainer-comments", str(comments),
    )
    try:
        assert code == 0, report.get("validator_output")
        assert gate(report, "x_account_verification")["status"] == "pass"
        assert gate(report, "pr_mod_validation.manual_review")["status"] == "pass"
        assert report["verdict"]["submission_readiness"] == "ready_for_ci"
    finally:
        cleanup(report)
        comments.unlink(missing_ok=True)


def update_candidate(version: str) -> Path:
    """A syntactically valid update of a real catalog mod (metadata only)."""
    catalog = prgate.load_snapshot()["catalog"]["mods"]["block-win-v"]
    lines = [
        line for line in catalog["metadata_lines"]
        if not line.startswith("// @version ")
    ] + [f"// @version {version}"]
    source = (
        "// ==WindhawkMod==\n" + "\n".join(lines) + "\n// ==/WindhawkMod==\n\n"
        "// ==WindhawkModReadme==\n/*\nUpdate fixture.\n*/\n"
        "// ==/WindhawkModReadme==\n\n"
        "#include <windhawk_api.h>\n\nBOOL Wh_ModInit() {\n    return TRUE;\n}\n"
    )
    path = ROOT / ".sandbox" / f"prgate-update-{version}.wh.cpp"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_update_detects_reused_version():
    mod = update_candidate("1.0.1")  # the deployed version in the snapshot
    code, report = run(mod, FIXTURES / "update-pr-body.md")
    try:
        assert code == 1
        assert report["input"]["change"] == "modified"
        assert report["verdict"]["submission_readiness"] == "blocked"
        messages = " ".join(a["message"] for a in gate(report, "pr_mod_validation.run")["annotations"])
        assert "already used" in messages
    finally:
        cleanup(report)
        mod.unlink(missing_ok=True)


def test_update_requires_changelog():
    mod = update_candidate("1.0.2")
    sandbox = ROOT / ".sandbox"
    body = Path(tempfile.mkdtemp(prefix="prgate-body-", dir=sandbox)) / "empty.md"
    body.write_text(
        "## Changelog\n\n<!-- changelog:start -->\n\n<!-- changelog:end -->\n",
        encoding="utf-8",
    )
    code, report = run(mod, body)
    try:
        assert code == 1
        assert report["verdict"]["submission_readiness"] == "blocked"
        messages = " ".join(a["message"] for a in gate(report, "pr_mod_validation.run")["annotations"])
        assert "changelog" in messages.lower()
    finally:
        cleanup(report)
        mod.unlink(missing_ok=True)
        body.unlink(missing_ok=True)
