#!/usr/bin/env python3
"""Threads 自動投稿スクリプト（スロット方式）。

毎日 09:00 / 15:00 / 21:00（JST, config/slots.json）の3枠で1本ずつ投稿する。
ワークフローは15分おきに実行し、「未投稿 かつ 予定時刻を経過」した枠だけを投稿する。

- 枠の状態は posts/state.json で管理（cronの遅延・スキップに耐える）
- 冪等性: state.json（枠単位）＋ processed.json（下書きID単位）の二重で二重投稿を防止
- 投稿は Threads Graph API の2段階（コンテナ作成 → 30秒待機 → 公開）
- 各API呼び出しは3回までリトライ。ダメなら status=failed とし監視警告

ドライラン（実際には投稿しない）:
  DRY_RUN=true / --dry-run

テスト:
  python src/publish.py --dry-run --now "2026-08-26 09:05"
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

import slots
import threads_common as tc
from threads_common import log

WAIT_SECONDS = int(tc.env("POST_WAIT_SECONDS", "30"))   # 仕様は30秒。テスト用に短縮可
RETRY_TRIES = 3
CATCHUP_MINUTES = int(tc.env("CATCHUP_MINUTES", "360"))  # 遅延・スキップ許容の窓（既定6時間）
GRAPH_API_BASE = tc.env("GRAPH_API_BASE", "https://graph.threads.net/v1.0")

QUEUE_PATH = tc.ROOT / "posts" / "queue.json"
STATE_PATH = tc.ROOT / "posts" / "state.json"
PROCESSED_PATH = tc.ROOT / "posts" / "processed.json"


# ---------------------------------------------------------------------------
# ファイル入出力
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default
    txt = path.read_text(encoding="utf-8").strip()
    return json.loads(txt) if txt else default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_state() -> dict:
    data = load_json(STATE_PATH, {"slots": {}})
    data.setdefault("slots", {})
    return data


def load_processed() -> dict:
    return {r["id"]: r for r in load_json(PROCESSED_PATH, [])}


def append_processed(processed: dict, post_id: str, posted_id: str):
    processed[post_id] = {
        "id": post_id,
        "posted_id": posted_id,
        "posted_at": tc.now_jst().isoformat(),
    }
    save_json(PROCESSED_PATH, list(processed.values()))


def find_pending_for_slot(queue: list, key: str):
    for item in queue:
        if item.get("slot_key") == key and item.get("status") == "pending":
            return item
    return None


# ---------------------------------------------------------------------------
# Threads Graph API（2段階投稿）
# ---------------------------------------------------------------------------
def _post(url: str, params: dict) -> dict:
    resp = requests.post(url, data=params, timeout=60)
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "id" not in data:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return data


def create_container(user_id: str, token: str, text: str) -> str:
    url = f"{GRAPH_API_BASE}/{user_id}/threads"
    return _post(url, {"media_type": "TEXT", "text": text, "access_token": token})["id"]


def publish_container(user_id: str, token: str, creation_id: str) -> str:
    url = f"{GRAPH_API_BASE}/{user_id}/threads_publish"
    return _post(url, {"creation_id": creation_id, "access_token": token})["id"]


def with_retry(fn, label: str, tries: int = RETRY_TRIES):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"[retry] {label} 失敗 {attempt}/{tries}: {e}")
            if attempt < tries:
                time.sleep(5 * attempt)
    raise last


def publish_text(user_id: str, token: str, text: str, dry_run: bool) -> str:
    if dry_run:
        log(f"[DRY RUN] コンテナ作成 → {WAIT_SECONDS}秒待機 → 公開（実際には投稿しません）")
        return f"DRYRUN-{int(time.time())}"
    creation_id = with_retry(
        lambda: create_container(user_id, token, text), label="create_container")
    log(f"コンテナ作成 creation_id={creation_id}。{WAIT_SECONDS}秒待機します")
    time.sleep(WAIT_SECONDS)
    return with_retry(
        lambda: publish_container(user_id, token, creation_id), label="publish_container")


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def process(dry_run: bool, now_override: str | None):
    now = tc.parse_scheduled(now_override) if now_override else tc.now_jst()
    queue = load_json(QUEUE_PATH, [])
    state = load_state()
    processed = load_processed()

    log(f"現在時刻: {now.isoformat()} / dry_run={dry_run} / "
        f"catchup={CATCHUP_MINUTES}分 / LENGTH_MODE={tc.LENGTH_MODE}")

    user_id = tc.env("THREADS_USER_ID", required=not dry_run) or "DRYRUN_USER"
    token = tc.env("THREADS_ACCESS_TOKEN", required=not dry_run) or "DRYRUN_TOKEN"

    due = slots.due_slots(now, CATCHUP_MINUTES)
    posted = failed = missing = 0

    for dt, _scfg in due:
        key = slots.slot_key(dt)
        st = state["slots"].get(key, {})

        # 冪等性: 既に投稿済みの枠は絶対に再投稿しない
        if st.get("status") == "posted":
            continue

        item = find_pending_for_slot(queue, key)

        # 台帳に投稿済みIDがあれば整合を取るだけ（二重投稿防止）
        if item and item["id"] in processed:
            item["status"] = "posted"
            item["posted_id"] = processed[item["id"]]["posted_id"]
            state["slots"][key] = {"status": "posted", "post_id": item["id"],
                                   "posted_id": item["posted_id"],
                                   "posted_at": processed[item["id"]]["posted_at"]}
            if not dry_run:
                save_json(STATE_PATH, state)
                save_json(QUEUE_PATH, queue)
            continue

        if item is None:
            missing += 1
            log(f"[missing] 枠 {key} に対応する下書き（pending）が在庫にありません")
            state["slots"][key] = {"status": "missing"}
            if not dry_run:
                save_json(STATE_PATH, state)
            tc.warn("在庫切れ（投稿できる下書きなし）",
                    f"枠 `{key}` に投稿できる pending の下書きがありませんでした。generate の在庫を確認してください。")
            continue

        # バリデーション（生成時にも通しているが投稿直前にも再チェック）
        errors = tc.validate_text(item["text"])
        if errors:
            item["status"] = "failed"
            item["error"] = " / ".join(errors)
            state["slots"][key] = {"status": "failed", "post_id": item["id"],
                                   "error": item["error"]}
            failed += 1
            if not dry_run:
                save_json(STATE_PATH, state)
                save_json(QUEUE_PATH, queue)
            tc.warn("投稿失敗（バリデーション）", f"枠 `{key}` / {item['id']}: {item['error']}")
            continue

        # 投稿
        log(f"[posting] 枠 {key} / {item['id']}"
            f"（{tc.count_length(item['text'])}文字, URL {tc.count_urls(item['text'])}個）")
        try:
            posted_id = publish_text(user_id, token, item["text"], dry_run)
        except Exception as e:  # noqa: BLE001
            item["status"] = "failed"
            item["error"] = str(e)
            state["slots"][key] = {"status": "failed", "post_id": item["id"], "error": str(e)}
            failed += 1
            if not dry_run:
                save_json(STATE_PATH, state)
                save_json(QUEUE_PATH, queue)
            tc.warn("投稿失敗（API）", f"枠 `{key}` / {item['id']}: 3回リトライしても投稿できませんでした: {e}")
            continue

        item["status"] = "posted"
        item["posted_id"] = posted_id
        item["error"] = None
        state["slots"][key] = {"status": "posted", "post_id": item["id"],
                               "posted_id": posted_id, "posted_at": now.isoformat()}
        posted += 1
        log(f"[posted] 枠 {key} / {item['id']} → posted_id={posted_id}")

        if not dry_run:
            append_processed(processed, item["id"], posted_id)  # 成功時に即flush
            save_json(STATE_PATH, state)
            save_json(QUEUE_PATH, queue)

    tc.summary_section(
        "Threads 投稿ジョブ結果",
        f"- 時刻: {now.isoformat()}\n- 投稿: {posted}\n- 失敗: {failed}\n"
        f"- 在庫切れ枠: {missing}\n- 対象枠数: {len(due)}"
        + ("\n- ※ドライラン（ファイル未更新）" if dry_run else ""))
    log(f"完了: posted={posted} failed={failed} missing={missing} 対象枠={len(due)}"
        + ("  ※ドライランのためファイルは書き換えていません" if dry_run else ""))


def main():
    ap = argparse.ArgumentParser(description="Threads 自動投稿（スロット方式）")
    ap.add_argument("--dry-run", action="store_true", help="実際には投稿しない")
    ap.add_argument("--now", default=None, help="現在時刻をISO8601で疑似指定（テスト用）")
    args = ap.parse_args()

    env_dry = str(tc.env("DRY_RUN", "false")).strip().lower() in ("1", "true", "yes")
    process(dry_run=args.dry_run or env_dry, now_override=args.now)


if __name__ == "__main__":
    main()
