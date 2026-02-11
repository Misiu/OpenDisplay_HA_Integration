"""Tests for the tag type sync scripts."""

import json
import os
import re
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path so we can import the modules
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import fetch_tag_types
import generate_tag_types


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TAG_TYPES_PY = textwrap.dedent("""\
    class Foo:
        def _load_fallback_types(self):
            fallback_definitions = {
                0: {"version": 4, "name": "M2 1.54\\"", "width": 152, "height": 152},
                1: {"version": 5, "name": "M2 2.9\\"", "width": 296, "height": 128},
                240: {"version": 2, "name": "SLT\u2010EM007 Segmented", "width": 0, "height": 0},
                250: {"version": 1, "name": "ConfigMode", "width": 0, "height": 0},
            }
            self._tag_types = {
                type_id: TagType(type_id, data) for type_id, data in fallback_definitions.items()
            }
""")


@pytest.fixture
def tag_types_file(tmp_path):
    """Write a minimal tag_types.py and return its path."""
    p = tmp_path / "tag_types.py"
    p.write_text(SAMPLE_TAG_TYPES_PY)
    return p


@pytest.fixture
def new_types_json(tmp_path):
    """Write a new_tag_types.json and return its path."""
    data = {
        0: {"version": 4, "name": 'M2 1.54"', "width": 152, "height": 152},
        1: {"version": 5, "name": 'M2 2.9"', "width": 296, "height": 128},
        240: {"version": 2, "name": "SLT\u2010EM007 Segmented", "width": 0, "height": 0},
        250: {"version": 1, "name": "ConfigMode", "width": 0, "height": 0},
    }
    p = tmp_path / "new_tag_types.json"
    p.write_text(json.dumps(data, indent=2))
    return p


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – load_new_tag_types
# ---------------------------------------------------------------------------

class TestLoadNewTagTypes:
    """Tests for loading and converting JSON tag types."""

    def test_keys_are_integers(self, new_types_json):
        """JSON string keys must be converted to integers."""
        result = generate_tag_types.load_new_tag_types(str(new_types_json))
        assert all(isinstance(k, int) for k in result.keys())

    def test_values_preserved(self, new_types_json):
        """Tag type data values must be preserved after loading."""
        result = generate_tag_types.load_new_tag_types(str(new_types_json))
        assert result[0]["name"] == 'M2 1.54"'
        assert result[250]["width"] == 0


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – parse_current_definitions
# ---------------------------------------------------------------------------

class TestParseCurrentDefinitions:
    """Tests for parsing fallback_definitions from tag_types.py."""

    def test_parses_all_entries(self, tag_types_file):
        """Should parse all entries from the fallback_definitions block."""
        content = tag_types_file.read_text()
        result = generate_tag_types.parse_current_definitions(content)
        assert len(result) == 4
        assert set(result.keys()) == {0, 1, 240, 250}

    def test_keys_are_integers(self, tag_types_file):
        """Parsed keys must be integers."""
        content = tag_types_file.read_text()
        result = generate_tag_types.parse_current_definitions(content)
        assert all(isinstance(k, int) for k in result.keys())

    def test_exits_on_missing_block(self):
        """Should exit if fallback_definitions block is not found."""
        with pytest.raises(SystemExit):
            generate_tag_types.parse_current_definitions("no such block here")


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – compute_changes
# ---------------------------------------------------------------------------

class TestComputeChanges:
    """Tests for computing diffs between current and new definitions."""

    def test_no_changes(self):
        """Identical data should produce no changes."""
        current = {
            0: '0: {"version": 4, "name": "Tag0", "width": 100, "height": 100},',
        }
        new = {0: {"version": 4, "name": "Tag0", "width": 100, "height": 100}}
        added, removed, modified = generate_tag_types.compute_changes(current, new)
        assert added == []
        assert removed == []
        assert modified == []

    def test_added(self):
        """New type IDs should be detected as added."""
        current = {}
        new = {5: {"version": 1, "name": "New", "width": 10, "height": 10}}
        added, removed, modified = generate_tag_types.compute_changes(current, new)
        assert added == [5]
        assert removed == []

    def test_removed(self):
        """Missing type IDs should be detected as removed."""
        current = {
            5: '5: {"version": 1, "name": "Old", "width": 10, "height": 10},',
        }
        new = {}
        added, removed, modified = generate_tag_types.compute_changes(current, new)
        assert removed == [5]
        assert added == []

    def test_modified(self):
        """Changed values should be detected as modified."""
        current = {
            0: '0: {"version": 1, "name": "Tag0", "width": 100, "height": 100},',
        }
        new = {0: {"version": 2, "name": "Tag0", "width": 100, "height": 100}}
        added, removed, modified = generate_tag_types.compute_changes(current, new)
        assert modified == [0]

    def test_sorting(self):
        """Results should be sorted numerically, not lexicographically."""
        current = {}
        new = {
            100: {"version": 1, "name": "A", "width": 1, "height": 1},
            2: {"version": 1, "name": "B", "width": 1, "height": 1},
            17: {"version": 1, "name": "C", "width": 1, "height": 1},
        }
        added, _, _ = generate_tag_types.compute_changes(current, new)
        assert added == [2, 17, 100]


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – generate_fallback_content
# ---------------------------------------------------------------------------

class TestGenerateFallbackContent:
    """Tests for generating the fallback_definitions dict content."""

    def test_format(self):
        """Each line should have 12-space indent, type_id, JSON data, and trailing comma."""
        data = {0: {"version": 1, "name": "Tag", "width": 10, "height": 20}}
        content = generate_tag_types.generate_fallback_content(data)
        assert content.startswith("            0:")
        assert content.endswith(",")

    def test_sorted_numerically(self):
        """Entries should be sorted by numeric type_id."""
        data = {
            100: {"version": 1, "name": "A", "width": 1, "height": 1},
            2: {"version": 1, "name": "B", "width": 1, "height": 1},
            17: {"version": 1, "name": "C", "width": 1, "height": 1},
        }
        content = generate_tag_types.generate_fallback_content(data)
        ids = [int(re.match(r"\s+(\d+):", line).group(1)) for line in content.split("\n")]
        assert ids == [2, 17, 100]

    def test_unicode_chars(self):
        """Unicode characters in names should be handled without errors."""
        data = {240: {"version": 2, "name": "SLT\u2010EM007", "width": 0, "height": 0}}
        content = generate_tag_types.generate_fallback_content(data)
        # Should contain the json-escaped unicode
        assert "\\u2010" in content or "\u2010" in content


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – update_tag_types_file
# ---------------------------------------------------------------------------

class TestUpdateTagTypesFile:
    """Tests for replacing fallback_definitions in file content."""

    def test_replaces_content(self, tag_types_file):
        """The fallback block should be replaced with new content."""
        content = tag_types_file.read_text()
        new_fallback = '            999: {"version": 1, "name": "New", "width": 1, "height": 1},'
        result = generate_tag_types.update_tag_types_file(content, new_fallback)
        assert "999:" in result
        # Old entries removed
        assert "250:" not in result

    def test_preserves_surrounding_code(self, tag_types_file):
        """Code around fallback_definitions should be unchanged."""
        content = tag_types_file.read_text()
        new_fallback = '            999: {"version": 1, "name": "New", "width": 1, "height": 1},'
        result = generate_tag_types.update_tag_types_file(content, new_fallback)
        assert "class Foo:" in result
        assert "self._tag_types" in result

    def test_unicode_in_replacement(self, tag_types_file):
        """Unicode escape sequences in replacement must not cause regex errors.

        This is the primary bug that was fixed: json.dumps() produces \\uXXXX
        sequences which re.sub() would interpret as bad regex escapes.
        """
        content = tag_types_file.read_text()
        # This would fail with re.sub() because \u2010 is a bad regex escape
        new_fallback = '            240: {"version": 2, "name": "SLT\\u2010EM007", "width": 0, "height": 0},'
        result = generate_tag_types.update_tag_types_file(content, new_fallback)
        assert "\\u2010" in result

    def test_exits_on_missing_block(self):
        """Should exit if fallback_definitions block is not found."""
        with pytest.raises(SystemExit):
            generate_tag_types.update_tag_types_file("no such block", "replacement")


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    """Tests for the human-readable change summary."""

    def test_empty_on_no_changes(self):
        assert generate_tag_types.build_summary([], [], []) == []

    def test_added(self):
        result = generate_tag_types.build_summary([1, 2], [], [])
        assert len(result) == 1
        assert "Added: 2" in result[0]

    def test_truncated(self):
        result = generate_tag_types.build_summary(list(range(10)), [], [])
        assert "..." in result[0]


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – set_github_output
# ---------------------------------------------------------------------------

class TestSetGithubOutput:
    """Tests for writing GitHub Actions outputs."""

    def test_writes_changed(self, tmp_path):
        output_file = tmp_path / "output.txt"
        output_file.write_text("")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):
            generate_tag_types.set_github_output(True, ["Added: 1 types (5)"])
        content = output_file.read_text()
        assert "changed=true" in content
        assert "summary=" in content

    def test_no_op_without_env(self, tmp_path):
        """Should not crash when GITHUB_OUTPUT is not set."""
        with patch.dict(os.environ, {}, clear=True):
            generate_tag_types.set_github_output(False, [])  # should not raise


# ---------------------------------------------------------------------------
# Tests for generate_tag_types – full main() integration
# ---------------------------------------------------------------------------

class TestMainIntegration:
    """Integration tests for the full generate_tag_types.main() flow."""

    def test_no_change_run(self, tag_types_file, new_types_json, tmp_path):
        """When data matches, output changed=false."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("")
        with patch.object(generate_tag_types, "TAG_TYPES_PATH", str(tag_types_file)), \
             patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
             patch("sys.argv", ["prog", str(new_types_json)]):
            generate_tag_types.main()
        assert "changed=false" in output_file.read_text()

    def test_added_type_run(self, tag_types_file, tmp_path):
        """When a new type is added, output changed=true and file is updated."""
        data = {
            0: {"version": 4, "name": 'M2 1.54"', "width": 152, "height": 152},
            1: {"version": 5, "name": 'M2 2.9"', "width": 296, "height": 128},
            240: {"version": 2, "name": "SLT\u2010EM007 Segmented", "width": 0, "height": 0},
            250: {"version": 1, "name": "ConfigMode", "width": 0, "height": 0},
            999: {"version": 1, "name": "Brand New", "width": 100, "height": 200},
        }
        json_file = tmp_path / "new.json"
        json_file.write_text(json.dumps(data, indent=2))

        output_file = tmp_path / "output.txt"
        output_file.write_text("")
        with patch.object(generate_tag_types, "TAG_TYPES_PATH", str(tag_types_file)), \
             patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
             patch("sys.argv", ["prog", str(json_file)]):
            generate_tag_types.main()
        assert "changed=true" in output_file.read_text()
        updated = tag_types_file.read_text()
        assert "999:" in updated
        assert "Brand New" in updated


# ---------------------------------------------------------------------------
# Tests for fetch_tag_types
# ---------------------------------------------------------------------------

class TestFetchTagTypes:
    """Tests for the fetch_tag_types module."""

    def test_fetch_file_list(self):
        """fetch_file_list should parse JSON filenames from HTML."""
        fake_html = '<a href="00.json">00.json</a> <a href="0A.json">0A.json</a> other.txt'
        mock_response = MagicMock()
        mock_response.read.return_value = fake_html.encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = fetch_tag_types.fetch_file_list()
        assert result == ["00.json", "0A.json"]

    def test_fetch_tag_types_parses_hex_ids(self):
        """Filenames should be converted from hex to decimal type IDs."""
        fake_json = json.dumps({
            "version": 1, "name": "Test", "width": 100, "height": 50
        }).encode("utf-8")

        mock_response = MagicMock()
        mock_response.read.return_value = fake_json
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = fetch_tag_types.fetch_tag_types(["0A.json"])
        # 0x0A = 10
        assert 10 in result
        assert result[10]["name"] == "Test"

    def test_fetch_tag_types_handles_errors(self):
        """Errors fetching individual files should not crash the whole run."""
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            result = fetch_tag_types.fetch_tag_types(["00.json"])
        assert result == {}

    def test_main_writes_json(self, tmp_path):
        """main() should write fetched data to the output JSON file."""
        output = tmp_path / "out.json"

        with patch.object(fetch_tag_types, "fetch_file_list", return_value=["01.json"]), \
             patch.object(fetch_tag_types, "fetch_tag_types", return_value={
                 1: {"version": 1, "name": "X", "width": 10, "height": 10}
             }), \
             patch("sys.argv", ["prog", str(output)]):
            fetch_tag_types.main()

        data = json.loads(output.read_text())
        assert "1" in data  # JSON keys are strings
        assert data["1"]["name"] == "X"
