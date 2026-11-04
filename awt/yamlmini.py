"""Dependency-free YAML *subset* reader for this bench's configuration files.

The repository deliberately has no mandatory Python dependencies, so the matrix,
threshold and refshot configuration files are read with this small parser. It
supports exactly what those files use:

* block mappings and block sequences, nesting by indentation (spaces only);
* `key:` / `key: value`, `- value`, `- key: value` (sequence of mappings);
* flow sequences `[a, b]` and flow mappings `{a: 1}` on a single line;
* scalars: int (dec/hex `0x..`), float, `true`/`false`/`null`/`~`, single and
  (YAML 1.2: `yes`/`no`/`on`/`off` stay *strings*, as in PyYAML's safe_load of
  a `!!str` tag -- write `true`/`false` explicitly)
  double quoted strings, plain strings;
* `#` comments and blank lines; document separators `---` (ignored).

Not supported (a `YamlMiniError` is raised rather than guessing): block scalars
(`|`, `>`), anchors/aliases, multi-line flow collections, tabs for indentation.

When PyYAML *is* installed, `load()` still uses this parser (so behaviour does
not depend on the machine), but `tests/unit/test_yamlmini.py` cross-checks both
parsers on the shipped configuration files.
"""
import re

__all__ = ["load", "loads", "YamlMiniError", "dump"]


class YamlMiniError(ValueError):
    pass


_INT = re.compile(r"[-+]?\d+$")
_HEX = re.compile(r"[-+]?0[xX][0-9a-fA-F]+$")
_FLOAT = re.compile(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _parse_scalar(text: str):
    text = text.strip()
    if not text:
        return None
    if text[0] in "\"'" and text[-1] == text[0] and len(text) >= 2:
        body = text[1:-1]
        if text[0] == '"':
            body = (body.replace("\\n", "\n").replace("\\t", "\t")
                        .replace('\\"', '"').replace("\\\\", "\\"))
        return body
    if text.startswith("[") and text.endswith("]"):
        return _parse_flow_seq(text[1:-1])
    if text.startswith("{") and text.endswith("}"):
        return _parse_flow_map(text[1:-1])
    low = text.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _HEX.match(text):
        return int(text, 16)
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text) and any(c in text for c in ".eE"):
        return float(text)
    if text[0] in ("|", ">"):
        raise YamlMiniError("block scalars ('%s') are not supported by yamlmini" % text[0])
    return text


def _split_flow(body: str):
    """Split a flow collection on commas, respecting quotes and nested brackets."""
    out, depth, quote, cur = [], 0, None, []
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [o.strip() for o in out]


def _parse_flow_seq(body: str):
    return [_parse_scalar(p) for p in _split_flow(body) if p.strip()]


def _parse_flow_map(body: str):
    out = {}
    for part in _split_flow(body):
        if not part.strip():
            continue
        key, _, val = part.partition(":")
        out[_parse_scalar(key)] = _parse_scalar(val)
    return out


def _strip_comment(line: str) -> str:
    """Remove a trailing `#` comment that is not inside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _tokenize(text: str):
    tokens = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        leading = raw[:len(raw) - len(raw.lstrip())]
        if "\t" in leading:
            raise YamlMiniError("line %d: tabs cannot be used for indentation" % lineno)
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if line.strip() in ("---", "..."):
            continue
        indent = len(line) - len(line.lstrip(" "))
        tokens.append((lineno, indent, line.strip()))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse_block(self, indent):
        nxt = self.peek()
        if nxt is None or nxt[1] < indent:
            return None
        if nxt[2].startswith("- ") or nxt[2] == "-":
            return self.parse_seq(indent)
        return self.parse_map(indent)

    def parse_seq(self, indent):
        out = []
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] < indent:
                break
            lineno, cur_indent, text = nxt
            if cur_indent > indent:
                raise YamlMiniError("line %d: unexpected indentation in sequence" % lineno)
            if not (text.startswith("- ") or text == "-"):
                break
            self.pos += 1
            rest = text[2:].strip() if text.startswith("- ") else ""
            if not rest:
                child_indent = self._child_indent(indent)
                out.append(self.parse_block(child_indent) if child_indent is not None else None)
                continue
            if _is_key(rest):
                # sequence of mappings: "- key: value" starts a nested mapping
                key_indent = cur_indent + (len(text) - len(text[2:].lstrip()))
                item = self._parse_inline_map_first(key_indent, rest, lineno)
                item.update(self._continue_map(key_indent))
                out.append(item)
            else:
                out.append(_parse_scalar(rest))
        return out

    def _child_indent(self, parent_indent):
        nxt = self.peek()
        if nxt is None or nxt[1] <= parent_indent:
            return None
        return nxt[1]

    def _parse_inline_map_first(self, key_indent, rest, lineno):
        key, _, val = _split_key(rest, lineno)
        if val == "":
            child_indent = self._child_indent(key_indent)
            if child_indent is not None and child_indent > key_indent:
                return {key: self.parse_block(child_indent)}
            return {key: None}
        return {key: _parse_scalar(val)}

    def _continue_map(self, indent):
        out = {}
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] != indent or not _is_key(nxt[2]):
                break
            lineno, _, text = nxt
            self.pos += 1
            item = self._parse_inline_map_first(indent, text, lineno)
            out.update(item)
        return out

    def parse_map(self, indent):
        out = {}
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] < indent:
                break
            lineno, cur_indent, text = nxt
            if cur_indent > indent:
                raise YamlMiniError("line %d: unexpected indentation in mapping" % lineno)
            if not _is_key(text):
                break
            self.pos += 1
            out.update(self._parse_inline_map_first(indent, text, lineno))
        return out


def _is_key(text: str) -> bool:
    if text.startswith("- "):
        return False
    key, sep, _ = _split_key(text, 0)
    return bool(sep) and key != ""


def _split_key(text: str, lineno):
    """Split 'key: value' honouring quotes; returns (key, sep, value)."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(text) or text[i + 1] in " \t"):
            return _parse_scalar(text[:i]), ":", text[i + 1:].strip()
    return text, "", ""


def loads(text: str):
    tokens = _tokenize(text)
    if not tokens:
        return {}
    parser = _Parser(tokens)
    base = tokens[0][1]
    value = parser.parse_block(base)
    if parser.pos != len(tokens):
        lineno = tokens[parser.pos][0]
        raise YamlMiniError("line %d: could not parse '%s'" % (lineno, tokens[parser.pos][2][:60]))
    return {} if value is None else value


def load(path: str):
    with open(path, encoding="utf-8") as f:
        return loads(f.read())


def _dump_value(value, indent, out):
    pad = " " * indent
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                out.append("%s%s:" % (pad, k))
                _dump_value(v, indent + 2, out)
            else:
                out.append("%s%s: %s" % (pad, k, _scalar_text(v)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                first = True
                for k, v in item.items():
                    prefix = pad + "- " if first else pad + "  "
                    first = False
                    if isinstance(v, (dict, list)) and v:
                        out.append("%s%s:" % (prefix, k))
                        _dump_value(v, indent + 4, out)
                    else:
                        out.append("%s%s: %s" % (prefix, k, _scalar_text(v)))
            elif isinstance(item, list) and item:
                out.append(pad + "-")
                _dump_value(item, indent + 2, out)
            else:
                out.append("%s- %s" % (pad, _scalar_text(item)))


def _scalar_text(value):
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text == "" or re.search(r"[:#\[\]{},]|^\s|\s$", text):
        return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')
    return text


def dump(value) -> str:
    out = []
    _dump_value(value, 0, out)
    return "\n".join(out) + "\n"
