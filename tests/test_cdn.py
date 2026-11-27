from __future__ import annotations

from tvboxgen.cdn import (
    github_to_jsdelivr,
    resolve_public_url,
    rewrite_urls_in_obj,
    url_reachable,
)


def test_raw_githubusercontent_to_jsdelivr() -> None:
    assert (
        github_to_jsdelivr(
            "https://raw.githubusercontent.com/gaotianliuyun/gao/master/js.json"
        )
        == "https://cdn.jsdelivr.net/gh/gaotianliuyun/gao@master/js.json"
    )


def test_refs_heads_path() -> None:
    assert (
        github_to_jsdelivr(
            "https://raw.githubusercontent.com/qist/tvbox/refs/heads/master/xiaosa/api.json"
        )
        == "https://cdn.jsdelivr.net/gh/qist/tvbox@master/xiaosa/api.json"
    )


def test_github_blob_and_raw() -> None:
    assert (
        github_to_jsdelivr(
            "https://github.com/tw-lewis/tvbox/blob/master/xiaosa/ITV.txt"
        )
        == "https://cdn.jsdelivr.net/gh/tw-lewis/tvbox@master/xiaosa/ITV.txt"
    )
    assert (
        github_to_jsdelivr(
            "https://github.com/tw-lewis/tvbox/raw/main/foo/bar.json"
        )
        == "https://cdn.jsdelivr.net/gh/tw-lewis/tvbox@main/foo/bar.json"
    )


def test_already_jsdelivr_and_non_github_unchanged() -> None:
    jsd = "https://cdn.jsdelivr.net/gh/a/b@main/c.json"
    assert github_to_jsdelivr(jsd) == jsd
    assert github_to_jsdelivr("https://9280.kstore.space/wex.json") == (
        "https://9280.kstore.space/wex.json"
    )


def test_resolve_public_url_requires_reachable_jsdelivr(monkeypatch) -> None:
    ok = "https://cdn.jsdelivr.net/gh/a/b@main/ok.json"

    def fake_reachable(url: str, timeout: float = 12.0) -> bool:
        return url == ok

    monkeypatch.setattr("tvboxgen.cdn.url_reachable", fake_reachable)

    assert (
        resolve_public_url("https://raw.githubusercontent.com/a/b/main/ok.json")
        == ok
    )
    # 改写后不可达 → 不产出
    assert (
        resolve_public_url(
            "https://raw.githubusercontent.com/a/b/main/missing.json"
        )
        is None
    )
    # 非 GitHub：仍需可达
    assert resolve_public_url("https://dead.example.com/x.json") is None
    monkeypatch.setattr(
        "tvboxgen.cdn.url_reachable",
        lambda url, timeout=12.0: url == "https://ok.example.com/x.json",
    )
    assert resolve_public_url("https://ok.example.com/x.json") == (
        "https://ok.example.com/x.json"
    )


def test_url_reachable_accepts_2xx(monkeypatch) -> None:
    class Resp:
        status_code = 200

    monkeypatch.setattr(
        "tvboxgen.cdn.requests.head",
        lambda *a, **k: Resp(),
    )
    assert url_reachable("https://cdn.jsdelivr.net/gh/a/b@main/x.json") is True


def test_rewrite_nested_obj_only_applies_verified(monkeypatch) -> None:
    monkeypatch.setattr(
        "tvboxgen.cdn.url_reachable",
        lambda url, timeout=12.0: url.endswith("spider.jar"),
    )
    obj = {
        "spider": "https://raw.githubusercontent.com/a/b/main/spider.jar",
        "sites": [{"api": "https://github.com/a/b/raw/main/api.js"}],
        "keep": "https://example.com/x",
    }
    out = rewrite_urls_in_obj(obj, verify=True)
    assert out["spider"] == "https://cdn.jsdelivr.net/gh/a/b@main/spider.jar"
    # 改写后不可达：不采用 jsDelivr，保留原地址
    assert out["sites"][0]["api"] == "https://github.com/a/b/raw/main/api.js"
    assert out["keep"] == "https://example.com/x"


def test_build_subscriptions_drops_unreachable(monkeypatch) -> None:
    from tvboxgen.build import build_subscriptions_product

    monkeypatch.setattr(
        "tvboxgen.cdn.url_reachable",
        lambda url, timeout=12.0: "ok" in url,
    )
    rows = [
        {
            "id": "ok",
            "name": "OK",
            "url": "https://raw.githubusercontent.com/a/b/main/ok.json",
        },
        {
            "id": "bad",
            "name": "BAD",
            "url": "https://raw.githubusercontent.com/a/b/main/bad.json",
        },
    ]
    out = build_subscriptions_product(rows)
    assert len(out) == 1
    assert out[0]["id"] == "ok"
    assert out[0]["url"] == "https://cdn.jsdelivr.net/gh/a/b@main/ok.json"
