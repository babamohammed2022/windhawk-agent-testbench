# Publishing this repository to GitHub — step by step

Suggested public identity (formal and humble, on purpose):
- **Name / slug:** `modest-windhawk-testbench`
- **Display name:** *A Modest Testbench for Windhawk Mods*
- **Description (for the About box):**
  "An unofficial, provisional Wine sandbox that compiles, loads and observes Windhawk
  mods on Linux, and reports honestly what it could and could not verify."
- **Topics:** `windhawk` `wine` `sandbox` `testing` `windows-mods` `ci`
- **Version tag:** `v0.1.0` (provisional; say so everywhere)

## 0. Before anything else
- Unpack the archive in a clean directory; delete any `.sandbox/` folder if present
  (it is disposable state: Wine prefix, logs, builds — never publish it).
- Read `docs/limitations.md` once: the honest limits belong in the repository, not in
  a footnote.
- The repository ships an MIT `LICENSE`. If you prefer another licence, replace it
  before the first push; do not publish without one.

## 1. With the `gh` CLI (fastest)
```bash
cd automatic-windhawk-testing          # or the unpacked directory
git init -b main                       # skip if .git is already present
git add -A
git commit -m "v0.1.0: a modest testbench for Windhawk mods (Wine, Linux)"
gh auth login                          # once, browser flow
gh repo create modest-windhawk-testbench \
   --public --source=. --remote=origin --push \
   --description "An unofficial, provisional Wine sandbox that compiles, loads and observes Windhawk mods on Linux, and reports honestly what it could and could not verify."
git tag -a v0.1.0 -m "provisional release: shim, runner, report, conformance suite"
git push origin v0.1.0
```

## 2. Without the CLI (web UI)
1. github.com → **New repository** → name `modest-windhawk-testbench`, public,
   *do not* tick README/LICENSE/.gitignore (this archive already provides them).
2. Locally:
   ```bash
   git init -b main && git add -A && git commit -m "v0.1.0: initial provisional release"
   git remote add origin https://github.com/<your-user>/modest-windhawk-testbench.git
   git push -u origin main
   git tag -a v0.1.0 -m "provisional release" && git push origin v0.1.0
   ```
3. In Settings → About: paste the description and topics above; add the link
   `docs/links.md` references (upstream Windhawk) so credit is visible.

## 3. After the first push (checklist)
- [ ] About box carries the disclaimer "unofficial, not affiliated with Windhawk".
- [ ] First release `v0.1.0` published from the tag, with a short note:
      "provisional: verified on Wine 10.0 headless; see docs/limitations.md".
- [ ] Issues enabled; pin one issue titled "What this bench cannot prove" linking
      `docs/limitations.md` (humility is a feature).
- [ ] Optional CI: `.github/workflows/ci.yml` (provided) installs Wine/MinGW on
      ubuntu-latest and runs `tests/run_tests.sh`; expect ~10 min per run.
- [ ] If you reuse code from Windhawk headers or MinHook, their licences/credits stay
      visible (`docs/links.md`, `third_party/minhook`).

## 4. Wording to keep everywhere (house style)
- Never write "the mod works"; write "the bench observed X; Y remains unverified".
- Prefer "provisional", "unofficial", "to the best of the bench's ability".
- Credit upstream first: Windhawk, MinHook, Wine, MinGW.
