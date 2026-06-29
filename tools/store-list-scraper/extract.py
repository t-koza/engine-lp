"""店舗詳細ページから構造化データを抽出する。

抽出優先度(正確性最優先):
1. JSON-LD (schema.org RealEstateAgent / LocalBusiness / Organization)
   → 店舗名・住所・電話・URL を最も信頼できる形で取得。
2. 1で欠けた項目のみ、保守的なヒューリスティクスで補完。
   - 電話: 日本の固定/フリーダイヤル形式の正規表現。複数あれば最初のものを店舗電話候補に。
   - メール: mailto: リンクのみ採用(本文スクレイプはノイズが多いので避ける)。
   - 問い合わせフォーム: アンカーのテキスト/href に問い合わせ系キーワードを含むリンク。

推測補完はしない。取れない項目は空文字のまま。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

PHONE_RE = re.compile(r"0\d{1,4}[-(]\d{1,4}[-)]\d{3,4}")
# 問い合わせフォーム候補を示すキーワード(href/テキスト)
CONTACT_KEYWORDS = [
    "問い合わせ", "問合せ", "問合わせ", "お問い合わせ", "お問合せ",
    "来店予約", "予約", "メール", "資料請求", "見学予約",
    "contact", "inquiry", "toiawase", "form", "mail", "reserve", "yoyaku", "request",
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class StoreRecord:
    company: str = ""
    store_name: str = ""
    prefecture: str = ""
    address: str = ""
    store_url: str = ""
    contact_form_url: str = ""
    hq_phone: str = ""
    store_phone: str = ""
    email: str = ""
    company_store_count: str = ""
    hq_url: str = ""
    recruit_url: str = ""
    google_review_count: str = ""
    google_rating: str = ""
    notes: str = ""
    # 内部用(出力しない)
    source_url: str = ""
    verified_store_url: bool = False
    verified_form_url: bool = False

    def as_row(self) -> List[str]:
        return [
            self.company, self.store_name, self.prefecture, self.address,
            self.store_url, self.contact_form_url, self.hq_phone, self.store_phone,
            self.email, self.company_store_count, self.hq_url, self.recruit_url,
            self.google_review_count, self.google_rating, self.notes,
        ]


COLUMNS = [
    "会社名", "店舗名", "都道府県", "住所", "店舗URL", "問い合わせフォームURL",
    "代表電話番号", "店舗電話番号", "お問い合わせメールアドレス", "店舗数（会社全体）",
    "本部URL", "採用ページURL", "Google口コミ件数", "Google評価", "備考",
]


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 末尾カンマ等の軽微な崩れを許容するための簡易リトライ
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue
        if isinstance(data, list):
            for d in data:
                yield d
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                for d in data["@graph"]:
                    yield d
            else:
                yield data


BUSINESS_TYPES = {"realestateagent", "localbusiness", "organization", "store", "homeandconstructionbusiness"}


def _type_matches(node: dict) -> bool:
    t = node.get("@type", "")
    types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
    return any(str(x).lower() in BUSINESS_TYPES for x in types)


def _compose_address(addr) -> str:
    if isinstance(addr, str):
        return addr.strip()
    if isinstance(addr, dict):
        parts = [
            addr.get("postalCode", ""),
            addr.get("addressRegion", ""),
            addr.get("addressLocality", ""),
            addr.get("streetAddress", ""),
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def extract_from_jsonld(soup: BeautifulSoup, rec: StoreRecord) -> None:
    for node in _iter_jsonld(soup):
        if not isinstance(node, dict) or not _type_matches(node):
            continue
        if not rec.store_name and node.get("name"):
            rec.store_name = str(node["name"]).strip()
        if not rec.address:
            addr = _compose_address(node.get("address"))
            if addr:
                rec.address = addr
        if not rec.store_phone and node.get("telephone"):
            rec.store_phone = str(node["telephone"]).strip()
        if not rec.store_url and node.get("url"):
            rec.store_url = str(node["url"]).strip()
        # AggregateRating があれば Google相当ではないが参考値として備考に残す素材
        agg = node.get("aggregateRating")
        if isinstance(agg, dict):
            if not rec.google_rating and agg.get("ratingValue"):
                rec.notes = (rec.notes + f" [site rating {agg.get('ratingValue')}]").strip()
            if not rec.google_review_count and agg.get("reviewCount"):
                rec.notes = (rec.notes + f" [site reviews {agg.get('reviewCount')}]").strip()


def extract_heuristics(soup: BeautifulSoup, base_url: str, rec: StoreRecord) -> None:
    text = soup.get_text("\n", strip=True)

    if not rec.store_phone:
        m = PHONE_RE.search(text)
        if m:
            rec.store_phone = m.group(0)

    if not rec.store_name:
        if soup.title and soup.title.string:
            rec.store_name = soup.title.string.split("｜")[0].split("|")[0].strip()

    # mailto: のみ採用
    if not rec.email:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                cand = href[7:].split("?")[0].strip()
                if EMAIL_RE.fullmatch(cand):
                    rec.email = cand
                    break

    # 問い合わせフォーム候補リンク
    if not rec.contact_form_url:
        rec.contact_form_url = find_contact_link(soup, base_url)


def find_contact_link(soup: BeautifulSoup, base_url: str) -> str:
    candidates: List[str] = []
    base_host = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "tel:", "mailto:")):
            continue
        label = (a.get_text(" ", strip=True) or "") + " " + href
        low = label.lower()
        if any(k.lower() in low for k in CONTACT_KEYWORDS):
            full = urljoin(base_url, href)
            # 同一ホストの問い合わせを優先
            if urlparse(full).netloc == base_host:
                candidates.insert(0, full)
            else:
                candidates.append(full)
    return candidates[0] if candidates else ""


def parse_store_page(html: str, base_url: str, rec: StoreRecord) -> StoreRecord:
    soup = BeautifulSoup(html, "lxml")
    extract_from_jsonld(soup, rec)
    extract_heuristics(soup, base_url, rec)
    if not rec.store_url:
        rec.store_url = base_url
    rec.source_url = base_url
    return rec


def find_links(html: str, base_url: str, pattern: re.Pattern) -> List[str]:
    """ロケーターページから、正規表現に一致する店舗詳細URLを発見する。"""
    soup = BeautifulSoup(html, "lxml")
    out: List[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip())
        full = full.split("#")[0]
        if pattern.search(full) and full not in seen:
            seen.add(full)
            out.append(full)
    return out
