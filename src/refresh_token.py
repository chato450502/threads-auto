#!/usr/bin/env python3
"""Threads 長期アクセストークンをリフレッシュし、GitHub Secrets を更新する。

Threads の長期トークンは有効期限が約60日。定期的にリフレッシュすると期限が延長される。
このスクリプトは月1回（refresh.yml）実行される想定。

処理:
  1) GET {BASE}/refresh_access_token?grant_type=th_refresh_token&access_token=OLD
     → 新しい access_token と expires_in
  2) GitHub API で対象リポジトリの Secret（既定 THREADS_ACCESS_TOKEN）を新トークンに更新
     （リポジトリ公開鍵で sealed box 暗号化して PUT）

必要な環境変数:
  THREADS_ACCESS_TOKEN       現在の長期トークン
  GH_PAT                     Secrets を更新できる PAT（repo secrets の書き込み権限）
  GITHUB_REPOSITORY          "owner/repo"（Actions では自動で入る）
  THREADS_TOKEN_SECRET_NAME  更新先のSecret名（既定 THREADS_ACCESS_TOKEN）
  DRY_RUN                    true なら実際には更新せずログのみ
"""
from __future__ import annotations

import base64
import datetime
import json
import sys

import requests

import threads_common as tc
from threads_common import log

GRAPH_API_BASE = tc.env("GRAPH_API_BASE", "https://graph.threads.net/v1.0")
TOKEN_META_PATH = tc.ROOT / "posts" / "token_meta.json"


def write_token_meta(expires_in: int):
    """トークン期限を記録（generate.py の30日監視が参照）。"""
    expires_at = tc.now_jst() + datetime.timedelta(seconds=expires_in)
    TOKEN_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_META_PATH.write_text(
        json.dumps({"expires_at": expires_at.isoformat(),
                    "refreshed_at": tc.now_jst().isoformat(),
                    "expires_in_days": expires_in // 86400}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    log(f"token_meta.json を更新（期限 {expires_at.date()}）")


def refresh_long_lived_token(old_token: str) -> dict:
    url = f"{GRAPH_API_BASE}/refresh_access_token"
    params = {"grant_type": "th_refresh_token", "access_token": old_token}
    resp = requests.get(url, params=params, timeout=60)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "access_token" not in data:
        raise RuntimeError(f"トークンリフレッシュ失敗 HTTP {resp.status_code}: {resp.text[:500]}")
    return data


# ---------------------------------------------------------------------------
# GitHub Secrets 更新
# ---------------------------------------------------------------------------
def _gh_headers(pat: str) -> dict:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_repo_public_key(repo: str, pat: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
    resp = requests.get(url, headers=_gh_headers(pat), timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"公開鍵取得失敗 HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """libsodium sealed box でリポジトリ公開鍵を使って暗号化（GitHub仕様）。"""
    try:
        from nacl import encoding, public
    except ImportError:
        sys.exit("[ERROR] PyNaCl 未インストール。pip install pynacl（requirements.txt参照）")
    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def update_secret(repo: str, pat: str, name: str, value: str):
    key = get_repo_public_key(repo, pat)
    encrypted_value = encrypt_secret(key["key"], value)
    url = f"https://api.github.com/repos/{repo}/actions/secrets/{name}"
    body = {"encrypted_value": encrypted_value, "key_id": key["key_id"]}
    resp = requests.put(url, headers=_gh_headers(pat), json=body, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"Secret更新失敗 HTTP {resp.status_code}: {resp.text[:500]}")
    log(f"Secret '{name}' を更新しました（HTTP {resp.status_code}）")


def main():
    dry_run = str(tc.env("DRY_RUN", "false")).strip().lower() in ("1", "true", "yes")

    old_token = tc.env("THREADS_ACCESS_TOKEN", required=True)
    secret_name = tc.env("THREADS_TOKEN_SECRET_NAME", "THREADS_ACCESS_TOKEN")

    log("トークンをリフレッシュします…")
    result = refresh_long_lived_token(old_token)
    new_token = result["access_token"]
    expires_in = int(result.get("expires_in", 0))
    log(f"新トークン取得（末尾4桁 …{new_token[-4:]}）。有効期限: 約{expires_in // 86400}日")

    write_token_meta(expires_in)

    if dry_run:
        log("[DRY RUN] Secret は更新しません（トークン取得の確認のみ）")
        return

    repo = tc.env("GITHUB_REPOSITORY", required=True)
    pat = tc.env("GH_PAT", required=True)
    update_secret(repo, pat, secret_name, new_token)
    log("完了。次回の投稿ジョブから新しいトークンが使われます")


if __name__ == "__main__":
    main()
