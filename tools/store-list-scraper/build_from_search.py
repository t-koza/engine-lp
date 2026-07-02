#!/usr/bin/env python3
"""WebSearch由来の実在データ(harvested.json)から暫定リストを生成する。

このスクリプトは推測でデータを作らない。harvested.json には、Web検索結果に
実際に現れた (会社名・店舗名・都道府県・店舗URL) のみを記録する。
住所/電話/フォーム/メール/Google等、検索だけで確実に取れない項目は空欄のまま。
店舗URLのHTTP200生存確認は開放ネット環境の run.py で別途行う。

    python build_from_search.py
出力: output/stores_search.csv / output/stores_search.xlsx
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from extract import StoreRecord
from export import write_csv, write_xlsx

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")

# 会社メタ(本部URL・店舗数・採用URL)。seeds.jsonと整合。
COMPANY_META = {
    "アパマンショップ": ("https://www.apamanshop.com/", "約1,093店舗(2021・賃貸仲介No.1/要確認)", "https://www.apamannet.com/recruit/"),
    "エイブル": ("https://www.able.co.jp/", "全国 直営+FC(最新要確認)", "https://www.able.co.jp/recruit/"),
    "ミニミニ": ("https://www.minimini.co.jp/", "グループ約1,000店舗(要確認)", "https://www.minimini.co.jp/recruit/"),
    "ピタットハウス": ("https://www.pitat.com/", "632店舗(2026/3末・直営116+NW516)", "https://www.starts.co.jp/recruit/"),
    "ハウスコム": ("https://www.housecom.co.jp/", "グループ約232店舗", "https://www.housecom.co.jp/recruit/"),
    "タウンハウジング": ("https://www.townhousing.co.jp/", "直営139店舗(東京/神奈川/埼玉/千葉)", "https://town-group.co.jp/recruit/companies/townhousing"),
    "アエラスグループ": ("https://www.aeras-group.jp/", "首都圏 直営67店舗", "https://www.aeras-group.jp/recruit/"),
    "リブマックス": ("https://www.livemax.co.jp/", "全国(要確認)", "https://www.livemax.co.jp/recruit/"),
    "ルームピア(ROOMPIA)": ("https://www.roompia.jp/", "直営9+サテライト16", "https://www.ambitiondxholdings.com/recruit/"),
    "CLCコーポレーション": ("https://www.clcnet.jp/", "東京東/神奈川/千葉(要確認)", ""),
    "CLCコーポレーション": ("https://www.clcnet.jp/", "東京東/神奈川/千葉(要確認)", "https://www.clcnet.jp/"),
}

NOTE = "検索由来・店舗URL要HTTP200確認(run.pyで自動検証)"


def norm(u: str) -> str:
    p = urlparse(u)
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme}://{p.netloc.lower()}{p.path.rstrip('/')}{q}".lower()


def main() -> None:
    with open(os.path.join(HERE, "harvested.json"), encoding="utf-8") as f:
        items = json.load(f)
    seen = set()
    recs = []
    for it in items:
        url = it.get("store_url", "").strip()
        key = norm(url) if url else ("n:" + it["company"] + it.get("store_name", "") + it.get("prefecture", ""))
        if key in seen:
            continue
        seen.add(key)
        hq, count, recruit = COMPANY_META.get(it["company"], ("", "", ""))
        recs.append(StoreRecord(
            company=it["company"],
            store_name=it.get("store_name", "").strip(),
            prefecture=it.get("prefecture", "").strip(),
            store_url=url,
            hq_url=hq,
            company_store_count=count,
            recruit_url=recruit,
            notes=(it.get("note", "") + " " + NOTE).strip(),
        ))
    os.makedirs(OUT, exist_ok=True)
    write_csv(os.path.join(OUT, "stores_search.csv"), recs)
    write_xlsx(os.path.join(OUT, "stores_search.xlsx"), recs)
    # 会社別件数
    per = {}
    for r in recs:
        per[r.company] = per.get(r.company, 0) + 1
    print(f"出力 {len(recs)}店舗")
    for c, n in sorted(per.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")


if __name__ == "__main__":
    main()
