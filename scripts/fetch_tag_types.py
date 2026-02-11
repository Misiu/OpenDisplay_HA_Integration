#!/usr/bin/env python3
"""Fetch tag type definitions from the OpenEPaperLink repository.

Downloads all tag type JSON files from the OpenEPaperLink GitHub repository
and saves them as a consolidated JSON file for further processing.
"""

import json
import re
import sys
import urllib.request


GITHUB_TREE_URL = (
    "https://github.com/OpenEPaperLink/OpenEPaperLink/tree/master/resources/tagtypes"
)
GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/OpenEPaperLink/OpenEPaperLink"
    "/master/resources/tagtypes"
)


def fetch_file_list():
    """Fetch the list of tag type JSON files from the repository."""
    print("Fetching tag type files from OpenEPaperLink repository...")
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(GITHUB_TREE_URL, headers=headers)

    with urllib.request.urlopen(req, timeout=30) as response:
        html = response.read().decode("utf-8")
        json_files = re.findall(r"([0-9a-fA-F]+\.json)", html)
        json_files = sorted(set(json_files))
        print(f"Found {len(json_files)} tag type files")
        return json_files


def fetch_tag_types(json_files):
    """Fetch and parse all tag type definitions."""
    tag_types = {}
    errors = []

    for filename in json_files:
        url = f"{GITHUB_RAW_URL}/{filename}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                type_id = int(filename.replace(".json", ""), 16)

                tag_types[type_id] = {
                    "version": data.get("version"),
                    "name": data.get("name"),
                    "width": data.get("width"),
                    "height": data.get("height"),
                }
        except Exception as e:
            errors.append(f"Error fetching {filename}: {e}")

    if errors:
        for error in errors:
            print(error)

    print(f"Successfully fetched {len(tag_types)} tag type definitions")
    return tag_types


def main():
    """Fetch tag type definitions and save to a JSON file."""
    output_file = sys.argv[1] if len(sys.argv) > 1 else "new_tag_types.json"

    try:
        json_files = fetch_file_list()
    except Exception as e:
        print(f"Error fetching file list: {e}")
        sys.exit(1)

    tag_types = fetch_tag_types(json_files)

    with open(output_file, "w") as f:
        json.dump(tag_types, f, indent=2)

    print(f"Tag types saved to {output_file}")


if __name__ == "__main__":
    main()
