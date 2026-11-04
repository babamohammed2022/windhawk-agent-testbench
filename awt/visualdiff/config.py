"""Thresholds and reference sets, per mod.

One threshold for every mod is wrong twice over: a taskbar mod compared against a
Windows 11 capture tolerates almost nothing, while the same mod on a Windows 7
Basic-theme reference differs everywhere that matters. So thresholds and
references are configurable per mod and per reference, in a YAML file the bench
finds next to the mod or in `configs/`.

Search order (first hit wins):

1. `--config PATH`
2. `<mod source>.visual.yaml`   e.g. `taskbar-clock.wh.cpp.visual.yaml`
3. `<mod source, no extension>.visual.yaml`
4. `configs/visual.local.yaml`  (git-ignored: reference paths are machine-local)
5. `configs/visual.yaml`

Reference screenshots are **never** committed (they are images of Microsoft's
interfaces); the committed `configs/visual.example.yaml` shows the shape and uses
`.cache/refshots/...` paths, which is where `python3 -m awt.refshots` will put
regenerated captures.
"""
import os

from .. import env as host_env
from .. import yamlmini
from . import metrics

SCHEMA = 1

DEFAULTS = {
    "tolerance": 0,             # per-channel delta that still counts as identical
    "ssim_min": 0.90,           # below this the comparison is a mismatch
    # SSIM means different things to the two implementations (metrics.py), so a
    # configuration may set a floor for each and the right one is used.
    "ssim_min_pure": None,
    "ssim_min_numpy": None,
    "similarity_min": 0.95,     # per-pixel similarity floor (1 - mean delta / 255)
    "identical_min": 0.90,      # fraction of pixels that must be within tolerance
    # Averages hide small, sharp, important changes: a taskbar shifted by two
    # pixels still leaves 98% of the image identical. These ceilings are what
    # catches that. Set them per reference - a clock mod WANTS its clock region to
    # differ, which is what --roi is for.
    "max_differing_ratio": 0.01,   # at most 1% of pixels may differ
    "max_region_area": None,       # largest single divergent region, in pixels
    "region_threshold": 24,     # delta above which a pixel joins a region
    "min_region_area": 16,      # smaller components are noise
    "max_regions": 50,
    "merge_distance": 0,
    "connectivity": 4,
    "roi": None,
    "roi_obtained": None,
    "roi_reference": None,
    "resize": False,
    "mode": "RGB",
    "max_pixels": None,
}


class ConfigError(Exception):
    """A thresholds file that cannot be used, with the reason."""


def candidate_paths(mod_path=None, explicit=None):
    """The files that would be consulted, in order, whether they exist or not."""
    paths = []
    if explicit:
        paths.append(explicit)
    if mod_path:
        paths.append(mod_path + ".visual.yaml")
        stem = mod_path
        for suffix in (".wh.cpp", ".cpp", ".cc", ".yaml"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        paths.append(stem + ".visual.yaml")
    paths.append(os.path.join(host_env.REPO, "configs", "visual.local.yaml"))
    paths.append(os.path.join(host_env.REPO, "configs", "visual.yaml"))
    seen, out = set(), []
    for path in paths:
        absolute = os.path.abspath(path)
        if absolute not in seen:
            seen.add(absolute)
            out.append(path)
    return out


def find_config(mod_path=None, explicit=None):
    for path in candidate_paths(mod_path, explicit):
        if explicit and path == explicit:
            return path                    # an explicit path is used even if absent
        if os.path.exists(path):
            return path
    return None


def load(path=None, mod_path=None):
    """Load a configuration file. A missing implicit file is not an error."""
    resolved = find_config(mod_path, path)
    if not resolved or not os.path.exists(resolved):
        if path:
            raise ConfigError("configuration file not found: %s" % path)
        return {"schema": SCHEMA, "defaults": dict(DEFAULTS), "mods": {},
                "_path": None, "_source": "built-in defaults"}
    try:
        document = yamlmini.load(resolved)
    except yamlmini.YamlMiniError as exc:
        raise ConfigError("%s: %s" % (resolved, exc))
    except OSError as exc:
        raise ConfigError("%s: %s" % (resolved, exc))
    if not isinstance(document, dict):
        raise ConfigError("%s: expected a mapping at the top level" % resolved)
    problems = validate(document)
    if problems:
        raise ConfigError("%s: %s" % (resolved, "; ".join(problems)))
    document["_path"] = resolved
    document["_source"] = resolved
    return document


def validate(document):
    """Structural checks only: unknown keys are reported, values are coerced."""
    problems = []
    known_top = ("schema", "defaults", "mods", "notes")
    for key in document:
        if str(key).startswith("_"):
            continue                      # bookkeeping added by load()
        if key not in known_top:
            problems.append("unknown top-level key %r (known: %s)"
                            % (key, ", ".join(known_top)))
    defaults = document.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        problems.append("`defaults:` must be a mapping")
    elif isinstance(defaults, dict):
        for key in defaults:
            if key not in DEFAULTS:
                problems.append("`defaults.%s` is not a known threshold (known: %s)"
                                % (key, ", ".join(sorted(DEFAULTS))))
    mods = document.get("mods")
    if mods is not None and not isinstance(mods, dict):
        problems.append("`mods:` must be a mapping of mod id -> settings")
    elif isinstance(mods, dict):
        for mod_id, settings in mods.items():
            if not isinstance(settings, dict):
                problems.append("`mods.%s` must be a mapping" % mod_id)
                continue
            for key in settings:
                if key not in ("thresholds", "references", "notes",
                               "default_reference"):
                    problems.append("`mods.%s.%s` is not a known key" % (mod_id, key))
            for index, reference in enumerate(settings.get("references") or []):
                if not isinstance(reference, dict):
                    problems.append("`mods.%s.references[%d]` must be a mapping"
                                    % (mod_id, index))
                    continue
                if not reference.get("path"):
                    problems.append("`mods.%s.references[%d].path` is required"
                                    % (mod_id, index))
                for key in reference:
                    if key not in ("id", "path", "build_number", "edition", "theme",
                                   "resolution", "roi", "roi_obtained",
                                   "roi_reference", "thresholds", "notes",
                                   "captured_at", "source"):
                        problems.append("`mods.%s.references[%d].%s` is not a known "
                                        "key" % (mod_id, index, key))
    return problems


def thresholds_for(config, mod_id=None, reference=None):
    """Effective thresholds: built-in defaults <- file defaults <- mod <- reference."""
    out = dict(DEFAULTS)
    out.update(config.get("defaults") or {})
    mod_entry = (config.get("mods") or {}).get(mod_id or "") or {}
    out.update(mod_entry.get("thresholds") or {})
    if isinstance(reference, dict):
        # a reference may carry its own ROI directly (that is how a taskbar strip
        # is compared across captures of different resolutions)
        for key in ("roi", "roi_obtained", "roi_reference", "resize", "mode",
                    "tolerance", "max_pixels"):
            if reference.get(key) is not None:
                out[key] = reference[key]
        out.update(reference.get("thresholds") or {})
    # SSIM means different things to the two implementations (see metrics.py);
    # a config may set a floor for each, and the matching one is used.
    implementation = "numpy" if metrics.numpy() is not None else "pure"
    key = "ssim_min_%s" % implementation
    holders = [config.get("defaults") or {}, mod_entry.get("thresholds") or {}]
    if isinstance(reference, dict):
        holders.append(reference.get("thresholds") or {})
    # Level by level, an implementation-specific floor replaces the generic one at
    # the SAME level; a more specific level (reference > mod > defaults) still wins
    # overall. Otherwise a global `ssim_min_pure` would silently override the floor
    # a reference set for itself.
    for holder in holders:
        if holder.get(key) is not None:
            out["ssim_min"] = holder[key]
        elif holder.get("ssim_min") is not None:
            out["ssim_min"] = holder["ssim_min"]
    out["_implementation"] = implementation
    reference = reference if isinstance(reference, dict) else {}
    out["_source"] = {"defaults": sorted((config.get("defaults") or {}).keys()),
                      "mod": sorted((mod_entry.get("thresholds") or {}).keys()),
                      "reference": sorted(
                          list((reference.get("thresholds") or {}).keys())
                          + [key for key in ("roi", "roi_obtained", "roi_reference",
                                             "resize", "mode", "tolerance")
                             if reference.get(key) is not None])}
    return out


def references_for(config, mod_id=None):
    """The reference set for a mod: one entry per build/theme, as configured."""
    entry = (config.get("mods") or {}).get(mod_id or "") or {}
    references = []
    for index, reference in enumerate(entry.get("references") or []):
        item = dict(reference)
        item.setdefault("id", "reference-%d" % (index + 1))
        references.append(item)
    if not references:
        defaults = entry.get("default_reference") or config.get("default_reference")
        if defaults:
            references = [dict(defaults, id="default")]
    return references


def describe(config, mod_id=None):
    """What will be applied, for the CLI and for the report."""
    return {
        "path": config.get("_path"),
        "source": config.get("_source"),
        "schema": config.get("schema", SCHEMA),
        "mod_id": mod_id,
        "references": references_for(config, mod_id),
        "thresholds": thresholds_for(config, mod_id),
    }
