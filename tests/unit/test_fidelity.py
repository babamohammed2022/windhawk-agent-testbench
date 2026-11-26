"""Offline regression tests for metadata, targeting, flags and report fidelity.

No Wine, Windows or compiler is involved: these tests cover the pure-Python
parsers and the additive report sections. They never upgrade a runtime verdict.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from awt import metadata  # noqa: E402

MOD = """// ==WindhawkMod==
// @id              fidelity-demo
// @name            Fidelity demo
// @name:it-IT      Demo fedelta
// @description     Demo
// @version         1.2.3
// @author          awt
// @github          https://example.invalid/awt
// @twitter         @awt
// @homepage        https://example.invalid
// @license         MIT
// @include         C:\\Windows\\System32\\notepad.exe
// @include         explorer.exe
// @exclude         *\\helper.exe
// @compilerOptions -lcomctl32 -std=c++20 -DFOO=1
// @architecture    x86-64
// ==/WindhawkMod==
"""

SETTINGS = """// ==WindhawkModSettings==
/*
- enabled: true
  $name: Enabled
- greeting: "hello sandbox"
  $name: Greeting
- items:
  - size: 4
    label: "first"
  - size: 7
    label: "second"
*/
// ==/WindhawkModSettings==
"""


def test_metadata_keeps_every_documented_tag_and_localisation():
    meta = metadata.parse_metadata(MOD)
    raw = meta["raw"]
    for key, expected in (("id", "fidelity-demo"), ("version", "1.2.3"),
                          ("author", "awt"), ("github", "https://example.invalid/awt"),
                          ("twitter", "@awt"), ("homepage", "https://example.invalid"),
                          ("license", "MIT"), ("architecture", "x86-64")):
        assert raw.get(key) == expected, (key, raw.get(key))
    assert meta["include"] == [r"C:\Windows\System32\notepad.exe", "explorer.exe"]
    assert meta["exclude"] == [r"*\helper.exe"]
    assert meta["compilerOptions"] == ["-lcomctl32", "-std=c++20", "-DFOO=1"]
    assert meta["localizations"] == {"it-IT": {"name": "Demo fedelta"}}
    assert meta["warnings"] == []


def test_metadata_defaults_are_backwards_compatible():
    meta = metadata.parse_metadata("no block here")
    assert meta["include"] == ["*"] and meta["raw"] == {}
    assert metadata.parse_metadata("// ==WindhawkMod==\n// @id x\n").get("raw") == {}


def test_nested_and_indexed_settings_are_flattened_without_display_keys():
    flat, tree, warnings = metadata.parse_settings_ex(SETTINGS)
    assert warnings == []
    assert flat == {"enabled": 1, "greeting": "hello sandbox",
                    "items[0].size": 4, "items[0].label": "first",
                    "items[1].size": 7, "items[1].label": "second"}
    assert tree[2]["items"][1]["size"] == 7
    assert not [k for k in flat if k.startswith("$")]
    assert metadata.parse_settings(SETTINGS) == flat


def test_legacy_flat_settings_and_shipped_examples_still_parse():
    assert metadata.parse_settings("""// ==WindhawkModSettings==
/*
- greeting: "hello sandbox"
  $name: Greeting
*/
// ==/WindhawkModSettings==
""") == {"greeting": "hello sandbox"}
    demo = open(os.path.join(REPO, "examples", "settings-demo.wh.cpp"),
                encoding="utf-8").read()
    assert metadata.parse_settings(demo) == {"enabled": 1, "greeting": "hello sandbox",
                                             "retries": 3}


def test_include_exclude_patterns_match_wildcards_and_full_paths():
    from awt import targeting
    assert targeting.matches("explorer.exe", "EXPLORER.EXE")
    assert targeting.matches("note*.exe", "notepad.exe")
    assert targeting.matches("C:\\Windows\\System32\\notepad.exe", "notepad.exe",
                             "C:\\Windows\\System32\\notepad.exe")
    assert not targeting.matches("C:\\Windows\\System32\\notepad.exe", "notepad.exe",
                                 "C:\\Temp\\notepad.exe")
    assert targeting.matches(r"*\helper.exe", "helper.exe", r"D:\tools\helper.exe")
    assert not targeting.matches("explorer.exe", "notepad.exe")
    assert targeting.matches("*", "anything.exe")


def test_target_selection_is_always_a_declared_stand_in():
    from awt import targeting
    meta = metadata.parse_metadata(MOD)
    sel = targeting.select(meta, {"subjects": []})
    assert sel["suggested_stand_in"] == "trayapp.exe"  # @include explorer.exe
    assert sel["target_kind"] == "stand-in" and sel["shell_semantics"] == "simulated"
    generic = dict(meta, include=[r"C:\Windows\System32\notepad.exe"], exclude=[])
    assert targeting.select(generic, {"subjects": []})["suggested_stand_in"] == "testapp.exe"
    assert sel["entries"][0]["excluded_by"] == []
    excluded = dict(meta, include=["explorer.exe"], exclude=["explorer.exe"])
    sel = targeting.select(excluded, {"subjects": ["system-tray"]})
    assert sel["suggested_stand_in"] is None
    assert "vetoed" in sel["reason"]
    tray = targeting.select(dict(meta, include=["explorer.exe"], exclude=[]),
                            {"subjects": ["system-tray"]})
    assert tray["suggested_stand_in"] == "trayapp.exe"
    assert "never to Explorer" in tray["reason"]


def test_compiler_options_are_classified_not_silently_dropped():
    from awt import compileroptions
    opts = compileroptions.classify(
        ["-lcomctl32", "-luxtheme", "-std=c++20", "-DFOO=1", "-Wl,--enable-auto-import",
         "-fno-exceptions", "-lmystery"])
    assert opts["libraries"] == ["comctl32", "uxtheme", "mystery"]
    assert opts["unverified_libraries"] == ["mystery"]
    assert opts["defines"] == ["-DFOO=1"]
    assert opts["standards"] == ["-std=c++20"]
    assert "-fno-exceptions" in [r["flag"] for r in opts["rejected"]]
    assert "not an audited" in opts["rejected"][0]["reason"]
    assert compileroptions.compile_flags(opts) == ["-DFOO=1"]
    assert "-lcomctl32" in compileroptions.link_flags(opts)
    assert "-DFOO=1" not in compileroptions.link_flags(opts)


def test_builder_records_options_and_toolchain_even_without_a_compiler():
    from awt import builder
    src = MOD.replace("-DFOO=1", "-DFOO=1 -fno-exceptions")
    path = os.path.join(REPO, ".sandbox", "fidelity-opts.wh.cpp")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        result = builder.build(path, sandbox=os.path.join(REPO, ".sandbox", "tmp-fidelity"))
    finally:
        os.unlink(path)
    assert result["compiler_options"]["defines"] == ["-DFOO=1"]
    assert [r["flag"] for r in result["compiler_options"]["rejected"]] == ["-fno-exceptions"]
    assert result["toolchain"]["target_arch"] == "x86-64"
    assert "Windhawk Clang" in result["toolchain"]["note"]
    assert any("not applied" in w["message"] for w in result["warnings"])


def test_accepted_flags_reach_the_real_compile_and_link_commands():
    from types import SimpleNamespace
    from unittest.mock import patch
    from awt import builder
    src = MOD.replace("-DFOO=1", "-DFOO=1 -fno-exceptions")
    path = os.path.join(REPO, ".sandbox", "fidelity-flags.wh.cpp")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    commands = []

    def fake_run(cmd, cwd=None):
        commands.append(list(cmd))
        if "-shared" in cmd:
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as f:
                f.write(b"MZ")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    try:
        with patch.object(builder, "missing_toolchain", return_value=[]), \
             patch.object(builder, "_run", side_effect=fake_run), \
             patch.object(builder, "_mangled_lifecycle", return_value={"Wh_ModInit": "_Z9Wh_ModInitv"}):
            result = builder.build(path, sandbox=os.path.join(REPO, ".sandbox", "tmp-fidelity-flags"))
    finally:
        os.unlink(path)
    assert result["ok"] is True, result["errors"]
    compile_cmd, link_cmd = commands[0], next(c for c in commands if "-shared" in c)
    assert "-DFOO=1" in compile_cmd and "-std=c++20" in compile_cmd
    assert "-lcomctl32" in link_cmd and "-luxtheme" not in link_cmd  # not in this mod
    assert not any("-fno-exceptions" in c for c in commands)


def test_api_inventory_marks_stubs_unknown_apis_and_lifecycle_honestly():
    from awt import fidelity
    source = """
BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"x");
    Wh_Disasm(nullptr, nullptr);
    Wh_ListValues(L"x");
    WindhawkUtils::HookSymbols(nullptr, nullptr, 0);
    return TRUE;
}
"""
    inv = fidelity.api_inventory(source)
    by_name = {a["name"]: a for a in inv["apis"]}
    assert by_name["Wh_ModInit"]["status"] == "lifecycle_export"
    assert by_name["Wh_Log"]["status"] == "shim_implemented"
    assert by_name["Wh_Disasm"]["status"] == "stub"
    assert by_name["Wh_ListValues"]["status"] == "unknown_api"
    assert by_name["WindhawkUtils::HookSymbols"]["status"] == "stub"
    assert all(a["evidence"].endswith("execution not proven") for a in inv["apis"])
    assert {w["id"] for w in inv["warnings"]} >= {"api.unknown", "api.stub"}


def test_capability_matrix_never_promotes_an_unexecuted_run():
    from awt import fidelity
    rep = {"run": {"backend": "unavailable"}, "verdict": {"level": "not_executed"},
           "artifacts": {}}
    caps = fidelity.capabilities(rep, {}, {}, "", {})
    assert not any(v["status"] == "verified" for v in caps["items"].values())
    assert caps["items"]["compile"]["status"] == "not_executed"
    assert caps["items"]["internal_symbols"]["status"] == "not_verifiable"
    assert caps["items"]["target_shell"]["status"] == "simulated"
    assert caps["summary"]["not_executed"] >= 5


def test_capability_matrix_uses_execution_evidence_when_present():
    from awt import fidelity
    rep = {"run": {"backend": "wine", "init_returned": True,
                   "hooks_fired": {"CreateFileW": 3}, "mode": "self"},
           "verdict": {"level": "public_hooks_verified"},
           "artifacts": {"screenshots": ["capture.png"]}}
    caps = fidelity.capabilities(rep, {"ok": True}, {}, "Wh_Log(L\"x\")", {})
    items = caps["items"]
    assert items["compile"]["status"] == "verified"
    assert items["load"]["status"] == "verified"
    assert items["public_hooks"]["status"] == "verified"
    assert items["settings"]["status"] == "simulated"
    assert items["persistent_storage"]["status"] == "simulated"
    assert items["injection"]["status"] == "simulated"
    assert items["screenshot"]["status"] == "verified"
    assert items["internal_symbols"]["status"] == "not_verifiable"
    assert items["unload_hook_integrity"]["status"] == "not_verifiable"


def test_report_attaches_fidelity_sections_without_touching_the_verdict():
    from awt import builder, report, runner, staticchecks
    path = os.path.join(REPO, "examples", "hello-log.wh.cpp")
    b = builder.build(path)
    rep = report.new_report(b, __import__("awt.analyzer", fromlist=["analyze"]).analyze(
        open(path, encoding="utf-8").read()))
    assert rep["verdict"]["verified"] == []
    for key in ("capabilities", "api_inventory", "fidelity_warnings", "target_selection"):
        assert key in rep, key
    assert rep["build"]["toolchain"]["target_arch"] == "x86-64"
    assert rep["target_selection"]["target_kind"] == "stand-in"
    text = report.summary(rep)
    # On a machine with a real toolchain the build is verified; on a machine
    # without one it is not. Assert the summary against the matrix it reports,
    # never against an assumed environment.
    expected = rep["capabilities"]["summary"].get("verified", 0)
    assert "capabilities" in text and "verified %d" % expected in text
    bad = staticchecks.run_checks("BOOL Wh_ModInit() { Wh_ListValues(L\"x\"); return TRUE; }")
    assert "api.unknown" in {c["id"] for c in bad["checks"]}


def test_new_examples_exercise_and_disclose_the_new_surfaces():
    from awt import fidelity, staticchecks
    array_path = os.path.join(REPO, "examples", "settings-array-demo.wh.cpp")
    array_src = open(array_path, encoding="utf-8").read()
    flat = metadata.parse_settings(array_src)
    assert flat["items[0].name"] == "first" and flat["items[1].size"] == 7
    assert metadata.parse_metadata(array_src)["localizations"]["it-IT"]["name"] == \
        "Demo impostazioni complesse"
    inv = fidelity.api_inventory(array_src)
    assert any(a["name"] == "Wh_GetIntSetting" for a in inv["apis"])

    tool_src = open(os.path.join(REPO, "examples", "tool-mod-demo.wh.cpp"),
                    encoding="utf-8").read()
    caps = fidelity.capabilities({"verdict": {"level": "not_executed"}, "run": {},
                                  "artifacts": {}}, {}, {}, tool_src, {})
    assert caps["items"]["tool_mod"]["status"] == "not_verifiable"
    assert "tool_mod.not_verifiable" in {c["id"] for c in
                                         staticchecks.run_checks(tool_src)["checks"]}

    symbol_src = open(os.path.join(REPO, "examples", "symbol-lookup-demo.wh.cpp"),
                      encoding="utf-8").read()
    symbols = {a["name"]: a["status"] for a in fidelity.api_inventory(symbol_src)["apis"]}
    assert symbols["Wh_FindFirstSymbol"] == "stub"
    assert "api.stub" in {c["id"] for c in staticchecks.run_checks(symbol_src)["checks"]}


def test_shell_surfaces_distinguish_start_xaml_from_public_win32():
    from awt import shellsurfaces
    source = """
// ==WindhawkMod==
// @id fake-start
// @include StartMenuExperienceHost.exe
// ==/WindhawkMod==
BOOL WINAPI Wh_ModInit() {
    HMODULE xaml = LoadLibraryW(L"Windows.UI.Xaml.dll");
    HMODULE dui = LoadLibraryW(L"dui.dll");
    FindWindowW(L"Shell_TrayWnd", NULL);
    TrackPopupMenu(nullptr, 0, 0, 0, 0, nullptr, nullptr);
    return TRUE;
}
"""
    meta = metadata.parse_metadata(source)
    result = shellsurfaces.analyze(source, meta)
    by_id = {s["id"]: s for s in result["surfaces"]}
    assert by_id["start_menu"]["coverage"] == "not_verifiable"
    assert by_id["start_menu"]["stand_in"] is None
    assert by_id["taskbar"]["coverage"] == "simulated"
    assert by_id["dark_mode_undocumented"]["coverage"] == "not_verifiable"
    assert result["summary"]["detected"] >= 4
    assert result["target_repercussion"]["suggested_stand_in"] is None
    assert any("symbols" in step for step in result["verification_plan"])


def test_shell_surface_detection_ignores_long_resource_blobs():
    from awt import shellsurfaces
    blob = "ZDUg" + "DUI" * 300
    source = ('const char* resource = "%s";\n'
              'BOOL WINAPI Wh_ModInit() { return TRUE; }\n') % blob
    result = shellsurfaces.analyze(source, metadata.parse_metadata(source))
    assert result["surfaces"] == [], result["surfaces"]


def test_multi_include_selects_explorer_stand_in_not_control_panel():
    from awt import targeting
    meta = {"include": ["explorer.exe", "control.exe"], "exclude": [], "raw": {}}
    sel = targeting.select(meta, {"subjects": ["system-tray"]})
    assert sel["suggested_stand_in"] == "trayapp.exe"
    assert "control.exe include is only probed" in sel["reason"]
    start = targeting.select({"include": ["StartMenuExperienceHost.exe"],
                              "exclude": [], "raw": {}}, {})
    assert start["suggested_stand_in"] is None
    assert "not_verifiable" in start["reason"]


def test_deprecated_utils_alias_and_real_windows_libraries_are_recognised():
    from awt import compileroptions, fidelity
    src = "WindhawkUtils::Wh_SetFunctionHookT(target, hook);"
    api = fidelity.api_inventory(src)["apis"][0]
    assert api["name"] == "WindhawkUtils::Wh_SetFunctionHookT"
    assert api["status"] == "shim_implemented"
    opts = compileroptions.classify(["-liphlpapi", "-lwlanapi", "-luuid"])
    assert opts["accepted"] == ["-liphlpapi", "-lwlanapi", "-luuid"]
    assert opts["unverified_libraries"] == []


def test_report_attaches_shell_surfaces_and_target_repercussion():
    from awt import builder, report
    path = os.path.join(REPO, "examples", "tray-icon-demo.wh.cpp")
    rep = report.new_report(builder.build(path),
                            {"public_hooks": [], "internal_hooks": [], "unknown_hooks": []})
    surfaces = rep["shell_surfaces"]
    assert surfaces["summary"]["detected"] >= 1
    assert any(s["id"] == "tray_icon" for s in surfaces["surfaces"])
    assert "shell surfaces" in report.summary(rep)


def test_shell_behaviour_capability_and_summary_never_claim_success():
    from awt import fidelity, report
    source = 'HMODULE h = LoadLibraryW(L"Windows.UI.Xaml.dll");'
    fake_build = {"ok": False, "mod_id": "x", "mod_path": None,
                  "meta": {"raw": {}, "include": []}, "errors": [], "warnings": [],
                  "settings": {}}
    fake_analysis = {"public_hooks": [], "internal_hooks": [], "unknown_hooks": []}
    rep = report.new_report(fake_build, fake_analysis)
    rep["verdict"].update({"level": "not_executed", "summary": ""})
    caps = fidelity.capabilities(rep, {}, {}, source, {})
    assert caps["items"]["shell_behaviour"]["status"] == "not_verifiable"
    assert "start_menu" in caps["items"]["shell_behaviour"]["evidence"]
    from awt import shellsurfaces
    rep["shell_surfaces"] = shellsurfaces.analyze(source)
    text = report.summary(rep)
    assert "real Windows   : unknown (nothing was executed here)" in text
    assert "shell surfaces :" in text


def test_summary_capability_count_follows_the_matrix_not_the_machine():
    from awt import report
    fake_build = {"ok": True, "mod_id": "x", "mod_path": None,
                  "meta": {"raw": {}, "include": []}, "errors": [], "warnings": [],
                  "settings": {}}
    rep = report.new_report(fake_build,
                            {"public_hooks": [], "internal_hooks": [], "unknown_hooks": []})
    assert rep["capabilities"]["items"]["compile"]["status"] == "verified"
    assert "verified 1" in report.summary(rep)
