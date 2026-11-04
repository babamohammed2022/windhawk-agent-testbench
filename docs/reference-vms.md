# Reference VMs

Three Windows installations cover most of what a mod can do to a shell, and they
are the three this bench is written against. None of them can be shipped in a
repository: **Windows images are Microsoft's, screenshots of Microsoft interfaces
are not ours to redistribute, and neither is committed here.** This document says
what to build, how to make it reproducible, and how to regenerate reference
screenshots locally instead.

For the protocol that drives them, see `docs/vm-protocol.md`. For what a run on
them proves, see `docs/backends.md`.

| VM | build | why it matters | reference geometry |
| --- | --- | --- | --- |
| Windows 7 Thin PC, Aero off | 7601 (SP1) | the **Basic/Classic** shell: no DWM composition, no Aero glass. Any mod that touches window frames, taskbar buttons or `uxtheme` behaves differently here, and it is the oldest shell still in the field. | 800×600, 32-bit colour, 96 DPI |
| Windows 8 Developer Preview | **8102** | the Metro/immersive transition: `twinui.dll` exists, `twinui.pcshell.dll` does **not** (it appears in Windows 10 1607). Mods that hook `twinui.pcshell.dll` must fail cleanly here, and this is the cheapest way to see that. | 1280×768, 32-bit colour, 96 DPI |
| Windows 11 21H2 | 22000 | the current XAML taskbar (`Taskbar.dll`, `twinui.pcshell.dll`), rounded corners, Mica. The environment most published shell mods target — and by far the slowest to run. | 1024×768 (see below), 32-bit colour, 100 % scale |

## How to obtain them

Nothing here is downloadable from this project, and no link in this repository
points at an image.

* **Windows 7 Thin PC** — a real SKU (Windows Embedded Standard 7 based, SP1,
  build 7601) that Microsoft distributed to OEMs and volume-license customers.
  Out of support since January 2020. Use your own license/MSDN/VLSC archive; if
  you cannot, use plain Windows 7 SP1 with Aero disabled — the shell behaviour
  that matters here (Basic theme, no composition) is the same, and the report
  records the edition and build the guest actually reports.
* **Windows 8 Developer Preview (build 8102)** — distributed publicly by
  Microsoft in September 2011 and long since withdrawn. It is preserved in
  archive communities; check the checksum of whatever you obtain and record the
  build in your VM configuration, because the bench reports what the guest says
  it is (`run.environment.build_number`), not what you intended to install.
* **Windows 11 21H2 (build 22000)** — Microsoft still publishes evaluation VHDs
  for Windows 11 client editions (Microsoft Evaluation Center, 90-day
  evaluation). Those images are *already* VMs, expire, and are the least effort.
  21H2 specifically is no longer the current release, so pin the build you
  install and note it in `guest.label`; a 22H2/23H2 guest is a different shell.

### The build 8102 / 8120 discrepancy

Notes circulated for this bench asked for "Windows 8 beta build **8120**". The
Windows 8 instance that browser7.win runs — the closest thing to a public
reference for that era — is build **8102**, the Developer Preview, at 1280×768
with 512 MB of RAM and a broken network stack (checked 2026-11-04). Build 8120 is
a different, less widely mirrored pre-beta. The bench does not resolve this by
guessing: `run.environment.build_number` comes from the guest itself
(`RtlGetVersion`/registry in the CI backend, `Win32_OperatingSystem` in the VM
agent), so whichever build you install is what the report says, and a comparison
against a reference screenshot taken on the other build is a comparison you can
see is wrong.

## Making each VM reproducible

The clean snapshot is the whole point (`docs/vm-protocol.md` §Snapshot
discipline). The VM-specific parts:

### Windows 7 Thin PC — Aero off

1. Personalization → **Windows Basic** or **Windows Classic** theme. Verify with
   `dwm` not running (`tasklist | findstr dwm` should be empty in Classic).
2. Turn off desktop composition-dependent eye candy: window animations, Aero
   Peek, Aero Shake (`SystemPropertiesAdvanced` → Performance → uncheck all but
   "Show window contents while dragging" if you want maximum determinism).
3. Classic theme with 800×600: the taskbar is one row of small buttons, no
   thumbnails. That is the reference shot.
4. Display: 800×600, 32-bit, 96 DPI, "Make text and other items larger" off.

### Windows 8 Developer Preview (8102)

1. 512 MB is what the public reference runs with; give it 2 GB if your host can,
   and note the difference — with too little RAM the Start screen tiles render
   progressively and a screenshot taken too early is a *different* screenshot.
2. Wait for the Start screen, not the lock screen: the agent's heartbeat is not
   enough, so let the job start with a delay or capture after the first
   `explorer.exe` window appears.
3. 1280×768 is below the 1366×768 minimum some immersive apps demand, which is
   exactly why it is interesting: it shows the "resolution too low" fallbacks.
4. Disable the network (the reference has none): a mod that calls
   `Wh_GetUrlContent` must fail cleanly rather than hang.

### Windows 11 21H2 — slow, plan for it

1. **Give it time, not retries.** `timeouts.boot: 900` is a floor, not a ceiling;
   1800 s is reasonable on a busy host. The failure mode to avoid is a boot that
   is *almost* finished: the taskbar appears before the tray icons do.
2. **Boot from an initialised snapshot**, never from a cold install: first-run
   OOBE, telemetry prompts, "finish setting up your device", Widgets
   onboarding and Store updates all change the desktop between runs. Take the
   snapshot after all of that has been dismissed once.
3. **Minimise reboots.** A revert to a running-state snapshot (QEMU: save the VM
   state, `virsh snapshot-revert --running`) avoids the whole boot; a disk-only
   snapshot costs 5–15 minutes each time on this build. Configure
   `commands.revert_snapshot` accordingly and record which you used.
4. Widgets, search highlights and the "recommended" area of the Start menu are
   network-driven and will differ run to run. Disable them
   (Settings → Personalization → Taskbar / Start) or crop them out with the
   visual-diff ROI (`python3 -m awt.visualdiff --help`) rather than chasing
   pixels you cannot control.
5. Transparency effects and Mica make pixels depend on what is behind them:
   either disable transparency, or always screenshot the same window layout.
6. 1024×768 (the public reference geometry) is below Windows 11's supported
   minimum of 1280×720 for some experiences; use it only if you want to compare
   against that reference. For judging a mod, 1920×1080 at 100 % scale is the
   more useful target — just keep it fixed.

## Reference screenshots

Reference screenshots are **local artifacts**, regenerated on demand, never
committed:

* they are images of Microsoft's interfaces, which this repository does not
  redistribute;
* they go stale the moment a cumulative update changes a glyph or a margin;
* a committed PNG cannot tell you which build, resolution, theme, DPI or clock
  produced it — which is exactly what a visual comparison needs to be fair.

Store them under `.cache/refshots/<version>/` (git-ignored) with a metadata file
recording at least: version/build, date captured, resolution, DPI, theme, source
(a VM you built, or a URL), and the navigation state (desktop, Start menu open,
flyout open, window focused). `python3 -m awt.refshots` is the intended way to
regenerate the browser7.win-based references; until it exists, capture them by
hand in the VM and write the metadata yourself — the visual comparison
(`python3 -m awt.visualdiff`) will refuse to compare an obtained screenshot with
a reference at a different resolution, and says so rather than scaling silently.

Two rules that are not negotiable:

1. A capture that failed is `unavailable`. A partial page, a half-booted desktop
   or a black session-0 rectangle is never passed off as a reference.
2. Public reference sites (browser7.win and similar) are **screenshot references
   only**. They are not execution environments, they must not be used as a
   backend for Feature 2, and access must stay gentle: no mass scraping, pauses
   between requests, respect their terms.
