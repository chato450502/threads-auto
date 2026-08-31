#!/usr/bin/env python3
"""Threads 自動投稿スクリプト（スロット方式・連投対応）。

毎日 09:00 / 15:00 / 21:00（JST, config/slots.json）の3枠で、それぞれ1連投（スレッド）を投稿する。
ワークフローは15分おきに実行し、「未投稿 かつ 予定時刻を経過」した枠だけを投稿する。

- 連投は Threads Graph API の2段階（コンテナ作成 → 待機 → 公開）を各投稿で行い、
  2本目以降は前の投稿へ reply_to_id で返信連結する。
- 途中失敗に備え、1投稿公開するごとに state.json / queue.json へ posted_ids を追記（再開可能）。
- 冪等性: state.json（枠単位）＋ processed.json（連投ID単位）で二重投稿を防止。
- 各API呼び出しは3回までリトライ。ダメなら status=failed とし監視警告。

ドライラン: DRY_RUN=true / --dry-run
テスト:   python src/publish.py --dry-run --now "2026-08-31 15:05"
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

WAIT_SECONDS = int(tc.env("POST_WAIT_SECONDS", "15"))
# 返信を繋ぐ前に親投稿が反映されるのを待つ間隔（短すぎると 500 になる）
BETWEEN_POSTS = int(tc.env("BETWEEN_POSTS_SECONDS", "25"))
RETRY_TRIES = 3
MAX_THREAD_ATTEMPTS = int(tc.env("MAX_THREAD_ATTEMPTS", "4"))
CATCHUP_MINUTES = int(tc.env("CATCHUP_MINUTES", "360"))
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


def item_posts(item: dict) -> list[str]:
    """新形式(posts=連投)・旧形式(text=単発)どちらも連投リストとして返す。"""
    if isinstance(item.get("posts"), list) and item["posts"]:
        return item["posts"]
    if item.get("text"):
        return [item["text"]]
    return []


def find_thread_for_slot(queue: list, key: str):
    for item in queue:
        if item.get("slot_key") == key and item.get("status") in ("pending", "in_progress"):
            return item
    return None


# ---------------------------------------------------------------------------
# Threads Graph API（2段階投稿・返信連結）
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


def create_container(user_id: str, token: str, text: str, reply_to_id: str | None) -> str:
    url = f"{GRAPH_API_BASE}/{user_id}/threads"
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        # Threads APIの返信パラメータは replied_to_id（reply_to_id は500になる）
        params["replied_to_id"] = reply_to_id
    return _post(url, params)["id"]


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
                time.sleep(10 * attempt)
    raise last


def publish_one(user_id, token, text, reply_to_id, dry_run) -> str:
    if dry_run:
        return f"DRYRUN-{int(time.time()*1000)%100000}"
    creation_id = with_retry(
        lambda: create_container(user_id, token, text, reply_to_id), label="create")
    time.sleep(WAIT_SECONDS)
    return with_retry(
        lambda: publish_container(user_id, token, creation_id), label="publish")


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def process(dry_run: bool, now_override: str | None):
    now = tc.parse_scheduled(now_override) if now_override else tc.now_jst()
    queue = load_json(QUEUE_PATH, [])
    state = load_state()
    processed = load_processed()

    log(f"現在時刻: {now.isoformat()} / dry_run={dry_run} / catchup={CATCHUP_MINUTES}分")

    user_id = tc.env("THREADS_USER_ID", required=not dry_run) or "DRYRUN_USER"
    token = tc.env("THREADS_ACCESS_TOKEN", required=not dry_run) or "DRYRUN_TOKEN"

    due = slots.due_slots(now, CATCHUP_MINUTES)
    posted = failed = missing = 0

    def persist():
        if not dry_run:
            save_json(STATE_PATH, state)
            save_json(QUEUE_PATH, queue)

    for dt, _scfg in due:
        key = slots.slot_key(dt)
        st = state["slots"].get(key, {})
        if st.get("status") == "posted":
            continue

        item = find_thread_for_slot(queue, key)
        if item is None:
            # 既に処理済みIDが台帳にあるなら整合を取るだけ
            missing += 1
            log(f"[missing] 枠 {key} に投稿できる連投（pending）がありません")
            state["slots"][key] = {"status": "missing"}
            persist()
            tc.warn("在庫切れ", f"枠 `{key}` に投稿できる連投がありませんでした。")
            continue

        posts = item_posts(item)
        # バリデーション（投稿直前の最終チェック）
        bad = [i + 1 for i, p in enumerate(posts) if tc.count_length(p) > tc.MAX_LENGTH]
        if not posts or bad:
            item["status"] = "failed"
            item["error"] = f"投稿本文が不正（空 or 500字超過: {bad}）"
            state["slots"][key] = {"status": "failed", "post_id": item.get("id"), "error": item["error"]}
            failed += 1
            persist()
            tc.warn("投稿失敗（検証）", f"枠 `{key}`: {item['error']}")
            continue

        # 再開: 既に投稿済みの本数を引き継ぐ
        posted_ids = list(st.get("posted_ids", item.get("posted_ids") or []))
        reply_to = posted_ids[-1] if posted_ids else None
        attempts = st.get("attempts", 0)
        item["status"] = "in_progress"
        state["slots"][key] = {"status": "in_progress", "post_id": item.get("id"),
                               "posted_ids": posted_ids, "attempts": attempts}
        log(f"[posting] 枠 {key}（連投{len(posts)}本 / 済{len(posted_ids)}本）型={item.get('kata','')}")

        try:
            for i in range(len(posted_ids), len(posts)):
                mid = publish_one(user_id, token, posts[i], reply_to, dry_run)
                posted_ids.append(mid)
                reply_to = mid
                state["slots"][key]["posted_ids"] = posted_ids
                item["posted_ids"] = posted_ids
                persist()  # 1本ごとに保存（途中失敗しても再開できる）
                log(f"   {i+1}/{len(posts)} 公開 id={mid}")
                if i + 1 < len(posts) and not dry_run:
                    time.sleep(BETWEEN_POSTS)
        except Exception as e:  # noqa: BLE001
            attempts += 1
            msg = f"連投{len(posted_ids)}/{len(posts)}本で中断: {e}"
            if attempts < MAX_THREAD_ATTEMPTS:
                # 途中失敗 → in_progress のまま残し、次回実行で続きから自動再開
                item["status"] = "in_progress"
                item["error"] = msg
                state["slots"][key] = {"status": "in_progress", "post_id": item.get("id"),
                                       "posted_ids": posted_ids, "attempts": attempts,
                                       "error": msg}
                persist()
                tc.warn("連投を中断（次回に続きから再開）",
                        f"枠 `{key}`: {msg}（試行{attempts}/{MAX_THREAD_ATTEMPTS}）")
            else:
                item["status"] = "failed"
                item["error"] = msg
                state["slots"][key] = {"status": "failed", "post_id": item.get("id"),
                                       "posted_ids": posted_ids, "attempts": attempts,
                                       "error": msg}
                failed += 1
                persist()
                tc.warn("投稿失敗（連投・再試行上限）", f"枠 `{key}`: {msg}")
            continue

        item["status"] = "posted"
        item["posted_ids"] = posted_ids
        item["error"] = None
        state["slots"][key] = {"status": "posted", "post_id": item.get("id"),
                               "posted_ids": posted_ids, "posted_at": now.isoformat()}
        if not dry_run:
            processed[item["id"]] = {"id": item["id"], "posted_ids": posted_ids,
                                     "posted_at": tc.now_jst().isoformat()}
            save_json(PROCESSED_PATH, list(processed.values()))
        posted += 1
        persist()
        log(f"[posted] 枠 {key} 完了（{len(posted_ids)}本）")

    tc.summary_section(
        "Threads 投稿ジョブ結果",
        f"- 時刻: {now.isoformat()}\n- 投稿(連投): {posted}\n- 失敗: {failed}\n"
        f"- 在庫切れ: {missing}\n- 対象枠: {len(due)}"
        + ("\n- ※ドライラン（ファイル未更新）" if dry_run else ""))
    log(f"完了: posted={posted} failed={failed} missing={missing} 対象枠={len(due)}"
        + ("  ※ドライラン" if dry_run else ""))


def main():
    ap = argparse.ArgumentParser(description="Threads 自動投稿（連投対応）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", default=None)
    args = ap.parse_args()
    env_dry = str(tc.env("DRY_RUN", "false")).strip().lower() in ("1", "true", "yes")
    process(dry_run=args.dry_run or env_dry, now_override=args.now)


if __name__ == "__main__":
    main()
