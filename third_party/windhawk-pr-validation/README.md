# Vendored Windhawk PR validation scripts

Snapshot of the PR-side validation code from
[ramensoftware/windhawk-mods](https://github.com/ramensoftware/windhawk-mods)
at commit `f11433e958a8f8a44449a19aefc096eedec76c22`, retrieved 2026-11-25.

| File | Upstream path | Notes |
| --- | --- | --- |
| `pr_validation.py` | `.github/pr_validation.py` | Header states `LICENSE: MIT`, copyright 2023 Mark Jansen. Kept verbatim. |
| `preprocessor.py` | `.github/preprocessor.py` | Kept verbatim; contains one PEP 695 alias, rewritten only in memory for Python 3.11 by `awt/prgate_runner.py`. |
| `extract_mod_symbols.py` | `.github/extract_mod_symbols.py` | Kept verbatim. |
| `pull_request_template.md` | `.github/pull_request_template.md` | Kept verbatim; copied into the synthetic worktree. |
| `data/` | generated | Public, metadata-only snapshot; see `data/manifest.json` and `docs/pr-readiness.md`. |

The upstream repository does not ship a separate LICENSE file in its `.github`
directory; the validator's own header is the source of the MIT notice above.
This bench is not affiliated with Windhawk. The files here are used only to
replay the same checks locally, and are never modified by the runner.

Regenerate the data snapshot with `python3 tools/refresh_prgate_snapshot.py`;
verify the pinned hashes with `python3 tests/unit/run_unit.py prgate`.
