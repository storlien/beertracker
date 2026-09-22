"""Unit tests for CLI Java Map parser fallback."""

from beertracker.cli import _parse_java_map


class TestParseJavaMap:
    def test_simple_map(self):
        content = "{4925562079=[37.0], 4106512455=[12.5, 8.0]}"
        result = _parse_java_map(content)
        assert result == {
            "4925562079": [37.0],
            "4106512455": [12.5, 8.0],
        }

    def test_single_entry(self):
        content = "{1234567890=[100.0]}"
        result = _parse_java_map(content)
        assert result == {"1234567890": [100.0]}

    def test_no_spaces(self):
        content = "{1234567890=[1.0,2.0],0987654321=[3.0]}"
        result = _parse_java_map(content)
        assert result == {
            "1234567890": [1.0, 2.0],
            "0987654321": [3.0],
        }

    def test_empty_map(self):
        content = "{}"
        result = _parse_java_map(content)
        assert result == {}
