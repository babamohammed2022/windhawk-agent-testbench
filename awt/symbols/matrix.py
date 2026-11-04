"""Load and normalise the build matrix (`configs/symbol-matrix.yaml`).

The matrix says *which* Windows builds a mod's symbols are checked against and
*where* the binaries come from. It is pure configuration: no network access
happens here, so loading it can never produce a claim about a symbol.

Resolution order for the file itself:

1. `--matrix <path>` on the command line,
2. `$AWT_SYMBOL_MATRIX`,
3. `configs/symbol-matrix.local.yaml` (git-ignored, machine specific),
4. `configs/symbol-matrix.yaml` (committed default),
5. the built-in `DEFAULT_BUILDS` below, if no file can be read.

The loader is `awt.yamlmini` so the bench keeps working with no third-party
packages; when PyYAML is installed the unit tests check both parsers agree.
"""
import os

from .. import env as host_env
from .. import yamlmini
from . import winbindex

CONFIG_DIR = os.path.join(host_env.REPO, "configs")
DEFAULT_PATH = os.path.join(CONFIG_DIR, "symbol-matrix.yaml")
LOCAL_PATH = os.path.join(CONFIG_DIR, "symbol-matrix.local.yaml")

# The minimum matrix the tool reports on even with no config file at all.
DEFAULT_BUILDS = [
    {"id": "win10-1507", "label": "Windows 10 1507 (RTM)", "channel": "1507",
     "required": True},
    {"id": "win10-22h2", "label": "Windows 10 22H2", "channel": "22H2",
     "required": True},
    {"id": "win11-21h2", "label": "Windows 11 21H2 (original release)",
     "channel": "11-21H2", "required": True},
    {"id": "win11-23h2", "label": "Windows 11 23H2", "channel": "11-23H2",
     "required": True},
    {"id": "win11-24h2", "label": "Windows 11 24H2", "channel": "11-24H2",
     "required": True},
    {"id": "win11-25h2", "label": "Windows 11 25H2", "channel": "11-25H2",
     "required": True},
]

DEFAULTS = {
    "schema": 1,
    "symbol_server": {"base_url": None, "timeout_seconds": 20, "attempts": 2,
                      "cache_dir": ".cache/symbols"},
    "winbindex": {"enabled": True, "cache_dir": ".cache/winbindex",
                  "timeout_seconds": 25, "repo": winbindex.WINBINDEX_REPO,
                  "ref": winbindex.WINBINDEX_REF},
    "arch": "x86-64",
    "builds": DEFAULT_BUILDS,
    "pinned_signatures": {},
    "local_modules": {},
    "limits": {"max_symbols_per_module": 400000, "max_requests": 0,
               "sleep_between_downloads": 0.2},
}


def candidate_paths(explicit=None):
    out = []
    if explicit:
        out.append(explicit)
    if os.environ.get("AWT_SYMBOL_MATRIX"):
        out.append(os.environ["AWT_SYMBOL_MATRIX"])
    out.append(LOCAL_PATH)
    out.append(DEFAULT_PATH)
    return out


def find_config(explicit=None):
    for path in candidate_paths(explicit):
        if path and os.path.isfile(path):
            return path
    return None


def _resolve_dir(value):
    if not value:
        return None
    if os.path.isabs(value):
        return value
    env_name = os.environ.get("AWT_CACHE_DIR")
    if env_name and str(value).startswith(".cache"):
        return os.path.join(env_name, *str(value).split("/")[1:])
    return os.path.join(host_env.REPO, value)


def _normalise_build(raw, notes):
    build = dict(raw or {})
    if not build.get("id"):
        notes.append("a build entry without an id was ignored: %r" % (raw,))
        return None
    build["id"] = str(build["id"]).strip().lower()
    build.setdefault("label", build["id"])
    build["channel"] = winbindex.channel_for(build.get("channel") or build["id"])
    build["required"] = bool(build.get("required"))
    build["enabled"] = True if "enabled" not in build else bool(build["enabled"])
    build["arch"] = build.get("arch")
    if not build.get("enabled"):
        notes.append("build %s is disabled in the matrix" % build["id"])
    return build


def _normalise_pinned(raw, notes):
    out = {}
    for module, channels in (raw or {}).items():
        module_key = str(module).lower()
        table = dict(out.get(module_key) or {})
        for channel, entry in (channels or {}).items():
            entry = dict(entry or {})
            try:
                timestamp = int(entry.get("timestamp"), 0) \
                    if isinstance(entry.get("timestamp"), str) \
                    else int(entry.get("timestamp"))
                size = int(entry.get("virtual_size") or entry.get("size")
                           or entry.get("virtualSize"), 0) \
                    if isinstance(entry.get("virtual_size") or entry.get("size")
                                  or entry.get("virtualSize"), str) \
                    else int(entry.get("virtual_size") or entry.get("size")
                             or entry.get("virtualSize"))
            except (TypeError, ValueError):
                notes.append("pinned signature for %s/%s is incomplete and was "
                             "ignored" % (module_key, channel))
                continue
            table[str(channel)] = {"timestamp": timestamp, "virtual_size": size,
                                   "version": entry.get("version")}
        if table:
            out[module_key] = table
    return out


def load(path=None, require=True):
    """Return the normalised matrix dict, plus a `notes` list of what changed."""
    notes = []
    if path and not os.path.isfile(path):
        # an explicit --matrix that does not exist is a user error, not a reason
        # to quietly fall back to the committed defaults.
        raise OSError("matrix file not found: %s" % path)
    found = find_config(path)
    raw = {}
    if found:
        try:
            raw = yamlmini.load(found) or {}
        except (yamlmini.YamlMiniError, OSError) as exc:
            notes.append("matrix %s could not be parsed (%s)" % (found, exc))
            if path or require:
                raise
            raw = {}
    else:
        notes.append("no matrix file found; using the built-in default builds")
        if path:
            raise OSError("matrix file not found: %s" % path)

    matrix = {
        "schema": int(raw.get("schema") or DEFAULTS["schema"]),
        "path": found,
        "source": "file" if found else "builtin-default",
        "arch": str(raw.get("arch") or DEFAULTS["arch"]),
        "notes": notes,
    }
    server = dict(DEFAULTS["symbol_server"])
    server.update(raw.get("symbol_server") or {})
    server["cache_dir"] = _resolve_dir(server.get("cache_dir"))
    matrix["symbol_server"] = server

    index = dict(DEFAULTS["winbindex"])
    index.update(raw.get("winbindex") or {})
    index["cache_dir"] = _resolve_dir(index.get("cache_dir"))
    matrix["winbindex"] = index

    builds = []
    for entry in (raw.get("builds") or DEFAULTS["builds"]):
        build = _normalise_build(entry, notes)
        if build:
            builds.append(build)
    matrix["builds"] = builds or [dict(b) for b in DEFAULT_BUILDS]

    pinned = dict((k.lower(), dict(v)) for k, v in winbindex.PINNED.items())
    pinned.update(_normalise_pinned(raw.get("pinned_signatures"), notes))
    matrix["pinned_signatures"] = pinned

    local = {}
    for module, entry in (raw.get("local_modules") or {}).items():
        entry = dict(entry or {})
        local[str(module).lower()] = {
            "pe": _resolve_dir(entry.get("pe")),
            "pdb": _resolve_dir(entry.get("pdb")),
        }
    matrix["local_modules"] = local

    limits = dict(DEFAULTS["limits"])
    limits.update(raw.get("limits") or {})
    matrix["limits"] = limits
    return matrix


def select_builds(matrix, ids=None, include_disabled=False):
    """Builds to report on, in matrix order."""
    builds = matrix.get("builds") or []
    if not include_disabled:
        builds = [b for b in builds if b.get("enabled", True)]
    if ids:
        wanted = set(str(i).strip().lower() for i in ids)
        builds = [b for b in builds if b["id"] in wanted]
        missing = wanted - set(b["id"] for b in builds)
        for build_id in sorted(missing):
            matrix["notes"].append("requested build %r is not in the matrix"
                                   % build_id)
    return builds


def describe(matrix):
    """Provenance block for the JSON report."""
    return {
        "path": matrix.get("path"),
        "source": matrix.get("source"),
        "arch": matrix.get("arch"),
        "builds": [{"id": b["id"], "label": b.get("label"),
                    "channel": b.get("channel"), "required": b.get("required"),
                    "enabled": b.get("enabled", True)}
                   for b in matrix.get("builds", [])],
        "symbol_server": matrix.get("symbol_server", {}).get("base_url"),
        "winbindex": (matrix.get("winbindex", {}).get("repo") + "@" +
                      str(matrix.get("winbindex", {}).get("ref"))),
        "pinned_modules": sorted(matrix.get("pinned_signatures") or {}),
        "local_modules": sorted(matrix.get("local_modules") or {}),
        "notes": list(matrix.get("notes") or []),
    }
