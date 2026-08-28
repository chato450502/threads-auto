#!/usr/bin/env python3
"""下書き自動生成スクリプト（Claude API）。

毎日 JST 3:00（generate.yml）に実行し、常に3日分（9本）の在庫を queue.json に維持する。
在庫が足りない分の枠だけを生成する。

品質ゲート（各下書きに適用）:
  1. 機械チェック … 500文字以内 / URL5個以内 / CTAブロック有無 / 禁止表現（config/ng_words.json）
  2. AI採点 … 別のClaude呼び出しで0-100点（トーン一致・4段構成・直近30投稿との類似度）
  3. 80点未満なら再生成（最大3回）
  4. 3回落ちたら fallback/evergreen.json の未使用定型投稿を1本使う

枠ごとのテーマ・トーンは config/slots.json。生成時は直近30投稿の本文を渡し重複を避けさせる。

ドライラン: DRY_RUN=true / --dry-run（Claudeを呼ばず、埋めるべき枠の一覧だけ表示）
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
from pathlib import Path

import slots
import threads_common as tc
from threads_common import log

MODEL = tc.env("GENERATE_MODEL", "claude-sonnet-4-6")
INVENTORY_DAYS = 3
SLOTS_PER_DAY_TARGET = INVENTORY_DAYS * 3     # 9本
MAX_REGEN = 3
PASS_SCORE = 80
RECENT_N = 30

QUEUE_PATH = tc.ROOT / "posts" / "queue.json"
NG_WORDS_PATH = tc.ROOT / "config" / "ng_words.json"
EVERGREEN_PATH = tc.ROOT / "fallback" / "evergreen.json"
TOKEN_META_PATH = tc.ROOT / "posts" / "token_meta.json"
PERSONA_PATH = tc.ROOT / "assets" / "persona.md"
CTA_REF_PATH = tc.ROOT / "assets" / "cta.md"

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


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


def load_ng_words() -> list[str]:
    data = load_json(NG_WORDS_PATH, {"ng_words": []})
    if isinstance(data, list):
        return data
    return data.get("ng_words", [])


# ---------------------------------------------------------------------------
# 在庫計算・直近投稿
# ---------------------------------------------------------------------------
def compose_text(body: str, cta: str) -> str:
    body = (body or "").strip()
    cta = (cta or "").strip()
    return (body + "\n\n" + cta).strip() if cta else body


def needed_slots(queue: list):
    """これから埋めるべき（下書きが無い）直近の枠を返す。"""
    existing = {it.get("slot_key") for it in queue
                if it.get("status") in ("pending", "posted")}
    out = []
    for dt, scfg in slots.upcoming_slots(SLOTS_PER_DAY_TARGET):
        if slots.slot_key(dt) not in existing:
            out.append((dt, scfg))
    return out


def recent_texts(queue: list, n: int = RECENT_N) -> list[str]:
    """直近の投稿本文（posted優先・新しい順）を最大n件。重複回避のプロンプトに使う。"""
    items = [it for it in queue if it.get("text")]
    items.sort(key=lambda it: (it.get("status") == "posted", it.get("slot_key", "")),
               reverse=True)
    return [it["text"] for it in items[:n]]


# ---------------------------------------------------------------------------
# 品質ゲート 1: 機械チェック
# ---------------------------------------------------------------------------
def machine_check(body: str, cta: str, ng_words: list[str]):
    text = compose_text(body, cta)
    reasons = []
    length = tc.count_length(text)
    if length > tc.MAX_LENGTH:
        reasons.append(f"{tc.MAX_LENGTH}文字超過（{length}文字）")
    if len(_URL_RE.findall(text)) > tc.MAX_URLS:
        reasons.append(f"URL{tc.MAX_URLS}個超過")
    if not (cta or "").strip():
        reasons.append("CTAブロックが無い")
    hit = [w for w in ng_words if w and w in text]
    if hit:
        reasons.append("禁止表現: " + ", ".join(hit))
    return (len(reasons) == 0), reasons


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
def _client():
    import anthropic
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境から解決


def _extract_json(text: str) -> dict:
    """本文中の最初の JSON オブジェクトを取り出してパース。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
    return json.loads(m.group(0))


def _persona_context() -> str:
    persona = read_text_file(PERSONA_PATH)
    return f"\n\n【キャラクター設定（口調・人称・語尾はこれに従う）】\n{persona}" if persona.strip() else ""


def generate_draft(slot_cfg: dict, recents: list[str]) -> dict:
    """1本の下書きを生成。{'body','cta','text'} を返す。"""
    client = _client()
    recent_block = "\n---\n".join(recents) if recents else "（まだありません）"
    cta_ref = read_text_file(CTA_REF_PATH)

    system = (
        "あなたは20〜30代女性向けの恋愛（男性心理・愛され・溺愛系）ジャンルのThreads投稿を書くプロの編集者です。"
        + _persona_context()
        + "\n\n【厳守ルール】"
        "\n- 本文は必ず「共感 → 痛み → 視点転換 → 実践Tips」の4段構成にする。"
        "\n- 最後に、本文内容に合ったCTA（プロフィール誘導など）を1つ付ける。CTAは本文とは別に出力する。"
        "\n- 全体（本文＋CTA）で500文字以内（日本語の文字数）。"
        "\n- アスタリスク（*）やページラベル（[1/3]等）は使わない。強調は「」を使う。"
        "\n- 断定的な誇大表現・性的表現・特定個人や性別を貶める表現は禁止。"
        "\n- 直近の投稿と内容・言い回しが重複しないようにする。"
        "\n\n【出力形式】次のJSONのみを出力（前後に説明文を付けない）:"
        '\n{"body": "本文（4段構成）", "cta": "CTA文"}'
    )
    user = (
        f"【今回の枠のテーマ】{slot_cfg.get('theme','')}\n"
        f"【トーン】{slot_cfg.get('tone','')}\n\n"
        f"【CTAの参考例（丸写し禁止・構成の参考のみ）】\n{cta_ref[:1500]}\n\n"
        f"【直近30投稿（重複回避のため。これらと似せない）】\n{recent_block}\n\n"
        "上記を踏まえ、新しい投稿を1本、JSONで出力してください。"
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=2000, system=system,
        messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    body, cta = data.get("body", ""), data.get("cta", "")
    return {"body": body, "cta": cta, "text": compose_text(body, cta)}


def score_draft(draft_text: str, slot_cfg: dict, recents: list[str]) -> tuple[int, dict]:
    """別のClaude呼び出しで0-100点を付ける。"""
    client = _client()
    recent_block = "\n---\n".join(recents) if recents else "（まだありません）"
    system = (
        "あなたはThreads投稿の品質評価者です。次の観点で0-100点の総合点を付けてください。"
        "\n1. トーン一致度（指定テーマ・トーンに合っているか）"
        "\n2. 4段構成（共感→痛み→視点転換→実践Tips）が成立しているか"
        "\n3. 直近30投稿との類似度（似ているほど減点）"
        "\n次のJSONのみを出力: "
        '{"score": 整数, "tone": 0-100, "structure": 0-100, "similarity_penalty": 0-100, "reason": "短評"}'
    )
    user = (
        f"【テーマ】{slot_cfg.get('theme','')}\n【トーン】{slot_cfg.get('tone','')}\n\n"
        f"【評価対象の投稿】\n{draft_text}\n\n"
        f"【直近30投稿】\n{recent_block}\n\n"
        "JSONで採点してください。"
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=600, system=system,
        messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    return int(data.get("score", 0)), data


# ---------------------------------------------------------------------------
# フォールバック（定型投稿）
# ---------------------------------------------------------------------------
def pick_evergreen():
    data = load_json(EVERGREEN_PATH, {"posts": []})
    posts = data.get("posts", []) if isinstance(data, dict) else data
    for p in posts:
        if not p.get("used"):
            p["used"] = True
            save_json(EVERGREEN_PATH, {"posts": posts} if isinstance(data, dict) else posts)
            return p
    return None


# ---------------------------------------------------------------------------
# 1枠分の下書きを確定
# ---------------------------------------------------------------------------
def produce_for_slot(slot_cfg: dict, recents: list[str], ng_words: list[str]):
    """(draft_dict, source) を返す。source: 'ai' / 'evergreen' / None(失敗)"""
    for attempt in range(1, MAX_REGEN + 1):
        try:
            draft = generate_draft(slot_cfg, recents)
        except Exception as e:  # noqa: BLE001
            log(f"  [gen] {attempt}/{MAX_REGEN} 生成エラー: {e}")
            continue
        ok, reasons = machine_check(draft["body"], draft["cta"], ng_words)
        if not ok:
            log(f"  [gen] {attempt}/{MAX_REGEN} 機械チェックNG: {reasons}")
            continue
        try:
            score, detail = score_draft(draft["text"], slot_cfg, recents)
        except Exception as e:  # noqa: BLE001
            log(f"  [gen] {attempt}/{MAX_REGEN} 採点エラー: {e}")
            continue
        log(f"  [gen] {attempt}/{MAX_REGEN} 採点={score}（{detail.get('reason','')[:40]}）")
        if score >= PASS_SCORE:
            draft["score"] = score
            return draft, "ai"

    # 3回落ちた → evergreen
    ev = pick_evergreen()
    if ev:
        log("  [gen] 3回不合格。evergreen定型投稿を使用")
        return {"body": ev.get("text", ""), "cta": "", "text": ev.get("text", ""),
                "evergreen_id": ev.get("id")}, "evergreen"
    return None, None


# ---------------------------------------------------------------------------
# 監視: トークン期限
# ---------------------------------------------------------------------------
def check_token_expiry():
    meta = load_json(TOKEN_META_PATH, None)
    if not meta or not meta.get("expires_at"):
        tc.summary_section("トークン期限", "token_meta.json が無いため期限を確認できません（refresh実行後に生成されます）。")
        return
    try:
        exp = tc.parse_scheduled(meta["expires_at"])
    except Exception:
        return
    days = (exp - tc.now_jst()).days
    if days < 30:
        tc.warn("トークン期限が近い", f"アクセストークンの残り約{days}日（{exp.date()}）。refresh_token を確認してください。")
    else:
        tc.summary_section("トークン期限", f"残り約{days}日（{exp.date()}）。")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def process(dry_run: bool):
    queue = load_json(QUEUE_PATH, [])
    ng_words = load_ng_words()
    todo = needed_slots(queue)

    log(f"在庫目標={SLOTS_PER_DAY_TARGET}本 / 不足={len(todo)}枠 / dry_run={dry_run} / model={MODEL}")

    if not todo:
        tc.summary_section("下書き生成", "在庫は充足しています（生成なし）。")
        check_token_expiry()
        return

    if dry_run:
        lines = "\n".join(f"- {slots.slot_key(dt)}: {s.get('theme','')}" for dt, s in todo)
        tc.summary_section("下書き生成（ドライラン）", f"埋めるべき枠 {len(todo)}件:\n{lines}")
        log("[DRY RUN] Claudeは呼びません。上記の枠を生成対象として表示のみ")
        check_token_expiry()
        return

    recents = recent_texts(queue)
    generated = evergreen = failed = 0

    for dt, scfg in todo:
        key = slots.slot_key(dt)
        log(f"[slot] {key} テーマ={scfg.get('theme','')}")
        draft, source = produce_for_slot(scfg, recents, ng_words)
        if source is None:
            failed += 1
            tc.warn("生成失敗（evergreen枯渇）", f"枠 `{key}` の下書きを用意できませんでした。fallback/evergreen.json を補充してください。")
            continue
        item = {
            "id": f"gen-{key.replace(' ', 'T').replace(':', '')}",
            "slot_key": key,
            "slot_time": scfg.get("time"),
            "theme": scfg.get("theme"),
            "tone": scfg.get("tone"),
            "source": source,
            "text": draft["text"],
            "body": draft.get("body", ""),
            "cta": draft.get("cta", ""),
            "score": draft.get("score"),
            "status": "pending",
            "posted_id": None,
            "error": None,
            "created_at": tc.now_jst().isoformat(),
        }
        queue.append(item)
        recents.insert(0, draft["text"])  # 次の生成で重複回避に反映
        save_json(QUEUE_PATH, queue)      # 都度flush
        if source == "ai":
            generated += 1
        else:
            evergreen += 1

    tc.summary_section(
        "下書き生成 結果",
        f"- AI合格: {generated}\n- evergreen使用: {evergreen}\n- 失敗: {failed}\n- 対象枠: {len(todo)}")

    if generated == 0 and todo:
        tc.warn("生成全滅", f"今回 {len(todo)} 枠すべてでAI生成が合格しませんでした（evergreen {evergreen} / 失敗 {failed}）。")

    check_token_expiry()


def main():
    ap = argparse.ArgumentParser(description="下書き自動生成（Claude）")
    ap.add_argument("--dry-run", action="store_true", help="Claudeを呼ばず不足枠のみ表示")
    args = ap.parse_args()
    env_dry = str(tc.env("DRY_RUN", "false")).strip().lower() in ("1", "true", "yes")
    process(dry_run=args.dry_run or env_dry)


if __name__ == "__main__":
    main()
