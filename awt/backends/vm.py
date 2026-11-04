"""Self-hosted VM backend: real client Windows, restored to a clean snapshot.

Some mods can only be judged on a real Windows shell - a taskbar mod on Windows
11 21H2, a Classic-theme mod on Windows 7. Nobody can ship those machines in a
repository, and Microsoft images must not be redistributed, so this backend does
the only honest thing: it drives a VM **the user already has**, and it keeps the
description of that VM in a git-ignored local configuration file.

    cp configs/vm.example.yaml configs/vm.local.yaml   # then edit it
    python3 -m awt.backends --backend self-hosted-vm --mod examples/hello-log.wh.cpp

The protocol between host and guest is a shared folder and four small files
(`docs/vm-protocol.md`):

    host -> guest   job.json            what to run, for how long
    guest -> host   heartbeat.json      the agent is alive, guest environment
                    result.json         the report of what actually happened
                    logs/*.log          host and mod logs
                    screenshot.png      optional desktop capture

The sequence per test run is: revert to the clean snapshot, start the VM, wait
for the agent's heartbeat, submit the job, wait for `result.json`, collect the
logs and the screenshot, stop the VM. Timeouts are deliberately generous (a cold
Windows 11 21H2 VM can need ten minutes to reach a usable desktop) and a run
that never answers is reported as `not_executed` with the timeout that expired -
never as a partial success.

Nothing here assumes a particular hypervisor: the start/stop/revert commands are
strings from the user's configuration, so QEMU/KVM, VirtualBox, Hyper-V and
VMware all work by supplying their own command lines.
"""
import json
import os
import shutil
import subprocess
import time

from .. import analyzer, builder, report as report_mod, verdict
from .. import env as host_env
from .base import (Backend, apply_environment, environment_block, shell_profile)


def _history_dir():
    """Same place the Wine runner files its copies: `.sandbox/history`."""
    path = os.path.join(host_env.SANDBOX, "history")
    os.makedirs(path, exist_ok=True)
    return path

PROTOCOL_VERSION = 1
CONFIG_TEMPLATE = os.path.join("configs", "vm.example.yaml")
# Only the `*.local.yaml` name: .gitignore ignores that pattern, so a VM
# configuration can never be committed by accident. Anything else goes through
# $AWT_VM_CONFIG.
DEFAULT_CONFIG_NAMES = (os.path.join("configs", "vm.local.yaml"),)
DEFAULT_TIMEOUTS = {"boot": 900, "job": 600, "poll": 5, "command": 300}
REQUIRED_COMMANDS = ("start", "stop")
OPTIONAL_COMMANDS = ("revert_snapshot", "status", "guest_screenshot")

INBOX = "inbox"
OUTBOX = "outbox"
JOB_FILE = "job.json"
HEARTBEAT_FILE = "heartbeat.json"
RESULT_FILE = "result.json"


class VmConfigError(Exception):
    """The local VM configuration is missing or unusable."""


def find_config(explicit=None):
    """$AWT_VM_CONFIG, then an explicit path, then the conventional local files."""
    if explicit:
        return explicit
    from_env = os.environ.get("AWT_VM_CONFIG")
    if from_env:
        return from_env
    for name in DEFAULT_CONFIG_NAMES:
        path = os.path.join(host_env.REPO, name)
        if os.path.exists(path):
            return path
    return None


def load_config(path=None):
    """Parse the local VM configuration. Raises VmConfigError with a fix hint."""
    from .. import yamlmini
    path = find_config(path)
    if not path or not os.path.exists(path):
        raise VmConfigError(
            "no VM configuration found. Copy %s to configs/vm.local.yaml (it is "
            "git-ignored) or point $AWT_VM_CONFIG at one - no VM image, snapshot "
            "or credential belongs in this repository" % CONFIG_TEMPLATE)
    try:
        config = yamlmini.load(path)          # yamlmini.load takes a path
    except yamlmini.YamlMiniError as exc:
        raise VmConfigError("%s: %s" % (path, exc))
    except OSError as exc:
        raise VmConfigError("%s: %s" % (path, exc))
    if not isinstance(config, dict):
        raise VmConfigError("%s: expected a mapping at the top level" % path)
    config["_path"] = path
    return config


def is_command(value):
    """A command is a non-empty string (run through a shell) or a list of
    arguments (run directly, no shell). The list form is the portable one: a
    Windows host driving Hyper-V has no `/bin/sh`, and quoting a path with spaces
    inside a shell string is where VM configurations usually break."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(isinstance(part, str) and str(part).strip()
                                   for part in value)
    return False


def validate_config(config):
    """Return a list of problems (empty when the configuration is usable)."""
    problems = []
    commands = config.get("commands")
    if not isinstance(commands, dict) or not commands:
        problems.append("`commands:` is missing or empty")
        commands = {}
    for name in REQUIRED_COMMANDS:
        if name not in commands:
            # a present-but-blank command is reported by the type check below,
            # once, with the more specific message
            problems.append("`commands.%s` is required" % name)
    for name, value in commands.items():
        if not is_command(value):
            problems.append("`commands.%s` must be a non-empty string or a list of "
                            "arguments (omit it instead of leaving it blank)" % name)
    shared = config.get("shared_folder")
    if not shared:
        problems.append("`shared_folder:` is required (the host path the guest sees)")
    elif not os.path.isabs(str(shared)):
        problems.append("`shared_folder:` must be an absolute host path, got %r"
                        % shared)
    guest = config.get("guest")
    if not isinstance(guest, dict) or not guest:
        problems.append("`guest:` is required (label, build_number, product_type)")
    hypervisor = config.get("hypervisor")
    if not hypervisor:
        problems.append("`hypervisor:` is required (qemu|virtualbox|hyperv|other)")
    timeouts = config.get("timeouts")
    if timeouts is not None and not isinstance(timeouts, dict):
        problems.append("`timeouts:` must be a mapping of seconds")
    return problems


def substitute(template, values):
    """Fill `{placeholders}` in a command template (string, or list of them).

    Unknown placeholders are an error rather than a silent empty string: a typo
    in a `qemu-system-x86_64 -drive file={disk}` line must not become a command
    that boots the wrong image.
    """
    import re
    if isinstance(template, (list, tuple)):
        return [substitute(part, values) for part in template]
    text = str(template)
    missing = sorted(set(re.findall(r"\{(\w+)\}", text)) - set(values))
    if missing:
        raise VmConfigError("command template refers to unknown placeholder(s) %s; "
                            "define them under `vars:` (known: %s)"
                            % (", ".join("{" + m + "}" for m in missing),
                               ", ".join(sorted(values)) or "none"))
    for key, value in values.items():
        text = text.replace("{%s}" % key, str(value))
    return text


def timeouts_of(config):
    """Effective timeouts: the configuration's values over generous defaults."""
    out = dict(DEFAULT_TIMEOUTS)
    for key, value in (config.get("timeouts") or {}).items():
        try:
            out[key] = int(value)
        except (TypeError, ValueError):
            continue
    return out


class VmBackend(Backend):
    """Drive a user-provided Windows VM through a shared folder."""

    name = "self-hosted-vm"
    label = "self-hosted Windows VM (QEMU/VirtualBox/Hyper-V, clean snapshot)"
    executes_on = "windows-vm"

    def __init__(self, config_path=None, workdir=None):
        self._config_path = config_path
        self._workdir = workdir
        self._config = None
        self._config_error = None

    # -- configuration ------------------------------------------------------
    def config_path(self):
        try:
            return find_config(self._config_path)
        except Exception:                                          # pragma: no cover
            return None

    def config(self):
        if self._config is None and self._config_error is None:
            try:
                self._config = load_config(self._config_path)
            except VmConfigError as exc:
                self._config_error = str(exc)
        if self._config_error:
            raise VmConfigError(self._config_error)
        return self._config

    def describe(self):
        return {
            "name": self.name,
            "label": self.label,
            "executes_on": self.executes_on,
            "shell_profile": (self.environment() or {}).get("shell_profile"),
            "limitations": [
                "requires a VM the user supplies: no Windows image, snapshot or "
                "credential is stored in this repository",
                "the guest agent runs the bench's shim host inside the VM, so "
                "internal symbol hooks stay not_verifiable_here unless the real "
                "Windhawk engine is installed in the guest",
                "outcomes are only as reproducible as the snapshot: revert it "
                "before every run (this backend does that by default)",
            ],
        }

    def available(self):
        path = self.config_path()
        if not path:
            return False, ("no VM configuration: copy %s to configs/vm.local.yaml "
                           "or set $AWT_VM_CONFIG" % CONFIG_TEMPLATE)
        try:
            config = self.config()
        except VmConfigError as exc:
            return False, str(exc)
        problems = validate_config(config)
        if problems:
            return False, "%s: %s" % (path, "; ".join(problems))
        if not shutil.which("sh") and os.name != "nt":
            return False, "no shell available to run the configured commands"
        guest = config.get("guest") or {}
        return True, ("VM %r via %s, shared folder %s (guest: %s, build %s, %s shell)"
                      % (config.get("vm_name") or guest.get("label") or "?",
                         config.get("hypervisor"), config.get("shared_folder"),
                         guest.get("label") or "?", guest.get("build_number") or "?",
                         guest.get("product_type") or "unknown"))

    def environment(self, snapshot_restored=None, job=None):
        try:
            config = self.config()
        except VmConfigError:
            return environment_block(backend=self.name, shell_profile="unknown",
                                     vm_used=None, collected_by=self.name,
                                     notes=["no usable VM configuration"])
        guest = config.get("guest") or {}
        product_type = guest.get("product_type")
        build = guest.get("build_number")
        profile = shell_profile(os_name="Windows", product_type=product_type,
                                build_number=build)
        notes = []
        caveats = []
        if profile == "server":
            caveats.append("the guest is a Windows Server edition: shell-related "
                           "outcomes are indicative, not verified")
        if not (config.get("commands") or {}).get("revert_snapshot"):
            caveats.append("no `commands.revert_snapshot` is configured, so the VM "
                           "state from previous runs is NOT rolled back and the "
                           "outcome is less reproducible")
        return environment_block(
            backend=self.name,
            os="Windows",
            os_version=guest.get("os_version"),
            edition=guest.get("label"),
            build_number=(int(build) if str(build or "").isdigit() else build),
            product_type=product_type,
            shell_profile=profile,
            virtualization=str(config.get("hypervisor") or "vm"),
            vm_used=True,
            snapshot_restored=snapshot_restored,
            runner=config.get("vm_name"),
            runner_image=guest.get("image"),
            arch=guest.get("arch") or "x86-64",
            hostname=guest.get("hostname"),
            collected_by="awt.backends.vm (configuration: %s)"
                         % os.path.basename(config.get("_path") or "?"),
            seconds=None,
            notes=notes + ([(job or {}).get("note")] if (job or {}).get("note") else []),
            caveats=caveats,
        )

    # -- shared-folder plumbing --------------------------------------------
    def workdir(self, slug=None):
        """The shared folder itself: that is the directory the guest can see.

        Staging anywhere else would be a protocol error - the guest agent reads
        `<share>/job.json` and writes `<share>/outbox/`, so the host must use the
        same directory. Per-run copies for the report live in `archive_dir()`.
        """
        path = self._workdir or self.config().get("shared_folder")
        if not path:
            raise VmConfigError("no shared_folder configured")
        os.makedirs(path, exist_ok=True)
        for sub in (INBOX, OUTBOX, os.path.join(OUTBOX, "logs")):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        return path

    def archive_dir(self, slug=None):
        """A git-ignored per-run copy of what the guest sent back."""
        path = os.path.join(host_env.SANDBOX, "vm", slug or "job")
        os.makedirs(path, exist_ok=True)
        return path

    def archive(self, workdir, archive_dir):
        """Copy the guest's outbox so the report can point at stable paths."""
        source = os.path.join(workdir, OUTBOX)
        target = os.path.join(archive_dir, OUTBOX)
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
            return target
        except (OSError, shutil.Error):
            return source

    def prepare_job(self, mod_path, seconds, mode, target, screenshot, workdir,
                    symbols=None, extra=None):
        """Stage the job directory the guest agent will pick up."""
        inbox = os.path.join(workdir, INBOX)
        for name in os.listdir(inbox):
            path = os.path.join(inbox, name)
            if os.path.isfile(path):
                os.unlink(path)
        staged = os.path.join(inbox, os.path.basename(mod_path))
        shutil.copyfile(mod_path, staged)
        dll = None
        host_binary = None
        notes = []
        missing = builder.missing_toolchain()
        if (extra or {}).get("prebuild", True) and missing:
            notes.append("nothing was cross-compiled for the guest: %s; the guest "
                         "agent will report not_executed unless it can build the "
                         "mod itself" % ", ".join(missing))
        elif (extra or {}).get("prebuild", True):
            # Cross-compile on the host (MinGW produces Windows binaries from
            # Linux) and stage both mod.dll and host.exe, so the guest only has
            # to run them. This keeps the compiler version part of the record and
            # means the VM needs no toolchain at all.
            build = builder.build(mod_path)
            if build["ok"] and build.get("dll") and os.path.exists(build["dll"]):
                dll = os.path.join(inbox, "mod.dll")
                shutil.copyfile(build["dll"], dll)
            else:
                notes.append("the mod did not cross-compile on the host (%d "
                             "error(s)); the guest agent will report not_executed "
                             "unless it can build it itself" % len(build["errors"]))
            try:
                host_exe = builder.build_host()
            except OSError as exc:
                host_exe = None
                notes.append("host.exe could not be built on this machine (%s)" % exc)
            if host_exe and os.path.exists(host_exe):
                host_binary = os.path.join(inbox, "host.exe")
                shutil.copyfile(host_exe, host_binary)
            else:
                notes.append("host.exe was not staged in inbox/: the guest agent "
                             "needs it to run anything, so this job cannot execute "
                             "in the VM until the host can cross-compile it")
        agent = (self.config().get("agent") or
                 os.path.join("vm", "agent", "awt_agent.ps1"))
        agent_src = agent if os.path.isabs(agent) else os.path.join(host_env.REPO,
                                                                    agent)
        agent_staged = None
        if os.path.exists(agent_src):
            agent_staged = os.path.join(inbox, os.path.basename(agent_src))
            shutil.copyfile(agent_src, agent_staged)
        job = {
            "protocol": PROTOCOL_VERSION,
            "job_id": "%s-%d" % (os.path.basename(mod_path).replace(".wh.cpp", "")
                                 or "mod", int(time.time())),
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mod_source": os.path.basename(staged),
            "mod_dll": os.path.basename(dll) if dll else None,
            "agent": os.path.basename(agent_staged) if agent_staged else None,
            "mode": mode,
            "seconds": int(seconds),
            "target": target,
            "screenshot": bool(screenshot),
            "symbols": bool(symbols),
            "timeout": timeouts_of(self.config())["job"],
            "host_exe": os.path.basename(host_binary) if host_binary else None,
            "expected_outputs": [RESULT_FILE, "logs/"],
            "notes": [n for n in notes if n],
        }
        _write_json(os.path.join(workdir, JOB_FILE), job)
        for stale in (HEARTBEAT_FILE, RESULT_FILE):
            path = os.path.join(workdir, OUTBOX, stale)
            if os.path.exists(path):
                os.unlink(path)
        return job

    def command_values(self, workdir, job):
        config = self.config()
        values = dict(config.get("vars") or {})
        values.update({
            "repo": host_env.REPO,
            "shared": config.get("shared_folder"),
            "job_dir": workdir,
            "inbox": os.path.join(workdir, INBOX),
            "outbox": os.path.join(workdir, OUTBOX),
            "job_id": (job or {}).get("job_id", ""),
            "vm_name": config.get("vm_name") or "",
            "hypervisor": config.get("hypervisor") or "",
        })
        return values

    def run_command(self, name, workdir, job=None, timeout=None):
        """(ok, detail) for one configured hypervisor command."""
        commands = self.config().get("commands") or {}
        template = commands.get(name)
        if not template:
            return None, "commands.%s is not configured" % name
        values = self.command_values(workdir, job)
        try:
            command = substitute(template, values)
        except VmConfigError as exc:
            return False, str(exc)
        limit = timeout or timeouts_of(self.config())["command"]
        log_path = os.path.join(workdir, OUTBOX, "logs", "command-%s.log" % name)
        use_shell = isinstance(command, str)
        try:
            proc = subprocess.run(command, shell=use_shell, capture_output=True,
                                  text=True, timeout=limit, cwd=host_env.REPO)
        except subprocess.TimeoutExpired:
            return False, ("commands.%s timed out after %ds: %s" % (name, limit,
                                                                    command))
        except OSError as exc:
            return False, "commands.%s could not be started: %s" % (name, exc)
        detail = {"command": command if use_shell else list(command),
                  "shell": use_shell, "exit_code": proc.returncode,
                  "stdout": (proc.stdout or "")[-2000:],
                  "stderr": (proc.stderr or "")[-2000:]}
        _write_json(log_path, detail)
        if proc.returncode != 0:
            return False, ("commands.%s exited %d: %s"
                           % (name, proc.returncode,
                              (detail["stderr"] or detail["stdout"]).strip()[:300]
                              or "no output"))
        return True, detail

    def wait_for_file(self, path, timeout, poll=2):
        """(found, seconds_waited). Polls the shared folder, generously."""
        started = time.time()
        deadline = started + max(1, int(timeout))
        while True:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True, round(time.time() - started, 1)
            if time.time() >= deadline:
                return False, round(time.time() - started, 1)
            time.sleep(max(0.2, poll))

    # -- the run ------------------------------------------------------------
    def run(self, mod_path, seconds=6, mode="auto", target=None, screenshot=False,
            symbols=None, keep_running=False, revert=True, **kwargs):
        hist = _history_dir()
        try:
            config = self.config()
        except VmConfigError as exc:
            return report_mod.finish(
                self.not_executed_report(mod_path, str(exc),
                                         environment_block(backend=self.name,
                                                           shell_profile="unknown",
                                                           vm_used=None)),
                hist)
        problems = validate_config(config)
        if problems:
            reason = "%s: %s" % (config.get("_path"), "; ".join(problems))
            return report_mod.finish(self.not_executed_report(mod_path, reason), hist)

        timeouts = timeouts_of(config)
        slug = kwargs.get("slug") or os.path.basename(mod_path).replace(".wh.cpp", "")
        try:
            workdir = self.workdir(slug)
        except VmConfigError as exc:
            return report_mod.finish(self.not_executed_report(mod_path, str(exc)), hist)
        archive_dir = self.archive_dir(slug)
        ana = analyzer.analyze(mod_path)
        job = self.prepare_job(mod_path, seconds, mode, target, screenshot, workdir,
                               symbols=symbols, extra=kwargs.get("extra"))
        notes = list(job.get("notes") or [])
        snapshot_restored = None

        if revert:
            ok, detail = self.run_command("revert_snapshot", workdir, job)
            if ok is None:
                snapshot_restored = False
                notes.append("no revert command configured: the VM keeps whatever "
                             "state the previous run left")
            elif ok:
                snapshot_restored = True
            else:
                snapshot_restored = False
                notes.append("snapshot revert failed: %s" % detail)
                return report_mod.finish(self._vm_report(
                    mod_path, ana, job, workdir, notes, snapshot_restored,
                    "could not restore the clean snapshot: %s" % detail,
                    result=None, host_log=None, screenshot_path=None), hist)

        ok, detail = self.run_command("start", workdir, job)
        if ok is not True:
            return report_mod.finish(self._vm_report(
                mod_path, ana, job, workdir, notes, snapshot_restored,
                "could not start the VM: %s" % detail, result=None, host_log=None,
                screenshot_path=None), hist)

        outbox = os.path.join(workdir, OUTBOX)
        heartbeat_path = os.path.join(outbox, HEARTBEAT_FILE)
        found, waited = self.wait_for_file(heartbeat_path, timeouts["boot"],
                                           timeouts["poll"])
        if not found:
            self._stop(keep_running, workdir, job, notes)
            self.archive(workdir, archive_dir)
            return report_mod.finish(self._vm_report(
                mod_path, ana, job, workdir, notes, snapshot_restored,
                "the guest agent never wrote %s within %ds - the VM did not boot, "
                "the shared folder is not mounted, or the agent is not scheduled "
                "to run (waited %.0fs)" % (HEARTBEAT_FILE, timeouts["boot"], waited),
                result=None, host_log=None, screenshot_path=None), hist)
        heartbeat = _read_json(heartbeat_path) or {}
        notes.append("guest agent heartbeat after %.0fs: %s"
                     % (waited, heartbeat.get("agent_version") or "?"))

        result_path = os.path.join(outbox, RESULT_FILE)
        found, waited = self.wait_for_file(result_path, timeouts["job"],
                                           timeouts["poll"])
        result = _read_json(result_path) if found else None
        self._stop(keep_running, workdir, job, notes)
        if not found or result is None:
            self.archive(workdir, archive_dir)
            return report_mod.finish(self._vm_report(
                mod_path, ana, job, workdir, notes, snapshot_restored,
                "the guest agent did not produce %s within %ds (waited %.0fs); the "
                "heartbeat was seen, so the guest is alive but the job did not "
                "complete" % (RESULT_FILE, timeouts["job"], waited),
                result=None, host_log=None, screenshot_path=None), hist)

        archived = self.archive(workdir, archive_dir)
        host_log = _collect_logs(archived, archive_dir)
        shot = None
        if screenshot:
            shot, note = _collect_screenshot(archived, archive_dir)
            notes.append(note)
        return report_mod.finish(self._vm_report(
            mod_path, ana, job, workdir, notes, snapshot_restored, None,
            result=result, host_log=host_log, screenshot_path=shot,
            heartbeat=heartbeat), hist)

    def _stop(self, keep_running, workdir, job, notes):
        if keep_running:
            notes.append("the VM was left running (keep_running)")
            return
        ok, detail = self.run_command("stop", workdir, job)
        if ok is False:
            notes.append("stopping the VM failed: %s" % detail)

    def _vm_report(self, mod_path, ana, job, workdir, notes, snapshot_restored,
                   failure_reason, result=None, host_log=None, screenshot_path=None,
                   heartbeat=None):
        """Assemble the v1 report from what the guest sent back (or didn't)."""
        from .. import metadata as metadata_mod
        environment = self.environment(snapshot_restored=snapshot_restored,
                                       job={"note": None})
        if result and isinstance(result.get("environment"), dict):
            # The guest knows its own Windows better than the host configuration,
            # so its values win - but list fields are merged, never replaced, or
            # the host-side caveats would silently disappear.
            guest_env = result["environment"]
            merged = dict(environment)
            for key, value in guest_env.items():
                if key not in merged or value is None:
                    continue
                if key in ("notes", "caveats") and isinstance(value, list):
                    merged[key] = list(merged.get(key) or []) + [
                        item for item in value if item not in (merged.get(key) or [])]
                else:
                    merged[key] = value
            merged["collected_by"] = guest_env.get("collected_by") or merged[
                "collected_by"]
            environment = merged
        environment["notes"] = [n for n in (environment.get("notes") or []) + notes
                                if n]
        try:
            with open(mod_path, encoding="utf-8", errors="replace") as f:
                meta = metadata_mod.parse_metadata(f.read())
        except OSError:
            meta = {"raw": {}, "include": []}
        build = {"ok": None, "errors": [], "warnings": []}
        if result and isinstance(result.get("build"), dict):
            build = dict(result["build"])
        rep = {
            "schema": 1,
            "mod": {"id": (meta.get("raw") or {}).get("id"),
                    "version": (meta.get("raw") or {}).get("version", "0.0.0"),
                    "include": meta.get("include") or [],
                    "architecture": (meta.get("raw") or {}).get("architecture",
                                                                "x86-64")},
            "build": build,
            "analysis": {key: ana.get(key, []) for key in
                         ("public_hooks", "internal_hooks", "unknown_hooks")},
            "include_analysis": ana.get("include_analysis", []),
            "run": {"backend": self.name, "job_id": job.get("job_id"),
                    "workdir": workdir, "reason": failure_reason},
            "environment": host_env.probe(),
            "shim": {"not_implemented_called": [], "simulated_with_limits": []},
            "verdict": {"level": "not_executed", "verified": [],
                        "not_verifiable_here": [], "requires_real_windows": False,
                        "not_executed_reason": failure_reason,
                        "summary": "level=not_executed; %s" % (failure_reason or
                                                               "no result")},
            "artifacts": {"screenshots": [], "wine_log": None, "dumps": [],
                          "guest_result": _existing(
                              os.path.join(workdir, OUTBOX, RESULT_FILE)) if result
                          else None,
                          "guest_heartbeat": _existing(
                              os.path.join(workdir, OUTBOX, HEARTBEAT_FILE))
                          if heartbeat else None,
                          "guest_job": _existing(os.path.join(workdir, JOB_FILE))},
        }
        if failure_reason:
            apply_environment(rep, environment, backend=self.name)
            return report_mod.attach_symbol_matrix(rep, None) or rep

        run_result = result.get("run") if isinstance(result.get("run"), dict) else {}
        markers = verdict.parse_host_markers(run_result.get("host_output", ""),
                                             run_result.get("mod_log", ""))
        for key in ("load_failed", "init_returned_code", "hooks_applied",
                    "target_exit_code", "crashed"):
            if key in run_result:            # the guest may report these directly
                markers[key] = run_result[key]
        markers["init_returned"] = (markers["init_returned_code"] == 1) \
            if markers["init_returned_code"] is not None else None
        hooks_fired = run_result.get("hooks_fired") or verdict.count_hooks_fired(
            run_result.get("mod_log", ""), ana.get("public_hooks") or [])
        verdict.apply_run_result(
            rep, {"public_hooks": ana.get("public_hooks") or [],
                  "internal_hooks": ana.get("internal_hooks") or [],
                  "internal_modules": ana.get("internal_modules") or []},
            markers, hooks_fired,
            exit_code=run_result.get("exit_code"),
            timed_out=bool(run_result.get("timed_out")),
            run_log=host_log, screenshot=screenshot_path,
            stub_log=run_result.get("stub_log", ""),
            mode=job.get("mode"), target=run_result.get("target") or job.get("target"))
        rep["run"]["log"] = [line.rstrip("\r\n") for line in
                             (run_result.get("mod_log") or "").splitlines()][:200]
        rep["run"]["backend"] = self.name
        if isinstance(result.get("verdict"), dict):
            # the guest's own claims are recorded, never merged into ours
            rep["run"]["guest_verdict"] = result["verdict"]
        apply_environment(rep, environment, backend=self.name)
        return report_mod.attach_symbol_matrix(rep, None) or rep


def _existing(path):
    """The path when the file is really there, else None (never a dangling ref)."""
    return path if path and os.path.exists(path) else None


def _write_json(path, payload):
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)          # atomic: the other side never reads a half file


def _read_json(path, attempts=3, delay=0.4):
    """Tolerant read: the writer may be mid-rename on a network/shared folder."""
    for attempt in range(attempts):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            if attempt == attempts - 1:
                return None
            time.sleep(delay)
    return None


def _collect_logs(outbox, workdir):
    """Merge the guest's logs into one file the report can point at."""
    source = os.path.join(outbox, "logs")
    merged = os.path.join(workdir, "guest-run.log")
    chunks = []
    if os.path.isdir(source):
        for name in sorted(os.listdir(source)):
            path = os.path.join(source, name)
            if not os.path.isfile(path) or not name.endswith(".log"):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    chunks.append("===== %s =====\n%s" % (name, f.read()))
            except OSError:
                continue
    if not chunks:
        return None
    try:
        with open(merged, "w", encoding="utf-8") as f:
            f.write("\n".join(chunks))
        return merged
    except OSError:                                          # pragma: no cover
        return None


MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "bmp": b"BM", "jpg": b"\xff\xd8"}


def _collect_screenshot(outbox, workdir):
    """(path or None, note). A missing or truncated capture is never presented."""
    for name in ("screenshot.png", "screenshot.bmp", "screenshot.jpg"):
        path = os.path.join(outbox, name)
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size == 0:
                return None, ("the guest wrote %s but it is empty: treated as "
                              "unavailable, not as a screenshot" % name)
            with open(path, "rb") as f:
                magic = f.read(8)
            expected = MAGIC.get(name[-3:])
            if expected and not magic.startswith(expected):
                return None, ("%s does not start with the %s signature (got %r): "
                              "the capture is corrupt and is reported as "
                              "unavailable, never as a screenshot"
                              % (name, name[-3:].upper(), magic[:4]))
            kept = os.path.join(workdir, name)
            if os.path.abspath(path) != os.path.abspath(kept):
                shutil.copyfile(path, kept)
            note = "guest screenshot %s (%d bytes)" % (name, size)
            if size < 1024:
                note += ("; unusually small for a desktop capture - check that the "
                         "guest really had a shell to draw")
            return kept, note
    return None, ("no screenshot was produced by the guest (expected %s in the "
                  "shared folder)" % "screenshot.png")
