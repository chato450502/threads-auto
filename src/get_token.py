#!/usr/bin/env python3
"""Threads 長期アクセストークン取得の補助スクリプト。

手作業の curl を避けるためのヘルパー。2ステップで使う:

  1) 認可URLを表示 → ブラウザで開き「みれい」としてログインして許可
       .venv/bin/python src/get_token.py url

  2) リダイレクト先URLの ?code=XXXX の XXXX を貼って交換（短期→長期まで一気に）
       .venv/bin/python src/get_token.py exchange --code "XXXX"

必要な環境変数（.env に記入）:
  THREADS_APP_ID        アプリのID（アプリの設定→ベーシック）
  THREADS_APP_SECRET    アプリのsecret（同上）
  THREADS_REDIRECT_URI  アプリに登録したリダイレクトURI（例 https://localhost/）
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse

import requests

import threads_common as tc

AUTH_BASE = "https://threads.net/oauth/authorize"
TOKEN_EXCHANGE = "https://graph.threads.net/oauth/access_token"
LONG_LIVED = "https://graph.threads.net/access_token"
GRAPH = "https://graph.threads.net/v1.0"
SCOPES = "threads_basic,threads_content_publish,threads_manage_replies"


def cmd_url(_args):
    app_id = tc.env("THREADS_APP_ID", required=True)
    redirect = tc.env("THREADS_REDIRECT_URI", required=True)
    params = {
        "client_id": app_id,
        "redirect_uri": redirect,
        "scope": SCOPES,
        "response_type": "code",
    }
    url = AUTH_BASE + "?" + urllib.parse.urlencode(params)
    print("\n▼ このURLをブラウザで開き、『みれい』としてログインして許可してください:\n")
    print(url)
    print("\n許可すると " + redirect + " に飛びます（ページは表示されなくてOK）。")
    print("アドレスバーの ?code= の後ろの文字列をコピーして、次を実行:")
    print('  .venv/bin/python src/get_token.py exchange --code "<コピーしたcode>"\n')


def _clean_code(code: str) -> str:
    # Threadsのcodeは末尾に "#_" が付くことがある。#以降とURLエンコードを除去
    code = code.strip()
    if "#" in code:
        code = code.split("#", 1)[0]
    return urllib.parse.unquote(code)


def cmd_exchange(args):
    app_id = tc.env("THREADS_APP_ID", required=True)
    secret = tc.env("THREADS_APP_SECRET", required=True)
    redirect = tc.env("THREADS_REDIRECT_URI", required=True)
    code = _clean_code(args.code)

    # (1) 認可コード → 短期トークン
    r = requests.post(TOKEN_EXCHANGE, data={
        "client_id": app_id,
        "client_secret": secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
        "code": code,
    }, timeout=60)
    if r.status_code >= 400:
        sys.exit(f"[ERROR] 短期トークン取得失敗 HTTP {r.status_code}: {r.text[:500]}")
    short = r.json()
    short_token = short.get("access_token")
    user_id = short.get("user_id")
    if not short_token:
        sys.exit(f"[ERROR] access_token が返りませんでした: {short}")
    print(f"[1/3] 短期トークン取得 OK（user_id={user_id}）")

    # (2) 短期 → 長期トークン
    r = requests.get(LONG_LIVED, params={
        "grant_type": "th_exchange_token",
        "client_secret": secret,
        "access_token": short_token,
    }, timeout=60)
    if r.status_code >= 400:
        sys.exit(f"[ERROR] 長期トークン交換失敗 HTTP {r.status_code}: {r.text[:500]}")
    longd = r.json()
    long_token = longd.get("access_token")
    expires_in = int(longd.get("expires_in", 0))
    print(f"[2/3] 長期トークン取得 OK（有効 約{expires_in // 86400}日）")

    # (3) /me で user_id を確定（念のため）
    try:
        me = requests.get(f"{GRAPH}/me", params={
            "fields": "id,username", "access_token": long_token}, timeout=60).json()
        user_id = me.get("id", user_id)
        username = me.get("username", "")
    except Exception:
        username = ""
    print(f"[3/3] アカウント確認 OK（id={user_id} {('@'+username) if username else ''}）\n")

    print("=" * 60)
    print("以下を GitHub Secrets（または .env）に設定してください:")
    print("=" * 60)
    print(f"THREADS_USER_ID={user_id}")
    print(f"THREADS_ACCESS_TOKEN={long_token}")
    print("=" * 60)
    print(f"※ このトークンは約{expires_in // 86400}日で失効します。"
          "refresh_token.py（月1回）で自動延命されます。")


def main():
    ap = argparse.ArgumentParser(description="Threads 長期トークン取得ヘルパー")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("url", help="認可URLを表示").set_defaults(func=cmd_url)
    ex = sub.add_parser("exchange", help="codeを短期→長期に交換")
    ex.add_argument("--code", required=True, help="リダイレクトURLの ?code= の値")
    ex.set_defaults(func=cmd_exchange)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
