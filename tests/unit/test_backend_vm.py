"""Tests for the self-hosted VM backend and its shared-folder protocol.

No VM is involved: `tests/unit/fixtures/fake_vm.py` plays the hypervisor *and*
the in-guest agent, including the ways a real VM fails (never boots, boots but
never finishes the job, crashes, writes a truncated screenshot). What is being
tested is therefore the protocol and the honesty of the report - which is the part
this repository owns. The part it does not own (a real Windows installation) is
covered by the user's own configuration and documented in docs/vm-protocol.md.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import SkipTest, require            # noqa: E402
from awt import builder                                     # noqa: E402
from awt.backends import base, cli, vm                      # noqa: E402

EXAMPLE = os.path.join(REPO, "examples", "hello-log.wh.cpp")
FAKE_VM = os.path.join(REPO, "tests", "unit", "fixtures", "fake_vm.py")
AGENT = os.path.join(REPO, "vm", "agent", "awt_agent.ps1")
TEMPLATE = os.path.join(REPO, "configs", "vm.example.yaml")


def default_commands(shared, log, behaviour):
    """Argument lists, not shell strings: the same test runs on a Windows host.

    A real configuration may use either form (`docs/vm-protocol.md`), and
    `vm.is_command` accepts both.
    """
    return {
        "revert_snapshot": [sys.executable, FAKE_VM, "--mark", "reverted",
                            "--mark-file", log],
        "start": [sys.executable, FAKE_VM, "--share", shared, "--behaviour",
                  behaviour, "--detach", "--wait", "8"],
        "stop": [sys.executable, FAKE_VM, "--mark", "stopped", "--mark-file", log],
    }


def write_config(behaviour="ok", boot=25, job=25, poll=1, guest=None,
                 extra_lines=(), commands=None, commands_fn=None):
    """A VM configuration whose hypervisor and guest are the fake fixture.

    `commands_fn(shared, log)` builds the command lines once the temporary share
    is known, which is what the failure tests need (a start command that must not
    run, a revert command that must fail).
    """
    tmp = tempfile.mkdtemp(prefix="awt-vm-")
    shared = os.path.join(tmp, "share")
    os.makedirs(os.path.join(shared, "outbox", "logs"), exist_ok=True)
    log = os.path.join(shared, "outbox", "logs", "commands.log")
    guest = guest or {"label": "Fake Windows 11 21H2", "build_number": 22000,
                      "product_type": "client", "arch": "x86-64"}
    cmds = commands_fn(shared, log) if commands_fn else (
        commands or default_commands(shared, log, behaviour))
    require_lists = all(isinstance(value, list) for value in cmds.values())
    lines = ["schema: 1", "vm_name: fake-" + behaviour, "hypervisor: qemu",
             "shared_folder: " + shared, "guest_workdir: 'S:\\awt'",
             "agent: vm/agent/awt_agent.ps1", "commands:"]
    for name in ("revert_snapshot", "start", "stop", "status"):
        if cmds.get(name) is not None:
            value = cmds[name]
            if isinstance(value, list):
                # yamlmini flow sequence: [a, b, c] - one entry per argument
                lines.append("  %s: [%s]" % (name, ", ".join(
                    "'%s'" % part.replace("\\", "\\\\").replace("'", "''")
                    for part in value)))
            else:
                lines.append("  %s: %s" % (name, value if value.startswith("'")
                                           else "'" + value + "'"))
    lines += list(extra_lines)
    lines += ["guest:"]
    for key, value in guest.items():
        lines.append("  %s: %s" % (key, value))
    lines += ["timeouts:", "  boot: %d" % boot, "  job: %d" % job,
              "  poll: %d" % poll, "  command: 30"]
    path = os.path.join(tmp, "vm.local.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    backend = vm.VmBackend(config_path=path, workdir=shared)
    return backend, {"tmp": tmp, "shared": shared, "config": path, "log": log}


def read_commands_log(info):
    try:
        with open(info["log"], encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def run_quiet(backend, **kwargs):
    kwargs.setdefault("seconds", 1)
    kwargs.setdefault("slug", "unit-test")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rep = backend.run(EXAMPLE, **kwargs)
    return rep


def test_missing_configuration_points_at_the_template():
    old = os.environ.pop("AWT_VM_CONFIG", None)
    try:
        require(vm.find_config(os.path.join(REPO, "configs", "vm.local.yaml"))
                == os.path.join(REPO, "configs", "vm.local.yaml"),
                "an explicit path must win")
        try:
            vm.load_config("/nonexistent/vm.local.yaml")
        except vm.VmConfigError as exc:
            require("vm.example.yaml" in str(exc),
                    "the error must tell the user which template to copy: %s" % exc)
            require("git-ignored" in str(exc) or "AWT_VM_CONFIG" in str(exc),
                    "the error must mention the local configuration route")
            return
        raise AssertionError("a missing configuration must raise VmConfigError")
    finally:
        if old:
            os.environ["AWT_VM_CONFIG"] = old


def test_configuration_discovery_order():
    backend, info = write_config()
    old = os.environ.get("AWT_VM_CONFIG")
    os.environ["AWT_VM_CONFIG"] = info["config"]
    try:
        require(vm.find_config() == info["config"],
                "$AWT_VM_CONFIG must be honoured")
        require(vm.find_config(TEMPLATE) == TEMPLATE,
                "an explicit path must beat the environment")
        loaded = vm.load_config()
        require(loaded["vm_name"].startswith("fake-"), "wrong file loaded")
    finally:
        if old is None:
            os.environ.pop("AWT_VM_CONFIG", None)
        else:
            os.environ["AWT_VM_CONFIG"] = old
    shutil.rmtree(info["tmp"], ignore_errors=True)


def test_committed_template_parses_and_stays_a_template():
    require(os.path.exists(TEMPLATE), "configs/vm.example.yaml must be committed")
    config = vm.load_config(TEMPLATE)
    require(vm.validate_config(config) == [],
            "the committed template must validate: %r" % vm.validate_config(config))
    require("/absolute/host/path" in str(config["vars"]["disk"]),
            "the template must not contain a real disk path")
    require(config["timeouts"]["boot"] >= 600,
            "boot timeouts must stay generous in the template")
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        ignore = f.read()
    require("*.local.yaml" in ignore,
            "configs/vm.local.yaml must be git-ignored by pattern")


def test_validate_config_reports_every_problem():
    problems = vm.validate_config({})
    joined = "; ".join(problems)
    for needed in ("commands", "start", "stop", "shared_folder", "guest",
                   "hypervisor"):
        require(needed in joined, "validate_config did not report %r: %s"
                % (needed, joined))
    problems = vm.validate_config({"hypervisor": "qemu", "guest": {"label": "x"},
                                   "commands": {"start": "true", "stop": "true"},
                                   "shared_folder": "relative/path"})
    require(len(problems) == 1 and "absolute" in problems[0],
            "a relative shared folder must be rejected: %r" % problems)
    good = {"hypervisor": "qemu", "guest": {"label": "x"},
            "commands": {"start": "true", "stop": "true"},
            "shared_folder": os.path.sep + "tmp"}
    require(vm.validate_config(good) == [], "a good config was rejected")


def test_commands_may_be_a_string_or_an_argument_list():
    require(vm.is_command("virsh start win11"), "a shell string is a command")
    require(vm.is_command(["virsh", "start", "win11"]), "an argument list is a command")
    require(not vm.is_command(""), "an empty string is not a command")
    require(not vm.is_command("   "), "a blank string is not a command")
    require(not vm.is_command([]), "an empty list is not a command")
    require(not vm.is_command(["virsh", 3]), "a non-string argument is not a command")
    require(not vm.is_command(None), "None is not a command")
    require(vm.substitute(["{vm_name}", "--share", "{shared}"],
                          {"vm_name": "w11", "shared": "/mnt"})
            == ["w11", "--share", "/mnt"], "placeholders in a list must be filled")
    problems = vm.validate_config({"hypervisor": "qemu", "guest": {"label": "x"},
                                   "shared_folder": os.path.sep + "tmp",
                                   "commands": {"start": ["qemu"], "stop": ""}})
    require(len(problems) == 1 and "commands.stop" in problems[0],
            "a blank command must be reported: %r" % problems)


def test_substitute_rejects_typos():
    values = {"shared": "/mnt/share", "vm_name": "win11"}
    require(vm.substitute("virsh start {vm_name}", values) == "virsh start win11",
            "placeholder substitution broke")
    try:
        vm.substitute("qemu -drive file={diks}", values)
    except vm.VmConfigError as exc:
        require("diks" in str(exc) and "vars:" in str(exc),
                "a typo must be reported with a fix hint: %s" % exc)
        return
    raise AssertionError("a typo'd placeholder must not become an empty string")


def test_timeouts_are_generous_and_overridable():
    require(vm.DEFAULT_TIMEOUTS["boot"] >= 600,
            "the default boot timeout must be generous")
    require(vm.timeouts_of({})["boot"] == vm.DEFAULT_TIMEOUTS["boot"],
            "defaults changed")
    got = vm.timeouts_of({"timeouts": {"boot": 42, "poll": "not-a-number"}})
    require(got["boot"] == 42, "a configured timeout was ignored")
    require(got["poll"] == vm.DEFAULT_TIMEOUTS["poll"],
            "an unparsable timeout must fall back to the default, not crash")


def test_environment_describes_the_configured_guest():
    backend, info = write_config()
    try:
        env = backend.environment()
        require(env["backend"] == "self-hosted-vm", "backend name")
        require(env["vm_used"] is True, "a VM backend must say a VM was used")
        require(env["build_number"] == 22000, "build number from the configuration")
        require(env["product_type"] == "client", "product type")
        require(env["shell_profile"] == "client", "a client guest is a client shell")
        require(base.indicative_notes(env) == [],
                "a real client Windows guest needs no caveat")
        require(env["virtualization"] == "qemu", "hypervisor recorded")
        ok, reason = backend.available()
        require(ok is True and "fake-" in reason, "availability reason: %r" % reason)
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_server_guest_and_missing_revert_are_flagged():
    backend, info = write_config(guest={"label": "Fake Windows Server 2022",
                                        "build_number": 20348,
                                        "product_type": "server"},
                                 commands={"start": [sys.executable, "-c", "pass"],
                                           "stop": [sys.executable, "-c", "pass"]})
    try:
        env = backend.environment()
        require(env["shell_profile"] == "server", "a server guest must read as server")
        notes = base.indicative_notes(env)
        require(notes and "INDICATIVE" in notes[0], "server guests are indicative")
        require(any("revert_snapshot" in caveat for caveat in env["caveats"]),
                "a configuration with no revert command must be a caveat: %r"
                % env["caveats"])
        require(any("Windows Server" in caveat for caveat in env["caveats"]),
                "a server guest must be a caveat too: %r" % env["caveats"])
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_prepare_job_stages_the_mod_and_records_the_toolchain_gap():
    backend, info = write_config()
    try:
        workdir = backend.workdir("staging")
        job = backend.prepare_job(EXAMPLE, 3, "auto", None, True, workdir)
        require(os.path.exists(os.path.join(workdir, "job.json")),
                "job.json was not written")
        require(os.path.exists(os.path.join(workdir, vm.INBOX,
                                            os.path.basename(EXAMPLE))),
                "the mod source was not staged into inbox/")
        require(job["protocol"] == vm.PROTOCOL_VERSION, "protocol version")
        require(job["seconds"] == 3 and job["screenshot"] is True, "job fields")
        require(os.path.exists(os.path.join(workdir, vm.INBOX, "awt_agent.ps1")),
                "the guest agent was not staged (the guest must run our version)")
        if builder.missing_toolchain():
            require(job["host_exe"] is None,
                    "host.exe cannot be staged without a toolchain")
            require(any("host.exe" in note or "cross-compiled" in note
                        for note in job["notes"]),
                    "the missing toolchain must be reported, not hidden: %r"
                    % job["notes"])
        # a stale heartbeat/result must never survive into the next job
        stale = os.path.join(workdir, vm.OUTBOX, vm.RESULT_FILE)
        with open(stale, "w", encoding="utf-8") as f:
            f.write("{}")
        backend.prepare_job(EXAMPLE, 3, "auto", None, False, workdir)
        require(not os.path.exists(stale), "a stale result.json was left behind")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_full_run_happy_path():
    backend, info = write_config("ok")
    try:
        rep = run_quiet(backend, screenshot=True)
        require(rep["verdict"]["level"] == "public_hooks_verified",
                "a successful guest run must reach the strongest level, got %r: %s"
                % (rep["verdict"]["level"], rep["verdict"]["summary"]))
        require(rep["run"]["backend"] == "self-hosted-vm", "backend recorded")
        require(rep["run"]["hooks_fired"] == {"DrawTextW": 2, "MessageBoxW": 1},
                "hooks fired by the guest were lost: %r" % rep["run"]["hooks_fired"])
        require(rep["run"]["init_returned"] is True, "init flag lost")
        require(rep["run"]["crashed"] is False, "crash invented")
        require(rep["run"]["exit_code"] == 0, "exit code lost")
        require(rep["run"]["log"] and "DrawTextW" in "\n".join(rep["run"]["log"]),
                "the guest's mod log was not carried into the report")

        env = rep["run"]["environment"]
        require(env["vm_used"] is True and env["snapshot_restored"] is True,
                "the snapshot restore must be recorded: %r" % env)
        require(env["build_number"] == 22000 and env["product_type"] == "client",
                "the guest's own environment must win: %r" % env)
        require(env["hostname"] == "FAKE-GUEST",
                "guest-reported values were dropped: %r" % env)
        require(env["collected_by"].startswith("fake-awt-vm-agent"),
                "the report must say who collected the environment: %r"
                % env["collected_by"])
        require(rep["verdict"]["indicative"] == [],
                "a client Windows VM run needs no indicative caveat: %r"
                % rep["verdict"]["indicative"])

        shots = rep["artifacts"]["screenshots"]
        require(len(shots) == 1 and os.path.exists(shots[0]),
                "the guest screenshot was not collected: %r" % shots)
        with open(shots[0], "rb") as f:
            require(f.read(8) == b"\x89PNG\r\n\x1a\n", "not a PNG")
        log = rep["artifacts"]["wine_log"]
        require(log and os.path.exists(log), "the merged guest log is missing")
        with open(log, encoding="utf-8") as f:
            merged = f.read()
        for name in ("host-output.log", "mod.log", "stub.log"):
            require(name in merged, "%s was not merged into the run log" % name)
        require(rep["shim"]["not_implemented_called"] == ["Wh_FindFirstSymbol"],
                "the guest's stub log was ignored")
        require(os.path.exists(rep["artifacts"]["guest_result"]),
                "the archived result.json path is dangling")
        commands = read_commands_log(info)
        require("reverted" in commands and "stopped" in commands,
                "the VM was not reverted and stopped: %r" % commands)
        for key in ("schema", "mod", "build", "analysis", "run", "verdict",
                    "artifacts", "environment"):
            require(key in rep, "v1 key %r missing" % key)
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_vm_that_never_boots_is_not_executed():
    backend, info = write_config("silent", boot=2, job=3, poll=1)
    try:
        started = time.time()
        rep = run_quiet(backend)
        require(rep["verdict"]["level"] == "not_executed",
                "a VM that never answered must not produce a verdict, got %r"
                % rep["verdict"]["level"])
        reason = rep["verdict"]["not_executed_reason"]
        require("heartbeat.json" in reason and "2s" in reason,
                "the reason must name the file and the timeout: %r" % reason)
        require(rep["verdict"]["verified"] == [], "nothing may be verified")
        require(rep["run"]["environment"]["snapshot_restored"] is True,
                "the revert did happen and must be recorded")
        require("stopped" in read_commands_log(info),
                "the VM must be stopped even when it never answered")
        require(time.time() - started < 25, "the boot timeout was not honoured")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_guest_that_never_finishes_the_job_is_not_executed():
    backend, info = write_config("no-result", boot=10, job=2, poll=1)
    try:
        rep = run_quiet(backend)
        require(rep["verdict"]["level"] == "not_executed",
                "no result.json means no verdict")
        reason = rep["verdict"]["not_executed_reason"]
        require("result.json" in reason and "heartbeat was seen" in reason,
                "the reason must distinguish 'alive but stuck': %r" % reason)
        require(any("heartbeat after" in note for note in
                    rep["run"]["environment"]["notes"]),
                "the heartbeat should be recorded in the notes: %r"
                % rep["run"]["environment"]["notes"])
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_snapshot_revert_failure_prevents_starting_the_vm():
    def commands(shared, log):
        return {"revert_snapshot": [sys.executable, "-c", "raise SystemExit(3)"],
                "start": [sys.executable, FAKE_VM, "--share", shared,
                          "--behaviour", "ok", "--detach"],
                "stop": [sys.executable, FAKE_VM, "--mark", "stopped",
                         "--mark-file", log]}

    backend, info = write_config("ok", commands_fn=commands)
    try:
        rep = run_quiet(backend)
        require(rep["verdict"]["level"] == "not_executed",
                "without a clean snapshot nothing may be claimed")
        require("snapshot" in rep["verdict"]["not_executed_reason"],
                "the reason must name the snapshot: %r"
                % rep["verdict"]["not_executed_reason"])
        require(rep["run"]["environment"]["snapshot_restored"] is False,
                "the failed revert must be recorded as False, not None")
        require(not os.path.exists(os.path.join(info["shared"], vm.OUTBOX,
                                                vm.HEARTBEAT_FILE)),
                "the VM must not have been started after a failed revert")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_guest_crash_is_reported_as_crashed():
    backend, info = write_config("crash")
    try:
        rep = run_quiet(backend)
        require(rep["verdict"]["level"] == "crashed",
                "a guest crash must be reported as crashed, got %r"
                % rep["verdict"]["level"])
        require(rep["run"]["crashed"] is True, "run.crashed not set")
        require(rep["run"]["exit_code"] == -1073741819,
                "the crash exit code was lost")
        require(rep["verdict"]["verified"] == ["build", "load"],
                "a crash verifies nothing about hooks: %r" % rep["verdict"]["verified"])
        log_path = rep["artifacts"]["wine_log"]
        require(log_path and os.path.exists(log_path),
                "the collected guest log is missing")
        with open(log_path, encoding="utf-8") as f:
            require("unhandled page fault" in f.read(),
                    "the crash evidence must be in the collected log")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_server_guest_marks_the_run_indicative():
    backend, info = write_config("server-guest")
    try:
        rep = run_quiet(backend)
        env = rep["run"]["environment"]
        require(env["product_type"] == "server" and env["build_number"] == 20348,
                "the guest reported a Server edition: %r" % env)
        require(env["shell_profile"] == "server", "shell profile from the guest")
        require(rep["verdict"]["indicative"],
                "a Windows Server guest must carry the indicative caveat")
        require(any("Windows Server" in note and "INDICATIVE" in note
                    for note in rep["verdict"]["indicative"]),
                "the caveat must name Server: %r" % rep["verdict"]["indicative"])
        require(rep["verdict"]["level"] == "public_hooks_verified",
                "indicative is a caveat, not a downgrade of what ran")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_truncated_screenshot_is_reported_as_unavailable():
    backend, info = write_config("bad-screenshot")
    try:
        rep = run_quiet(backend, screenshot=True)
        require(rep["artifacts"]["screenshots"] == [],
                "a 9-byte file is not a screenshot and must not be listed")
        notes = rep["run"]["environment"]["notes"]
        require(any("unavailable" in note or "corrupt" in note for note in notes),
                "the unusable capture must be explained: %r" % notes)
        require(rep["verdict"]["level"] == "public_hooks_verified",
                "a missing screenshot does not invalidate the run itself")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_no_revert_command_is_reported_not_assumed():
    def commands(shared, log):
        return {"start": [sys.executable, FAKE_VM, "--share", shared,
                          "--behaviour", "ok", "--detach"],
                "stop": [sys.executable, FAKE_VM, "--mark", "stopped",
                         "--mark-file", log]}

    backend, info = write_config("ok", commands_fn=commands)
    try:
        rep = run_quiet(backend)
        require(rep["run"]["environment"]["snapshot_restored"] is False,
                "with no revert command nothing was restored")
        require(any("NOT rolled back" in caveat for caveat in
                    rep["run"]["environment"]["caveats"]),
                "the missing rollback must be a caveat: %r"
                % rep["run"]["environment"]["caveats"])
        require(rep["verdict"]["indicative"],
                "an unrolled-back VM qualifies the outcome")
    finally:
        shutil.rmtree(info["tmp"], ignore_errors=True)


def test_json_helpers_are_atomic_and_tolerant():
    tmp = tempfile.mkdtemp(prefix="awt-json-")
    path = os.path.join(tmp, "result.json")
    try:
        vm._write_json(path, {"protocol": 1, "status": "completed"})
        require(not os.path.exists(path + ".tmp"), "the temporary file was left behind")
        require(vm._read_json(path)["status"] == "completed", "round trip failed")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"protocol": 1, "stat')          # truncated mid-write
        require(vm._read_json(path, attempts=2, delay=0.01) is None,
                "a truncated JSON file must read as None, never as a partial dict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wait_for_file_honours_its_timeout():
    tmp = tempfile.mkdtemp(prefix="awt-wait-")
    try:
        found, waited = vm.VmBackend(TEMPLATE).wait_for_file(
            os.path.join(tmp, "never.json"), 1, poll=1)
        require(found is False and 0.9 <= waited < 5,
                "wait_for_file did not respect the timeout: %r after %.1fs"
                % (found, waited))
        path = os.path.join(tmp, "there.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        found, waited = vm.VmBackend(TEMPLATE).wait_for_file(path, 5, poll=1)
        require(found is True, "an existing file was not found")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_dispatches_to_the_vm_backend():
    backend, info = write_config("ok")
    old = os.environ.get("AWT_VM_CONFIG")
    os.environ["AWT_VM_CONFIG"] = info["config"]
    tmp = tempfile.mkdtemp(prefix="awt-cli-")
    out = os.path.join(tmp, "report.json")
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--backend", "self-hosted-vm", "--mod", EXAMPLE,
                             "--seconds", "1", "--json", out])
        require(code == 0, "a successful VM run must exit 0, got %d" % code)
        require(os.path.exists(out), "--json did not write the report")
        with open(out, encoding="utf-8") as f:
            saved = json.load(f)
        require(saved["run"]["backend"] == "self-hosted-vm", "report backend")
        require(saved["run"]["environment"]["vm_used"] is True, "report environment")
    finally:
        if old is None:
            os.environ.pop("AWT_VM_CONFIG", None)
        else:
            os.environ["AWT_VM_CONFIG"] = old
        shutil.rmtree(info["tmp"], ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_lists_backends_as_json():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["--list-json"])
    require(code == 0, "listing must succeed anywhere")
    doc = json.loads(buffer.getvalue())
    require(doc["kind"] == "backend_capabilities", "document kind")
    names = [entry["name"] for entry in doc["backends"]]
    require(names == ["wine", "windows-runner", "self-hosted-vm"],
            "backend list: %r" % names)


def test_protocol_matches_the_committed_agent_and_docs():
    require(os.path.exists(AGENT), "vm/agent/awt_agent.ps1 must be committed")
    with open(AGENT, encoding="utf-8") as f:
        agent = f.read()
    for name in ("job.json", "heartbeat.json", "result.json", "screenshot.png",
                 "outbox", "inbox"):
        require(name in agent, "the agent does not mention %r" % name)
    require(vm.PROTOCOL_VERSION == 1, "protocol version changed")
    require("$Share" in agent and "ConvertTo-Json" in agent,
            "the agent must be plain PowerShell 5.1 with no dependencies")
    for forbidden in ("password", "Invoke-WebRequest", "DownloadString",
                      "curl", "token"):
        require(forbidden not in agent.lower(),
                "the agent must not fetch anything or handle credentials: %r"
                % forbidden)
    doc = os.path.join(REPO, "docs", "vm-protocol.md")
    if os.path.exists(doc):
        with open(doc, encoding="utf-8") as f:
            text = f.read()
        for name in ("job.json", "heartbeat.json", "result.json"):
            require(name in text, "docs/vm-protocol.md does not document %r" % name)
