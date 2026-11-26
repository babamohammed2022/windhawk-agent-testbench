#!/usr/bin/env python3
"""Submission-readiness gate for a Windhawk mod pull request.

This is a *local replay* of the checks Windhawk runs on a PR in
ramensoftware/windhawk-mods. The authoritative part is upstream's own
`pr_validation.py`, vendored verbatim in `third_party/windhawk-pr-validation/`
and run against a synthetic one-file PR diff (the shape its CI expects).

It deliberately does not answer "will this be merged?": the official compiler
matrix, the X account verification, the AI review, the reviewer and the
maintainer all run outside this bench. The report says which of those are still
missing instead of assuming they passed.

Usage:
    python3 -m awt.prgate mods/my-mod.wh.cpp --author my-github-login \
        --author-id 123456 --pr-body pr-body.md --json .sandbox/prgate-report.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from awt.metadata import parse_metadata

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "third_party" / "windhawk-pr-validation"
DATA = VENDOR / "data"
SANDBOX = ROOT / ".sandbox"

ANNOTATION_RE = re.compile(r"^::(error|warning)\s+file=([^,]+),line=(\d+)::(.*)$")
DEFAULT_NEW_BODY = (
    "## Mod authorship\n\n- [x] The submitter, with AI assistance\n"
)
DEFAULT_UPDATE_BODY = (
    "## Changelog\n\n<!-- changelog:start -->\n\n<!-- changelog:end -->\n"
)


def decode_annotation(text: str) -> str:
    return urllib.parse.unquote(text).replace("%0D", "\r").replace("%0A", "\n")


def git(args: list[str], cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def load_snapshot() -> dict:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    return {
        "manifest": manifest,
        "catalog": json.loads((DATA / "catalog.json").read_text(encoding="utf-8")),
        "author_data": json.loads(
            (DATA / "mod_author_data.json").read_text(encoding="utf-8")
        ),
        "spdx_license_ids": json.loads(
            (DATA / "spdx_license_ids.json").read_text(encoding="utf-8")
        ),
        "windows_filenames": json.loads(
            (DATA / "windows_filenames.json").read_text(encoding="utf-8")
        ),
    }


def synthetic_source(mod_id: str, entry: dict) -> str:
    lines = (entry or {}).get("metadata_lines") or [
        f"// @id {mod_id}", f"// @name {(entry or {}).get('values', {}).get('name', mod_id)}"
    ]
    return "// ==WindhawkMod==\n" + "\n".join(lines) + "\n// ==/WindhawkMod==\n"


def build_pr_worktree(mod_path: Path, mod_id: str, change: str, snapshot: dict) -> Path:
    """Create a real git history whose HEAD^1..HEAD is exactly one A/M file."""
    SANDBOX.mkdir(exist_ok=True)
    base = SANDBOX / "prgate"
    base.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    (work / "mods").mkdir()
    (work / ".github").mkdir()
    shutil.copyfile(VENDOR / "pull_request_template.md",
                    work / ".github" / "pull_request_template.md")

    # get_all_mod_names() scans mods/ for name-collision checks; use metadata
    # stubs of the deployed catalog so a local run sees the same names as CI.
    for other_id, entry in snapshot["catalog"]["mods"].items():
        (work / "mods" / f"{other_id}.wh.cpp").write_text(
            synthetic_source(other_id, entry), encoding="utf-8"
        )
    if change == "added":
        (work / "mods" / f"{mod_id}.wh.cpp").unlink(missing_ok=True)

    git(["init", "-q"], work)
    git(["config", "user.email", "awt-prgate@localhost"], work)
    git(["config", "user.name", "AWT PR gate"], work)
    git(["add", "-A"], work)
    git(["commit", "-q", "-m", "base"], work)
    base_commit = git(["rev-parse", "HEAD"], work)

    destination = work / "mods" / f"{mod_id}.wh.cpp"
    destination.write_text(mod_path.read_text(encoding="utf-8"), encoding="utf-8")
    git(["add", "-A"], work)
    git(["commit", "-q", "-m", "candidate mod"], work)
    head_commit = git(["rev-parse", "HEAD"], work)

    tree = git(["rev-parse", "HEAD^{tree}"], work)
    merge = git(
        ["commit-tree", tree, "-p", base_commit, "-p", head_commit, "-m", "PR merge"],
        work,
    )
    git(["reset", "--hard", "-q", merge], work)
    return work


def parse_github_output(path: Path) -> dict:
    """Parse the multiline GITHUB_OUTPUT format written by upstream's validator."""
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" not in line:
            index += 1
            continue
        name, delimiter = line.split("<<", 1)
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        values[name] = "\n".join(body)
        index += 1
    return values


def parse_annotations(output: str) -> list[dict]:
    annotations = []
    for line in output.splitlines():
        match = ANNOTATION_RE.match(line)
        if match:
            level, file, line_number, message = match.groups()
            annotations.append(
                {
                    "level": level,
                    "file": decode_annotation(file),
                    "line": int(line_number),
                    "message": decode_annotation(message),
                }
            )
        elif line.startswith("::error::"):
            annotations.append(
                {
                    "level": "error",
                    "file": None,
                    "line": None,
                    "message": decode_annotation(line[len("::error::") :]),
                }
            )
    return annotations


def resolve_author_id(author: str, explicit: int | None) -> tuple[int | None, str]:
    if explicit is not None:
        return explicit, "command line"
    try:
        result = subprocess.run(
            ["gh", "api", f"users/{author}", "--jq", ".id"],
            text=True, capture_output=True, check=False, timeout=20,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip()), "GitHub API via gh"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None, "unavailable"


def run_validator(
    work: Path, author: str, author_id: int, body: str, comments: str, data_mode: str
) -> tuple[subprocess.CompletedProcess, dict, list[dict]]:
    output_file = work.parent / f"{work.name}.github-output"
    fetch_file = work.parent / f"{work.name}.fetches.json"
    env = os.environ.copy()
    # The runner executes in a different cwd; relative PYTHONPATH entries from
    # the caller would otherwise point at the synthetic worktree.
    inherited_path = [
        str(Path(entry).resolve()) if entry else str(ROOT)
        for entry in env.get("PYTHONPATH", "").split(os.pathsep)
    ]
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONPATH": os.pathsep.join([str(VENDOR), *inherited_path]),
            "PR_AUTHOR": author,
            "PR_AUTHOR_ID": str(author_id),
            "PR_BODY": body,
            "PR_MAINTAINER_COMMENTS": comments,
            "GITHUB_OUTPUT": str(output_file),
            "AWT_PRGATE_VENDOR": str(VENDOR),
            "AWT_PRGATE_DATA_DIR": str(DATA),
            "AWT_PRGATE_FETCH_LOG": str(fetch_file),
            "AWT_PRGATE_DATA_MODE": data_mode,
        }
    )
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "awt" / "prgate_runner.py")],
            cwd=work, env=env, text=True, capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        result = subprocess.CompletedProcess(
            args=[], returncode=3, stdout=exc.stdout or "", stderr=str(exc)
        )
    github_output = parse_github_output(output_file) if output_file.exists() else {}
    fetch_data = (
        json.loads(fetch_file.read_text(encoding="utf-8"))
        if fetch_file.exists() else {"mode": data_mode, "fetches": [], "compatibility_shims": []}
    )
    return result, github_output, fetch_data


def gate(gate_id: str, title: str, status: str, blocking: bool, **extra) -> dict:
    return {
        "id": gate_id,
        "title": title,
        "status": status,
        "blocking_for_immediate_acceptance": blocking,
        **extra,
    }


def assess(args, snapshot: dict, result: dict, github_output: dict,
           annotations: list[dict], fetch_data: dict, work: Path) -> dict:
    # The upstream script also prints a summary as ::error:: (or ::warning::)
    # when it exits. Keep it in the raw annotations, but don't count the summary
    # as a second finding of the same problem.
    runner_summary = re.compile(r"^Got \d+ (?:errors|review items)\b")
    errors = [
        a for a in annotations
        if a["level"] == "error" and not runner_summary.match(a["message"].strip())
    ]
    warnings = [a for a in annotations if a["level"] == "warning"]
    unavailable = (
        result["returncode"] == 3
        or "PRGATE_UNAVAILABLE" in result["stdout"]
        or "PRGATE_VALIDATOR_CRASH" in result["stdout"]
    )
    try:
        review_count = int(github_output.get("review_item_count", "0"))
    except ValueError:
        review_count = len(warnings)
    review_items = [
        decode_annotation(line) for line in github_output.get("review_items", "").splitlines()
        if line
    ] or [a["message"] for a in warnings]

    gates = []
    failed = bool(errors) or result["returncode"] != 0
    if unavailable:
        run_status, review_status = "unavailable", "unavailable"
    elif failed:
        run_status, review_status = "fail", "unavailable"
    else:
        run_status = "pass"
        review_status = "needs_review" if review_count else "pass"

    gates.append(gate(
        "pr_mod_validation.run",
        "PR mod validation: errors that block a merge (upstream job 'Test changed files')",
        run_status, True,
        error_count=len(errors), annotations=annotations,
        note="Official pr_validation.py executed verbatim against a synthetic one-file PR diff.",
    ))
    gates.append(gate(
        "pr_mod_validation.manual_review",
        "PR mod validation: review items (upstream job 'Manual review')",
        review_status, True,
        review_item_count=review_count, review_items=review_items,
        note="Non-blocking in upstream CI, but a reviewer must inspect each item before merging.",
    ))

    version_matrix = ["1.6.1", "1.7.3", "2.0.0-alpha.8"]
    gates.append(gate(
        "mod_compatibility_check",
        "Official compilation matrix (windhawk-cli / compile_mod.py)",
        "unavailable", True,
        versions=version_matrix,
        note="Requires real Windhawk developer builds on Windows; a mod that compiles here "
             "with this bench's shim is not a substitute. Check the GitHub check on the real PR.",
    ))

    source = (work / "mods" / f"{result['mod_id']}.wh.cpp").read_text(encoding="utf-8")
    twitter = parse_metadata(source)["raw"].get("twitter")
    if not twitter:
        x_status, x_note = "not_applicable", "The mod declares no @twitter metadata."
    elif re.search(rf"(?m)^Verified\s+{re.escape(twitter)}\s*$",
                   result["maintainer_comments"]):
        x_status, x_note = "pass", "Maintainer verification found in supplied comments."
    else:
        x_status = "needs_maintainer"
        x_note = ("X account ownership is verified by the maintainer in a PR comment "
                  "('Verified <url>'); pass it with --maintainer-comments.")
    gates.append(gate(
        "x_account_verification", "X (Twitter) account ownership", x_status,
        x_status in ("needs_maintainer",), note=x_note, twitter=twitter,
    ))

    gates.append(gate(
        "pr_flow", "PR flow: labels, /ai-review, /ready-for-reviewer",
        "not_verifiable_here", True,
        note="GitHub-side automation (pr_labels.yml, pr_commands.yml, "
             "pr_queued_ai_review.yml) decides whose turn it is. "
             "An AI review is not a merge approval.",
    ))
    gates.append(gate(
        "human_review", "AI review findings and human/maintainer approval",
        "not_verifiable_here", True,
        note="No local tool can reproduce the reviewer's decision or branch protection.",
    ))

    if unavailable:
        readiness = "unknown"
    elif failed:
        readiness = "blocked"
    elif review_count or x_status == "needs_maintainer":
        readiness = "needs_manual_review"
    else:
        readiness = "ready_for_ci"

    if readiness == "unknown":
        summary = "The official validator could not run to completion; nothing was accepted."
    elif readiness == "blocked":
        summary = f"{len(errors)} blocking error(s) from the official validator."
    elif readiness == "needs_manual_review":
        summary = (f"No blocking errors, but {review_count} review item(s)"
                   f"{' and an unverified X account' if x_status == 'needs_maintainer' else ''}.")
    else:
        summary = ("No blocking errors and no review items locally; the official compile "
                   "matrix and the human review are still outstanding.")

    not_verifiable = [g["id"] for g in gates
                      if g["status"] in ("unavailable", "not_verifiable_here")]
    manifest = snapshot["manifest"]
    fetches = fetch_data.get("fetches", [])
    data_quality = {
        "mode": args.data_mode,
        "note": (
            "Live fetches were used; the pinned hashes below describe the fallback "
            "snapshot, not the data seen by this run."
            if args.data_mode == "live" else
            "Checks replayed with the pinned, dated data snapshot (no live fetches)."
        ),
        "compatibility_shims": fetch_data.get("compatibility_shims", []),
        "snapshot_generated_at": manifest["generated_at"],
        "upstream_commit": manifest["upstream"]["commit"],
        "exact_sources": [name for name, meta in manifest["files"].items()
                          if meta.get("exact")],
        "derived_sources": [
            {"name": name, "note": meta["note"]}
            for name, meta in manifest["files"].items() if not meta.get("exact")
        ],
        "fetches": fetches,
    }
    if any(f["kind"] == "winbindex_unknown" for f in fetches):
        data_quality["warning"] = (
            "A symbol-hook module name was not present in the derived Windows filename list; "
            "winbindex was not consulted, so that check is not fully verifiable here."
        )

    return {
        "schema": "awt.prgate/1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "input": {
            "mod": str(args.mod),
            "mod_id": result["mod_id"],
            "change": result["change"],
            "author": args.author,
            "author_id": result["author_id"],
            "author_id_source": result["author_id_source"],
            "pr_body_source": str(args.pr_body) if args.pr_body else "built-in template",
        },
        "official_validator": {
            "source": manifest["upstream"]["repository"],
            "commit": manifest["upstream"]["commit"],
            "sha256": manifest["files"]["pr_validation.py"]["sha256"],
            "exit_code": result["returncode"],
        },
        "gates": gates,
        "data_quality": data_quality,
        "verdict": {
            "submission_readiness": readiness,
            "blocking_errors": len(errors),
            "review_items": review_count,
            "summary": summary,
            "verified": [
                "The upstream validator was executed unmodified on a synthetic PR diff "
                "with exactly one added or modified mods/<id>.wh.cpp file.",
                f"Data snapshot: upstream {manifest['upstream']['commit']}, "
                f"taken {manifest['generated_at']}.",
            ],
            "not_verifiable_here": not_verifiable,
            "immediate_acceptance": (
                "not_verifiable_here: a clean local run is not an approval. The official "
                "compiler matrix, X verification, AI review, human review and branch "
                "protection all happen on the real PR."
            ),
        },
        "exit_code": (3 if readiness == "unknown" else 1 if readiness in
                      ("blocked", "needs_manual_review") else 0),
    }


STATUS_LABELS = {
    "pass": "PASS", "fail": "FAIL", "needs_review": "REVIEW",
    "unavailable": "UNKNOWN", "not_verifiable_here": "EXTERNAL",
    "needs_maintainer": "MAINTAINER", "not_applicable": "N/A",
}


def print_summary(report: dict) -> None:
    verdict = report["verdict"]
    print(f"PR gate: {report['input']['mod_id']} "
          f"({report['input']['change']}) by {report['input']['author']}")
    for gate_item in report["gates"]:
        print(f"  [{STATUS_LABELS.get(gate_item['status'], '?'):10s}] "
              f"{gate_item['title']}")
        if gate_item.get("note") and gate_item["status"] not in ("pass", "not_applicable"):
            print(f"             {gate_item['note']}")
        if gate_item.get("error_count"):
            for annotation in gate_item["annotations"][:8]:
                where = f"{annotation['file']}:{annotation['line']}" if annotation["file"] else "workflow"
                print(f"             {where}: {annotation['message'].strip()}")
        if gate_item.get("review_item_count"):
            for item in gate_item["review_items"][:8]:
                print(f"             review: {item.strip()}")
        if gate_item["id"] == "pr_mod_validation.run" and gate_item["status"] in ("fail", "unavailable"):
            if not gate_item.get("error_count"):
                for line in report.get("validator_output", "").splitlines()[:6]:
                    print(f"             validator: {line.strip()}")
    print(f"Submission readiness: {verdict['submission_readiness'].upper()}")
    print(f"Immediate acceptance: {verdict['immediate_acceptance']}")
    print(f"Report: {report.get('report_path', '(stdout only)')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay Windhawk's PR validators for a mod before submitting it.",
        epilog="A PASS means 'the local checks found no blockers', never 'will be merged'.",
    )
    parser.add_argument("mod", type=Path, help="the .wh.cpp file to submit")
    parser.add_argument("--author", required=True, help="GitHub login of the PR author")
    parser.add_argument("--author-id", type=int,
                        help="GitHub user id (resolved with `gh api` when omitted)")
    parser.add_argument("--pr-body", type=Path,
                        help="file containing the intended PR description")
    parser.add_argument("--maintainer-comments", type=Path,
                        help="file with maintainer comments (for X verification)")
    parser.add_argument("--change", choices=("auto", "added", "modified"), default="auto")
    parser.add_argument("--data-mode", choices=("snapshot", "live"), default="snapshot")
    parser.add_argument("--json", type=Path, default=SANDBOX / "prgate-report.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.mod.is_file() or not args.mod.name.endswith(".wh.cpp"):
        parser.error(f"not a .wh.cpp file: {args.mod}")
    snapshot = load_snapshot()
    metadata = parse_metadata(args.mod.read_text(encoding="utf-8"))
    mod_id = metadata["raw"].get("id")
    if not mod_id:
        parser.error("the mod has no @id metadata")

    change = args.change
    if change == "auto":
        change = "modified" if mod_id in snapshot["catalog"]["mods"] else "added"
    author_id, author_id_source = resolve_author_id(args.author, args.author_id)
    if author_id is None:
        print("error: cannot determine the GitHub user id; pass --author-id N "
              "(see `gh api users/<login> --jq .id`)", file=sys.stderr)
        return 2

    body = (args.pr_body.read_text(encoding="utf-8") if args.pr_body
            else (DEFAULT_UPDATE_BODY if change == "modified" else DEFAULT_NEW_BODY))
    comments = (args.maintainer_comments.read_text(encoding="utf-8")
                if args.maintainer_comments else "")
    work = build_pr_worktree(args.mod, mod_id, change, snapshot)
    process, github_output, fetch_data = run_validator(
        work, args.author, author_id, body, comments, args.data_mode
    )
    annotations = parse_annotations(process.stdout)
    result = {
        "returncode": process.returncode,
        "stdout": process.stdout,
        "mod_id": mod_id,
        "change": change,
        "author_id": author_id,
        "author_id_source": author_id_source,
        "maintainer_comments": comments,
    }
    report = assess(args, snapshot, result, github_output, annotations, fetch_data, work)
    report["validator_output"] = process.stdout[-12000:]
    report["stderr"] = process.stderr[-4000:] if process.stderr else ""
    report["worktree"] = str(work)
    report["report_path"] = str(args.json)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.quiet:
        print_summary(report)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
