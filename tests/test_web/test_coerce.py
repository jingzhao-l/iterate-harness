"""Tests for the WebUI defensive coercion helpers (web/_coerce.py).

Covers numeric coercion (int/float) with non-numeric fallbacks and the list
guard, exercising normal values, malformed persisted data, and edge cases
(bools, floats, numeric strings, None).
"""

from __future__ import annotations

from iterate_harness.web._coerce import as_float, as_int, as_list


class TestAsInt:
    def test_integers_pass_through(self):
        assert as_int(5) == 5
        assert as_int(0) == 0
        assert as_int(-3) == -3

    def test_bool_is_rejected(self):
        assert as_int(True) == 0
        assert as_int(False) == 0

    def test_whole_float_is_converted(self):
        assert as_int(4.0) == 4

    def test_fractional_float_falls_back(self):
        assert as_int(4.5) == 0

    def test_numeric_string_is_parsed(self):
        assert as_int("42") == 42
        assert as_int(" 7 ") == 7

    def test_non_numeric_string_falls_back(self):
        assert as_int("abc") == 0

    def test_none_and_other_types_fall_back(self):
        assert as_int(None) == 0
        assert as_int([1, 2]) == 0
        assert as_int({"a": 1}) == 0

    def test_custom_fallback(self):
        assert as_int("nope", fallback=-1) == -1
        assert as_int(None, fallback=-1) == -1


class TestAsFloat:
    def test_numbers_pass_through(self):
        assert as_float(3.5) == 3.5
        assert as_float(2) == 2.0

    def test_bool_is_rejected(self):
        assert as_float(True) == 0.0
        assert as_float(False) == 0.0

    def test_numeric_string_is_parsed(self):
        assert as_float("1.25") == 1.25
        assert as_float(" 0.5 ") == 0.5

    def test_non_numeric_values_fall_back(self):
        assert as_float("oops") == 0.0
        assert as_float(None) == 0.0
        assert as_float(["x"]) == 0.0

    def test_custom_fallback(self):
        assert as_float("bad", fallback=1.0) == 1.0


class TestAsList:
    def test_list_is_returned(self):
        assert as_list([1, 2, 3]) == [1, 2, 3]

    def test_non_list_values_become_empty(self):
        assert as_list(None) == []
        assert as_list("abc") == []
        assert as_list({"a": 1}) == []
        assert as_list(5) == []
