"""Tests for the visual-comparison configuration (`awt.visualdiff.config`).

Thresholds decide whether a picture is called a match, so the precedence rules
are pinned exactly: built-in defaults, then the file's `defaults:`, then the mod,
then the individual reference. Two traps get their own assertions - an
implementation-specific SSIM floor must not override a floor a reference set for
itself, and an explicit `--config` that does not exist must be an error rather
than a silent fall back to the defaults.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import require                      # noqa: E402
from tests.unit.fixtures import images                      # noqa: E402
from awt import env as host_env                             # noqa: E402
from awt import visualdiff, yamlmini                        # noqa: E402
from awt.visualdiff import cli as visual_cli                # noqa: E402
from awt.visualdiff import config as config_mod             # noqa: E402
from awt.visualdiff import metrics                          # noqa: E402

TEMPLATE = os.path.join(REPO, "configs", "visual.example.yaml")


def _tmp():
    return tempfile.mkdtemp(prefix="awt-vcfg-")


def _write(tmp, text, name="visual.yaml"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_candidate_order_is_explicit_mod_local_repo():
    paths = config_mod.candidate_paths("examples/taskbar-clock.wh.cpp", "custom.yaml")
    require(paths[0] == "custom.yaml", "an explicit path must be consulted first")
    require(paths[1] == "examples/taskbar-clock.wh.cpp.visual.yaml",
            "the mod-specific file comes next: %r" % paths[1])
    require(paths[2] == "examples/taskbar-clock.visual.yaml",
            "then the same name without the source suffix: %r" % paths[2])
    require(paths[-2:] == [os.path.join(host_env.REPO, "configs", "visual.local.yaml"),
                           os.path.join(host_env.REPO, "configs", "visual.yaml")],
            "the repo-wide files come last: %r" % paths[-2:])
    require(len(paths) == len(set(os.path.abspath(item) for item in paths)),
            "the candidate list must not repeat a file: %r" % paths)
    require(config_mod.candidate_paths() == config_mod.candidate_paths(None, None),
            "no arguments means just the repo-wide files")
    require(len(config_mod.candidate_paths()) == 2,
            "without a mod there are two candidates: %r" % config_mod.candidate_paths())


def test_find_config_prefers_existing_files_but_honours_explicit_paths():
    tmp = _tmp()
    explicit = os.path.join(tmp, "gone.yaml")
    require(config_mod.find_config(None, explicit) == explicit,
            "an explicit path is returned even when it does not exist")
    mod_path = os.path.join(tmp, "clock.wh.cpp")
    with open(mod_path, "w", encoding="utf-8") as f:
        f.write("// ==WindhawkMod==\n")
    side = _write(tmp, "schema: 1\n", name="clock.wh.cpp.visual.yaml")
    require(config_mod.find_config(mod_path, None) == side,
            "a file next to the mod must be found: %r" % config_mod.find_config(mod_path))
    os.remove(side)
    require(config_mod.find_config(mod_path, None) is None
            or os.path.exists(config_mod.find_config(mod_path, None)),
            "with nothing local, only an existing repo-wide file may be returned")


def test_loading_nothing_gives_the_documented_defaults():
    tmp = _tmp()
    saved = os.getcwd()
    os.chdir(tmp)                       # keep repo-wide files out of the way
    try:
        config = config_mod.load(None, mod_path=os.path.join(tmp, "none.wh.cpp"))
    finally:
        os.chdir(saved)
    require(config["_source"] == "built-in defaults",
            "the source must say where the numbers came from: %r" % config["_source"])
    require(config["_path"] is None, "no file was used")
    require(config["defaults"] == config_mod.DEFAULTS,
            "the defaults must be the module's own")
    require(config["schema"] == config_mod.SCHEMA, "the schema must be stated")
    thresholds = config_mod.thresholds_for(config, None)
    for key, value in config_mod.DEFAULTS.items():
        require(thresholds[key] == value,
                "default %r changed: %r != %r" % (key, thresholds[key], value))
    require(thresholds["_implementation"] in ("pure", "numpy"),
            "the implementation behind the thresholds must be recorded")


def test_an_explicit_missing_file_is_an_error_not_a_fallback():
    tmp = _tmp()
    try:
        config_mod.load(os.path.join(tmp, "nope.yaml"))
    except config_mod.ConfigError as exc:
        require("not found" in str(exc), "the reason must be clear: %s" % exc)
    else:
        raise AssertionError("an explicit --config that does not exist must fail")


def test_a_broken_file_is_refused_with_every_problem_listed():
    tmp = _tmp()
    broken = _write(tmp, "schema: 1\ndefaults:\n  ssim_min: 0.9\n  nonsense_key: 3\n"
                         "surprise: yes\nmods:\n  clock:\n    thresholdz:\n"
                         "      ssim_min: 0.5\n    references:\n"
                         "      - id: a\n      - path: ''\n        weird: 1\n")
    try:
        config_mod.load(broken)
    except config_mod.ConfigError as exc:
        text = str(exc)
        for fragment in ("unknown top-level key", "nonsense_key", "thresholdz",
                         "references[0].path` is required", "weird"):
            require(fragment in text, "the error should mention %r: %s"
                    % (fragment, text))
        require(broken in text, "the error must name the file")
    else:
        raise AssertionError("a broken configuration must be refused")
    require(config_mod.validate({"_path": "x", "_source": "y", "schema": 1}) == [],
            "bookkeeping keys must not be reported as unknown")
    require(config_mod.validate({"defaults": "nope"})[0].startswith("`defaults:`"),
            "a non-mapping defaults must be reported")
    require(config_mod.validate({"mods": "nope"})[0].startswith("`mods:`"),
            "a non-mapping mods must be reported")
    require(config_mod.validate({"mods": {"a": "nope"}})[0].endswith("must be a "
                                                                    "mapping"),
            "a non-mapping mod entry must be reported")
    require("references[0]` must be a mapping" in config_mod.validate(
        {"mods": {"a": {"references": ["nope"]}}})[0],
            "a non-mapping reference must be reported")


def test_threshold_precedence_defaults_mod_reference():
    config = {"schema": 1,
              "defaults": {"ssim_min": 0.80, "tolerance": 3, "max_regions": 9},
              "mods": {"clock": {"thresholds": {"ssim_min": 0.70,
                                                "min_region_area": 5},
                                 "references": [
                                     {"id": "a", "path": "a.png",
                                      "thresholds": {"ssim_min": 0.60}},
                                     {"id": "b", "path": "b.png", "roi": "0,0,10,10",
                                      "tolerance": 12, "resize": True,
                                      "thresholds": {"identical_min": 0.5}}]}}}
    effective = config_mod.thresholds_for(config, "clock")
    require(effective["ssim_min"] == 0.70,
            "with no reference the mod floor applies, not the file default: %r"
            % effective["ssim_min"])
    require(effective["max_regions"] == 9 and effective["tolerance"] == 3,
            "file defaults must survive")
    first = config_mod.thresholds_for(config, "clock", config["mods"]["clock"][
        "references"][0])
    require(first["ssim_min"] == 0.60, "the reference overrides the file: %r"
            % first["ssim_min"])
    require(first["min_region_area"] == 5, "the mod-level value must survive")
    require(first["tolerance"] == 3, "an unrelated default must not change")
    second = config_mod.thresholds_for(config, "clock", config["mods"]["clock"][
        "references"][1])
    require(second["roi"] == "0,0,10,10", "a reference ROI must be picked up")
    require(second["tolerance"] == 12, "a reference-level tolerance must win")
    require(second["resize"] is True, "a reference may allow resizing")
    require(second["identical_min"] == 0.5, "reference thresholds must apply")
    require(second["ssim_min"] == 0.70,
            "a reference without its own floor keeps the mod-level one: %r"
            % second["ssim_min"])
    unknown = config_mod.thresholds_for(config, "not-configured")
    require(unknown["ssim_min"] == 0.80 and unknown["min_region_area"]
            == config_mod.DEFAULTS["min_region_area"],
            "an unconfigured mod gets the file defaults only")
    require(unknown["_source"]["mod"] == [], "no mod keys were consulted: %r"
            % unknown["_source"])
    require(sorted(unknown["_source"]["defaults"]) == ["max_regions", "ssim_min",
                                                       "tolerance"],
            "the provenance must list the keys that were used: %r"
            % unknown["_source"])
    tolerant = config_mod.thresholds_for(config, "clock", "just-a-path")
    require(tolerant["ssim_min"] == 0.70 and tolerant["tolerance"] == 3,
            "a string reference must not break threshold resolution: %r"
            % tolerant["ssim_min"])


def test_an_implementation_floor_never_overrides_a_reference_floor():
    """`ssim_min_pure` is a global convenience; a reference's own floor is a
    decision. The more specific level must win."""
    config = {"schema": 1,
              "defaults": {"ssim_min": 0.90, "ssim_min_pure": 0.50,
                           "ssim_min_numpy": 0.55},
              "mods": {"clock": {"references": [
                  {"id": "win7", "path": "win7.png",
                   "thresholds": {"ssim_min": 0.75}}]}}}
    reference = config["mods"]["clock"]["references"][0]
    effective = config_mod.thresholds_for(config, "clock", reference)
    require(effective["ssim_min"] == 0.75,
            "the reference floor must win over the global implementation floor: %r"
            % effective["ssim_min"])
    without_reference = config_mod.thresholds_for(config, "clock")
    expected = 0.55 if metrics.numpy() is not None else 0.50
    require(without_reference["ssim_min"] == expected,
            "with no reference floor the implementation-specific one applies: %r"
            % without_reference["ssim_min"])
    mod_level = {"schema": 1,
                 "defaults": {"ssim_min_pure": 0.40, "ssim_min_numpy": 0.45},
                 "mods": {"clock": {"thresholds": {"ssim_min": 0.70}}}}
    require(config_mod.thresholds_for(mod_level, "clock")["ssim_min"] == 0.70,
            "a mod-level generic floor beats a global implementation floor")
    require(config_mod.thresholds_for({"schema": 1, "defaults": {}}, None)["ssim_min"]
            == config_mod.DEFAULTS["ssim_min"],
            "an empty file leaves the built-in floor alone")


def test_references_for_assigns_ids_and_falls_back():
    config = {"schema": 1, "mods": {
        "clock": {"references": [{"path": "a.png"}, {"id": "win7", "path": "b.png"}]},
        "other": {"default_reference": {"path": "fallback.png"}},
        "empty": {"notes": "nothing configured yet"}}}
    found = config_mod.references_for(config, "clock")
    require([item["id"] for item in found] == ["reference-1", "win7"],
            "missing ids are numbered, present ones kept: %r" % found)
    require(found[0]["path"] == "a.png", "the path must survive")
    fallback = config_mod.references_for(config, "other")
    require(fallback == [{"path": "fallback.png", "id": "default"}],
            "default_reference must be used: %r" % fallback)
    require(config_mod.references_for(config, "empty") == [],
            "a mod without references gives an empty list")
    require(config_mod.references_for(config, "unknown") == [],
            "an unknown mod gives an empty list")
    describe = config_mod.describe(config, "clock")
    require(sorted(describe) == ["mod_id", "path", "references", "schema", "source",
                                 "thresholds"],
            "describe changed shape: %r" % sorted(describe))
    require(describe["mod_id"] == "clock" and len(describe["references"]) == 2,
            "describe must report what will be used")


def test_the_committed_template_parses_validates_and_resolves():
    require(os.path.exists(TEMPLATE), "configs/visual.example.yaml is missing")
    document = yamlmini.load(TEMPLATE)
    require(config_mod.validate(document) == [],
            "the committed template must be valid: %r" % config_mod.validate(document))
    require(document["schema"] == 1, "the template must state its schema")
    require(document["defaults"]["max_region_area"] is None,
            "`null` must become None, not a string")
    require(document["defaults"]["resize"] is False, "`false` must become False")
    require(document["defaults"]["mode"] == "RGB", "the comparison mode must parse")
    config = config_mod.load(TEMPLATE)
    require(config["_path"] == TEMPLATE, "the resolved path must be recorded")
    ids = [item["id"] for item in config_mod.references_for(config, "taskbar-clock")]
    require(ids == ["win11-21h2-1080p", "win7-thinpc-basic"],
            "the template must show two references for the clock mod: %r" % ids)
    windows = config["mods"]["taskbar-clock"]["references"]
    win11 = config_mod.thresholds_for(config, "taskbar-clock", windows[0])
    require(win11["roi"] == "0,980,1920,120", "the taskbar ROI must resolve")
    require(win11["tolerance"] == 6, "the ClearType tolerance must resolve")
    require(win11["identical_min"] == 0.80, "the reference floor must resolve")
    require(win11["max_differing_ratio"] == 0.05,
            "the clock is allowed to differ, so the ceiling must resolve")
    require(win11["ssim_min"] == 0.88,
            "the mod-level floor must win over both defaults: %r" % win11["ssim_min"])
    win7 = config_mod.thresholds_for(config, "taskbar-clock", windows[1])
    require(win7["ssim_min"] == 0.75, "the Basic theme floor must resolve: %r"
            % win7["ssim_min"])
    require(win7["roi"] is None, "a reference without a ROI must not inherit one")
    classic = config["mods"]["classic-taskbar"]["references"][0]
    require(classic["build_number"] == 8102,
            "the Windows 8 build must be 8102 (see docs/reference-vms.md), got %r"
            % classic["build_number"])
    with open(TEMPLATE, encoding="utf-8") as f:
        template_text = f.read()
    require("NOT 8120" in template_text,
            "the template should point at the documented discrepancy")
    for entry in windows + [classic]:
        require(entry["path"].startswith(".cache/refshots/"),
                "reference images must live in the ignored cache: %r" % entry["path"])
        require(entry["edition"] and entry["theme"],
                "every reference must say which Windows it came from")


def test_cli_show_config_prints_what_will_be_applied():
    tmp = _tmp()
    base = images.desktop(width=32, height=24)
    obtained = images.write(base, tmp, "shot.png")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = visual_cli.main([obtained, "--config", TEMPLATE,
                                "--mod-id", "taskbar-clock", "--show-config"])
    require(code == 0, "--show-config must exit 0")
    printed = json.loads(buffer.getvalue())
    require(printed["mod_id"] == "taskbar-clock", "the mod id must be echoed")
    require(printed["thresholds"]["ssim_min"] == 0.88,
            "the mod-level floor must be resolved: %r" % printed["thresholds"])
    require(printed["thresholds"]["roi"] is None,
            "a ROI belongs to a reference, so it must not appear at mod level")
    require(printed["references"][0]["roi"] == "0,980,1920,120",
            "the per-reference ROI must be printed with the reference")
    require([item["id"] for item in printed["references"]]
            == ["win11-21h2-1080p", "win7-thinpc-basic"],
            "the reference set must be printed")
    require("_implementation" in printed["thresholds"],
            "which implementation produced the floors must be visible")


def test_configured_references_that_do_not_exist_are_unavailable():
    """The template points at generated captures. Until they exist the result must
    be `unavailable`, never an invented match."""
    if os.path.isdir(os.path.join(host_env.REPO, ".cache", "refshots")):
        require(True, "")            # captures were generated: nothing to prove here
        return
    tmp = _tmp()
    base = images.desktop(width=32, height=24)
    obtained = images.write(base, tmp, "shot.png")
    section = visualdiff.compare(obtained, mod_id="taskbar-clock",
                                 config_path=TEMPLATE)
    require(section["overall"]["status"] == visualdiff.STATUS_UNAVAILABLE,
            "missing reference captures must be unavailable: %r"
            % section["overall"])
    require(section["overall"]["comparisons"] == 2,
            "both configured references must be attempted")
    for comparison in section["comparisons"]:
        require("obtained" in comparison["reasons"][0]
                or "reference" in comparison["reasons"][0],
                "the reason must say which file failed: %r" % comparison["reasons"])
        require(not comparison["metrics"], "no metric may be invented")
    require(section["notes"] and TEMPLATE in section["notes"][0],
            "the section must say which configuration was used: %r" % section["notes"])


def test_no_references_configured_is_reported_not_guessed():
    tmp = _tmp()
    base = images.desktop(width=32, height=24)
    obtained = images.write(base, tmp, "shot.png")
    section = visualdiff.compare(obtained, mod_id="nothing-configured",
                                 config={"schema": 1, "mods": {}})
    require(section["comparisons"] == [], "nothing may be compared")
    require(section["overall"]["status"] == visualdiff.STATUS_UNAVAILABLE,
            "no reference means unavailable: %r" % section["overall"])
    require("no reference was configured" in " ".join(section["notes"]),
            "the reason must be in the report: %r" % section["notes"])
    require(visual_cli.main([obtained, "--mod-id", "nothing-configured",
                             "--config", _write(tmp, "schema: 1\nmods: {}\n")]) == 2,
            "the CLI must refuse to run without a reference")


def test_relative_reference_paths_are_resolved_against_the_repository():
    tmp = _tmp()
    base = images.desktop(width=32, height=24)
    obtained = images.write(base, tmp, "shot.png")
    relative = os.path.relpath(obtained, host_env.REPO)
    config = {"schema": 1, "mods": {"clock": {"references": [
        {"id": "repo-relative", "path": relative}]}}}
    section = visualdiff.compare(obtained, mod_id="clock", config=config)
    require(section["overall"]["status"] == visualdiff.STATUS_MATCH,
            "a repo-relative reference must be found and must match: %r"
            % section["overall"])
    absolute = {"schema": 1, "mods": {"clock": {"references": [
        {"id": "absolute", "path": obtained}]}}}
    require(visualdiff.compare(obtained, mod_id="clock",
                               config=absolute)["overall"]["status"]
            == visualdiff.STATUS_MATCH, "an absolute reference must work too")
    overrides = {"tolerance": 200, "identical_min": 0.0, "similarity_min": 0.0,
                 "ssim_min": 0.0, "max_differing_ratio": 1.0}
    other = images.write(images.noise(base, 60, seed=9), tmp, "noisy.png")
    require(visualdiff.compare(other, mod_id="clock", config=absolute,
                               overrides=overrides)["overall"]["status"]
            == visualdiff.STATUS_MATCH,
            "overrides must reach the comparison: %r" % overrides)
    require(visualdiff.compare(other, mod_id="clock", config=absolute)[
        "overall"]["status"] == visualdiff.STATUS_MISMATCH,
            "without the overrides the same pair must fail")
