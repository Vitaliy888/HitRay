import pytest

from main import filter_sources, RU_KW, EU_KW

def test_filter_sources_all():
    sources = ["http://example.com/ru", "http://example.com/eu", "http://example.com/us"]
    assert filter_sources(sources, 'all') == sources

def test_filter_sources_ru():
    sources = [
        "http://example.com/ru_node",
        "http://example.com/russia_server",
        "http://example.com/eu_node",
        "http://example.com/other"
    ]
    expected = [
        "http://example.com/ru_node",
        "http://example.com/russia_server"
    ]
    assert filter_sources(sources, 'ru') == expected

def test_filter_sources_eu():
    sources = [
        "http://example.com/ru_node",
        "http://example.com/eu_node",
        "http://example.com/germany_server",
        "http://example.com/france_node",
        "http://example.com/other"
    ]
    expected = [
        "http://example.com/ru_node",
        "http://example.com/eu_node",
        "http://example.com/germany_server",
        "http://example.com/france_node",
        "http://example.com/other"
    ]
    assert filter_sources(sources, 'eu') == expected

def test_filter_sources_case_insensitive():
    sources = [
        "http://t.org/RU_ND",
        "http://t.org/GERMANY_SRV",
        "http://t.org/Oth"
    ]
    assert filter_sources(sources, 'ru') == ["http://t.org/RU_ND"]
    assert filter_sources(sources, 'eu') == ["http://t.org/GERMANY_SRV"]

def test_filter_sources_empty_sources():
    assert filter_sources([], 'ru') == []
    assert filter_sources([], 'eu') == []
    assert filter_sources([], 'all') == []

def test_filter_sources_no_matches():
    sources = ["http://testsite.org/us_server", "http://testsite.org/jp_server"]
    assert filter_sources(sources, 'ru') == []
    assert filter_sources(sources, 'eu') == []
