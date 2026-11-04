"""Backend registry: `awt.backends.select()` picks one that can actually run.

Order matters: an explicit choice is honoured first, then a native Windows
backend when this process is on Windows, then Wine, then a configured
self-hosted VM. A backend that is not available is reported with its reason
instead of being tried and failing halfway.
"""
import os

from .base import (Backend, ENVIRONMENT_KEYS, apply_environment, environment_block,
                   exit_code_for, indicative_notes, shell_profile)
from .vm import VmBackend
from .wine import WineBackend
from .windows_ci import WindowsRunnerBackend

ORDER = ("wine", "windows-runner", "self-hosted-vm")


def registry():
    """name -> backend instance (constructed lazily but cheaply)."""
    return {
        "wine": WineBackend(),
        "windows-runner": WindowsRunnerBackend(),
        "self-hosted-vm": VmBackend(),
    }


def names():
    return list(ORDER)


def get(name):
    backend = registry().get(name)
    if backend is None:
        raise KeyError("unknown backend %r (available: %s)"
                       % (name, ", ".join(ORDER)))
    return backend


def select(preferred=None, config=None, verbose=False):
    """(backend, reason). `reason` explains the choice or why nothing is usable."""
    backends = registry()
    order = list(ORDER)
    if preferred:
        if preferred not in backends:
            raise KeyError("unknown backend %r (available: %s)"
                           % (preferred, ", ".join(ORDER)))
        order = [preferred] + [n for n in ORDER if n != preferred]
    # a native Windows process should not try Wine first
    if os.name == "nt" and not preferred:
        order = ["windows-runner", "self-hosted-vm", "wine"]
    tried = []
    for name in order:
        if name == "self-hosted-vm" and not config and not VmBackend().config_path():
            tried.append("%s: no VM configuration file" % name)
            continue
        backend = backends[name]
        ok, reason = backend.available()
        if verbose:
            print("[backends] %s: %s (%s)" % (name, "available" if ok else "unavailable",
                                              reason))
        if ok:
            return backend, reason
        tried.append("%s: %s" % (name, reason))
    return None, "; ".join(tried) or "no backend available"


def describe_all():
    """Capability table for the report and for `python3 -m awt.backends`."""
    out = []
    for name in ORDER:
        backend = registry()[name]
        ok, reason = backend.available()
        entry = backend.describe()
        entry.update({"available": ok, "reason": reason,
                      "environment": backend.environment()})
        out.append(entry)
    return out


__all__ = ["Backend", "ENVIRONMENT_KEYS", "ORDER", "VmBackend", "WineBackend",
           "WindowsRunnerBackend", "apply_environment", "describe_all",
           "environment_block", "exit_code_for", "get", "indicative_notes",
           "names", "registry", "select", "shell_profile"]
