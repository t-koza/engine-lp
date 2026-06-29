#!/usr/bin/env python3
"""賃貸仲介会社 店舗単位リスト 生成パイプライン。

開放ネット環境で実行する想定。実サイトを巡回し、HTTP200生存確認・店舗詳細抽出・
重複排除を行って CSV / Excel / 取得ログ / 取得失敗一覧 を output/ に出力する。

使い方:
    pip install -r requirements.txt
    python run.py                      # 全社・優先5都府県
    python run.py --max-stores 600     # 600件到達で打ち切り
    python run.py --only エイブル,ミニミニ
    python run.py --no-robots          # robots無視(非推奨)

正確性最優先。推測でデータを作らない。取得できない値は空欄。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from common import Fetcher, setup_logging
from export import (write_csv, write_failed, write_fetch_log, write_summary, write_xlsx)
from extract import StoreRecord, find_contact_link, find_links, parse_store_page

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS_PATH = os.path.join(HERE, "seeds.json")
OUT_DIR = os.path.join(HERE, "output")
log = logging.getLogger("scraper")

# ロケーターのページネーション追従用(同一パス配下の page= / /page/N 等)
PAGINATION_HINTS = re.compile(r"(page=|/page/|p=|pageno=|offset=)", re.I)
MAX_LOCATOR_PAGES = int(os.environ.get("SCRAPER_MAX_LOCATOR_PAGES", "40"))


def load_seeds() -> dict:
    with open(SEEDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def norm_url(u: str) -> str:
    if not u:
        return ""
    p = urlparse(u)
    path = p.path.rstrip("/")
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme}://{p.netloc.lower()}{path}{q}".lower()


class Runner:
    def __init__(self, fetcher: Fetcher, max_stores: Optional[int]):
        self.f = fetcher
        self.max_stores = max_stores
        self.records: List[StoreRecord] = []
        self.fetch_log: List[Dict] = []
        self.failed: List[Dict] = []
        self._seen_keys = set()
        self._verified_store_urls = 0
        self._verified_forms = 0
        self._pages_fetched = 0

    # -- logging helpers ------------------------------------------------
    def _logrow(self, company: str, url: str, kind: str, res, note: str = "") -> None:
        self.fetch_log.append({
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
            "company": company, "url": url, "kind": kind,
            "status": res.status, "ok": res.ok, "redirected": res.redirected,
            "cross_host_redirect": res.cross_host_redirect, "final_url": res.final_url,
            "bytes": res.bytes_len, "elapsed_ms": res.elapsed_ms,
            "note": (note + (" " + res.error if res.error else "")).strip(),
        })

    def _dedup_key(self, rec: StoreRecord) -> Optional[str]:
        if rec.store_url:
            return "u:" + norm_url(rec.store_url)
        if rec.address:
            return "a:" + rec.company + "|" + re.sub(r"\s+", "", rec.address)
        if rec.store_name:
            return "n:" + rec.company + "|" + rec.store_name + "|" + rec.prefecture
        return None

    def _reached_cap(self) -> bool:
        return self.max_stores is not None and len(self.records) >= self.max_stores

    # -- crawl ----------------------------------------------------------
    def collect_detail_urls(self, company: str, locator_url: str, pattern: re.Pattern) -> List[str]:
        """ロケーターページ(+ページネーション)から店舗詳細URLを集める。"""
        to_visit = [locator_url]
        visited = set()
        detail_urls: List[str] = []
        seen_detail = set()
        while to_visit and len(visited) < MAX_LOCATOR_PAGES:
            page = to_visit.pop(0)
            if norm_url(page) in visited:
                continue
            visited.add(norm_url(page))
            res = self.f.fetch(page)
            self._pages_fetched += 1
            self._logrow(company, page, "locator", res)
            if not res.ok or not res.text:
                if page == locator_url:
                    self.failed.append({"company": company, "prefecture": "",
                                        "url": page,
                                        "reason": f"ロケーター取得失敗 status={res.status} {res.error}".strip()})
                continue
            for d in find_links(res.text, page, pattern):
                if d not in seen_detail:
                    seen_detail.add(d)
                    detail_urls.append(d)
            # ページネーション追従(同一ロケーターパス配下のみ)
            base_path = urlparse(locator_url).path.rstrip("/")
            for a_href in find_links(res.text, page, PAGINATION_HINTS):
                pp = urlparse(a_href)
                if pp.netloc == urlparse(locator_url).netloc and pp.path.rstrip("/").startswith(base_path[:max(1, len(base_path)-3)]):
                    if norm_url(a_href) not in visited and a_href not in to_visit:
                        to_visit.append(a_href)
        return detail_urls

    def process_company(self, c: dict, prefectures: Optional[set]) -> None:
        company = c["company"]
        pattern = re.compile(c["store_detail_url_regex"])
        # 採用/本部URLの生存確認(任意項目だが備考精度のため)
        recruit_url = c.get("recruit_url", "")
        if recruit_url:
            rr = self.f.fetch(recruit_url)
            self._pages_fetched += 1
            self._logrow(company, recruit_url, "recruit", rr)
            if not rr.ok:
                recruit_url = ""  # 生存しないものは空欄化(リンク切れ禁止)

        for loc in c.get("locators", []):
            if self._reached_cap():
                return
            pref = loc.get("prefecture", "")
            if prefectures and not any(p in pref for p in prefectures) and "全国" not in pref and "首都圏" not in pref:
                continue
            detail_urls = self.collect_detail_urls(company, loc["url"], pattern)
            log.info("%s [%s] 店舗詳細候補 %d件", company, pref, len(detail_urls))
            if not detail_urls:
                self.failed.append({"company": company, "prefecture": pref, "url": loc["url"],
                                    "reason": "店舗詳細リンクを検出できず(要 store_detail_url_regex 調整 または JS描画)"})
            for durl in detail_urls:
                if self._reached_cap():
                    return
                self._handle_store(c, pref, durl, recruit_url)

    def _handle_store(self, c: dict, pref: str, durl: str, recruit_url: str) -> None:
        company = c["company"]
        res = self.f.fetch(durl)
        self._pages_fetched += 1
        self._logrow(company, durl, "store", res)
        if not res.ok or not res.text:
            self.failed.append({"company": company, "prefecture": pref, "url": durl,
                                "reason": f"店舗ページ取得失敗 status={res.status} {res.error}".strip()})
            return
        rec = StoreRecord(company=company, prefecture=pref,
                          hq_url=c.get("hq_url", ""), recruit_url=recruit_url,
                          company_store_count=c.get("company_store_count_note", ""))
        parse_store_page(res.text, res.final_url or durl, rec)

        # 品質ゲート: 店舗名 と (住所 or 店舗URL) が必要
        if not rec.store_name or not (rec.address or rec.store_url):
            self.failed.append({"company": company, "prefecture": pref, "url": durl,
                                "reason": "必須項目(店舗名/住所orURL)不足のため除外"})
            return

        notes = []
        # 店舗URLは実際に取得済み→200確認済み
        if res.ok:
            rec.verified_store_url = True
            self._verified_store_urls += 1
            notes.append("店舗URL:HTTP200確認")
        if res.cross_host_redirect:
            notes.append(f"別ホストへリダイレクト({res.final_url})")

        # 問い合わせフォームURLの生存確認
        if rec.contact_form_url:
            fr = self.f.fetch(rec.contact_form_url)
            self._pages_fetched += 1
            self._logrow(company, rec.contact_form_url, "form", fr)
            if fr.ok:
                rec.verified_form_url = True
                self._verified_forms += 1
                rec.contact_form_url = fr.final_url or rec.contact_form_url
                notes.append("フォーム:HTTP200確認")
            else:
                notes.append(f"フォーム未生存(status={fr.status})→空欄化")
                rec.contact_form_url = ""

        rec.notes = (" / ".join(notes) + (" / " + rec.notes if rec.notes else "")).strip(" /")

        key = self._dedup_key(rec)
        if key is None or key in self._seen_keys:
            return
        self._seen_keys.add(key)
        self.records.append(rec)

    # -- output ---------------------------------------------------------
    def export(self) -> None:
        os.makedirs(OUT_DIR, exist_ok=True)
        write_csv(os.path.join(OUT_DIR, "stores.csv"), self.records)
        write_xlsx(os.path.join(OUT_DIR, "stores.xlsx"), self.records)
        write_fetch_log(os.path.join(OUT_DIR, "fetch_log.csv"), self.fetch_log)
        write_failed(os.path.join(OUT_DIR, "failed_companies.csv"), self.failed)
        per_company: Dict[str, int] = {}
        for r in self.records:
            per_company[r.company] = per_company.get(r.company, 0) + 1
        write_summary(os.path.join(OUT_DIR, "run_summary.md"), {
            "records": len(self.records),
            "verified_store_urls": self._verified_store_urls,
            "verified_forms": self._verified_forms,
            "pages_fetched": self._pages_fetched,
            "failed": len(self.failed),
            "per_company": per_company,
        })
        log.info("出力完了: %d店舗 / 巡回%dページ / 失敗%d", len(self.records), self._pages_fetched, len(self.failed))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="賃貸仲介 店舗リスト生成")
    ap.add_argument("--max-stores", type=int, default=None, help="この件数に達したら打ち切り")
    ap.add_argument("--only", type=str, default="", help="会社名カンマ区切りで対象を限定")
    ap.add_argument("--prefectures", type=str, default="東京都,神奈川県,埼玉県,千葉県,大阪府",
                    help="対象都道府県(カンマ区切り)。空で全件。")
    ap.add_argument("--no-robots", action="store_true", help="robots.txtを無視(非推奨)")
    ap.add_argument("--min-interval", type=float, default=1.5, help="同一ホスト最小間隔(秒)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    setup_logging(os.path.join(OUT_DIR, "run.log"))
    seeds = load_seeds()
    fetcher = Fetcher(respect_robots=not args.no_robots, min_interval=args.min_interval)
    runner = Runner(fetcher, args.max_stores)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    prefectures = {s.strip() for s in args.prefectures.split(",") if s.strip()} or None

    for c in seeds["companies"]:
        if only and not any(o in c["company"] for o in only):
            continue
        if runner._reached_cap():
            break
        log.info("=== %s 処理開始 ===", c["company"])
        try:
            runner.process_company(c, prefectures)
        except Exception as e:  # noqa: BLE001
            log.exception("会社処理中に例外: %s", c["company"])
            runner.failed.append({"company": c["company"], "prefecture": "", "url": c.get("hq_url", ""),
                                  "reason": f"処理中例外: {type(e).__name__}: {e}"})

    runner.export()


if __name__ == "__main__":
    main()
