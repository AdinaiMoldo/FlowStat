"""Which host a URL names, and which hosts may be called (US-39, docs/dsl-spec.md section 5.5).

The same function decides for the check at save (a constant URL) and for the call (the URL after
the arguments were rendered), so the two cannot disagree about what a host is.
"""

from __future__ import annotations

import pytest

from flowstat.model.hosts import host_of, leading_host, parse_hosts


def test_no_list_is_none_and_a_list_is_a_set_of_lower_case_names() -> None:
    assert parse_hosts(None) is None and parse_hosts("") is None and parse_hosts(" , ,") is None
    assert parse_hosts("Api.Example.KG, stat.gov.kg ,api.example.kg.") == frozenset(
        {"api.example.kg", "stat.gov.kg"}
    )


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://api.example.kg/v1/x", "api.example.kg"),
        ("HTTP://API.Example.kg:8080/x?y=1", "api.example.kg"),  # case and port do not matter
        ("https://api.example.kg./x", "api.example.kg"),  # the root dot
        ("https://user:pw@api.example.kg/x", "api.example.kg"),  # who is called, not who logs in
        ("https://api.example.kg@evil.example/x", "evil.example"),  # the usual trick
        ("http://[::1]:9/x", "::1"),
        ("http://127.0.0.1:9100", "127.0.0.1"),
        ("api.example.kg/x", None),  # no scheme: no host
        ("https:///x", None),
        ("", None),
    ],
)
def test_the_host_of_a_url(url: str, host: str | None) -> None:
    assert host_of(url) == host


def test_a_url_that_cannot_be_read_has_no_host() -> None:
    assert host_of("http://[bad/x") is None


@pytest.mark.parametrize(
    ("parts", "host"),
    [
        (("https://api.example.kg/v1",), "api.example.kg"),
        (("https://api.example.kg/v1/", object()), "api.example.kg"),  # the end is a reference
        (("https://api.example.kg?q=", object()), "api.example.kg"),
        (("https://", object(), "/v1"), None),  # the host itself is a reference
        (("https://api.", object()), None),  # the host goes on past the text
        ((object(),), None),
        ((), None),
    ],
)
def test_a_host_that_is_wholly_written_in_the_constant_part_of_an_argument(
    parts: tuple[object, ...], host: str | None
) -> None:
    assert leading_host(parts) == host
