"""Parse the Windhawk metadata block and the YAML settings block of a .wh.cpp mod."""
import json
import re


def parse_metadata(source: str) -> dict:
    meta = {"include": [], "exclude": [], "compilerOptions": [], "raw": {}}
    m = re.search(r"//\s*==WindhawkMod==\s*\n(.*?)//\s*==/WindhawkMod==\s*\n", source, re.S)
    if not m:
        return meta
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*//\s*@(\w+)\s*(.*)$", line)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        if key == "include":
            meta["include"].append(val)
        elif key == "exclude":
            meta["exclude"].append(val)
        elif key == "compilerOptions":
            meta["compilerOptions"] += val.split()
        else:
            meta["raw"][key] = val
    if not meta["include"]:
        meta["include"] = ["*"]
    return meta


def _yaml_scalar(text: str):
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text in ("true", "false"):
        return 1 if text == "true" else 0
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse_settings(source: str) -> dict:
    """Defaults of the settings block, flat: name -> default value."""
    out = {}
    m = re.search(r"//\s*==WindhawkModSettings==\s*\n/\*\n(.*?)\n\*/\n//\s*==/WindhawkModSettings==", source, re.S)
    if not m:
        return out
    current = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+([\w\.\-]+)\s*:\s*(.*)$", line)
        sub = re.match(r"^\s+\$?(\w+)\s*:\s*(.*)$", line)
        if item:
            current = item.group(1)
            out[current] = _yaml_scalar(item.group(2))
        elif sub and sub.group(1).startswith("$"):
            continue  # display metadata ($name, $description, ...)
        elif sub and current:
            continue  # nested option metadata of a select setting
    return out


def settings_json(settings: dict) -> str:
    return json.dumps(settings, indent=1)
