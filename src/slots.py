"""投稿枠（スロット）の時刻計算。

config/slots.json で 1日の投稿枠（既定 09:00 / 15:00 / 21:00 JST）とテーマ・トーンを定義。
- publish.py … due_slots() で「予定時刻を経過した枠」を取得
- generate.py … upcoming_slots() で「これから埋めるべき枠」を取得
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import threads_common as tc

CONFIG_PATH = tc.ROOT / "config" / "slots.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def slot_key(dt: datetime.datetime) -> str:
    """枠の一意キー（JSTの分まで）。例: '2026-08-26 09:00'"""
    return dt.astimezone(tc.JST).strftime("%Y-%m-%d %H:%M")


def _make_dt(date: datetime.date, hhmm: str) -> datetime.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime.combine(date, datetime.time(h, m), tzinfo=tc.JST)


def slots_on(date: datetime.date) -> list[tuple[datetime.datetime, dict]]:
    cfg = load_config()
    out = [(_make_dt(date, s["time"]), s) for s in cfg["slots"]]
    out.sort(key=lambda x: x[0])
    return out


def upcoming_slots(n: int, now: datetime.datetime | None = None):
    """now より後の直近 n 枠を (dt, slot_cfg) で返す（3日分=9枠などの在庫計算用）。"""
    now = now or tc.now_jst()
    out = []
    day = now.astimezone(tc.JST).date()
    guard = 0
    while len(out) < n and guard < 60:
        for dt, s in slots_on(day):
            if dt > now:
                out.append((dt, s))
                if len(out) >= n:
                    break
        day += datetime.timedelta(days=1)
        guard += 1
    return out


def due_slots(now: datetime.datetime | None = None, catchup_minutes: int = 360):
    """now 以前で、まだ処理対象になりうる枠を古い順に返す。

    cron の遅延・スキップに耐えるため、直近 catchup_minutes 分（既定6時間）以内に
    予定時刻を迎えた枠を拾う。古すぎる枠（ダウンタイム明けなど）は陳腐化とみなし拾わない。
    """
    now = now or tc.now_jst()
    window_start = now - datetime.timedelta(minutes=catchup_minutes)
    out = []
    today = now.astimezone(tc.JST).date()
    for day in (today - datetime.timedelta(days=1), today):
        for dt, s in slots_on(day):
            if window_start <= dt <= now:
                out.append((dt, s))
    out.sort(key=lambda x: x[0])
    return out
