# The self-hosted VM protocol (Feature 2)

A VM backend is only useful if the host and the guest agree on something small,
boring and documented. This is that agreement: **one shared folder, four files,
protocol version 1**. No network service, no credentials, no agent installation
beyond copying one PowerShell script that the host stages anyway.

Implementation: `awt/backends/vm.py` (host side), `vm/agent/awt_agent.ps1`
(guest side). Both are unit-tested against `tests/unit/fixtures/fake_vm.py`,
which plays the hypervisor *and* the guest, including the failure modes.

## Sequence

```
host                                          guest (Windows VM)
────                                          ──────────────────
1. revert the clean snapshot      ──┐
2. start the VM                   ──┤  commands.revert_snapshot / commands.start
3. stage <share>/job.json + inbox/  │
4. wait for outbox/heartbeat.json ──┘         agent starts at logon, writes
                                    ◄──────   heartbeat.json (with its own
                                              Windows build, edition, profile)
5. (the agent reads job.json)                 runs inbox\host.exe --mod inbox\mod.dll
                                              writes outbox/logs/*.log
                                    ◄──────   writes outbox/screenshot.png (optional)
6. wait for outbox/result.json      ◄──────   writes outbox/result.json (atomic)
7. copy outbox/ to .sandbox/vm/<mod>/
8. stop the VM                              commands.stop
9. build the report from result.json
```

Step 4 waits `timeouts.boot` seconds (default **900**), step 6 waits
`timeouts.job` (default **600**), both polled every `timeouts.poll` seconds
(default 5). These defaults are generous on purpose: a cold Windows 11 21H2 VM on
spinning rust can need ten minutes to reach a usable desktop, and a run that
times out is reported as `not_executed` with the timeout that expired — never as
a partial success.

## The shared folder

`shared_folder` in the configuration **is** the job directory: the backend stages
into it and reads back from it, because that is the only directory the guest can
see. Per-run copies of everything the guest returned are archived under
`.sandbox/vm/<mod>/` (git-ignored), and the report points at those stable paths.

```
<shared_folder>/
├── job.json                 host → guest   what to run (written last, atomically)
├── inbox/
│   ├── <mod>.wh.cpp         host → guest   the mod source, for the record
│   ├── mod.dll              host → guest   cross-compiled on the host (MinGW)
│   ├── host.exe             host → guest   the bench's loader/host, cross-compiled
│   └── awt_agent.ps1        host → guest   the agent matching this checkout
└── outbox/
    ├── heartbeat.json       guest → host   "I am alive, this is my Windows"
    ├── result.json          guest → host   what actually happened
    ├── screenshot.png       guest → host   optional desktop capture
    ├── logs/
    │   ├── host-output.log  guest → host   stdout of host.exe (the markers)
    │   ├── mod.log          guest → host   the mod's own log (AWT_MOD_LOG)
    │   ├── stub.log         guest → host   shim stubs that were called
    │   └── command-*.log    host           each hypervisor command + exit code
    └── storage/             guest          the mod's Windhawk-style storage dir
```

All JSON files are written to `<name>.json.tmp` and then renamed, so a reader on
the other side of a network share never sees a half-written file. The host also
retries a failed parse a few times before giving up.

## `job.json` (host → guest)

| field | type | meaning |
| --- | --- | --- |
| `protocol` | int | always `1` |
| `job_id` | string | `<mod-id>-<unix time>`, echoed back by the guest |
| `created` | string | UTC timestamp |
| `mod_source` | string | file name inside `inbox/` |
| `mod_dll` | string\|null | `mod.dll` when the host could cross-compile it |
| `host_exe` | string\|null | `host.exe` when the host could cross-compile it |
| `agent` | string\|null | the staged agent script name |
| `mode` | string | `auto` \| `self` \| `target` (host.exe's mode) |
| `seconds` | int | how long the mod stays loaded |
| `target` | string\|null | target process for `--mode target` |
| `screenshot` | bool | whether the guest should capture the desktop |
| `symbols` | bool | whether a symbol-matrix run was requested |
| `timeout` | int | the guest's own budget, in seconds |
| `expected_outputs` | list | `["result.json", "logs/"]` |
| `notes` | list | honest gaps, e.g. "nothing was cross-compiled for the guest: MinGW …" |

If `host_exe` or `mod_dll` is `null`, the guest cannot run anything and must say
so in `result.json` (`status: not_executed`, with the reason). The committed
agent does exactly that instead of trying to compile inside the VM.

## `heartbeat.json` (guest → host)

`protocol`, `agent_version`, `heartbeat_at`, `share`, `powershell`, and
`environment` — the guest's own `run.environment` block (`os`, `os_version`,
`edition`, `build_number`, `product_type`, `shell_profile`, `virtualization`,
`vm_used`, `arch`, `hostname`, `collected_by`, `collected_at`, `notes`,
`caveats`). The agent reads these from
`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion` and `Win32_OperatingSystem`,
which is why the guest's values **win** over the configuration file: the machine
that ran the mod is the authority on what it is.

The heartbeat is refreshed every 30 s while the agent waits, so the host can tell
"still booting" from "gone".

## `result.json` (guest → host)

| field | type | meaning |
| --- | --- | --- |
| `protocol`, `agent`, `job_id` | | as above |
| `started`, `finished` | string | UTC timestamps |
| `status` | string | `completed` \| `not_executed` |
| `reason` | string\|null | what went wrong, in the guest's words |
| `environment` | object | merged into `run.environment` (guest values win) |
| `build` | object | `{ok, errors, warnings}` — the DLL arrived prebuilt |
| `run.host_output` | string | host.exe's stdout/stderr: the `[awt-host]` markers |
| `run.mod_log` | string | the mod's own log text |
| `run.stub_log` | string | shim stubs that were called |
| `run.exit_code` | int\|null | host.exe's exit code |
| `run.timed_out` | bool | whether the guest had to kill it |
| `run.hooks_fired` | object | optional; otherwise counted from `mod_log` |
| `artifacts.screenshot` | string\|null | `screenshot.png` when one was captured |
| `artifacts.logs` | list | file names in `logs/` |
| `verdict` | object | optional; recorded verbatim as `run.guest_verdict` |

The host derives the verdict itself, with the same shared code as every other
backend (`awt/verdict.py`): markers → `load_failed` / `crashed` /
`public_hooks_verified` / `loads_no_crash`. A guest that claims a verdict is
quoted, never believed: `run.guest_verdict` keeps its claim, `verdict.level`
comes from the evidence.

The collected screenshot is validated before it is listed: a file whose first
bytes are not the PNG/BMP/JPEG signature is reported as *unavailable*, and a
valid but suspiciously small capture (< 1 KiB) is accepted with a note that the
guest may not have had a shell to draw.

## Configuration

`configs/vm.example.yaml` is the committed template; copy it to
`configs/vm.local.yaml` (ignored by the `*.local.yaml` pattern) or point
`$AWT_VM_CONFIG` at any path. No VM image, snapshot name or credential belongs in
this repository.

Commands run from the repository root and come in two forms:

```yaml
# a single string -> run through a shell (sh -c on Linux, cmd /c on Windows)
commands:
  revert_snapshot: 'virsh snapshot-revert {vm_name} {snapshot} --running'
  start: 'qemu-system-x86_64 -name {vm_name} -drive file={disk},if=virtio -virtfs local,path={shared},mount_tag=awt,security_model=mapped-xattr -display none -daemonize'
  stop: 'virsh destroy {vm_name}'

# a list -> run directly, no shell: portable and free of quoting problems
commands:
  revert_snapshot: [powershell, -NoProfile, -File, vm/restore-snapshot.ps1, '{vm_name}', '{snapshot}']
  start: [powershell, -NoProfile, -Command, 'Start-VM -Name {vm_name}']
  stop: [powershell, -NoProfile, -Command, 'Stop-VM -Name {vm_name} -Force']
```

Placeholders come from `vars:` plus `{repo}`, `{shared}`, `{job_dir}`, `{inbox}`,
`{outbox}`, `{job_id}`, `{vm_name}`, `{hypervisor}`. An unknown placeholder is an
error, not an empty string: a typo in `-drive file={diks}` must not boot the wrong
image.

`start` must return promptly (launch the VM in the background); the backend then
waits for the heartbeat, so the command does not have to block until Windows is
up. Sharing the folder: QEMU `-virtfs`/virtio-9p, VirtualBox
`VBoxManage sharedfolder add`, Hyper-V an SMB share mapped to a drive letter. The
guest sees it as the drive in `guest_workdir` (`S:\awt` by convention).

### Installing the agent in the guest

Once, by hand, inside the VM — then it needs no attention:

```powershell
# copy the staged agent somewhere stable, e.g. S:\awt\awt_agent.ps1 -> C:\awt\
schtasks /create /tn "awt-agent" /sc onlogon /rl highest ^
  /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\awt\awt_agent.ps1 -Share S:\awt -Once"
```

Or drop a shortcut to the same command in the Startup folder. The script is plain
Windows PowerShell 5.1: it installs nothing, downloads nothing, opens no socket
and contains no credentials. `-Once` handles a single job and exits, which is what
the backend expects; without it the agent keeps watching, which is handy when
driving the VM by hand.

Because the agent auto-starts at logon, the VM must log a user in automatically
(netplwiz / `AutoAdminLogon`) for the protocol to work unattended.

## Failure modes, and what the report says

| what happened | `verdict.level` | where it shows up |
| --- | --- | --- |
| no configuration file | `not_executed` | reason names `configs/vm.example.yaml` and `$AWT_VM_CONFIG` |
| configuration invalid | `not_executed` | every problem listed (`commands.start` required, `shared_folder` must be absolute, …) |
| snapshot revert failed | `not_executed` | `snapshot_restored: false`, the VM is **not** started |
| no revert command configured | whatever ran | caveat: "the VM state from previous runs is NOT rolled back" |
| VM never boots / no agent | `not_executed` | "the guest agent never wrote heartbeat.json within 900s"; the stop command still runs |
| guest alive, job never finished | `not_executed` | "…did not produce result.json within 600s; the heartbeat was seen, so the guest is alive but the job did not complete" |
| host.exe missing in `inbox/` | `not_executed` | the job note says the host could not cross-compile |
| guest crash | `crashed` | `run.crashed: true`, exit code, and the crash text in the merged log |
| truncated/absent screenshot | unchanged | the capture is *unavailable*, with the reason; the run itself stands |
| guest is Windows Server | unchanged | `shell_profile: server` + an `indicative` caveat |

## Snapshot discipline

The clean snapshot is what makes a result reproducible, so build it once,
carefully:

1. Install Windows, activate or leave in evaluation, install **nothing else**
   (no Windhawk, no browser, no antivirus beyond the default).
2. Fix the display: one resolution, one colour depth, one DPI scale (100 %), and
   keep it — screenshots are only comparable at a fixed geometry
   (`docs/reference-vms.md` lists what the reference VMs use).
3. Turn off everything that changes the desktop by itself: Windows Update,
   screensaver, lock-on-idle, transparency/animation effects where the mod does
   not depend on them, notifications, and the "finish setting up your device"
   prompts.
4. Set the timezone and clock to something fixed if the mod renders times, or
   accept that time-dependent pixels will differ.
5. Log a user in automatically, install the agent task, verify the share is
   visible as `S:\awt`, reboot once, verify the heartbeat appears.
6. Shut down cleanly and take the snapshot (`clean`). Never test on it directly.

Revert before every run — the backend does it by default and records whether it
happened.
