#!/usr/bin/env python3
"""新しいアカウントでテンプレを使い始めるときの初期化スクリプト。

前の運用者の投稿履歴・在庫・状態を空に戻す。設定ファイル（自分用に書き換えるもの）は
消さず、案内だけ表示する。

使い方:
  python scripts/init_account.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  reset: {path.relative_to(ROOT)}")


def main():
    print("■ 状態ファイルを空に初期化します（投稿履歴・在庫・素材使用履歴）")
    write(ROOT / "posts" / "queue.json", "[]\n")
    write(ROOT / "posts" / "state.json", '{\n  "slots": {}\n}\n')
    write(ROOT / "posts" / "processed.json", "[]\n")
    write(ROOT / "posts" / "used_videos.json", "[]\n")
    tm = ROOT / "posts" / "token_meta.json"
    if tm.exists():
        tm.unlink()
        print(f"  remove: posts/token_meta.json（トークン取得後に再生成される）")

    # evergreen を使う場合のため used フラグを戻す（任意）
    ev = ROOT / "fallback" / "evergreen.json"
    if ev.exists():
        data = json.loads(ev.read_text(encoding="utf-8"))
        posts = data.get("posts", []) if isinstance(data, dict) else data
        for p in posts:
            p["used"] = False
        ev.write_text(json.dumps({"posts": posts} if isinstance(data, dict) else posts,
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("  reset: fallback/evergreen.json（used=false）")

    print("\n■ 次に、自分用に書き換えるファイル（消していません）:")
    for f in [
        "config/account.json     … ハンドル・表示名・ジャンル・note誘導先（account.example.json 参照）",
        "config/slots.json       … 投稿枠の時刻・テーマ・トーン",
        "config/ng_words.json    … 禁止表現",
        "assets/persona.md       … キャラクター設定（声・口調）",
        "assets/cta.md           … CTAの参考例",
        "assets/youtube-channels.txt … 素材にする定番チャンネル",
        "kata-library/*.md       … 型（そのまま流用可 / 自分で追加も可）",
    ]:
        print("  - " + f)
    print("\n■ そのあと GitHub Secrets を登録 → SETUP.md の手順へ")


if __name__ == "__main__":
    main()
