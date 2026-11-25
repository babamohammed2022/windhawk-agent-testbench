# Visual preview: edit → rebuild → compare

`awt preview` runs a **real Win32 specimen** twice under Wine: first without a mod,
then with the compiled mod loaded in-process. It produces an offline HTML viewer,
two PNG captures, a derived difference heatmap and a JSON manifest. This is an
initial visual iteration loop, **not** noVNC, hot reload, a Windows desktop or the
real Windhawk engine.

## Quick start

On Linux with the toolchain installed by `./setup.sh`:

```bash
python3 -m awt.cli preview examples/visual-playground.wh.cpp
# Open the index.html path printed by the command.

# Negative control: the same mod installed, but the visible effect disabled.
python3 -m awt.cli preview examples/visual-playground.wh.cpp --settings enabled=false

# Your own compatible Win32 public-API mod:
python3 -m awt.cli preview path/to/mod.wh.cpp --settings-json settings.json --settle-ms 1200
```

Edit the mod, rerun the command and open the **new** report. A failed compile does
not replace the old preview with a misleading successful image: every attempt has
a unique `.sandbox/previews/<timestamp>-<id>/` directory and explicit status.
There is no browser-exposed execution endpoint. To view it remotely, serve only
that attempt directory via your environment's static file server, not the repo,
Wine prefixes or your home directory. The HTML embeds its images, so it also works
when downloaded and opened offline; manifest paths are original machine paths.

The HTML shows before/after, a difference map where comparable, bounded host logs
and the provenance manifest. No runtime dependencies on CDNs, JavaScript, Pillow,
ImageMagick or numpy are needed. All log/metadata text is escaped; a restrictive
CSP prevents embedded scripts and external assets.

## What it exercises

`host/playground.cpp` creates a 640×380 client area with a GDI heading, standard
buttons, checkbox, edit, listbox and label. The demo mod intercepts `DrawTextW`,
paints a blue accent behind that specific heading and changes the text. It is a
real inline hook via the shim, not a CSS overlay or a generated screenshot.

The **same executable and scene** are used in both phases. Each phase receives a
fresh Wine prefix, display and storage directory; inherited `AWT_*`/`WINE*`
variables are cleared before explicit configuration. Xvfb chooses an available
display with `-displayfd`; X11 TCP listening is disabled. No global `pkill` is used.
Commands have deadlines and their process groups are terminated on timeout; the
phase's own wineserver is stopped and waited for in a `finally` block. Prefixes
are removed only after successful wineserver cleanup to avoid retaining gigabytes
per attempt. Cleanup failure is a failed preview, not a successful comparison.

The specimen pumps messages and captures its client pixels after a paint and a
fixed settling interval (default 800 ms, supported range 100–10000 ms). It writes
an uncompressed 32-bit BMP through GDI. Python checks format, dimensions and exact
length before converting to PNG, dropping the undefined alpha channel. Captures
must be at most 2048×1536. A clean process exit, capture marker and (for the mod
phase) successful init are required before an image is presented as captured.

The specimen applies queued hooks and calls optional after-init. The current shim
owns process-detach cleanup: the specimen does not call uninit a second time.
This does **not** implement full Windhawk lifecycle or safe DLL hot reload.

## Reading the result

Preview has its own `preview_schema: 1` manifest, not a fabricated runtime verdict.
The `verification` block lists exactly what was observed and what remains outside
scope. A successful preview does not upgrade the regular `verdict.level`.

| Status | CLI exit | Meaning |
| --- | --- | --- |
| `captured` | 0 | Both runs completed and their real client captures were compared |
| `not_executed` | 3 | Missing runtime/compiler prerequisites; no captures substituted |
| `build_failed` | 1 | Mod compilation failed |
| `run_failed` | 1 | Init, capture, process completion, timeout or cleanup failed |
| `comparison_unavailable` | 1 | Captures exist but comparison cannot be completed, e.g. different geometry |
| `error` | 1 | Host compile, capture decoding or other infrastructure error; see reason |
| Invalid arguments/source/settings | 2 | Usage/input error before an attempt is started |

`visual_comparison.overall.status` is separate. Both `match` and `mismatch` can
accompany a successful capture. A mismatch is often precisely the desired effect;
a match might mean a disabled mod, an unexercised feature or an invisible effect.
The manifest includes source/binary hashes, Wine version, settings, requested
includes vs actual specimen target, phase logs, shim stub calls and full comparison
metrics with their implementation/thresholds. The host log includes mod stderr;
it is not an independent security boundary against a malicious mod.

## Honest limits

- Linux/Wine only for this command. Native Windows/VM preview transport is not yet
  connected. `AWT_WINE` selects Wine; `AWT_WINESERVER` can select its matching server.
- The specimen **forces loading irrespective of metadata include/exclude filters**.
  It is not the real application, Explorer or the Windows shell. Process-name
  checks can refuse init. That is reported, not patched out of the mod.
- Captures cover the **client area**, not title bars, desktop/taskbar, popup menus,
  tooltips or other windows. A mod affecting those may show no effect here.
- Settling time is a heuristic, not an application-specific ready condition.
  Animations, clocks, asynchronous workers and Wine/font differences can vary.
- This version has no click automation, live input, settings editor in the browser,
  watch server, overlay slider or hot reload. Rerunning starts fresh processes.
- Each run is isolated from the bench's normal prefix/storage, but Wine exposes
  host files through Z:. **Wine is not a security sandbox**: use a container/VM for
  untrusted mods. Fresh prefixes do not make arbitrary native code safe.
- Equal images do not establish correctness; different images do not establish
  causality for arbitrary mods. The demo acceptance includes an independent pixel
  assertion and enabled/disabled controls; arbitrary mods still need their own
  behavioural assertions and Windows calibration.

## Tests and CI

```bash
python3 tests/unit/run_unit.py test_preview  # parser, viewer, orchestration, cleanup with fixtures/mocks
python3 tests/run_preview_tests.py          # real Wine captures + real DrawTextW hook
bash tests/run_tests.sh                     # units, runtime acceptance, then real preview acceptance
```

The standalone runtime script returns 3 for unavailable prerequisites, never pass.
It asserts the real blue pixels appear with the demo enabled and disappear with it
disabled, validates geometry, init and cleanup, and checks exact baseline agreement
for the disabled fixed specimen. The main acceptance script runs this stage only
after the toolchain gate; missing preview prerequisites there fail the complete
acceptance run rather than silently passing it. CI uploads self-contained HTML,
PNG/JSON and phase logs, explicitly **not** Wine prefixes or compiled binaries.

In the development sandbox for this change Wine/MinGW/Xvfb are absent. Offline unit
tests cannot establish C++ compilation, runtime capture or Wine hook correctness;
those require the real acceptance stage on a toolchain-equipped runner.
