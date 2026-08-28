#!/usr/bin/env python3
"""Threads バズ投稿収集スクリプト（Apify 経由）。

⚠️ 規約グレーのため収集は控えめに（週1回・20件程度）。

Actor:
  本文取得   futurizerush~meta-threads-scraper
  返信取得   futurizerush~threads-replies-scraper  （--with-replies 時）

使い方例:
  # キーワード検索（top）
  python3 scripts/fetch_threads.py --mode search --keywords "男性心理" "溺愛" --max 20
  # アカウント指定（相対上位方式）＋型化用の返信取得
  python3 scripts/fetch_threads.py --mode user --usernames @aaa @bbb \\
      --per-user-top 3 --with-replies
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

import common

APIFY_BASE = "https://api.apify.com/v2"
REPLIES_ACTOR = "futurizerush~threads-replies-scraper"


# ----------------------------------------------------------------------------
# Apify 実行
# ----------------------------------------------------------------------------
def run_actor(actor_id, input_obj):
    """run-sync-get-dataset-items でActorを同期実行し、dataset items(list) を返す。"""
    token = common.env("APIFY_TOKEN", required=True)
    url = (f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
           f"?token={urllib.parse.quote(token)}")
    body = json.dumps(input_obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        sys.exit(f"[ERROR] Apify実行失敗 {actor_id}: {e.code} {detail}")


def normalize_username(u):
    """URL や @ 付きを素のユーザー名に正規化。"""
    u = u.strip()
    m = re.search(r"threads\.net/@?([A-Za-z0-9_.]+)", u)
    if m:
        return m.group(1)
    return u.lstrip("@").strip("/")


# ----------------------------------------------------------------------------
# 本文取得
# ----------------------------------------------------------------------------
def fetch_posts(args):
    max_posts = args.max
    if max_posts < 10:  # Actor の最低制約
        common.eprint(f"[info] max_posts が {max_posts} のため最低値 10 に引き上げます")
        max_posts = 10

    input_obj = {
        "mode": args.mode,
        "max_posts": max_posts,
        "search_filter": args.search_filter,
    }
    if args.mode == "search":
        if not args.keywords:
            sys.exit("[ERROR] --mode search には --keywords が必要です")
        input_obj["keywords"] = list(args.keywords)
    else:  # user
        if not args.usernames:
            sys.exit("[ERROR] --mode user には --usernames が必要です")
        input_obj["usernames"] = [normalize_username(u) for u in args.usernames]

    items = run_actor(args.actor, input_obj)

    posts = []
    for it in items:
        text = (it.get("text_content") or "").strip()
        if not text:
            continue
        posts.append({
            "text": text,
            "like_count": int(it.get("like_count") or 0),
            "post_url": it.get("post_url") or "",
            "username": it.get("username") or "",
            "is_reply": bool(it.get("is_reply")),
        })
    return posts


def select_parents(posts, args):
    """親投稿として採用する投稿を選定。

    - is_reply=True は親として採用しない
    - --min-likes: 絶対値フィルタ
    - --per-user-top N: アカウントごとの相対上位N件（いいね水準の差を吸収）
    """
    parents = [p for p in posts if not p["is_reply"] and p["post_url"]]

    if args.min_likes:
        parents = [p for p in parents if p["like_count"] >= args.min_likes]

    if args.per_user_top:
        by_user = {}
        for p in parents:
            by_user.setdefault(p["username"], []).append(p)
        selected = []
        for user, plist in by_user.items():
            plist.sort(key=lambda x: x["like_count"], reverse=True)
            selected.extend(plist[:args.per_user_top])
        parents = selected

    parents.sort(key=lambda x: x["like_count"], reverse=True)
    return parents


# ----------------------------------------------------------------------------
# 返信取得（連投の続きを再構成）
# ----------------------------------------------------------------------------
def fetch_replies(parent_urls):
    """選ばれた投稿URLを返信Actorに渡す。1回20URLまで。

    post_urls は [{"url": ...}] のオブジェクト配列形式。
    include_nested_replies=true。
    返り値: source_post_url をキーにした返信リストの辞書。
    """
    all_items = []
    for i in range(0, len(parent_urls), 20):
        chunk = parent_urls[i:i + 20]
        input_obj = {
            "post_urls": [{"url": u} for u in chunk],
            "include_nested_replies": True,
        }
        items = run_actor(REPLIES_ACTOR, input_obj)
        all_items.extend(items)
        if i + 20 < len(parent_urls):
            time.sleep(1)

    by_source = {}
    for it in all_items:
        src = it.get("source_post_url") or ""
        by_source.setdefault(src, []).append({
            "text": (it.get("text_content") or "").strip(),
            "like_count": int(it.get("like_count") or 0),
            "post_url": it.get("post_url") or "",
            "author_username": it.get("author_username") or it.get("username") or "",
            "reply_to_username": it.get("reply_to_username") or "",
        })
    return by_source


def build_thread(parent, replies_for_parent):
    """親投稿＋その本人による自己返信（連投の続き）を時系列順に再構成。

    author_username と reply_to_username が親の投稿者と一致する返信だけを
    「連投の続き」として採用する。
    """
    author = parent["username"]
    continuation = [
        r for r in replies_for_parent
        if r["text"]
        and r["author_username"] == author
        and (not r["reply_to_username"] or r["reply_to_username"] == author)
    ]
    # Apify の返信は概ね時系列。念のため取得順を維持（安定ソート）。
    thread = [{"text": parent["text"], "like_count": parent["like_count"],
               "post_url": parent["post_url"]}]
    for r in continuation:
        thread.append({"text": r["text"], "like_count": r["like_count"],
                       "post_url": r["post_url"]})
    return thread


# ----------------------------------------------------------------------------
# 保存
# ----------------------------------------------------------------------------
def save_threads(threads, meta):
    lines = [f"# Threads バズ投稿収集\n",
             f"- 取得日: {common.today()}",
             f"- 収集条件: {meta}",
             f"- 件数: {len(threads)} スレッド\n",
             "---\n"]
    for idx, thread in enumerate(threads, 1):
        total = len(thread)
        author = thread[0].get("author", "")
        lines.append(f"\n## 投稿 {idx}（連投{total}本） {author}".rstrip())
        lines.append(f"元URL: {thread[0]['post_url']}\n")
        for n, post in enumerate(thread, 1):
            lines.append(f"### [{n}/{total}]（❤️ {post['like_count']:,}）")
            lines.append(post["text"] + "\n")
    content = "\n".join(lines)
    filename = f"{common.today()}_threads-buzz.md"
    path = common.save_material(filename, content, subdir="buzz")
    print(f"[saved] {path}")
    return path


def remove_standalone_duplicates(threads):
    """連投の続きが別スレッドの単独投稿として重複掲載されないよう除去。"""
    # 続きとして既に採用されたURL集合
    used_urls = set()
    for t in threads:
        for post in t[1:]:  # 親以外
            if post.get("post_url"):
                used_urls.add(post["post_url"])
    cleaned = []
    for t in threads:
        if len(t) == 1 and t[0].get("post_url") in used_urls:
            continue  # これは別スレッドの続きなのでスキップ
        cleaned.append(t)
    return cleaned


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Threads バズ投稿収集（Apify）")
    p.add_argument("--mode", choices=["search", "user"], default="search")
    p.add_argument("--keywords", nargs="*", default=None)
    p.add_argument("--usernames", nargs="*", default=None)
    p.add_argument("--max", dest="max", type=int, default=20,
                   help="max_posts（最低10。下回れば10に引き上げ）")
    p.add_argument("--search-filter", dest="search_filter",
                   choices=["top", "recent"], default="top")
    p.add_argument("--min-likes", dest="min_likes", type=int, default=0,
                   help="いいね絶対値の下限")
    p.add_argument("--per-user-top", dest="per_user_top", type=int, default=0,
                   help="アカウントごとの相対上位N件（アカウント指定収集で推奨）")
    p.add_argument("--with-replies", dest="with_replies", action="store_true",
                   help="連投の続きを返信Actorで取得・再構成（型化用は必須）")
    p.add_argument("--actor", default="futurizerush~meta-threads-scraper")
    args = p.parse_args()

    posts = fetch_posts(args)
    parents = select_parents(posts, args)
    if not parents:
        sys.exit("[WARN] 条件に合う親投稿がありませんでした。")

    print(f"[info] 親投稿 {len(parents)} 件を採用")

    threads = []
    if args.with_replies:
        by_source = fetch_replies([p["post_url"] for p in parents])
        for parent in parents:
            replies = by_source.get(parent["post_url"], [])
            thread = build_thread(parent, replies)
            thread[0]["author"] = "@" + parent["username"] if parent["username"] else ""
            threads.append(thread)
    else:
        for parent in parents:
            threads.append([{
                "text": parent["text"], "like_count": parent["like_count"],
                "post_url": parent["post_url"],
                "author": "@" + parent["username"] if parent["username"] else "",
            }])

    threads = remove_standalone_duplicates(threads)

    meta = (f"mode={args.mode} "
            + (f"keywords={args.keywords} " if args.keywords else "")
            + (f"usernames={args.usernames} " if args.usernames else "")
            + f"filter={args.search_filter} "
            + (f"min_likes={args.min_likes} " if args.min_likes else "")
            + (f"per_user_top={args.per_user_top} " if args.per_user_top else "")
            + (f"with_replies " if args.with_replies else ""))
    save_threads(threads, meta.strip())


if __name__ == "__main__":
    main()
