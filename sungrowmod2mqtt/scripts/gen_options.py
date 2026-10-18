#!/usr/bin/env python3
"""Generate registers options/schema/translations from catalog.json.

Run from the sungrowmod2mqtt directory:

    python3 scripts/gen_options.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "src" / "catalog.json"
CONFIG_PATH = ROOT / "config.yaml"
TRANSLATIONS_PATH = ROOT / "translations" / "en.yaml"

BEGIN = "# BEGIN GENERATED REGISTERS"
END = "# END GENERATED REGISTERS"

CATEGORY_LABELS = {
    "backup": "Backup",
    "battery": "Battery",
    "clock": "Clock",
    "control": "Control",
    "ems": "EMS",
    "energy": "Energy",
    "export_limit": "Export limit",
    "faults": "Faults",
    "firmware": "Firmware",
    "grid": "Grid",
    "identity": "Identity",
    "load_control": "Load control",
    "meter": "Meter",
    "meter_ch2": "Meter channel 2",
    "mppt": "MPPT",
    "mppt3_4": "MPPT 3/4",
    "power": "Power",
    "status": "Status",
    "thermal": "Thermal",
}


def load_grouped() -> dict[str, list[dict]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in data["registers"]:
        category = str(item.get("category") or "other")
        grouped[category].append(item)
    return dict(sorted(grouped.items()))


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def render_options_block(grouped: dict[str, list[dict]]) -> str:
    lines = ["registers:"]
    for category, registers in grouped.items():
        lines.append(f"  {category}:")
        for reg in registers:
            default = "true" if reg.get("enabled_by_default") else "false"
            lines.append(f"    {reg['slug']}: {default}")
    return "\n".join(lines)


def render_schema_block(grouped: dict[str, list[dict]]) -> str:
    lines = ["registers:"]
    for category, registers in grouped.items():
        lines.append(f"  {category}:")
        for reg in registers:
            lines.append(f"    {reg['slug']}: bool?")
    return "\n".join(lines)


def render_translations_block(grouped: dict[str, list[dict]]) -> str:
    lines = [
        "registers:",
        "  name: Registers",
        "  description: >-",
        "    Choose which registers to poll from the inverter and publish to Home Assistant.",
        "    Turned-off registers are not read over Modbus and are not created as entities.",
        "    Defaults match the previous core sensor set.",
    ]
    for category, registers in grouped.items():
        label = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
        lines.append(f"  {category}:")
        lines.append(f"    name: {label}")
        for reg in registers:
            name = str(reg["name"])
            unit = str(reg.get("unit") or "").strip()
            field_label = f"{name} ({unit})" if unit else name
            # YAML-safe quoting when needed
            if any(ch in field_label for ch in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`")):
                field_label = json.dumps(field_label)
            lines.append(f"    {reg['slug']}:")
            lines.append(f"      name: {field_label}")
    return "\n".join(lines)


def replace_marked_section(text: str, replacement: str, *, indent: int) -> str:
    begin = BEGIN
    end = END
    if begin not in text or end not in text:
        raise RuntimeError(f"Missing {begin!r} / {end!r} markers")
    before, rest = text.split(begin, 1)
    _, after = rest.split(end, 1)
    body = _indent(replacement, indent)
    return f"{before}{begin}\n{body}\n{' ' * indent}{end}{after}"


def main() -> None:
    grouped = load_grouped()

    config = CONFIG_PATH.read_text(encoding="utf-8")
    # Options and schema each have their own marker pair — locate by context.
    options_marker_count = config.count(BEGIN)
    if options_marker_count != 2:
        raise RuntimeError(
            f"config.yaml must contain exactly two {BEGIN!r} markers "
            f"(options + schema); found {options_marker_count}"
        )

    first_begin = config.find(BEGIN)
    first_end = config.find(END, first_begin) + len(END)
    second_begin = config.find(BEGIN, first_end)
    second_end = config.find(END, second_begin) + len(END)

    options_indent = len(config[config.rfind("\n", 0, first_begin) + 1 : first_begin])
    schema_indent = len(config[config.rfind("\n", 0, second_begin) + 1 : second_begin])

    options_block = render_options_block(grouped)
    schema_block = render_schema_block(grouped)

    new_config = (
        config[:first_begin]
        + BEGIN
        + "\n"
        + _indent(options_block, options_indent)
        + "\n"
        + (" " * options_indent)
        + END
        + config[first_end:second_begin]
        + BEGIN
        + "\n"
        + _indent(schema_block, schema_indent)
        + "\n"
        + (" " * schema_indent)
        + END
        + config[second_end:]
    )
    CONFIG_PATH.write_text(new_config, encoding="utf-8")

    translations = TRANSLATIONS_PATH.read_text(encoding="utf-8")
    translations = replace_marked_section(
        translations,
        render_translations_block(grouped),
        indent=2,
    )
    TRANSLATIONS_PATH.write_text(translations, encoding="utf-8")
    print(f"Updated {CONFIG_PATH.relative_to(ROOT)} and {TRANSLATIONS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
