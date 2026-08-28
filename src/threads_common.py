"""Threads自動投稿システムの共通ユーティリティ。

- .env 読み込み（ローカル実行用。GitHub Actions では Secrets を環境変数で渡す）
- 文字数カウント（絵文字を1文字として正しく数える書記素クラスタ方式）
- URL数カウント
- 投稿前バリデーション
- JST の時刻処理
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except Exception:  # 予備（zoneinfoが無い環境）
    from datetime import timezone, timedelta
    JST = timezone(timedelta(hours=9))

ROOT = Path(__file__).resolve().parent.parent

# 制約
MAX_LENGTH = 500          # Threadsの本文上限（文字数）
MAX_URLS = 5              # URLは5つまで

# 文字数カウントの方式:
#   graphemes  … 書記素クラスタ（絵文字=1。人間が見た「文字数」。既定・推奨）
#   codepoints … Pythonのlen()（コードポイント数）
#   bytes      … UTF-8バイト数
LENGTH_MODE = os.environ.get("LENGTH_MODE", "graphemes").strip().lower()

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
def load_env():
    """ルートの .env を読み込む（既存の環境変数は上書きしない）。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def env(key, default=None, required=False):
    load_env()
    val = os.environ.get(key, default)
    if required and (val is None or val == ""):
        sys.exit(f"[ERROR] 環境変数 {key} が未設定です。Secrets もしくは .env を確認してください。")
    return val


# ---------------------------------------------------------------------------
# 文字数・URL数
# ---------------------------------------------------------------------------
def _count_graphemes(text: str) -> int:
    """書記素クラスタ数。ZWJや肌色修飾を含む絵文字も1文字として数える。

    regex パッケージがあれば \\X を使う。無ければコードポイント数にフォールバック
    （その場合 requirements.txt の regex を入れてください）。
    """
    try:
        import regex  # type: ignore
        return len(regex.findall(r"\X", text))
    except Exception:
        return len(text)


def count_length(text: str, mode: str | None = None) -> int:
    mode = (mode or LENGTH_MODE)
    if mode == "bytes":
        return len(text.encode("utf-8"))
    if mode == "codepoints":
        return len(text)
    return _count_graphemes(text)


def count_urls(text: str) -> int:
    return len(_URL_RE.findall(text))


def validate_text(text: str) -> list[str]:
    """投稿前バリデーション。問題があればエラーメッセージのリストを返す（空なら合格）。"""
    errors: list[str] = []
    if text is None or text.strip() == "":
        errors.append("本文が空です")
        return errors
    length = count_length(text)
    if length > MAX_LENGTH:
        errors.append(f"本文が{MAX_LENGTH}文字を超えています（{length}文字 / mode={LENGTH_MODE}）")
    urls = count_urls(text)
    if urls > MAX_URLS:
        errors.append(f"URLが{MAX_URLS}個を超えています（{urls}個）")
    return errors


# ---------------------------------------------------------------------------
# 時刻
# ---------------------------------------------------------------------------
def now_jst() -> datetime:
    return datetime.now(JST)


def parse_scheduled(value: str) -> datetime:
    """ISO8601 文字列をパース。タイムゾーンが無ければ JST とみなす。"""
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def log(*args):
    ts = now_jst().strftime("%Y-%m-%d %H:%M:%S JST")
    print(f"[{ts}]", *args, flush=True)


def eprint(*args):
    print(*args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 監視・通知（GitHub Actions Summary ＋ Webhook）
# ---------------------------------------------------------------------------
def summary_write(markdown: str):
    """GitHub Actions の Summary に追記（ローカルでは標準出力）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(markdown + "\n")
            return
        except Exception as e:  # noqa: BLE001
            eprint(f"[warn] Summary書き込み失敗: {e}")
    print("[SUMMARY]", markdown, flush=True)


def send_webhook(text: str):
    """Discord/Slack 互換 Webhook に送信（両対応のため content と text 両方を入れる）。"""
    url = env("NOTIFY_WEBHOOK_URL")
    if not url:
        return
    try:
        import requests
        requests.post(url, json={"content": text, "text": text}, timeout=20)
    except Exception as e:  # noqa: BLE001
        eprint(f"[warn] Webhook送信失敗: {e}")


def warn(title: str, body: str = ""):
    """警告を Summary と Webhook の両方に出す。"""
    log(f"[WARN] {title} {body}".rstrip())
    summary_write(f"### ⚠️ {title}\n\n{body}")
    send_webhook(f"⚠️ [Threads自動投稿] {title}\n{body}".rstrip())


def summary_section(title: str, body: str):
    summary_write(f"### {title}\n\n{body}")
