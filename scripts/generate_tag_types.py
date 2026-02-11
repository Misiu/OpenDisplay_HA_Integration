#!/usr/bin/env python3
"""Generate updated tag_types.py from fetched tag type definitions.

Reads a JSON file of tag type definitions (produced by fetch_tag_types.py),
compares them against the current fallback definitions in tag_types.py,
and updates the file if there are changes.

Sets GitHub Actions outputs for downstream workflow steps.
"""

import json
import os
import re
import sys


TAG_TYPES_PATH = "custom_components/opendisplay/tag_types.py"
FALLBACK_PATTERN = re.compile(
    r"(        fallback_definitions = \{)\n(.*?)\n(        \})", re.DOTALL
)
ENTRY_PATTERN = re.compile(r"\s+(\d+):")


def load_new_tag_types(input_file):
    """Load new tag types from JSON, converting keys to integers."""
    with open(input_file, "r") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def parse_current_definitions(content):
    """Extract current fallback definitions from tag_types.py content."""
    match = FALLBACK_PATTERN.search(content)
    if not match:
        print("Error: Could not find fallback_definitions in tag_types.py")
        sys.exit(1)

    current_types = {}
    for line in match.group(2).split("\n"):
        m = ENTRY_PATTERN.match(line)
        if m:
            type_id = int(m.group(1))
            current_types[type_id] = line.strip()

    return current_types


def compute_changes(current_types, new_tag_types):
    """Compute added, removed, and modified tag types."""
    added = []
    removed = []
    modified = []

    for type_id in sorted(new_tag_types.keys()):
        if type_id not in current_types:
            added.append(type_id)
        else:
            new_line = f"{type_id}: {json.dumps(new_tag_types[type_id], ensure_ascii=False)},"
            if new_line != current_types[type_id]:
                modified.append(type_id)

    for type_id in sorted(current_types.keys()):
        if type_id not in new_tag_types:
            removed.append(type_id)

    return added, removed, modified


def generate_fallback_content(new_tag_types):
    """Generate the new fallback_definitions dict content."""
    lines = []
    for type_id in sorted(new_tag_types.keys()):
        type_data = new_tag_types[type_id]
        line = f"            {type_id}: {json.dumps(type_data, ensure_ascii=False)},"
        lines.append(line)
    return "\n".join(lines)


def update_tag_types_file(content, new_fallback):
    """Replace fallback_definitions content in tag_types.py."""
    match = FALLBACK_PATTERN.search(content)
    if not match:
        print("Error: Could not find fallback_definitions in tag_types.py")
        sys.exit(1)

    start = match.start(2)
    end = match.end(2)
    return content[:start] + new_fallback + content[end:]


def build_summary(added, removed, modified):
    """Build a human-readable summary of changes."""
    summary = []
    if added:
        ids = ", ".join(map(str, added[:5]))
        suffix = "..." if len(added) > 5 else ""
        summary.append(f"Added: {len(added)} types ({ids}{suffix})")
    if removed:
        ids = ", ".join(map(str, removed[:5]))
        suffix = "..." if len(removed) > 5 else ""
        summary.append(f"Removed: {len(removed)} types ({ids}{suffix})")
    if modified:
        ids = ", ".join(map(str, modified[:5]))
        suffix = "..." if len(modified) > 5 else ""
        summary.append(f"Modified: {len(modified)} types ({ids}{suffix})")
    return summary


def set_github_output(changed, summary):
    """Set GitHub Actions step outputs."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return

    with open(github_output, "a") as f:
        f.write(f"changed={'true' if changed else 'false'}\n")
        if summary:
            f.write(f"summary={'|'.join(summary)}\n")


def main():
    """Generate updated tag_types.py from fetched definitions."""
    input_file = sys.argv[1] if len(sys.argv) > 1 else "new_tag_types.json"

    new_tag_types = load_new_tag_types(input_file)

    with open(TAG_TYPES_PATH, "r") as f:
        content = f.read()

    current_types = parse_current_definitions(content)

    print(f"Current definitions: {len(current_types)} types")
    print(f"New definitions: {len(new_tag_types)} types")

    added, removed, modified = compute_changes(current_types, new_tag_types)
    changed = bool(added or removed or modified)

    new_fallback = generate_fallback_content(new_tag_types)
    new_content = update_tag_types_file(content, new_fallback)

    with open(TAG_TYPES_PATH, "w") as f:
        f.write(new_content)

    summary = build_summary(added, removed, modified)

    if changed:
        print("CHANGED=true")
        print(f"SUMMARY={'|'.join(summary)}")
    else:
        print("CHANGED=false")
        print("No changes detected")

    set_github_output(changed, summary)


if __name__ == "__main__":
    main()
