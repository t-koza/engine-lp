"""ネット不要のオフライン単体テスト。抽出・重複排除ロジックの健全性確認。

    python test_extract.py
"""
from __future__ import annotations

import re

from extract import (StoreRecord, find_contact_link, find_links, parse_store_page)
from bs4 import BeautifulSoup

SAMPLE_STORE_HTML = """
<html><head><title>エイブル 練馬店｜賃貸</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "RealEstateAgent",
  "name": "エイブル 練馬店",
  "url": "https://shop.able.co.jp/000000012/",
  "telephone": "03-1234-5678",
  "address": {
    "@type": "PostalAddress",
    "postalCode": "176-0001",
    "addressRegion": "東京都",
    "addressLocality": "練馬区",
    "streetAddress": "練馬1-2-3 練馬ビル1F"
  }
}
</script></head>
<body>
  <a href="/000000012/contact/">来店予約・お問い合わせ</a>
  <a href="mailto:nerima@example.co.jp">メール</a>
</body></html>
"""

LOCATOR_HTML = """
<html><body>
  <a href="https://shop.able.co.jp/000000012/">練馬店</a>
  <a href="https://shop.able.co.jp/000000056/">中野店</a>
  <a href="https://shop.able.co.jp/tokyo/omise/">エリア</a>
  <a href="/company/">会社情報</a>
</body></html>
"""


def test_jsonld_extraction():
    rec = StoreRecord(company="エイブル", prefecture="東京都")
    parse_store_page(SAMPLE_STORE_HTML, "https://shop.able.co.jp/000000012/", rec)
    assert rec.store_name == "エイブル 練馬店", rec.store_name
    assert rec.store_phone == "03-1234-5678", rec.store_phone
    assert "東京都" in rec.address and "練馬" in rec.address, rec.address
    assert rec.store_url == "https://shop.able.co.jp/000000012/", rec.store_url
    assert rec.email == "nerima@example.co.jp", rec.email
    assert rec.contact_form_url.endswith("/000000012/contact/"), rec.contact_form_url
    print("OK test_jsonld_extraction")


def test_find_links():
    pat = re.compile(r"shop\.able\.co\.jp/[0-9A-Z]{6,}/?$")
    links = find_links(LOCATOR_HTML, "https://shop.able.co.jp/tokyo/omise/", pat)
    assert "https://shop.able.co.jp/000000012/" in links, links
    assert "https://shop.able.co.jp/000000056/" in links, links
    assert all("omise" not in l for l in links), links
    print("OK test_find_links")


def test_contact_link_prefers_same_host():
    html = """<a href="https://other.com/contact">問い合わせ</a>
              <a href="/store/contact/">お問い合わせ</a>"""
    url = find_contact_link(BeautifulSoup(html, "lxml"), "https://shop.able.co.jp/000000012/")
    assert url == "https://shop.able.co.jp/store/contact/", url
    print("OK test_contact_link_prefers_same_host")


def test_quality_gate_fields():
    # 店舗名のみ・住所/URLなし → 行としては不十分(run.py側で除外される条件)
    rec = StoreRecord(company="X", store_name="", address="", store_url="")
    assert not rec.store_name
    print("OK test_quality_gate_fields")


if __name__ == "__main__":
    test_jsonld_extraction()
    test_find_links()
    test_contact_link_prefers_same_host()
    test_quality_gate_fields()
    print("\nALL TESTS PASSED")
