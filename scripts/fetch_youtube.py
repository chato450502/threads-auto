#!/usr/bin/env python3
"""YouTube 素材収集スクリプト。

サブコマンド:
  search     キーワード検索（再生回数順・統計付き）※要 YOUTUBE_API_KEY
  transcript 字幕取得（URL または動画ID）※APIキー不要
  auto       検索 → 上位から字幕取得（字幕なしは自動スキップ）※要 YOUTUBE_API_KEY
  channel    チャンネルの直近N本から再生回数上位を選定 → 字幕取得 ※要 YOUTUBE_API_KEY

使い方例:
  python3 scripts/fetch_youtube.py transcript "https://www.youtube.com/watch?v=xxxx"
  python3 scripts/fetch_youtube.py search --keyword "男性心理" --max 10
  python3 scripts/fetch_youtube.py auto --keyword "愛され" --max 5
  python3 scripts/fetch_youtube.py channel --recent 30 --top 3 --min-minutes 2
  python3 scripts/fetch_youtube.py channel --list        # ランキング表示のみ
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

import common

API_BASE = "https://www.googleapis.com/youtube/v3"


# ----------------------------------------------------------------------------
# 低レベルヘルパ
# ----------------------------------------------------------------------------
def _http_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get(endpoint, params):
    params = dict(params)
    params["key"] = common.env("YOUTUBE_API_KEY", required=True)
    url = f"{API_BASE}/{endpoint}?" + urllib.parse.urlencode(params)
    return _http_json(url)


def extract_video_id(url_or_id):
    """URL でも ID でも動画IDを取り出す。"""
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", s)
    if m:
        return m.group(1)
    # v= を含まないが query にあるケース
    q = urllib.parse.urlparse(s)
    qs = urllib.parse.parse_qs(q.query)
    if "v" in qs:
        return qs["v"][0]
    sys.exit(f"[ERROR] 動画IDを抽出できません: {url_or_id}")


def oembed_title(video_id):
    """oEmbed でタイトルを取得（APIキー不要）。失敗したら空文字。"""
    try:
        url = ("https://www.youtube.com/oembed?format=json&url="
               + urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}", safe=""))
        data = _http_json(url)
        return data.get("title", "")
    except Exception:
        return ""


def iso8601_to_seconds(dur):
    """PT#H#M#S -> 秒。"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


# ----------------------------------------------------------------------------
# 字幕取得
# ----------------------------------------------------------------------------
def get_transcript_text(video_id):
    """youtube-transcript-api 1.x 系で字幕を取得。取得できなければ None。"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        sys.exit("[ERROR] youtube-transcript-api 未インストール。"
                 " .venv/bin/pip install youtube-transcript-api")
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["ja", "ja-JP", "en"])
        parts = [snippet.text for snippet in fetched]
        return "\n".join(p for p in parts if p and p.strip())
    except Exception as e:
        common.eprint(f"[skip] 字幕取得失敗 {video_id}: {e}")
        return None


def save_transcript(video_id, title=None):
    if not title:
        title = oembed_title(video_id)
    text = get_transcript_text(video_id)
    if text is None:
        return None
    slug = common.slugify(title or video_id)
    filename = f"{common.today()}_{slug}_{video_id}.md"
    header = (
        f"# {title or '(タイトル取得失敗)'}\n\n"
        f"- 出所: https://www.youtube.com/watch?v={video_id}\n"
        f"- 動画ID: {video_id}\n"
        f"- 取得日: {common.today()}\n"
        f"- 種別: YouTube字幕（書き起こし素材）\n\n"
        f"---\n\n"
    )
    path = common.save_material(filename, header + text)
    print(f"[saved] {path}")
    return path


# ----------------------------------------------------------------------------
# search
# ----------------------------------------------------------------------------
def fetch_video_stats(video_ids):
    """videos.list で統計・長さ・タイトルをまとめて取得。"""
    out = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(chunk),
            "maxResults": 50,
        })
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            out[item["id"]] = {
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"].get("channelTitle", ""),
                "views": int(stats.get("viewCount", 0)),
                "seconds": iso8601_to_seconds(item.get("contentDetails", {}).get("duration")),
            }
    return out


def cmd_search(args):
    data = api_get("search", {
        "part": "snippet",
        "q": args.keyword,
        "type": "video",
        "order": "viewCount",
        "maxResults": min(args.max, 50),
    })
    ids = [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
    stats = fetch_video_stats(ids)
    ranked = sorted(stats.values(), key=lambda v: v["views"], reverse=True)
    print(f"# 検索: {args.keyword}（再生回数順）\n")
    for v in ranked:
        mins = v["seconds"] // 60
        print(f"{v['views']:>12,}回  {mins:>3}分  {v['title']}  "
              f"https://www.youtube.com/watch?v={v['id']}")
    return ranked


def cmd_auto(args):
    ranked = cmd_search(args)
    print("\n# 字幕取得（字幕なしは自動スキップ）\n")
    saved = 0
    for v in ranked:
        if args.min_minutes and v["seconds"] < args.min_minutes * 60:
            continue
        if save_transcript(v["id"], v["title"]):
            saved += 1
        if saved >= args.top:
            break
    print(f"\n[done] {saved}本の字幕を保存")


# ----------------------------------------------------------------------------
# transcript
# ----------------------------------------------------------------------------
def cmd_transcript(args):
    vid = extract_video_id(args.target)
    path = save_transcript(vid)
    if path is None:
        sys.exit("[ERROR] 字幕を取得できませんでした（字幕が存在しない可能性）")


# ----------------------------------------------------------------------------
# channel
# ----------------------------------------------------------------------------
def resolve_channel_id(handle_or_url):
    """@ハンドル / URL / UC〜ID からチャンネルIDを解決。

    日本語チャンネル名は forHandle で見つからないことがあるため、
    見つからなければ type=channel の検索APIでフォールバックする。
    """
    s = handle_or_url.strip()
    # UC〜ID 直接
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", s):
        return s
    # URL から抽出
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", s)
    if m:
        return m.group(1)
    handle = None
    m = re.search(r"@([A-Za-z0-9_.\-一-龠ぁ-んァ-ヶ]+)", s)
    if m:
        handle = m.group(1)
    elif not s.startswith("http"):
        handle = s.lstrip("@")

    # forHandle で試す
    if handle:
        try:
            data = api_get("channels", {"part": "id", "forHandle": handle})
            items = data.get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass

    # フォールバック: type=channel 検索
    query = handle or s
    data = api_get("search", {
        "part": "snippet", "q": query, "type": "channel", "maxResults": 1,
    })
    items = data.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]
    sys.exit(f"[ERROR] チャンネルを解決できません: {handle_or_url}")


def channel_uploads_playlist(channel_id):
    data = api_get("channels", {"part": "contentDetails", "id": channel_id})
    items = data.get("items", [])
    if not items:
        sys.exit(f"[ERROR] チャンネル情報取得失敗: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def playlist_video_ids(playlist_id, limit):
    ids = []
    page = None
    while len(ids) < limit:
        params = {"part": "contentDetails", "playlistId": playlist_id,
                  "maxResults": min(50, limit - len(ids))}
        if page:
            params["pageToken"] = page
        data = api_get("playlistItems", params)
        for it in data.get("items", []):
            ids.append(it["contentDetails"]["videoId"])
        page = data.get("nextPageToken")
        if not page:
            break
    return ids[:limit]


def rank_channel_videos(channel_ref, recent, min_minutes):
    cid = resolve_channel_id(channel_ref)
    uploads = channel_uploads_playlist(cid)
    vids = playlist_video_ids(uploads, recent)
    stats = fetch_video_stats(vids)
    videos = list(stats.values())
    # Shorts（2分未満）除外
    videos = [v for v in videos if v["seconds"] >= min_minutes * 60]
    videos.sort(key=lambda v: v["views"], reverse=True)
    return videos


def load_channel_list():
    path = common.ASSETS / "youtube-channels.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def cmd_channel(args):
    channels = args.channels if args.channels else load_channel_list()
    if not channels:
        sys.exit("[ERROR] チャンネル未指定かつ assets/youtube-channels.txt が空です。")
    saved = 0
    for ch in channels:
        print(f"\n# チャンネル: {ch}（直近{args.recent}本・再生回数順・{args.min_minutes}分未満除外）\n")
        videos = rank_channel_videos(ch, args.recent, args.min_minutes)
        for i, v in enumerate(videos, 1):
            mins = v["seconds"] // 60
            print(f"{i:>2}. {v['views']:>12,}回  {mins:>3}分  {v['title']}  "
                  f"https://www.youtube.com/watch?v={v['id']}")
        if args.list:
            continue
        for v in videos[:args.top]:
            if save_transcript(v["id"], v["title"]):
                saved += 1
    if not args.list:
        print(f"\n[done] {saved}本の字幕を保存")


# ----------------------------------------------------------------------------
# argparse
# ----------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description="YouTube 素材収集")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="キーワード検索（再生回数順）")
    s.add_argument("--keyword", required=True)
    s.add_argument("--max", type=int, default=10)
    s.set_defaults(func=cmd_search)

    t = sub.add_parser("transcript", help="字幕取得（URL/ID・キー不要）")
    t.add_argument("target")
    t.set_defaults(func=cmd_transcript)

    a = sub.add_parser("auto", help="検索→上位から字幕取得")
    a.add_argument("--keyword", required=True)
    a.add_argument("--max", type=int, default=10, help="検索件数")
    a.add_argument("--top", type=int, default=3, help="字幕を取得する本数")
    a.add_argument("--min-minutes", dest="min_minutes", type=int, default=2)
    a.set_defaults(func=cmd_auto)

    c = sub.add_parser("channel", help="チャンネルの直近から再生回数上位を字幕取得")
    c.add_argument("--channels", nargs="*", default=None,
                   help="@ハンドル/URL/UC〜ID。省略時 assets/youtube-channels.txt")
    c.add_argument("--recent", type=int, default=30, help="直近何本を対象にするか")
    c.add_argument("--top", type=int, default=3, help="字幕を取得する本数")
    c.add_argument("--min-minutes", dest="min_minutes", type=int, default=2,
                   help="この分数未満（Shorts）は除外")
    c.add_argument("--list", action="store_true", help="ランキング表示のみ")
    c.set_defaults(func=cmd_channel)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
