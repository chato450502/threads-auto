#!/usr/bin/env python3
"""下書き自動生成（kata-write 自動化版）。

YouTube字幕を素材に、kata-library の「型」を使って みれい の連投（スレッド）を生成する。
毎日 JST 3:00（generate.yml）に実行し、常に3日分（9枠）の在庫を queue.json に維持する。

kata-write の流れを自動化:
  1. 素材: 定番チャンネル（assets/youtube-channels.txt）から未使用・伸びている動画を選び字幕取得
  2. 型選択: kata-library の型を「直近2枠で使った型は避ける／同日同型2本は避ける」でローテーション
  3. リライト: 型プロンプト＋_common-modules＋persona を適用し、素材の内容で連投を生成
  4. CTA: 連投の最終投稿にプロフ/リンク誘導を みれい の声で付ける（ほぼ毎回）
  5. 検証: 各投稿500字以内／アスタリスク無し／ページラベル無し

各枠 = 1連投（型の thread_range に応じ 3〜10投稿）。

ドライラン: DRY_RUN=true / --dry-run（Claude・YouTubeを呼ばず不足枠のみ表示）
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import slots
import threads_common as tc
from threads_common import log

sys.path.insert(0, str(tc.ROOT / "scripts"))
import fetch_youtube as yt  # noqa: E402

MODEL = tc.env("GENERATE_MODEL", "claude-sonnet-4-6")
INVENTORY_DAYS = 3
SLOTS_PER_DAY_TARGET = INVENTORY_DAYS * 3
MAX_REGEN = 3
POST_DELIM = "===NEXT==="

QUEUE_PATH = tc.ROOT / "posts" / "queue.json"
USED_VIDEOS_PATH = tc.ROOT / "posts" / "used_videos.json"
KATA_DIR = tc.ROOT / "kata-library"
PERSONA_PATH = tc.ROOT / "assets" / "persona.md"
CTA_REF_PATH = tc.ROOT / "assets" / "cta.md"
COMMON_PATH = KATA_DIR / "_common-modules.md"
CLAUDE_MD = tc.ROOT / "CLAUDE.md"
ACCOUNT_PATH = tc.ROOT / "config" / "account.json"

PAGE_LABEL = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]")


# ---------------------------------------------------------------------------
# 入出力
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default
    txt = path.read_text(encoding="utf-8").strip()
    return json.loads(txt) if txt else default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ---------------------------------------------------------------------------
# 型ライブラリの読み込み
# ---------------------------------------------------------------------------
def _parse_frontmatter(text: str):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = {}
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
            return fm, parts[2]
    return {}, text


def _section(body: str, heading: str) -> str:
    """'## <heading>' 見出し以下、次の '## ' 手前までを返す。"""
    lines = body.splitlines()
    out, capturing = [], False
    for ln in lines:
        if ln.startswith("## "):
            if capturing:
                break
            capturing = heading in ln
            continue
        if capturing:
            out.append(ln)
    return "\n".join(out).strip()


def load_katas() -> list[dict]:
    out = []
    for p in sorted(KATA_DIR.glob("*.md")):
        if p.name in ("INDEX.md", "_common-modules.md"):
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        out.append({
            "slug": fm.get("name", p.stem),
            "category": fm.get("category", ""),
            "thread_range": fm.get("thread_range", "3-5"),
            "fit": _section(body, "適合する素材の特徴"),
            "prompt": _section(body, "ブロック別の型プロンプト"),
            "path": p,
        })
    return out


def thread_range(tr: str):
    m = re.findall(r"\d+", tr or "")
    if len(m) >= 2:
        return int(m[0]), int(m[1])
    if len(m) == 1:
        return int(m[0]), int(m[0])
    return 3, 5


# ---------------------------------------------------------------------------
# 型ローテーション
# ---------------------------------------------------------------------------
def choose_kata(katas: list[dict], queue: list, slot_date: str) -> dict:
    """直近2枠で使った型を避け、同日に同じ型を2本作らない。残りから使用回数の少ない型を選ぶ。"""
    withk = [it for it in queue if it.get("kata")]
    withk.sort(key=lambda it: it.get("slot_key", ""), reverse=True)
    recent2 = [it["kata"] for it in withk[:2]]
    same_day = {it["kata"] for it in queue
                if it.get("kata") and str(it.get("slot_key", "")).startswith(slot_date)}

    allowed = [k for k in katas if k["slug"] not in recent2 and k["slug"] not in same_day]
    if not allowed:
        allowed = [k for k in katas if k["slug"] not in same_day] or katas

    def uses(k):
        return sum(1 for it in queue if it.get("kata") == k["slug"])
    allowed.sort(key=uses)
    return allowed[0]


# ---------------------------------------------------------------------------
# 在庫計算・素材プール
# ---------------------------------------------------------------------------
def needed_slots(queue: list):
    existing = {it.get("slot_key") for it in queue
                if it.get("status") in ("pending", "posted", "in_progress")}
    return [(dt, s) for dt, s in slots.upcoming_slots(SLOTS_PER_DAY_TARGET)
            if slots.slot_key(dt) not in existing]


def build_video_pool(need: int, used_ids: set) -> list[dict]:
    """定番チャンネルから、未使用・2分以上・再生回数上位の動画を集める。"""
    pool, seen = [], set()
    for ch in yt.load_channel_list():
        if len(pool) >= need + 3:
            break
        try:
            vids = yt.rank_channel_videos(ch, recent=30, min_minutes=2)
        except Exception as e:  # noqa: BLE001
            log(f"  [warn] チャンネル {ch} の取得失敗: {e}")
            continue
        for v in vids:
            if v["id"] in used_ids or v["id"] in seen:
                continue
            seen.add(v["id"])
            pool.append(v)
    return pool


# ---------------------------------------------------------------------------
# Claude 生成
# ---------------------------------------------------------------------------
def _client():
    import anthropic
    return anthropic.Anthropic()


def load_account() -> dict:
    """アカウント固有の値（ハンドル・表示名・ジャンル・note誘導先）を config/account.json から読む。"""
    acc = load_json(ACCOUNT_PATH, {}) or {}
    if not acc.get("note_link"):  # 後方互換: 旧CLAUDE.mdからの拾い上げ
        md = read_text_file(CLAUDE_MD)
        m = re.search(r"リンク誘導先（note）\*\*:\s*(\S+)", md)
        if m:
            acc["note_link"] = m.group(1)
    return acc


def _clean_post(p: str) -> str:
    """本文に紛れ込んだコードフェンス（```）行や区切りの残骸を除去する。"""
    lines = [ln for ln in p.splitlines() if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def _split_posts(text: str) -> list[str]:
    parts = [_clean_post(p) for p in (text or "").strip().split(POST_DELIM)]
    return [p for p in parts if p]


def validate_posts(posts: list[str]) -> list[str]:
    issues = []
    for i, p in enumerate(posts, 1):
        n = tc.count_length(p)
        if n > tc.MAX_LENGTH:
            issues.append(f"{i}本目 {n}字超過")
        if "*" in p:
            issues.append(f"{i}本目 アスタリスク")
        if PAGE_LABEL.search(p):
            issues.append(f"{i}本目 ページラベル")
    return issues


def generate_thread(kata: dict, transcript: str, title: str):
    client = _client()
    persona = read_text_file(PERSONA_PATH)
    common = read_text_file(COMMON_PATH)
    cta_ref = read_text_file(CTA_REF_PATH)
    acc = load_account()
    note = acc.get("note_link", "")
    handle = acc.get("handle", "")
    name = acc.get("display_name", "") or "このアカウント"
    genre = acc.get("genre", "")
    lo, hi = thread_range(kata["thread_range"])

    system = (
        f"あなたは Threads アカウント {handle}「{name}」の中の人です。"
        + (f"発信ジャンルは「{genre}」。" if genre else "")
        + "YouTube動画の書き起こしを素材に、下記の『型』の構造で連投（スレッド）を作ります。"
        "\n\n【キャラクター設定（声・口調・人称・語尾はこれに従う）】\n" + persona +
        "\n\n【共通ルール（出力形式・禁止事項）】\n" + common +
        "\n\n【今回使う型（この構造・機能に従う。フレーズは真似ない）】\n" + kata["prompt"] +
        "\n\n【厳守】"
        f"\n- 連投は {lo}〜{hi} 投稿。素材の論点数に応じて本数を決める。"
        "\n- 内容は素材（書き起こし）から取る。型からは構造だけ。素材の言い回しをそのまま使わない。"
        "\n- 各投稿は500文字以内（日本語の文字数）。アスタリスク（*）やページラベル（[1/3]等）は使わない。"
        f"\n- 最終投稿は、本文の流れから自然につながるCTA（プロフィールのリンク誘導）を {name} の声で新規に書く。"
        + (f"\n  誘導先の参考: プロフィールのリンク（note: {note}）。URLは本文に貼らずプロフ誘導でよい。" if note else "") +
        "\n\n【CTAの参考例（丸写し禁止・構成と機能だけ参考）】\n" + cta_ref[:1200] +
        f"\n\n【出力形式】各投稿を『{POST_DELIM}』の行で区切って順番に出力する。JSONやコードブロック・説明文・見出し番号は付けない。"
    )
    user = (
        f"【素材：YouTube動画『{title}』の書き起こし】\n{transcript[:6000]}\n\n"
        f"この素材の内容だけを使い、上の型に従って {lo}〜{hi} 投稿の連投を作成してください。"
        f"各投稿を『{POST_DELIM}』で区切って出力。"
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=4000, system=system,
        messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _split_posts(text)


def produce_thread(kata: dict, transcript: str, title: str):
    """検証を通る連投を作る。最大MAX_REGEN回。返り値: posts or None"""
    lo, _hi = thread_range(kata["thread_range"])
    for attempt in range(1, MAX_REGEN + 1):
        try:
            posts = generate_thread(kata, transcript, title)
        except Exception as e:  # noqa: BLE001
            log(f"  [gen] {attempt}/{MAX_REGEN} 生成エラー: {e}")
            continue
        if len(posts) < max(2, lo - 1):
            log(f"  [gen] {attempt}/{MAX_REGEN} 投稿数不足（{len(posts)}本）")
            continue
        issues = validate_posts(posts)
        if issues:
            log(f"  [gen] {attempt}/{MAX_REGEN} 検証NG: {issues[:3]}")
            continue
        log(f"  [gen] {attempt}/{MAX_REGEN} OK（{len(posts)}本連投）")
        return posts
    return None


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def process(dry_run: bool):
    queue = load_json(QUEUE_PATH, [])
    used_videos = set(load_json(USED_VIDEOS_PATH, []))
    todo = needed_slots(queue)
    katas = load_katas()

    log(f"在庫目標={SLOTS_PER_DAY_TARGET}枠 / 不足={len(todo)}枠 / 型数={len(katas)} / dry_run={dry_run}")

    if not todo:
        tc.summary_section("下書き生成", "在庫は充足しています（生成なし）。")
        return
    if not katas:
        tc.warn("型が無い", "kata-library に型がありません。/kata-register で登録してください。")
        return

    if dry_run:
        lines = "\n".join(f"- {slots.slot_key(dt)}: {s.get('theme','')}" for dt, s in todo)
        tc.summary_section("下書き生成（ドライラン）", f"埋めるべき枠 {len(todo)}件:\n{lines}")
        log("[DRY RUN] YouTube/Claudeは呼びません。対象枠の表示のみ")
        return

    pool = build_video_pool(len(todo), used_videos)
    log(f"素材プール: {len(pool)}本の候補動画")

    made = failed = 0
    pool_i = 0
    for dt, scfg in todo:
        key = slots.slot_key(dt)
        slot_date = key.split(" ")[0]
        kata = choose_kata(katas, queue, slot_date)

        # 素材を確保（字幕が取れる動画に当たるまでプールを進める）
        transcript = title = video_id = None
        while pool_i < len(pool):
            cand = pool[pool_i]
            pool_i += 1
            t = yt.get_transcript_text(cand["id"])
            if t and len(t) > 200:
                transcript, title, video_id = t, cand["title"], cand["id"]
                break
        if not transcript:
            failed += 1
            tc.warn("素材切れ", f"枠 `{key}`: 字幕の取れる未使用動画がありませんでした。")
            continue

        log(f"[slot] {key} 型={kata['slug']} 素材=『{title[:30]}』")
        posts = produce_thread(kata, transcript, title)
        if not posts:
            failed += 1
            tc.warn("生成失敗", f"枠 `{key}`（型 {kata['slug']}）で検証を通る連投を作れませんでした。")
            continue

        queue.append({
            "id": f"gen-{key.replace(' ', 'T').replace(':', '')}",
            "slot_key": key,
            "slot_time": scfg.get("time"),
            "kata": kata["slug"],
            "source_video": video_id,
            "source_title": title,
            "posts": posts,
            "status": "pending",
            "posted_ids": [],
            "error": None,
            "created_at": tc.now_jst().isoformat(),
        })
        used_videos.add(video_id)
        save_json(QUEUE_PATH, queue)
        save_json(USED_VIDEOS_PATH, sorted(used_videos))
        made += 1

    tc.summary_section("下書き生成 結果",
                       f"- 生成: {made}連投\n- 失敗: {failed}\n- 対象枠: {len(todo)}")
    if made == 0 and todo:
        tc.warn("生成全滅", f"今回 {len(todo)} 枠すべてで連投を作れませんでした。")


def main():
    ap = argparse.ArgumentParser(description="下書き自動生成（kata-write自動化）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="生成する枠数の上限（テスト用）")
    args = ap.parse_args()
    env_dry = str(tc.env("DRY_RUN", "false")).strip().lower() in ("1", "true", "yes")
    if args.limit is not None:
        global SLOTS_PER_DAY_TARGET
        SLOTS_PER_DAY_TARGET = args.limit
    process(dry_run=args.dry_run or env_dry)


if __name__ == "__main__":
    main()
