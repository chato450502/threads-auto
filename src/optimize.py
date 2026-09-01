#!/usr/bin/env python3
"""型の自動最適化（週1）。

自分の投稿の成績（measure.py が出す performance.json）を見て、
  - 伸びている型は そのまま使い続ける
  - 伸びていない型を引退させ、新しくリサーチした型と入れ替える（1-in-1-out）
を自動で行う。すべて GitHub 上で完結（optimize.yml から実行）。

安全のためのガードレール:
  - 一定回数(MIN_SAMPLES)以上使われ、かつ登録から一定日数(PROBATION_DAYS)経った型だけ引退対象
  - 引退は「明確に下位（中央値のRATIO倍未満）」のときだけ、1回の実行で最大1つ
  - 有効な型が MIN_ACTIVE を下回らないよう維持
  - 入れ替えが起きたときだけ新しい型をリサーチ（コスト・規約配慮）

要 権限: threads_manage_insights（measure.py 用）
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

import threads_common as tc
from threads_common import log

sys.path.insert(0, str(tc.ROOT / "scripts"))
import fetch_threads as ft  # noqa: E402

import measure  # noqa: E402
import generate as g  # 型ファイルのパース・account読込を再利用  # noqa: E402

MODEL = tc.env("GENERATE_MODEL", "claude-sonnet-4-6")
KATA_DIR = tc.ROOT / "kata-library"
INDEX_PATH = KATA_DIR / "INDEX.md"
PERFORMANCE_PATH = tc.ROOT / "posts" / "performance.json"

MIN_SAMPLES = int(tc.env("OPT_MIN_SAMPLES", "3"))
PROBATION_DAYS = int(tc.env("OPT_PROBATION_DAYS", "21"))
MIN_ACTIVE = int(tc.env("OPT_MIN_ACTIVE", "4"))
RATIO = float(tc.env("OPT_RATIO", "0.6"))


# ---------------------------------------------------------------------------
# 型ファイルの読み込み（frontmatter付き・引退含め全部）
# ---------------------------------------------------------------------------
def load_all_katas() -> list[dict]:
    out = []
    for p in sorted(KATA_DIR.glob("*.md")):
        if p.name in ("INDEX.md", "_common-modules.md"):
            continue
        text = p.read_text(encoding="utf-8")
        fm, _body = g._parse_frontmatter(text)
        out.append({"path": p, "fm": fm, "text": text,
                    "slug": fm.get("name", p.stem),
                    "active": str(fm.get("active", "true")).strip().lower() != "false"})
    return out


def _age_days(fm: dict) -> int:
    d = fm.get("registered", "")
    try:
        reg = datetime.date.fromisoformat(d.strip())
        return (tc.now_jst().date() - reg).days
    except Exception:
        return 999  # 日付不明は「古い」とみなす


def set_frontmatter_field(text: str, key: str, value: str) -> str:
    """frontmatter の key を value に更新（無ければ追記）。"""
    if not text.startswith("---"):
        return text
    head, _, rest = text[3:].partition("---")
    lines = head.splitlines()
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(f"{key}:"):
            lines[i] = f"{key}: {value}"
            found = True
            break
    if not found:
        lines.append(f"{key}: {value}")
    return "---" + "\n".join(lines) + "---" + rest


# ---------------------------------------------------------------------------
# 引退判定
# ---------------------------------------------------------------------------
def choose_retire(katas: list[dict], perf: dict):
    """引退させるべき型を1つ返す（無ければ None）。"""
    active = [k for k in katas if k["active"]]
    # 1つ引退＝1つ追加なので有効数は変わらない。floorを下回っている時だけ引退を止める。
    if len(active) < MIN_ACTIVE:
        return None, f"有効な型が最低数({MIN_ACTIVE})未満のため引退しない"

    per = perf.get("per_kata", {})
    # 判定に足るデータがある型だけを候補に
    eligible = []
    for k in active:
        p = per.get(k["slug"], {})
        n = p.get("post_count", 0)
        if n >= MIN_SAMPLES and _age_days(k["fm"]) >= PROBATION_DAYS:
            eligible.append((k, p.get("avg_views", 0)))
    if len(eligible) < 2:
        return None, "判定に足るデータがある型が少ない（蓄積中）"

    views = sorted(v for _k, v in eligible)
    median = views[len(views) // 2]
    worst_k, worst_v = min(eligible, key=lambda kv: kv[1])
    if median > 0 and worst_v < median * RATIO:
        return worst_k, f"最下位 {worst_k['slug']}（平均表示{worst_v}）が中央値{median}の{RATIO}倍未満"
    return None, "明確な下位型なし（全型が許容範囲）"


# ---------------------------------------------------------------------------
# 新しい型のリサーチ＋型化（自動 kata-register）
# ---------------------------------------------------------------------------
def collect_buzz_thread() -> dict | None:
    """ベンチマークアカウントから伸びている連投を1つ集める。"""
    acc = g.load_account()
    accounts = acc.get("benchmark_accounts") or []
    if not accounts:
        return None
    usernames = [ft.normalize_username(a) for a in accounts]
    items = ft.run_actor("futurizerush~meta-threads-scraper", {
        "mode": "user", "usernames": usernames, "max_posts": 10, "search_filter": "top"})
    parents = []
    for it in items:
        text = (it.get("text_content") or "").strip()
        if not text or it.get("is_reply"):
            continue
        parents.append({"text": text, "like_count": int(it.get("like_count") or 0),
                        "post_url": it.get("post_url") or "", "username": it.get("username") or ""})
    if not parents:
        return None
    parents.sort(key=lambda x: x["like_count"], reverse=True)
    top = parents[0]
    # 連投の続きを取得して結合
    thread_texts = [top["text"]]
    try:
        by_src = ft.fetch_replies([top["post_url"]])
        thread = ft.build_thread(top, by_src.get(top["post_url"], []))
        thread_texts = [p["text"] for p in thread]
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] 返信取得失敗（1本目のみ使用）: {e}")
    return {"author": "@" + top["username"], "posts": thread_texts, "likes": top["like_count"]}


def register_kata_from_buzz(buzz: dict, existing_slugs: list[str]) -> str | None:
    """バズ連投から新しい型を1つ作り kata-library に保存。slug を返す。"""
    import anthropic
    client = anthropic.Anthropic()
    joined = "\n---\n".join(buzz["posts"])
    example = ""
    for p in sorted(KATA_DIR.glob("*.md")):
        if p.name not in ("INDEX.md", "_common-modules.md"):
            example = p.read_text(encoding="utf-8")
            break

    system = (
        "あなたはSNSバズ投稿の構造アナリストです。渡されたThreadsの連投を分析し、"
        "再利用できる『型（構造テンプレート）』を1つ作ります。"
        "\n\n【型化のルール】"
        "\n- フレーズの実例ではなく『機能』で書く（例: ×『\"エグい話\"で始める』 → ○『核心を明かす予告で始める。表現は自由』）"
        "\n- 元投稿の本文はコピーしない（出典は要約1行のみ）"
        "\n- 数字（項目数など）は『素材から決める』と可変にする"
        "\n- 7観点で分析: 分類/連投数と各ブロックの役割/フック技法/読者を留める仕掛け/エンゲージ源(保存等)/心理設計/ファネル役割"
        f"\n- 既存の型（重複を避ける）: {', '.join(existing_slugs)}"
        "\n\n【出力形式】次のMarkdownファイルそのものを出力（前後に説明を付けない）。"
        "1行目に 'SLUG: <英小文字ハイフンの一意なslug>' を書き、2行目以降に下記フォーマットのファイル本体:"
        "\n---\nname: <slug>\ncategory: <日本語の分類名>\nregistered: " + tc.now_jst().date().isoformat() +
        "\nsource_summary: <元連投の要約1行・本文コピー禁止>\nthread_range: <例 3-5>\nhook: <フック技法>"
        "\nengagement: <保存/リプ/共有 等>\nfunnel_role: <認知/教育/CTA前段 等>\nuses: 0\nactive: true\n---\n\n"
        "## 適合する素材の特徴\n- ...\n\n## ブロック別の型プロンプト\n### [1/N] フック\n1. ...\n\n## この型が効く理由（心理設計）\n- ...\n\n"
        "【参考：既存の型ファイルの書き方】\n" + example[:1500]
    )
    user = f"次のThreads連投（{buzz['author']}・いいね{buzz['likes']}）を型化してください:\n\n{joined[:5000]}"
    resp = client.messages.create(model=MODEL, max_tokens=2000, system=system,
                                  messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()

    m = re.match(r"SLUG:\s*([a-z0-9\-]+)\s*\n(.*)$", text, re.DOTALL)
    if not m:
        log(f"  [warn] 型化の出力を解釈できませんでした: {text[:120]}")
        return None
    slug = m.group(1)
    if slug in existing_slugs:
        slug = f"{slug}-{tc.now_jst().strftime('%m%d')}"
    file_text = m.group(2).strip() + "\n"
    if "name:" not in file_text[:200]:
        log("  [warn] frontmatterが不正")
        return None
    (KATA_DIR / f"{slug}.md").write_text(file_text, encoding="utf-8")
    log(f"  新しい型を登録: {slug}")
    return slug


# ---------------------------------------------------------------------------
# INDEX 再生成
# ---------------------------------------------------------------------------
def rebuild_index(katas: list[dict]):
    header = (
        "# 型ライブラリ 目次（INDEX）\n\n"
        "> `optimize`（週1）と `/kata-register` が更新する。引退した型は active=false。\n\n"
        "| slug | 分類 | 連投数 | フック技法 | 主エンゲージ源 | ファネル役割 | 使用回数 | 状態 | 登録日 |\n"
        "|------|------|--------|-----------|---------------|-------------|---------|------|--------|\n")
    rows = []
    for k in sorted(katas, key=lambda x: x["slug"]):
        fm = k["fm"]
        state = "有効" if k["active"] else "引退"
        rows.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            k["slug"], fm.get("category", ""), fm.get("thread_range", ""),
            fm.get("hook", ""), fm.get("engagement", ""), fm.get("funnel_role", ""),
            fm.get("uses", "0"), state, fm.get("registered", "")))
    INDEX_PATH.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    token = tc.env("THREADS_ACCESS_TOKEN", required=True)
    perf = measure.measure(token)  # 最新の成績を測ってから判定

    katas = load_all_katas()
    active = [k for k in katas if k["active"]]
    log(f"有効な型: {len(active)}種 / 全型: {len(katas)}種")

    retire, reason = choose_retire(katas, perf)
    if retire is None:
        tc.summary_section("型の最適化", f"今週は入れ替えなし。\n理由: {reason}")
        log(f"入れ替えなし: {reason}")
        rebuild_index(katas)
        return

    log(f"引退候補: {retire['slug']}（{reason}）")

    # 新しい型をリサーチ（入れ替えが起きるときだけ）
    buzz = collect_buzz_thread()
    if not buzz:
        tc.summary_section("型の最適化", f"引退候補 {retire['slug']} はあったが、リサーチ素材が集まらず今週は保留。")
        rebuild_index(katas)
        return
    new_slug = register_kata_from_buzz(buzz, [k["slug"] for k in katas])
    if not new_slug:
        tc.summary_section("型の最適化", f"引退候補 {retire['slug']} はあったが、新しい型の作成に失敗し今週は保留。")
        rebuild_index(katas)
        return

    # 引退を適用（ファイルは残し active=false に）
    new_text = set_frontmatter_field(retire["text"], "active", "false")
    retire["path"].write_text(new_text, encoding="utf-8")
    log(f"引退適用: {retire['slug']} → active=false")

    katas = load_all_katas()
    rebuild_index(katas)
    tc.summary_section(
        "型の最適化 実行 🔁",
        f"- 引退: **{retire['slug']}**（{reason}）\n"
        f"- 新規: **{new_slug}**（{buzz['author']} の連投から型化）\n"
        f"- 有効な型: {len([k for k in katas if k['active']])}種")


if __name__ == "__main__":
    main()
