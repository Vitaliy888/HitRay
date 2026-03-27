import hashlib
from database import url_hash

def test_url_hash_basic():
    url = "https://example.com"
    expected = hashlib.md5(url.encode()).hexdigest()[:8]
    assert url_hash(url) == expected
    assert len(url_hash(url)) == 8

def test_url_hash_deterministic():
    url = "https://another-example.com/path?query=1"
    assert url_hash(url) == url_hash(url)

def test_url_hash_different_inputs():
    url1 = "https://example.com/1"
    url2 = "https://example.com/2"
    assert url_hash(url1) != url_hash(url2)

def test_url_hash_unicode():
    url = "https://пример.рф/путь"
    expected = hashlib.md5(url.encode()).hexdigest()[:8]
    assert url_hash(url) == expected

def test_url_hash_empty_string():
    url = ""
    expected = hashlib.md5(url.encode()).hexdigest()[:8]
    assert url_hash(url) == expected
