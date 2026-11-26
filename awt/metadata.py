"""Parse the Windhawk metadata block and the YAML settings block of a .wh.cpp mod.

The metadata block is a list of `// @tag value` lines. Single-value tags are
exposed in ``raw``; repeated tags (``@include``, ``@exclude``,
``@compilerOptions``) keep their order in dedicated lists. Localised tags such
as ``@name:it-IT`` are collected in ``localizations`` and also kept verbatim in
``raw`` so callers can see exactly what the source said.

The settings block is Windhawk's YAML subset. It is parsed into a tree first;
``parse_settings`` then flattens it to the dotted/indexed keys understood by
the shim, e.g. ``items[0].name``. Display keys (``$name``, ``$description``,
``$options``, ...) are metadata, not values, and are never returned.
"""
import json
import re

from . import yamlmini

META_RE = re.compile(r"//\s*==WindhawkMod==\s*\r?\n(.*?)//\s*==/WindhawkMod==",
                     re.S)
SETTINGS_RE = re.compile(
    r"//\s*==WindhawkModSettings==\s*\r?\n/\*\r?\n(.*?)\r?\n\*/\r?\n"
    r"//\s*==/WindhawkModSettings==", re.S)
TAG_RE = re.compile(r"^\s*//\s*@([\w:.\-]+)\s*(.*)$")
LIST_TAGS = ("include", "exclude", "compilerOptions")
LOCALIZED_TAG_RE = re.compile(r"^([\w.\-]+):([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)$")


def _tag_value(text):
    """Split a tag value into tokens, honouring quotes.

    Backslashes are kept literally: Windows paths such as
    ``C:\\Windows\\System32\\notepad.exe`` must not be interpreted as shell
    escapes. A bare ``#`` starts a comment when outside quotes.
    """
    out, quote = [], None
    for token in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', text):
        if quote:
            out[-1] += " " + token
            if token.endswith(quote):
                out[-1] = out[-1][1:-1]
                quote = None
            continue
        if token.startswith("#") and token[0] == "#":
            break
        if token[:1] in ('"', "'") and len(token) == 1:
            quote = token[0]
            out.append(token)
            continue
        if token[:1] in ('"', "'") and token[-1:] == token[:1]:
            token = token[1:-1]
        out.append(token)
    return out


def parse_metadata(source: str) -> dict:
    meta = {"include": [], "exclude": [], "compilerOptions": [], "raw": {},
            "localizations": {}, "warnings": []}
    m = META_RE.search(source)
    if not m:
        meta["include"] = ["*"]
        return meta
    for line in m.group(1).splitlines():
        mm = TAG_RE.match(line)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        if key in LIST_TAGS:
            meta[key].extend(_tag_value(val))
            continue
        if key in meta["raw"]:
            meta["warnings"].append("repeated @%s tag; the last value was used" % key)
        meta["raw"][key] = val
        loc = LOCALIZED_TAG_RE.match(key)
        if loc:
            base, locale = loc.group(1), loc.group(2)
            meta["localizations"].setdefault(locale, {})[base] = val
    if not meta["include"]:
        meta["include"] = ["*"]
    return meta


def _yaml_scalar(text: str):
    """Legacy flat-value fallback; used only if the YAML subset parser fails."""
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text in ("true", "false"):
        return 1 if text == "true" else 0
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _legacy_settings(body: str):
    out = {}
    current = None
    for line in body.splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+([\w.\-]+)\s*:\s*(.*)$", line)
        sub = re.match(r"^\s+(\$\w+)\s*:\s*(.*)$", line)
        if item:
            current = item.group(1)
            out[current] = _yaml_scalar(item.group(2))
        elif sub:
            continue  # display metadata
        elif line.startswith("  ") and current:
            continue  # nested option metadata
    return out


class _SettingsParser(yamlmini._Parser):
    """Allow YAML's indentless sequences under a key, as used in mod settings.

    `- items:` followed by `  - size: 4` puts the child dash at the same
    indentation as the parent key. The shared configuration reader requires a
    deeper indent; settings blocks use this shape, so handle it here only.
    """

    def _parse_inline_map_first(self, key_indent, rest, lineno):
        key, sep, val = yamlmini._split_key(rest, lineno)
        nxt = self.peek()
        if (not val and nxt and nxt[1] == key_indent and
                (nxt[2].startswith("- ") or nxt[2] == "-")):
            return {key: self.parse_seq(key_indent)}
        return super()._parse_inline_map_first(key_indent, rest, lineno)


def _parse_settings_yaml(body: str):
    tokens = yamlmini._tokenize(body)
    if not tokens:
        return []
    parser = _SettingsParser(tokens)
    value = parser.parse_block(tokens[0][1])
    if parser.pos != len(tokens):
        raise yamlmini.YamlMiniError("line %d: could not parse '%s'" % (
            tokens[parser.pos][0], tokens[parser.pos][2][:60]))
    return [] if value is None else value


def parse_settings_tree(source: str):
    """The settings block as a list of mapping items, with display keys removed.

    Returns ``(tree, warnings)``. No block yields ``([], [])``; a block that the
    small YAML reader cannot parse falls back to the legacy flat reader and
    reports that fact instead of guessing.
    """
    m = SETTINGS_RE.search(source)
    if not m:
        return [], []
    body = m.group(1)
    try:
        tree = _parse_settings_yaml(body)
    except yamlmini.YamlMiniError as exc:
        flat = _legacy_settings(body)
        return ([{k: v} for k, v in flat.items()],
                ["settings YAML could not be parsed fully (%s); used the flat "
                 "fallback" % exc])
    if isinstance(tree, dict):
        tree = [tree]
    if not isinstance(tree, list):
        return [], ["settings YAML is neither a list nor a mapping; no defaults parsed"]
    clean = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        clean.append({k: v for k, v in item.items() if not k.startswith("$")})
    return clean, []


def _flatten(value, path, out):
    """Flatten a setting value into shim-addressable scalar paths."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).startswith("$"):
                continue
            _flatten(child, "%s.%s" % (path, key) if path else str(key), out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _flatten(child, "%s[%d]" % (path, index), out)
    elif value is None:
        out[path] = ""
    elif isinstance(value, bool):
        out[path] = 1 if value else 0
    else:
        out[path] = value


def parse_settings_ex(source: str):
    """Return ``(flat_defaults, tree, warnings)`` for a mod's settings block."""
    tree, warnings = parse_settings_tree(source)
    out = {}
    for item in tree:
        for key, value in item.items():
            # `$options` is display metadata; plain `options` is a list of
            # select choices, not a setting value addressed by the mod.
            if key == "options" and isinstance(value, (list, dict)):
                continue
            _flatten(value, key, out)
    return out, tree, warnings


def parse_settings(source: str) -> dict:
    """Defaults of the settings block, flat: name -> scalar default value."""
    flat, _tree, _warnings = parse_settings_ex(source)
    return flat


def settings_json(settings: dict) -> str:
    return json.dumps(settings, indent=1, ensure_ascii=False)
