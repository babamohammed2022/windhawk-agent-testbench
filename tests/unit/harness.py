"""Shared helpers for the Wine-free unit tests."""


class SkipTest(Exception):
    """Raised by a test that cannot run in this environment.

    A skip is reported as a skip: it never counts as a pass and never claims the
    behaviour under test works.
    """


def require(condition, reason):
    if not condition:
        raise SkipTest(reason)


def optional_import(name):
    try:
        return __import__(name)
    except ImportError:
        raise SkipTest("%s not installed in this interpreter" % name)
