"""Unit tests for fastermcp.resources.uri_template."""

from fastermcp.resources.uri_template import match_uri_template


def test_simple_variable_match():
    result = match_uri_template("res://items/42", "res://items/{id}")
    assert result == {"id": "42"}


def test_wildcard_variable_match():
    result = match_uri_template("res://files/a/b/c.txt", "res://files/{path*}")
    assert result == {"path": "a/b/c.txt"}


def test_multiple_variables():
    result = match_uri_template("res://users/alice/items/99", "res://users/{user}/items/{id}")
    assert result == {"user": "alice", "id": "99"}


def test_no_match_returns_none():
    assert match_uri_template("res://other/42", "res://items/{id}") is None


def test_partial_match_returns_none():
    assert match_uri_template("res://items/42/extra", "res://items/{id}") is None


def test_literal_only_template():
    assert match_uri_template("res://status", "res://status") == {}
    assert match_uri_template("res://other", "res://status") is None


def test_query_params_extracted():
    result = match_uri_template("res://items/42?format=json", "res://items/{id}{?format}")
    assert result == {"id": "42", "format": "json"}


def test_query_params_not_in_template_ignored():
    result = match_uri_template("res://items/42?extra=yes", "res://items/{id}")
    assert result == {"id": "42"}


def test_url_encoded_value_decoded():
    result = match_uri_template("res://items/hello%20world", "res://items/{name}")
    assert result == {"name": "hello world"}


def test_empty_segment_no_match():
    assert match_uri_template("res://items/", "res://items/{id}") is None


def test_invalid_regex_template_returns_none():
    # Hyphenated names produce invalid regex group names
    assert match_uri_template("res://x/1", "res://x/{bad-name}") is None
