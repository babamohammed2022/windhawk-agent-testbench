# PR readiness — Windhawk submission validators (offline replay)

This is an **advisory gate** for a mod that is about to be submitted as a pull
request to [`ramensoftware/windhawk-mods`](https://github.com/ramensoftware/windhawk-mods).
It answers a narrower question than "will it be accepted?":

> Did the checks that Windhawk itself runs on a PR find anything that blocks a
> merge, and which of the remaining checks cannot be reproduced here?

The authoritative part is **upstream's own validator**, not a reimplementation.
`third_party/windhawk-pr-validation/pr_validation.py`, `preprocessor.py`,
`extract_mod_symbols.py` and `pull_request_template.md` are verbatim copies of
`.github/` at upstream commit `f11433e958a8f8a44449a19aefc096eedec76c22` (snapshot
taken 2026-11-25). SHA-256 hashes are pinned in
`third_party/windhawk-pr-validation/data/manifest.json` and verified by
`tests/unit/test_prgate.py::test_snapshot_integrity`.

Nothing here changes upstream's verdicts to "pass". When the validator needs a
value that is not in the snapshot (for example an archived README image), the
gate is reported `unavailable`, never silently skipped.

## What Windhawk runs on a PR today

| Upstream check | What it does | Replay here |
| --- | --- | --- |
| `PR mod validation` → job **Test changed files** (`.github/workflows/pr_validation.yml`) | Exactly one added/modified `mods/<id>.wh.cpp`; PR description (authorship for new mods, changelog for updates); metadata, README, settings YAML, symbol hooks, encoding, line lengths, code compactness, callback signatures/scopes; GitHub author id and X account records; symbol extraction. Errors block the merge. | **Yes**, exact upstream script, replayed against a synthetic one-file PR diff. |
| `PR mod validation` → job **Manual review** | Collects upstream "review items" as annotations and fails separately; a reviewer must inspect them. | **Yes**, review items are reported and are blocking for "ready immediately". |
| `Mod compatibility check` (`.github/workflows/mod_compatibility_check.yml`) | Compiles the changed mod with Windhawk **1.6.1**, **1.7.3** and **2.0.0-alpha.8** on Windows; compiler warnings fail the check. | **No** — requires real Windhawk developer builds. Always `unavailable`; check the real PR. |
| `PR flow labels` / `PR flow commands` / `PR flow queued AI review` (`pr_labels.yml`, `pr_commands.yml`, `pr_queued_ai_review.yml`) | Move the PR through `waiting-for-author` → `waiting-for-ai-review` → `waiting-for-reviewer`; `/ai-review` and `/ready-for-reviewer` are commands. | **No** — GitHub-side automation. Reported `not_verifiable_here`. |
| X (Twitter) ownership | The maintainer confirms ownership with a `Verified <url>` line in a PR comment; the validator reads those comments. | **Partly** — supply the comments with `--maintainer-comments`; otherwise `needs_maintainer` when `@twitter` is present. |
| AI review findings, human review, branch protection | Decisions taken on GitHub, not by a validator. | **No** — always `not_verifiable_here`. |

Branch protection settings are not publicly readable by this sandbox, so no
claim is made about which checks are *required* for a particular PR.

## Run it

```bash
python3 -m pip install pyyaml                 # once; the upstream validator needs it
python3 -m awt.prgate mods/my-mod.wh.cpp \
    --author my-github-login \
    --author-id 123456                        # or omit: resolved via `gh api users/<login>`
    --pr-body pr-body.md \
    --json .sandbox/prgate-report.json
```

Options worth knowing:

* `--pr-body FILE` — the *intended* PR description. New mods must keep the
  `## Mod authorship` section; updates need real text between
  `<!-- changelog:start -->` and `<!-- changelog:end -->`. Without this option a
  minimal template is used, which is enough for a new mod but correctly fails
  the changelog check for an update.
* `--change auto|added|modified` — `auto` decides from the catalog snapshot.
* `--maintainer-comments FILE` — maintainer comments, for X verification.
* `--data-mode snapshot|live` — replay the dated snapshot (default, offline and
  deterministic), or run the upstream validator with its real network fetches.
  Live mode needs access to `raw.githubusercontent.com`, `spdx.org` and
  `winbindex.m417z.com`.
* `--quiet` — no human summary; only the JSON report and the exit code.

The bench builds a throwaway git history under `.sandbox/prgate/run-*/` whose
`HEAD^1..HEAD` is exactly one added or modified file — the same shape the
upstream workflow gets from GitHub's test merge commit. The synthetic history is
kept for diagnosis. Nothing is pushed and no network call is made in snapshot
mode.

Exit codes: **0** no local blocker (`ready_for_ci`), **1** blocked or needs a
review, **2** usage error (for example an unresolvable GitHub user id), **3**
the validator could not run to completion (`unknown`, never a pass).

## Read the report

```json
"verdict": {
  "submission_readiness": "ready_for_ci",
  "blocking_errors": 0,
  "review_items": 0,
  "summary": "...",
  "verified": ["..."],
  "not_verifiable_here": ["mod_compatibility_check", "pr_flow", "human_review"],
  "immediate_acceptance": "not_verifiable_here: ..."
}
```

`submission_readiness` values:

| Value | Meaning |
| --- | --- |
| `blocked` | At least one upstream error (or a validator failure). Fix it before submitting. |
| `needs_manual_review` | No errors, but upstream raised review items or X ownership is unverified. |
| `ready_for_ci` | No errors and no review items **locally**; compile matrix and human review still pending. |
| `unknown` | The validator could not run; nothing was checked. Do not re-interpret this as a pass. |

Always quote `verdict.blocking_errors`, `verdict.review_items`,
`verdict.not_verifiable_here` and `verdict.immediate_acceptance` together. In
particular, `ready_for_ci` is **not** "accepted", "will be merged" or "works on
Windows": this bench never runs the Windhawk compiler, does not inject into a
real shell, and cannot see GitHub-side decisions. Even a zero-error run only
says that the upstream *source-level* validators found nothing on that snapshot
of catalog data.

`data_quality` records the snapshot date, the upstream commit, which data files
are exact copies, which are metadata projections, the actual fetches replayed
and any interpreter compatibility shim. On Python 3.11, upstream's single PEP 695
`type Macros = ...` alias is rewritten **in memory** by
`awt/prgate_runner.py`; the vendored file stays byte-identical, and the shim is
reported in `data_quality.compatibility_shims`.

## Data snapshot and freshness

`third_party/windhawk-pr-validation/data/` contains:

* `mod_author_data.json` — exact copy of the catalog's author records;
* `spdx_license_ids.json` — SPDX identifiers, extracted from
  `spdx/license-list-data`;
* `catalog.json` — **metadata only** for the 982 deployed mods: names, GitHub
  URLs, versions and version histories. Raw mod sources (over 100 MB) are not
  vendored; the runner synthesizes a metadata-only source where upstream's
  validator reads the deployed mod;
* `windows_filenames.json` — **derived subset** from the Windows file names
  used in deployed mods' `@include` lines, not the live winbindex list. Symbol
  hook module names absent from it are reported as not fully verifiable;
* `manifest.json` — provenance, hashes and freshness flags.

Refresh when upstream changes:

```bash
python3 tools/refresh_prgate_snapshot.py          # needs GH_TOKEN or `gh auth login`
python3 tests/unit/run_unit.py prgate             # hashes must still verify
```

The refresh tool stores no token and no source code. When copying a newer
upstream `pr_validation.py`, update the pinned SHA-256 in `manifest.json` by
re-running the refresh tool.

## Files

* `awt/prgate.py` — CLI, synthetic PR history, gates and report.
* `awt/prgate_runner.py` — runs the unmodified upstream script in the worktree.
* `third_party/windhawk-pr-validation/` — verbatim upstream scripts, PR template
  and dated data snapshot.
* `tests/fixtures/prgate/` — small mods and PR bodies used by the tests.
* `tests/unit/test_prgate.py` — end-to-end replay tests (skip cleanly when
  PyYAML is not installed).
