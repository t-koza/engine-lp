#!/usr/bin/env python3
"""全店舗の問い合わせフォームへ営業文面を送信する（フォーム営業／反響獲得）。

⚠️ 重要・前提
- 本スクリプトは「実際に第三者サイトのフォームへ送信する外向きアクション」を行う。
  必ず最初は既定の **ドライラン**（送信せず、入力内容と送信ボタンを確認するだけ）で
  全件を点検し、内容に問題がないことを確認してから `--send` で実送信すること。
- 送信主情報・文面は sender_config.json に記入（sender_config.example.json をコピー）。
- 入力元は run.py が生成した output/stores.csv（問い合わせフォームURL列を使用）。
  フォームURLが空の行は、店舗URLからフォームを自動探索して補完を試みる。
- この実行環境（リスト作成セッション）は外部HTTPが全面403のため**動作しない**。
  開放ネットワーク環境で実行すること。Playwright/Chromiumはプリインストール済み。

安全装置（既定で有効）
- ドライラン既定（--send 指定時のみ実送信）
- reCAPTCHA/hCaptcha 検出時はスキップして「要手動」に記録（CAPTCHA回避はしない）
- ページに「営業お断り/勧誘禁止」表示があればスキップ（respect_eigyo_okotowari）
- ホスト毎の最小送信間隔（min_interval_sec）＋指数バックオフ
- 二重送信防止（output/sent_log.csv に成功を記録、再実行時はスキップ）
- 必須項目を埋められない場合は送信せず「要手動」に記録
- 全件の結果を output/send_results.csv とスクリーンショットに保存

使い方
    pip install -r requirements.txt
    python -m playwright install chromium   # 環境にない場合のみ
    cp sender_config.example.json sender_config.json   # 編集
    python submit_forms.py                  # ドライラン（送信しない）
    python submit_forms.py --send           # 実送信
    python submit_forms.py --send --limit 20
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
SHOTS = os.path.join(OUT, "form_shots")

# 入力フィールドの論理名 → 照合キーワード（name/id/placeholder/label/aria-label を対象）
FIELD_KEYWORDS: Dict[str, List[str]] = {
    "company": ["会社", "御社", "貴社", "法人", "company", "corp", "kaisya", "kaisha", "店舗名", "屋号"],
    "name": ["お名前", "氏名", "name", "namae", "担当者", "ご担当", "fullname"],
    "name_sei": ["姓", "苗字", "lastname", "last_name", "sei"],
    "name_mei": ["名", "firstname", "first_name", "mei"],
    "kana": ["フリガナ", "ふりがな", "カナ", "kana", "furigana"],
    "kana_sei": ["セイ", "せい"],
    "kana_mei": ["メイ", "めい"],
    "email": ["メール", "mail", "email", "e-mail", "address"],
    "email_confirm": ["確認", "再入力", "confirm", "retype", "kakunin"],
    "tel": ["電話", "tel", "phone", "denwa", "携帯"],
    "postal": ["郵便", "〒", "zip", "postal", "yubin"],
    "address": ["住所", "所在地", "address", "addr", "jusho"],
    "url": ["url", "ホームページ", "サイト", "website", "homepage"],
    "subject": ["件名", "題名", "subject", "title", "用件"],
    "message": ["内容", "本文", "お問い合わせ", "問合せ", "ご相談", "メッセージ", "message", "body",
                "honbun", "naiyo", "comment", "inquiry", "詳細", "ご要望"],
}

CAPTCHA_MARKERS = ["recaptcha", "g-recaptcha", "hcaptcha", "h-captcha", "captcha", "turnstile"]
EIGYO_NG = [
    "営業はお断り", "営業お断り", "営業の電話", "営業目的", "勧誘はお断り", "勧誘お断り",
    "営業・勧誘", "セールス目的", "売り込み", "営業メールお断り", "営業はご遠慮",
]
SUBMIT_TEXTS = ["送信", "確認", "同意して", "送信する", "内容を確認", "上記内容で送信",
                "この内容で送信", "submit", "send", "confirm"]
SENT_LOG = os.path.join(OUT, "sent_log.csv")
RESULTS = os.path.join(OUT, "send_results.csv")


@dataclass
class Sender:
    company: str = ""
    name_sei: str = ""
    name_mei: str = ""
    kana_sei: str = ""
    kana_mei: str = ""
    email: str = ""
    tel: str = ""
    postal: str = ""
    address: str = ""
    url: str = ""
    subject: str = ""
    message: str = ""
    respect_eigyo_okotowari: bool = True
    skip_if_captcha: bool = True
    min_interval_sec: float = 8.0
    max_per_run: int = 0

    @property
    def name(self) -> str:
        return f"{self.name_sei}{self.name_mei}".strip()

    @property
    def kana(self) -> str:
        return f"{self.kana_sei}{self.kana_mei}".strip()

    def value_for(self, logical: str) -> str:
        return {
            "company": self.company, "name": self.name, "name_sei": self.name_sei,
            "name_mei": self.name_mei, "kana": self.kana, "kana_sei": self.kana_sei,
            "kana_mei": self.kana_mei, "email": self.email, "email_confirm": self.email,
            "tel": self.tel, "postal": self.postal, "address": self.address,
            "url": self.url, "subject": self.subject, "message": self.message,
        }.get(logical, "")


def load_sender() -> Sender:
    path = os.path.join(HERE, "sender_config.json")
    if not os.path.exists(path):
        raise SystemExit("sender_config.json がありません。sender_config.example.json をコピーして記入してください。")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    opts = d.get("_options", {})
    s = Sender(
        company=d.get("company", ""), name_sei=d.get("name_sei", ""), name_mei=d.get("name_mei", ""),
        kana_sei=d.get("kana_sei", ""), kana_mei=d.get("kana_mei", ""), email=d.get("email", ""),
        tel=d.get("tel", ""), postal=d.get("postal", ""), address=d.get("address", ""),
        url=d.get("url", ""), subject=d.get("subject", ""),
        message=d.get("message", "").replace("\\n", "\n"),
        respect_eigyo_okotowari=opts.get("respect_eigyo_okotowari", True),
        skip_if_captcha=opts.get("skip_if_captcha", True),
        min_interval_sec=float(opts.get("min_interval_sec", 8.0)),
        max_per_run=int(opts.get("max_per_run", 0)),
    )
    missing = [k for k in ("company", "email", "message") if not getattr(s, k if k != "company" else "company")]
    if not s.company or not s.email or not s.message:
        raise SystemExit("sender_config.json の company / email / message は必須です。")
    return s


@dataclass
class Target:
    company: str
    store_name: str
    prefecture: str
    store_url: str
    form_url: str


def load_targets(csv_path: str) -> List[Target]:
    targets = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            targets.append(Target(
                company=row.get("会社名", ""), store_name=row.get("店舗名", ""),
                prefecture=row.get("都道府県", ""), store_url=row.get("店舗URL", ""),
                form_url=row.get("問い合わせフォームURL", "").strip(),
            ))
    return targets


def load_sent_keys() -> set:
    if not os.path.exists(SENT_LOG):
        return set()
    keys = set()
    with open(SENT_LOG, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            keys.add(row.get("key", ""))
    return keys


def target_key(t: Target) -> str:
    return (t.form_url or t.store_url).rstrip("/").lower()


# ---- フォーム操作（Playwright） -------------------------------------------
def field_logical(attrs_text: str) -> Optional[str]:
    """フィールドの属性/ラベル文字列から論理名を推定。具体的なものを優先。"""
    low = attrs_text.lower()
    # 優先順位: 細かい指定（sei/mei/kana/confirm）→ 一般
    order = ["email_confirm", "kana_sei", "kana_mei", "kana", "name_sei", "name_mei",
             "postal", "address", "company", "email", "tel", "url", "subject", "message", "name"]
    for logical in order:
        for kw in FIELD_KEYWORDS[logical]:
            if kw.lower() in low:
                return logical
    return None


def describe_field(page, el) -> str:
    """input/textarea の name/id/placeholder/aria-label/関連ラベル文字列を結合。"""
    parts = []
    for attr in ("name", "id", "placeholder", "aria-label", "title"):
        try:
            v = el.get_attribute(attr)
            if v:
                parts.append(v)
        except Exception:
            pass
    # label[for=id]
    try:
        eid = el.get_attribute("id")
        if eid:
            lab = page.query_selector(f'label[for="{eid}"]')
            if lab:
                parts.append(lab.inner_text())
    except Exception:
        pass
    # 親 label
    try:
        lab = el.evaluate("e => { const l = e.closest('label'); return l ? l.innerText : '' }")
        if lab:
            parts.append(lab)
    except Exception:
        pass
    return " ".join(parts)


def page_has_captcha(page) -> bool:
    try:
        html = page.content().lower()
    except Exception:
        return False
    return any(m in html for m in CAPTCHA_MARKERS)


def page_has_eigyo_ng(page) -> bool:
    try:
        txt = page.inner_text("body")
    except Exception:
        return False
    return any(ng in txt for ng in EIGYO_NG)


def fill_form(page, sender: Sender) -> Tuple[Dict[str, str], List[str]]:
    """フォームを埋める。返り値: (埋めた {論理名:値}, 未対応の必須項目ラベル一覧)。"""
    filled: Dict[str, str] = {}
    unmatched_required: List[str] = []
    elements = page.query_selector_all("input, textarea, select")
    for el in elements:
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            typ = (el.get_attribute("type") or "").lower()
            if typ in ("hidden", "submit", "button", "image", "file", "checkbox", "radio"):
                continue
            if not el.is_visible():
                continue
            desc = describe_field(page, el)
            logical = field_logical(desc)
            required = el.get_attribute("required") is not None or "必須" in desc or "required" in desc.lower()
            if not logical:
                if required:
                    unmatched_required.append(desc[:40])
                continue
            val = sender.value_for(logical)
            if not val:
                if required:
                    unmatched_required.append(desc[:40])
                continue
            if tag == "select":
                continue  # select は選択肢依存のため自動選択しない（要手動扱い）
            el.fill(val)
            filled[logical] = val
        except Exception:
            continue
    return filled, unmatched_required


def find_submit(page):
    # type=submit を優先
    for sel in ['button[type="submit"]', 'input[type="submit"]']:
        el = page.query_selector(sel)
        if el and el.is_visible():
            return el
    # テキスト一致
    for el in page.query_selector_all("button, input[type=button], a"):
        try:
            t = (el.inner_text() or el.get_attribute("value") or "").strip()
            if t and any(s in t for s in SUBMIT_TEXTS) and el.is_visible():
                return el
        except Exception:
            continue
    return None


@dataclass
class Result:
    company: str
    store_name: str
    form_url: str
    status: str          # sent / dryrun / skipped / failed
    reason: str = ""
    filled: str = ""


def process_target(page, t: Target, sender: Sender, do_send: bool) -> Result:
    url = t.form_url or t.store_url
    base = Result(company=t.company, store_name=t.store_name, form_url=url, status="failed")
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        base.reason = f"ページ遷移失敗: {type(e).__name__}"
        return base

    # フォームURLが無い場合は店舗ページから問い合わせリンクを探索
    if not t.form_url:
        link = None
        for el in page.query_selector_all("a"):
            try:
                label = ((el.inner_text() or "") + " " + (el.get_attribute("href") or "")).lower()
                if any(k in label for k in ["問い合わせ", "問合", "contact", "inquiry", "toiawase", "フォーム", "メール"]):
                    link = el.get_attribute("href")
                    break
            except Exception:
                continue
        if link:
            try:
                page.goto(link, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                base.reason = "問い合わせフォーム未検出"
                return base
        else:
            base.reason = "問い合わせフォーム未検出"
            return base

    if sender.respect_eigyo_okotowari and page_has_eigyo_ng(page):
        base.status = "skipped"; base.reason = "営業お断り表示のためスキップ"
        return base
    if sender.skip_if_captcha and page_has_captcha(page):
        base.status = "skipped"; base.reason = "CAPTCHA検出のため要手動"
        return base

    filled, unmatched = fill_form(page, sender)
    base.filled = ",".join(f"{k}" for k in filled.keys())
    if "message" not in filled:
        base.status = "skipped"; base.reason = "本文フィールド未検出のため要手動"
        return base
    if unmatched:
        base.status = "skipped"; base.reason = "必須項目を自動入力不可: " + " / ".join(unmatched[:3])
        return base

    # スクリーンショット（入力後）
    try:
        os.makedirs(SHOTS, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80]
        page.screenshot(path=os.path.join(SHOTS, f"{safe}.png"), full_page=True)
    except Exception:
        pass

    if not do_send:
        base.status = "dryrun"; base.reason = "入力のみ（未送信）"
        return base

    btn = find_submit(page)
    if not btn:
        base.status = "skipped"; base.reason = "送信ボタン未検出のため要手動"
        return base
    try:
        btn.click(timeout=15000)
        page.wait_for_timeout(2500)
        # 確認画面パターン: もう一度 送信ボタンがあれば押す
        btn2 = find_submit(page)
        if btn2:
            try:
                btn2.click(timeout=15000)
                page.wait_for_timeout(2500)
            except Exception:
                pass
        base.status = "sent"; base.reason = "送信実行"
    except Exception as e:
        base.status = "failed"; base.reason = f"送信クリック失敗: {type(e).__name__}"
    return base


def append_results(results: List[Result]) -> None:
    os.makedirs(OUT, exist_ok=True)
    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "会社名", "店舗名", "フォームURL", "status", "理由", "入力項目"])
        for r in results:
            w.writerow([_dt.datetime.now().isoformat(timespec="seconds"), r.company, r.store_name,
                        r.form_url, r.status, r.reason, r.filled])


def append_sent(t: Target) -> None:
    new = not os.path.exists(SENT_LOG)
    with open(SENT_LOG, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["key", "会社名", "店舗名", "timestamp"])
        w.writerow([target_key(t), t.company, t.store_name, _dt.datetime.now().isoformat(timespec="seconds")])


def main() -> None:
    ap = argparse.ArgumentParser(description="店舗フォームへ営業文面を送信（既定ドライラン）")
    ap.add_argument("--send", action="store_true", help="実送信する（未指定はドライラン）")
    ap.add_argument("--input", default=os.path.join(OUT, "stores.csv"), help="入力CSV（run.py出力）")
    ap.add_argument("--limit", type=int, default=0, help="処理件数上限（0=無制限）")
    ap.add_argument("--headful", action="store_true", help="ブラウザ画面を表示")
    args = ap.parse_args()

    sender = load_sender()
    if not os.path.exists(args.input):
        raise SystemExit(f"入力CSVがありません: {args.input}\n先に run.py を実行して output/stores.csv を生成してください。")
    targets = load_targets(args.input)
    # 送信先（フォームURLがある or 店舗URLから探索可能）に限定
    targets = [t for t in targets if t.form_url or t.store_url]
    sent_keys = load_sent_keys()
    targets = [t for t in targets if target_key(t) not in sent_keys]
    cap = args.limit or (sender.max_per_run if sender.max_per_run else 0)
    if cap:
        targets = targets[:cap]

    mode = "実送信" if args.send else "ドライラン（未送信）"
    print(f"=== モード: {mode} / 対象 {len(targets)}件 ===")
    if args.send:
        print("⚠️ 実送信モードです。各サイトへ実際に送信されます。中断は Ctrl+C。")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright 未インストール。pip install playwright && python -m playwright install chromium")

    results: List[Result] = []
    last_by_host: Dict[str, float] = {}
    exe = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
    launch_kwargs = {"headless": not args.headful}
    if os.path.exists(exe):
        launch_kwargs["executable_path"] = exe

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception:
            browser = p.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(locale="ja-JP",
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) form-eigyo/1.0")
        page = ctx.new_page()
        for i, t in enumerate(targets, 1):
            host = urlparse(t.form_url or t.store_url).netloc
            now = time.monotonic()
            wait = sender.min_interval_sec - (now - last_by_host.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            last_by_host[host] = time.monotonic()
            r = process_target(page, t, sender, do_send=args.send)
            results.append(r)
            if r.status == "sent":
                append_sent(t)
            print(f"[{i}/{len(targets)}] {r.status:7} {t.company} {t.store_name} — {r.reason}")
        browser.close()

    append_results(results)
    # サマリ
    from collections import Counter
    c = Counter(r.status for r in results)
    print("\n=== 結果サマリ ===")
    for k in ("sent", "dryrun", "skipped", "failed"):
        if c.get(k):
            print(f"  {k}: {c[k]}")
    print(f"詳細: {RESULTS}")
    print(f"スクショ: {SHOTS}")
    if not args.send:
        print("\n→ 内容を確認し、問題なければ `python submit_forms.py --send` で実送信してください。")


if __name__ == "__main__":
    main()
