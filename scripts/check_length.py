#!/usr/bin/env python3
"""投稿の機械検証スクリプト。

投稿ファイル内のコードブロック（``` 区切り）を1投稿とみなし、各投稿について:
  - 500字超過
  - アスタリスク（*）混入
  - ページラベル（[1/3] など）混入
を検出する。1件でもNGがあれば終了コード1で終了する。

使い方:
  python3 scripts/check_length.py output/posts/2026-07-23_xxx.md
"""
import re
import sys

MAX_CHARS = 500
PAGE_LABEL = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]")


def extract_blocks(text):
    """``` で挟まれたコードブロックの中身を投稿として抽出。"""
    blocks = []
    lines = text.splitlines()
    inside = False
    buf = []
    for line in lines:
        if line.lstrip().startswith("```"):
            if inside:
                blocks.append("\n".join(buf))
                buf = []
                inside = False
            else:
                inside = True
                buf = []
            continue
        if inside:
            buf.append(line)
    if inside and buf:  # 閉じ忘れ救済
        blocks.append("\n".join(buf))
    return blocks


def check_file(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    blocks = extract_blocks(text)
    if not blocks:
        print(f"[WARN] {path}: コードブロック（投稿）が見つかりません")
        return False

    ng = False
    for i, block in enumerate(blocks, 1):
        content = block.strip("\n")
        length = len(content)
        issues = []
        if length > MAX_CHARS:
            issues.append(f"文字数超過 {length}字 (>{MAX_CHARS})")
        if "*" in content:
            issues.append("アスタリスク混入")
        if PAGE_LABEL.search(content):
            issues.append("ページラベル混入")
        status = "NG" if issues else "OK"
        detail = "  ｜  ".join(issues) if issues else f"{length}字"
        print(f"  投稿[{i}] {status}: {detail}")
        if issues:
            ng = True
    return not ng


def main():
    if len(sys.argv) < 2:
        sys.exit("使い方: python3 scripts/check_length.py <投稿ファイル> [...]")
    all_ok = True
    for path in sys.argv[1:]:
        print(f"# {path}")
        ok = check_file(path)
        all_ok = all_ok and ok
        print()
    if all_ok:
        print("[PASS] すべての投稿が検証を通過しました")
        sys.exit(0)
    else:
        print("[FAIL] NG投稿があります。修正してください")
        sys.exit(1)


if __name__ == "__main__":
    main()
