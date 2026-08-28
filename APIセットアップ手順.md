# API セットアップ手順

このシステムで使う API キーの取得方法をまとめます。取得したキーは `.env` の
プレースホルダを書き換えて貼り付けてください。

---

## 0. どのキーがいつ必要か

| 機能 | 必要なキー |
|------|-----------|
| YouTube 字幕取得（`transcript`） | **不要**（すぐ使える） |
| YouTube 検索・チャンネル選定（`search`/`auto`/`channel`） | `YOUTUBE_API_KEY` |
| Threads バズ投稿収集（`fetch_threads.py`） | `APIFY_TOKEN` |

まずは字幕取得だけで動作確認できます。検索・Threads収集を使う段階でキーを取得してください。

---

## 1. YouTube Data API v3 キー

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセスしてログイン。
2. 画面上部でプロジェクトを新規作成（任意の名前でOK）。
3. 「APIとサービス」→「ライブラリ」で **YouTube Data API v3** を検索し、「有効にする」。
4. 「APIとサービス」→「認証情報」→「認証情報を作成」→「APIキー」。
5. 表示されたキーをコピー。
6. `.env` の `YOUTUBE_API_KEY=` の後ろに貼り付ける。

> 補足: 無料枠は1日あたり 10,000 ユニット。検索は1回100ユニット消費するので、
> 使いすぎに注意。字幕取得はこのAPIを使わないためユニットを消費しません。

---

## 2. Apify トークン（Threads 収集）

1. [Apify](https://apify.com/) にアクセスしてアカウント作成・ログイン。
2. 右上のアカウントメニュー →「Settings」→「API & Integrations」。
3. **Personal API token** をコピー（トークン取得のみでOK。Actor の事前設定は不要）。
4. `.env` の `APIFY_TOKEN=` の後ろに貼り付ける。

> 使用する Actor（`.env` に設定済み）:
> - 本文取得: `futurizerush~meta-threads-scraper`
> - 返信取得: `futurizerush~threads-replies-scraper`（`--with-replies` 時に自動使用）
>
> ⚠️ Threads 収集は規約グレーです。**週1回・20件程度**の控えめな運用にしてください。

---

## 3. 動作確認

キー不要の字幕取得でテスト（適当な日本語動画URLで）:

```
.venv/bin/python scripts/fetch_youtube.py transcript "https://www.youtube.com/watch?v=＜動画ID＞"
```

`materials/` に `.md` が保存されれば成功です。
