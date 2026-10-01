"""Unit tests for rapidmcp.resources.uri_template."""

from rapidmcp.resources.uri_template import match_uri_template


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


def test_encoded_slash_cannot_smuggle_extra_segments_into_a_single_variable():
    assert match_uri_template("res://files/..%2F..%2Fsecret", "res://files/{name}") is None


def test_encoded_slash_is_allowed_in_a_wildcard_variable():
    result = match_uri_template("res://files/a%2Fb.txt", "res://files/{path*}")
    assert result == {"path": "a/b.txt"}


def test_other_percent_escapes_are_still_decoded():
    result = match_uri_template("res://files/my%20file.txt", "res://files/{name}")
    assert result == {"name": "my file.txt"}


def test_encoded_backslash_cannot_smuggle_segments_into_a_single_variable():
    assert match_uri_template("res://files/..%5C..%5Csecret", "res://files/{name}") is None


def test_dot_segments_are_not_a_valid_single_variable():
    assert match_uri_template("res://files/..", "res://files/{name}") is None
    assert match_uri_template("res://files/.", "res://files/{name}") is None


def test_parent_segments_are_rejected_in_a_wildcard_variable():
    assert match_uri_template("res://files/a/../../etc", "res://files/{path*}") is None
    assert match_uri_template("res://files/a%2F..%2Fb", "res://files/{path*}") is None


def test_null_bytes_are_rejected():
    assert match_uri_template("res://files/a%00.txt", "res://files/{name}") is None


def test_names_that_merely_contain_dots_still_match():
    assert match_uri_template("res://files/notes..final.txt", "res://files/{name}") == {
        "name": "notes..final.txt"
    }
