"""共通ユーティリティ。

- .env の読み込み（依存を増やさず自前パーサ）
- slug 生成 / 日付
- materials への保存ヘルパ
"""
import os
import re
import sys
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIALS = ROOT / "materials"
MATERIALS_BUZZ = MATERIALS / "buzz"
ASSETS = ROOT / "assets"


def load_env():
    """ルートの .env を読み込んで os.environ に反映（既存値は上書きしない）。"""
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
    if required and not val:
        sys.exit(f"[ERROR] 環境変数 {key} が未設定です。.env を確認してください。")
    return val


def today():
    return datetime.date.today().isoformat()


def slugify(text, maxlen=40):
    """日本語もそのまま残しつつ、ファイル名に使えない文字を除去した slug。"""
    if not text:
        return "untitled"
    text = text.strip()
    # 記号類をハイフンに
    text = re.sub(r"[\\/:*?\"<>|#\s　]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if len(text) > maxlen:
        text = text[:maxlen]
    return text or "untitled"


def ensure_dirs():
    MATERIALS.mkdir(parents=True, exist_ok=True)
    MATERIALS_BUZZ.mkdir(parents=True, exist_ok=True)


def save_material(filename, content, subdir=None):
    """materials（またはサブディレクトリ）にテキスト保存し、パスを返す。"""
    ensure_dirs()
    base = MATERIALS if subdir is None else (MATERIALS / subdir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / filename
    path.write_text(content, encoding="utf-8")
    return path


def eprint(*args):
    print(*args, file=sys.stderr)
