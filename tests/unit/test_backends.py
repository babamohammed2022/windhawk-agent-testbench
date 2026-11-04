"""Tests for the backend contract and the shared marker/verdict logic.

Every backend must produce the same v1 report shape and must never claim more
than the machine it ran on can prove. These tests run on a Linux machine with no
Wine, no MinGW and no Windows, which is exactly the point: the honesty rules are
checkable without any of them.
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import SkipTest, require            # noqa: E402
from awt import runner, verdict                             # noqa: E402
from awt import backends                                    # noqa: E402
from awt.backends import base                               # noqa: E402

EXAMPLE = os.path.join(REPO, "examples", "hello-log.wh.cpp")


def _fake_report(level="loads_no_crash"):
    return {
        "schema": 1,
        "mod": {"id": "test", "version": "1.0", "include": ["*"],
                "architecture": "x86-64"},
        "build": {"ok": True, "errors": [], "warnings": []},
        "analysis": {"public_hooks": [], "internal_hooks": [], "unknown_hooks": []},
        "include_analysis": [],
        "run": {"backend": "wine"},
        "environment": {},
        "shim": {"not_implemented_called": [], "simulated_with_limits": []},
        "verdict": {"level": level, "verified": ["build", "load"],
                    "not_verifiable_here": ["internal-symbols: ?X@@YAXXZ"],
                    "requires_real_windows": True, "summary": "level=%s" % level},
        "artifacts": {"screenshots": [], "wine_log": None, "dumps": []},
    }


def test_environment_block_is_complete_with_defaults():
    env = base.environment_block(backend="wine")
    for key in base.ENVIRONMENT_KEYS:
        require(key in env, "environment block is missing %r" % key)
    require(env["notes"] == [], "notes must default to an empty list")
    require(isinstance(env["collected_at"], str) and env["collected_at"],
            "collected_at must be filled in")
    require(env["collected_by"] == "awt.backends", "collected_by default changed")
    require(env["shell_profile"] is None, "shell_profile is a guess unless given")


def test_environment_block_rejects_unknown_fields():
    try:
        base.environment_block(backend="wine", shell_profil="client")
    except KeyError as exc:
        require("shell_profil" in str(exc), "the error should name the bad field")
        return
    raise AssertionError("a typo'd environment field must not be accepted silently")


def test_shell_profile_follows_product_type():
    cases = [(1, "client"), (3, "server"), (2, "server"),
             ("client", "client"), ("server", "server"), (None, "unknown")]
    for product_type, expected in cases:
        got = base.shell_profile(os_name="Windows", product_type=product_type,
                                 build_number=19045)
        require(got == expected, "product_type %r -> %r, expected %r"
                % (product_type, got, expected))


def test_shell_profile_uses_server_build_numbers_as_a_fallback():
    for build in (20348, 17763, 14393):
        got = base.shell_profile(os_name="Windows", build_number=build)
        require(got == "server", "build %d should read as a server shell, got %r"
                % (build, got))
    got = base.shell_profile(os_name="Windows", build_number=19045)
    require(got == "unknown", "a client-only build must not be guessed as client")


def test_shell_profile_refuses_to_decide_build_26100():
    """26100 is both Windows 11 24H2 and Windows Server 2025."""
    got = base.shell_profile(os_name="Windows", build_number=26100)
    require(got == "unknown", "build 26100 is ambiguous and must stay unknown")


def test_shell_profile_wine_and_non_windows():
    require(base.shell_profile(wine=True) == "wine", "wine flag must win")
    require(base.shell_profile(os_name="Linux", product_type=1) == "unknown",
            "a non-Windows OS is not a client Windows shell")


def test_indicative_notes_per_profile():
    server = base.environment_block(backend="windows-runner", shell_profile="server",
                                    build_number=20348)
    notes = base.indicative_notes(server)
    require(len(notes) == 1 and "INDICATIVE" in notes[0] and "20348" in notes[0],
            "a server shell must be flagged indicative with its build: %r" % notes)
    wine = base.environment_block(backend="wine", shell_profile="wine")
    notes = base.indicative_notes(wine)
    require(len(notes) == 1 and "SIMULATED" in notes[0],
            "Wine is simulated, not indicative: %r" % notes)
    client = base.environment_block(backend="self-hosted-vm", shell_profile="client")
    require(base.indicative_notes(client) == [],
            "a real client Windows shell needs no caveat")
    unknown = base.environment_block(backend="x", shell_profile="unknown")
    require("could not be identified" in base.indicative_notes(unknown)[0],
            "an unidentified environment must say so")


def test_apply_environment_never_touches_the_verdict():
    rep = _fake_report()
    before = json.dumps(rep["verdict"], sort_keys=True)
    env = base.environment_block(backend="windows-runner", shell_profile="server",
                                 build_number=20348)
    base.apply_environment(rep, env, backend="windows-runner")
    after = dict(rep["verdict"])
    after.pop("indicative")
    require(json.dumps(after, sort_keys=True) == before,
            "apply_environment changed the verdict: %r" % after)
    require(rep["run"]["backend"] == "windows-runner", "backend not recorded")
    require(rep["run"]["environment"]["shell_profile"] == "server",
            "environment not attached")
    require(len(rep["verdict"]["indicative"]) == 1, "indicative note missing")
    # idempotent: attaching twice must not duplicate notes
    base.apply_environment(rep, env, backend="windows-runner")
    require(len(rep["verdict"]["indicative"]) == 1, "notes duplicated")


def test_apply_environment_keeps_an_unavailable_backend_label():
    rep = _fake_report("not_executed")
    rep["run"]["backend"] = "unavailable"
    env = base.environment_block(backend="wine", shell_profile="wine")
    base.apply_environment(rep, env)          # no explicit backend
    require(rep["run"]["backend"] == "unavailable",
            "a run that could not happen must not be relabelled as wine")


def test_registry_covers_the_three_backends():
    require(backends.names() == ["wine", "windows-runner", "self-hosted-vm"],
            "backend list changed: %r" % backends.names())
    for name in backends.names():
        backend = backends.get(name)
        require(backend.name == name, "%s reports itself as %s" % (name, backend.name))
        require(isinstance(backend, base.Backend), "%s is not a Backend" % name)
    try:
        backends.get("dosbox")
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown backend must raise KeyError")


def test_select_explains_every_backend_it_tried():
    if os.name == "nt":
        raise SkipTest("this test asserts that nothing is available on Linux")
    backend, reason = backends.select()
    require(backend is None, "some backend claimed to be available: %r" % reason)
    for name in backends.names():
        require(name in reason, "select() did not explain %s: %s" % (name, reason))
    preferred, reason = backends.select(preferred="windows-runner")
    require(preferred is None and "Windows" in reason,
            "the windows-runner backend must refuse to run off Windows")


def test_describe_all_shape():
    entries = backends.describe_all()
    require(len(entries) == 3, "expected one entry per backend")
    for entry in entries:
        for key in ("name", "label", "executes_on", "available", "reason",
                    "environment", "limitations"):
            require(key in entry, "%s is missing %r" % (entry.get("name"), key))
        require(isinstance(entry["reason"], str) and entry["reason"],
                "%s gave no reason" % entry["name"])
        require(isinstance(entry["limitations"], list) and entry["limitations"],
                "%s documents no limitations" % entry["name"])
        require(entry["environment"]["backend"] == entry["name"],
                "%s environment/backend mismatch" % entry["name"])


def test_wine_backend_says_why_it_cannot_run_here():
    backend = backends.get("wine")
    ok, reason = backend.available()
    if os.name == "nt":
        raise SkipTest("wine availability differs on Windows")
    require(ok is False, "wine reported itself available without wine installed")
    require("wine" in reason or "toolchain" in reason or "Xvfb" in reason,
            "the reason should name what is missing: %r" % reason)
    env = backend.environment()
    require(env["shell_profile"] == "wine", "wine environment profile changed")
    require(env["vm_used"] is None, "Wine is not a VM and must not claim to be one")


def test_verdict_parses_host_markers():
    out = ("[awt-host] init returned 1\n[awt-host] hooks applied count 2\n"
           "[awt-host] target exit code 0\n")
    markers = verdict.parse_host_markers(out, "DrawTextW: called\n")
    require(markers["init_returned_code"] == 1, "init code not parsed")
    require(markers["init_returned"] is True, "init_returned not derived")
    require(markers["hooks_applied"] == 2, "hooks count not parsed")
    require(markers["target_exit_code"] == 0, "target exit code not parsed")
    require(markers["crashed"] is False, "a clean run reads as crashed")
    require(markers["load_failed"] is False, "load failure invented")


def test_verdict_load_failure_is_detected():
    markers = verdict.parse_host_markers("[awt-host] load failed err=126\n")
    require(markers["load_failed"] is True and markers["load_error"] == 126,
            "load failure not parsed: %r" % markers)
    require(markers["init_returned"] is None, "no init claim without a marker")


def test_verdict_falls_back_to_the_mod_log():
    """The host markers may be missing when only the mod's own log survives."""
    markers = verdict.parse_host_markers("", "autoinit returned 1\nhooks applied 3\n")
    require(markers["init_returned_code"] == 1, "autoinit fallback broken")
    require(markers["hooks_applied"] == 3, "mod-log hooks fallback broken")


def test_verdict_zeroes_hooks_when_init_failed():
    markers = verdict.parse_host_markers(
        "[awt-host] init returned 0\n[awt-host] hooks applied count 4\n")
    require(markers["hooks_applied"] == 0,
            "a mod that did not initialise applied no hooks")


def test_verdict_crash_detection():
    for text in ("wine: unhandled page fault at 0x0",
                 "Unhandled exception 0xc0000005",
                 "[awt-host] target exit code 3221225477"):
        markers = verdict.parse_host_markers(text, "")
        require(markers["crashed"] is True, "crash not detected in %r" % text)
    markers = verdict.parse_host_markers("[awt-host] target exit code 1", "")
    require(markers["crashed"] is False, "exit code 1 is a failure, not a crash")


def test_a_mod_log_mentioning_an_error_code_is_not_a_crash():
    """The mod's own text is evidence about the mod, not about the host."""
    markers = verdict.parse_host_markers(
        "[awt-host] init returned 1\n[awt-host] hooks applied count 1\n"
        "[awt-host] target exit code 0\n",
        "DrawTextW: skipping, last error was 0xc0000005\nunhandled exception in a "
        "string literal the mod printed\n")
    require(markers["crashed"] is False,
            "a clean host run must not be reported as crashed because of mod text")
    require(verdict.verdict_level(markers, {"DrawTextW": 1})
            == "public_hooks_verified", "the level was downgraded by mod text")


def test_verdict_ladder_is_unchanged():
    ok = {"load_failed": False, "load_error": None, "init_returned_code": 1,
          "init_returned": True, "hooks_applied": 1, "target_exit_code": 0,
          "crashed": False}
    require(verdict.verdict_level(ok, {"DrawTextW": 1}) == "public_hooks_verified",
            "the strongest level changed")
    require(verdict.verdict_level(ok, {}) == "loads_no_crash",
            "loading without a hook firing is not verification")
    crashed = dict(ok, crashed=True)
    require(verdict.verdict_level(crashed, {"DrawTextW": 1}) == "crashed",
            "a crash must outrank hooks that fired")
    for code in (0, 2, 3, 5, 6, 7):
        require(verdict.verdict_level(dict(ok, init_returned_code=code), {})
                == "load_failed", "init code %d should be load_failed" % code)
    require(verdict.verdict_level(dict(ok, load_failed=True), {}) == "load_failed",
            "load_failed must win")


def test_count_hooks_fired_uses_the_mod_log():
    fired = verdict.count_hooks_fired("DrawTextW: a\nDrawTextW(b)\nOther: c\n",
                                      ["DrawTextW", "MessageBoxW"])
    require(fired == {"DrawTextW": 2}, "hook counting changed: %r" % fired)


def test_apply_run_result_keeps_the_v1_shape():
    rep = _fake_report()
    markers = {"load_failed": False, "load_error": None, "init_returned_code": 1,
               "init_returned": True, "hooks_applied": 2, "target_exit_code": 0,
               "crashed": False}
    analysis = {"public_hooks": ["DrawTextW"],
                "internal_hooks": ["?ShowStartView@XamlLauncher@@UEAAJXZ"],
                "internal_modules": ["twinui.pcshell.dll"]}
    verdict.apply_run_result(rep, analysis, markers, {"DrawTextW": 2},
                             exit_code=0, timed_out=False, run_log="/tmp/run.log",
                             screenshot="/tmp/shot.png", stub_log="Wh_GetSymbol\n",
                             mode="self", target="awt-host.exe (self)")
    for key in ("schema", "mod", "build", "analysis", "include_analysis", "run",
                "environment", "shim", "verdict", "artifacts"):
        require(key in rep, "v1 key %r disappeared" % key)
    require(rep["verdict"]["level"] == "public_hooks_verified", "level wrong")
    require(rep["verdict"]["verified"] == ["build", "load", "public API hooks"],
            "verified list changed: %r" % rep["verdict"]["verified"])
    require("internal-symbols: ?ShowStartView@XamlLauncher@@UEAAJXZ"
            in rep["verdict"]["not_verifiable_here"],
            "internal symbols must stay not verifiable here")
    require("internal-module: twinui.pcshell.dll"
            in rep["verdict"]["not_verifiable_here"], "internal module dropped")
    require(rep["verdict"]["requires_real_windows"] is True,
            "requires_real_windows must survive a successful run")
    require(rep["shim"]["not_implemented_called"] == ["Wh_GetSymbol"],
            "shim stub log not recorded")
    require(rep["artifacts"]["screenshots"] == ["/tmp/shot.png"], "screenshot lost")
    require(rep["artifacts"]["wine_log"] == "/tmp/run.log", "run log lost")
    require(rep["run"]["mode"] == "self" and rep["run"]["target"],
            "run mode/target not recorded")


def test_runner_refactor_still_reports_not_executed_without_wine():
    """Regression guard: `awt.runner` now shares the verdict module."""
    require(os.path.exists(EXAMPLE), "examples/hello-log.wh.cpp is missing")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rep = runner.run(EXAMPLE, seconds=1)
    require(rep["schema"] == 1, "report schema changed")
    require(rep["verdict"]["level"] == "not_executed",
            "without wine the level must be not_executed, got %r"
            % rep["verdict"]["level"])
    require(rep["run"]["backend"] == "unavailable",
            "run.backend must say unavailable, got %r" % rep["run"]["backend"])
    require(rep["verdict"]["verified"] == [],
            "nothing ran, so nothing may be listed as verified")
    require(rep["verdict"]["requires_real_windows"] is False,
            "not_executed makes no claim at all, not even about real Windows")
    require("not_executed_reason" in rep["verdict"], "no reason recorded")
    printed = buffer.getvalue()
    require("not_executed" in printed, "the human summary no longer prints")
    return rep


def test_main_cli_exposes_the_backends():
    """`python3 -m awt.cli run --backend ...` and `awt.cli backends`."""
    from awt import cli as main_cli
    if os.name == "nt":
        raise SkipTest("the refusal paths below are the non-Windows ones")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main_cli.main(["run", EXAMPLE, "--seconds", "1",
                              "--backend", "windows-runner"])
    require(code == 3, "a not_executed backend run must exit 3, got %d" % code)
    require("not_executed" in buffer.getvalue(),
            "the CLI must print the honest summary")

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main_cli.main(["backends", "--list-json"])
    require(code == 0, "listing backends must succeed anywhere")
    doc = json.loads(buffer.getvalue())
    require([entry["name"] for entry in doc["backends"]] == backends.names(),
            "the CLI capability table does not match the registry")

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = main_cli.main(["run", EXAMPLE, "--backend", "dosbox"])
    require(code == 2, "an unknown backend is a usage error (exit 2), got %d" % code)
    require("unknown backend" in buffer.getvalue() and "\"" not in buffer.getvalue(),
            "the usage error must be readable: %r" % buffer.getvalue())


def test_exit_code_convention_is_shared():
    for level, expected in (("build_failed", 1), ("not_executed", 3),
                            ("load_failed", 0), ("crashed", 0),
                            ("loads_no_crash", 0), ("public_hooks_verified", 0)):
        got = base.exit_code_for({"verdict": {"level": level}})
        require(got == expected, "level %s -> exit %d, expected %d"
                % (level, got, expected))
    require(base.exit_code_for({}) == 0, "an empty report must not crash the CLI")
    require(base.exit_code_for(None) == 0, "None must not crash the CLI")


def test_backend_not_executed_matches_the_runner_semantics():
    """Every backend's 'I could not run this' report agrees with the runner's."""
    with_wine = test_runner_refactor_still_reports_not_executed_without_wine()
    for name in ("windows-runner", "self-hosted-vm"):
        backend = backends.get(name)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rep = backend.not_executed_report(EXAMPLE, "test reason")
        require(rep["verdict"]["level"] == "not_executed",
                "%s: level changed" % name)
        require(rep["verdict"]["verified"] == with_wine["verdict"]["verified"],
                "%s: verified list differs from the runner" % name)
        require(rep["verdict"]["requires_real_windows"]
                == with_wine["verdict"]["requires_real_windows"],
                "%s: requires_real_windows differs from the runner" % name)
        require(rep["schema"] == 1 and rep["mod"]["id"], "%s: report shape" % name)
        require(rep["run"]["backend"] == name, "%s: backend not recorded" % name)
        require(rep["run"]["environment"]["backend"] == name,
                "%s: environment not attached" % name)
