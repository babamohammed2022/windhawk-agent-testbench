#!/usr/bin/env python3
"""Unit-test runner for the parts of the bench that do not need Wine.

No pytest, no third-party dependency: every `tests/unit/test_*.py` module exposes
`test_*` functions; a test raises `SkipTest` to opt out (for example when an
optional accelerator such as numpy or PyYAML is absent).

    python3 tests/unit/run_unit.py            # run everything
    python3 tests/unit/run_unit.py -v         # per-test lines
    python3 tests/unit/run_unit.py test_pdb   # only modules matching a substring

Exit code 0 = all executed tests passed. Skips are reported, never counted as
pass, and a skipped test never claims the behaviour works.
"""
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)


from tests.unit.harness import SkipTest  # noqa: E402  (REPO is on sys.path above)


def _load_module(path):
    name = "awt_unit_%s" % os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = "-v" in argv or "--verbose" in argv
    filters = [a for a in argv if not a.startswith("-")]

    passed, failed, skipped = 0, 0, []
    failures = []
    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    for fname in files:
        if filters and not any(f in fname for f in filters):
            continue
        module = _load_module(os.path.join(HERE, fname))
        names = sorted(n for n in dir(module) if n.startswith("test_"))
        if verbose and names:
            print("-- %s" % fname)
        for name in names:
            fn = getattr(module, name)
            if not callable(fn):
                continue
            label = "%s::%s" % (fname, name)
            try:
                fn()
            except SkipTest as exc:
                skipped.append((label, str(exc)))
                if verbose:
                    print("SKIP %s (%s)" % (label, exc))
                continue
            except Exception:
                failed += 1
                failures.append((label, traceback.format_exc()))
                if verbose:
                    print("FAIL %s" % label)
            else:
                passed += 1
                if verbose:
                    print("ok   %s" % label)

    for label, tb in failures:
        print("=== FAIL %s\n%s" % (label, tb))
    for label, reason in skipped:
        print("SKIP %s: %s" % (label, reason))
    print("---- unit: %d passed, %d failed, %d skipped" % (passed, failed, len(skipped)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
