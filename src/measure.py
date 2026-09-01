#!/usr/bin/env python3
"""投稿の成績（インサイト）を型ごとに集計する。

自動投稿した連投の「1本目（ルート投稿）」の表示回数・いいね数を Threads API から取得し、
その連投を作った「型」ごとに平均を出して posts/performance.json に書き出す。
optimize.py が、この成績を見て伸びていない型を入れ替える。

要 権限: threads_manage_insights（無いと 500「permission」になる）
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

import threads_common as tc
from threads_common import log

GRAPH_API_BASE = tc.env("GRAPH_API_BASE", "https://graph.threads.net/v1.0")
QUEUE_PATH = tc.ROOT / "posts" / "queue.json"
PERFORMANCE_PATH = tc.ROOT / "posts" / "performance.json"

# 連投の代表指標として使うメトリクス（ルート投稿の lifetime 値）
METRICS = "views,likes,replies,reposts,quotes,shares"


def load_json(path: Path, default):
    if not path.exists():
        return default
    txt = path.read_text(encoding="utf-8").strip()
    return json.loads(txt) if txt else default


def fetch_insights(media_id: str, token: str) -> dict | None:
    """1投稿のインサイトを {metric: value} で返す。取れなければ None。"""
    url = f"{GRAPH_API_BASE}/{media_id}/insights"
    try:
        r = requests.get(url, params={"metric": METRICS, "access_token": token}, timeout=60)
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] insights取得エラー {media_id}: {e}")
        return None
    if r.status_code >= 400:
        log(f"  [warn] insights {media_id}: HTTP {r.status_code} {r.text[:120]}")
        return None
    out = {}
    for m in r.json().get("data", []):
        vals = m.get("values") or [{}]
        out[m.get("name")] = vals[0].get("value", 0)
    return out


def measure(token: str) -> dict:
    queue = load_json(QUEUE_PATH, [])
    posted = [it for it in queue
              if it.get("status") == "posted" and it.get("kata") and it.get("posted_ids")]
    log(f"成績測定: 投稿済み連投 {len(posted)}件")

    per_kata: dict[str, dict] = {}
    for it in posted:
        kata = it["kata"]
        root = it["posted_ids"][0]
        ins = fetch_insights(root, token)
        if ins is None:
            continue
        views = int(ins.get("views", 0) or 0)
        likes = int(ins.get("likes", 0) or 0)
        d = per_kata.setdefault(kata, {"samples": []})
        d["samples"].append({
            "slot_key": it.get("slot_key"),
            "root_id": root,
            "views": views,
            "likes": likes,
            "replies": int(ins.get("replies", 0) or 0),
            "reposts": int(ins.get("reposts", 0) or 0),
        })

    # 集計
    for kata, d in per_kata.items():
        s = d["samples"]
        d["post_count"] = len(s)
        d["avg_views"] = round(sum(x["views"] for x in s) / len(s), 1) if s else 0
        d["avg_likes"] = round(sum(x["likes"] for x in s) / len(s), 1) if s else 0
        d["total_views"] = sum(x["views"] for x in s)

    result = {"generated_at": tc.now_jst().isoformat(), "per_kata": per_kata}
    PERFORMANCE_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"performance.json 更新（型 {len(per_kata)}種）")
    return result


def main():
    token = tc.env("THREADS_ACCESS_TOKEN", required=True)
    result = measure(token)
    rows = sorted(result["per_kata"].items(),
                  key=lambda kv: kv[1]["avg_views"], reverse=True)
    body = "\n".join(
        f"- {k}: 平均表示 {v['avg_views']}／平均いいね {v['avg_likes']}（{v['post_count']}投稿）"
        for k, v in rows) or "（まだ測定対象の投稿がありません）"
    tc.summary_section("型ごとの成績", body)
    for k, v in rows:
        log(f"  {k}: avg_views={v['avg_views']} avg_likes={v['avg_likes']} n={v['post_count']}")


if __name__ == "__main__":
    main()
