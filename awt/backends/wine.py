"""The existing backend: headless Wine on Linux, wrapped - not rewritten.

`awt.runner.run` already builds, loads, drives and logs a mod under Wine and
produces the v1 report. This adapter adds only what Feature 2 requires: an
explicit availability check with a reason, and a `run.environment` block that
says plainly that the shell involved is Wine's, not Windows'.
"""
import os
import platform

from .. import builder
from .. import env as host_env
from .. import runner
from .base import Backend, apply_environment, environment_block


class WineBackend(Backend):
    name = "wine"
    label = "headless Wine on Linux (Xvfb + per-repo prefix)"
    executes_on = "linux"

    def describe(self):
        return {
            "name": self.name,
            "label": self.label,
            "executes_on": self.executes_on,
            "shell_profile": "wine",
            "limitations": [
                "Wine is not Windows: no Microsoft binaries, no real shell",
                "remote CreateRemoteThread+LoadLibraryW injection is broken on "
                "Wine 10.0 and AppInit_DLLs is not honoured, so mods are "
                "exercised in the simulated in-process host "
                "(docs/limitations.md items 1-2)",
                "internal symbols cannot be resolved at all (item 3)",
            ],
        }

    def available(self):
        wine = host_env.find_wine()
        if not wine:
            return False, ("wine is not installed (looked for $AWT_WINE, "
                           "/usr/lib/wine/wine64, wine64, wine)")
        missing = builder.missing_toolchain()
        if missing:
            return False, "cross toolchain missing: %s" % ", ".join(missing)
        if not host_env.which("Xvfb"):
            return False, "Xvfb is not installed: no headless display for the run"
        version = None
        try:
            import subprocess
            version = subprocess.run([wine, "--version"], capture_output=True,
                                     text=True, timeout=20).stdout.strip() or None
        except Exception:                                  # pragma: no cover
            version = None
        return True, "wine %s at %s" % (version or "(version unknown)", wine)

    def environment(self, wine_version=None):
        ok, reason = self.available()
        return environment_block(
            backend=self.name,
            os=platform.system(),
            os_version=platform.release(),
            edition=None,
            build_number=None,
            product_type=None,
            shell_profile="wine",
            virtualization="wine prefix (%s)" % os.path.join(host_env.SANDBOX,
                                                             "prefix"),
            vm_used=None,
            snapshot_restored=False,
            runner=None,
            runner_image=None,
            arch=platform.machine(),
            hostname=None,
            collected_by="awt.backends.wine",
            seconds=None,
            notes=[("wine: %s" % reason) if ok else ("wine unavailable: %s" % reason)],
        )

    def run(self, mod_path, seconds=6, mode="auto", target=None,
            screenshot=False, **kwargs):
        rep = runner.run(mod_path, seconds=seconds, mode=mode, target=target,
                         screenshot=screenshot, symbols=kwargs.get("symbols"))
        # no explicit backend: keep whatever the runner decided ("wine" when it
        # ran, "unavailable" when it could not).
        return apply_environment(rep, self.environment())
