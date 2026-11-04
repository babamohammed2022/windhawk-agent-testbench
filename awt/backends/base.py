"""Execution backends: the same report, produced by different machines.

The bench started with exactly one way to run a mod - headless Wine on Linux.
That answer is honest but narrow: Wine is not Windows, and its shell is not the
Windows shell. This package makes "where did this actually run?" a first-class
part of the report, without changing the report's meaning:

    wine              existing path, unchanged (Linux + Xvfb + Wine prefix)
    windows-runner    native execution on a Windows CI runner (GitHub Actions)
    self-hosted-vm    a user-provided bootable Windows VM, restored to a clean
                      snapshot before each run, driven through a shared folder

Rules every backend follows:

* the JSON report keeps its v1 shape - backends only *add* keys
  (`run.environment`, `verdict.indicative`);
* a backend that cannot run anything says so and produces `not_executed`, never
  a green verdict;
* an environment whose shell is not the client Windows shell marks shell-related
  outcomes **indicative** - GitHub runners are Windows Server, and Windows Server
  has no immersive taskbar, no Start menu experience and a different explorer;
* no credentials and no VM images live in this repository: the VM backend reads a
  git-ignored local configuration file.
"""
import time

SHELL_PROFILES = ("client", "server", "wine", "unknown")

# Keys of the `run.environment` block. Stable and always present (None when the
# backend could not determine a value) so a consumer never has to guess.
ENVIRONMENT_KEYS = (
    "backend", "os", "os_version", "edition", "build_number", "product_type",
    "shell_profile", "virtualization", "vm_used", "snapshot_restored",
    "runner", "runner_image", "arch", "hostname", "collected_by", "collected_at",
    "seconds", "notes", "caveats",
)

# `notes` is the operational trail (what happened, what was slow, what failed).
# `caveats` is what a reader must know before *interpreting* the verdict, and it
# is the only list that reaches `verdict.indicative` besides the shell profile.

SERVER_SHELL_NOTE = (
    "shell-related outcomes are INDICATIVE: this backend runs on Windows Server "
    "(build {build}), whose shell is not the client Windows shell a mod targets - "
    "no immersive taskbar, no Start menu experience, different explorer behaviour")

WINE_SHELL_NOTE = (
    "shell-related outcomes are SIMULATED, not indicative: this backend is Wine on "
    "Linux, which is not Windows at all (see docs/limitations.md)")

UNKNOWN_OS_NOTE = (
    "the execution environment could not be identified, so no claim is made about "
    "which Windows shell was involved")


def environment_block(**fields) -> dict:
    """A normalised `run.environment` block: every key present, values or None."""
    out = dict((key, None) for key in ENVIRONMENT_KEYS)
    out["notes"] = []
    out["caveats"] = []
    for key, value in fields.items():
        if key not in out:
            raise KeyError("unknown environment field %r (allowed: %s)"
                           % (key, ", ".join(ENVIRONMENT_KEYS)))
        out[key] = value
    if out["notes"] is None:
        out["notes"] = []
    if out["caveats"] is None:
        out["caveats"] = []
    out["collected_at"] = out.get("collected_at") or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["collected_by"] = out.get("collected_by") or "awt.backends"
    return out


def shell_profile(os_name=None, product_type=None, build_number=None,
                  wine=False) -> str:
    """`client` / `server` / `wine` / `unknown` - what kind of shell was running.

    `product_type` is the Windows product type: 1 = workstation, 2 = domain
    controller, 3 = server. When it is missing, well-known Server build numbers
    are used as a fallback, and anything else stays `unknown` rather than
    pretending to be a client shell.
    """
    if wine:
        return "wine"
    if os_name and "windows" not in str(os_name).lower():
        return "unknown"
    if product_type in (2, 3, "server", "domain-controller"):
        return "server"
    if product_type in (1, "client", "workstation"):
        return "client"
    server_builds = {
        20348,   # Windows Server 2022 (GitHub windows-2022/2025 runners)
        17763,   # Windows Server 2019 (GitHub windows-2019 runner)
        14393,   # Windows Server 2016
        26100,   # ambiguous: Server 2025 and client 24H2 share the build number,
                 # so it is deliberately NOT decided here
    }
    try:
        build = int(build_number) if build_number is not None else None
    except (TypeError, ValueError):
        build = None
    if build in server_builds and build != 26100:
        return "server"
    return "unknown"


def indicative_notes(env) -> list:
    """What a reader must be told before trusting shell-related outcomes."""
    notes = []
    profile = (env or {}).get("shell_profile")
    if profile == "server":
        notes.append(SERVER_SHELL_NOTE.format(build=(env or {}).get("build_number")
                                              or "?"))
    elif profile == "wine":
        notes.append(WINE_SHELL_NOTE)
    elif profile == "unknown":
        notes.append(UNKNOWN_OS_NOTE)
    for caveat in (env or {}).get("caveats") or []:
        if caveat and caveat not in notes:
            notes.append(caveat)
    return notes


def apply_environment(rep, env, backend=None):
    """Attach a backend's environment to a report. Purely additive.

    `verdict.level`, `verdict.verified`, `verdict.not_verifiable_here` and
    `verdict.requires_real_windows` are left exactly as the execution produced
    them: where a run happened never changes what it proved.
    """
    if env.get("shell_profile") is None:
        env = dict(env, shell_profile="unknown")
    run = rep.setdefault("run", {})
    # an explicit backend wins; otherwise whatever the execution already decided
    # is kept ("unavailable" must not be relabelled as a working backend).
    if backend:
        run["backend"] = backend
    elif not run.get("backend"):
        run["backend"] = env.get("backend")
    run["environment"] = env
    verdict = rep.setdefault("verdict", {})
    existing = list(verdict.get("indicative") or [])
    for note in indicative_notes(env):
        if note not in existing:
            existing.append(note)
    verdict["indicative"] = existing
    return rep


def exit_code_for(rep):
    """The bench's exit-code convention, identical for every backend and CLI.

    0 = the harness did its job and a verdict exists (including `crashed`: that is
    a result about the mod, not a failure of the bench), 1 = the mod did not
    compile, 3 = nothing could be executed here, so no claim is made at all.
    """
    level = ((rep or {}).get("verdict") or {}).get("level")
    if level == "build_failed":
        return 1
    if level == "not_executed":
        return 3
    return 0


class Backend:
    """The interface every execution backend implements."""

    name = "abstract"
    label = "abstract backend"
    executes_on = "unknown"

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label,
                "executes_on": self.executes_on,
                "shell_profile": None, "limitations": []}

    def available(self):
        """(bool, reason). A reason is required when unavailable."""
        raise NotImplementedError

    def environment(self) -> dict:
        return environment_block(backend=self.name)

    def run(self, mod_path, seconds=6, mode="auto", target=None,
            screenshot=False, **kwargs) -> dict:
        raise NotImplementedError

    # -- shared helpers -----------------------------------------------------
    def not_executed_report(self, mod_path, reason, environment=None):
        """A report that claims nothing, for a backend that could not run."""
        from .. import report as report_mod
        env = environment or self.environment()
        rep = {
            "schema": 1,
            "mod": {"id": None, "version": "0.0.0", "include": [],
                    "architecture": "x86-64"},
            "build": {"ok": False, "errors": [], "warnings": [],
                      "toolchain_missing": []},
            "analysis": {"public_hooks": [], "internal_hooks": [],
                         "unknown_hooks": []},
            "include_analysis": [],
            "run": {"backend": self.name, "reason": reason},
            "environment": {},
            "shim": {"not_implemented_called": [], "simulated_with_limits": []},
            "verdict": {
                # Same semantics as the Wine runner's own not_executed path:
                # nothing was analysed and nothing was executed, so no claim is
                # made - not even the claim that real Windows would settle it.
                "level": "not_executed",
                "verified": [],
                "not_verifiable_here": [],
                "requires_real_windows": False,
                "not_executed_reason": reason,
                "summary": "level=not_executed; %s" % reason,
            },
            "artifacts": {"screenshots": [], "wine_log": None, "dumps": []},
        }
        rep["mod"] = dict(rep["mod"], **_mod_identity(mod_path))
        apply_environment(rep, env, backend=self.name)
        rep["environment"] = _host_probe()
        return report_mod.attach_symbol_matrix(rep, None) or rep


def _mod_identity(mod_path):
    """Best-effort mod id/version/include from the source, without compiling."""
    try:
        from .. import metadata as metadata_mod
        with open(mod_path, encoding="utf-8", errors="replace") as f:
            meta = metadata_mod.parse_metadata(f.read())
        return {"id": (meta.get("raw") or {}).get("id"),
                "version": (meta.get("raw") or {}).get("version", "0.0.0"),
                "include": meta.get("include") or [],
                "architecture": (meta.get("raw") or {}).get("architecture",
                                                            "x86-64")}
    except OSError:
        return {}


def _host_probe():
    try:
        from .. import env as host_env
        return host_env.probe()
    except Exception:                              # pragma: no cover
        return {}
