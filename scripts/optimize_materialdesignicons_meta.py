"""Optimize the Material Design Icons metadata for fast runtime lookup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "opendisplay"
    / "imagegen"
    / "assets"
    / "materialdesignicons-webfont.meta.json"
)
DEFAULT_SOURCE_PATH = DEFAULT_OUTPUT_PATH


def _optimize_meta(raw_meta: list[dict[str, object]] | dict[str, object]) -> dict[str, str]:
    """Normalize metadata into a flat name/alias -> codepoint mapping."""
    optimized: dict[str, str] = {}

    if isinstance(raw_meta, dict):
        for name, codepoint in raw_meta.items():
            if isinstance(name, str) and isinstance(codepoint, str):
                optimized[name] = codepoint
        return dict(sorted(optimized.items()))

    for entry in raw_meta:
        name = entry.get("name")
        codepoint = entry.get("codepoint")
        if not isinstance(name, str) or not isinstance(codepoint, str):
            continue

        optimized[name] = codepoint
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            continue

        for alias in aliases:
            if isinstance(alias, str):
                optimized.setdefault(alias, codepoint)

    return dict(sorted(optimized.items()))


def _load_source_json(source_path: Path) -> list[dict[str, object]] | dict[str, object]:
    """Read and parse the source metadata JSON from disk."""
    try:
        raw_json = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as err:
        msg = f"Failed to read source metadata file: {err}"
        raise RuntimeError(msg) from err
    except json.JSONDecodeError as err:
        msg = f"Failed to parse source metadata JSON: {err}"
        raise RuntimeError(msg) from err

    if not isinstance(raw_json, (list, dict)):
        msg = "Source JSON must be a list or dict"
        raise ValueError(msg)

    if isinstance(raw_json, dict):
        return raw_json

    entries: list[dict[str, object]] = []
    for item in raw_json:
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for metadata optimization."""
    parser = argparse.ArgumentParser(description="Optimize local Material Design Icons metadata.")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_PATH,
        help="Source metadata JSON path (defaults to in-place optimization).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output path for the optimized metadata JSON.",
    )
    return parser


def main() -> None:
    """Run the metadata optimization workflow."""
    parser = _build_argument_parser()
    args = parser.parse_args()

    entries = _load_source_json(args.source)
    optimized = _optimize_meta(entries)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(optimized, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {len(optimized)} names/aliases to {args.output}")


if __name__ == "__main__":
    main()
