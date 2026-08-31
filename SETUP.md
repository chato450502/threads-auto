# セットアップ手順（新しいアカウントで使い始める人向け）

このリポジトリは、Threadsに **AIが型＋YouTube素材から連投を自動生成し、1日3枠で自動投稿**する
システムです。この手順どおりに進めれば、あなた自身のアカウント・あなた自身のAPIキーで、
独立して動かせます。

> 所要：慣れた人で30〜60分。一番の難所は「Threadsのトークン取得」です。

---

## 0. 用意するもの

- **GitHubアカウント**（無料）
- **投稿するThreadsアカウント**（＝あなたの運用アカウント）
- 3つのAPIキー：Threads（Meta）／Anthropic（Claude）／YouTube Data API
- パソコンで少しだけコマンドを打てる環境（トークン取得の1回だけ）

---

## 1. テンプレから自分のリポジトリを作る

1. このリポジトリのページ右上 **「Use this template」→「Create a new repository」**
2. **Private** を選び、好きな名前で作成
3. できた自分のリポジトリを、パソコンに `git clone` する（またはCodespaces等）

---

## 2. Python環境を用意（トークン取得と初期化に使う）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # 後でこの .env に値を入れる
```

---

## 3. Threadsのトークンを取得（最重要・つまずきポイント）

### 3-1. Metaでアプリを作る
1. [developers.facebook.com/apps](https://developers.facebook.com/apps) →「アプリを作成」→ ユースケース **Threads**
2. 権限に次の**3つ**を付与（⚠️ 3つ目が連投に必須）：
   - `threads_basic`
   - `threads_content_publish`
   - **`threads_manage_replies`**（これが無いと返信＝連投がHTTP500で失敗します）
3. 「アプリの設定→ベーシック」で **アプリID** と **app secret** を控える
4. ユースケースの設定で **Redirect Callback URLs** に `https://localhost/` を登録
5. **Threads testers** に自分の運用アカウントを追加し、そのアカウント側で承認

### 3-2. `.env` にアプリ情報を書く
```
THREADS_APP_ID=（アプリID）
THREADS_APP_SECRET=（app secret）
THREADS_REDIRECT_URI=https://localhost/
```

### 3-3. トークンを取得
```bash
.venv/bin/python src/get_token.py url
```
表示URLを**運用アカウントでログイン中のブラウザ**で開く →「許可」→ `https://localhost/?code=XXXX#_` の
`code=`の後ろ〜`#`の前をコピー →
```bash
.venv/bin/python src/get_token.py exchange --code "コピーしたcode"
```
出てきた `THREADS_USER_ID` と `THREADS_ACCESS_TOKEN` を控える（`.env` にも書いておくと後がラク）。

> トークンは約60日で失効。`refresh.yml`（月1）が自動延命します。

---

## 4. 他のキーを取得

- **Anthropic（Claude）**：[console.anthropic.com](https://console.anthropic.com/) で APIキー（`sk-ant-...`）
- **YouTube Data API v3**：[Google Cloud Console](https://console.cloud.google.com/) で API有効化 → APIキー
- **GH_PAT**：GitHubの [Personal access tokens](https://github.com/settings/tokens) で、対象リポジトリに
  **Contents=Read/write・Secrets=Read/write**（fine-grained）か、classicなら `repo`＋`workflow`。
  月1のトークン自動延命（Secret更新）に使います
- **（任意）通知Webhook**：Discord/Slackの Incoming Webhook URL

---

## 5. GitHub Secrets を登録

自分のリポジトリの **Settings → Secrets and variables → Actions** に登録：

| Secret 名 | 中身 | 必須 |
|-----------|------|------|
| `THREADS_USER_ID` | 3-3で取得 | ✅ |
| `THREADS_ACCESS_TOKEN` | 3-3で取得（`threads_manage_replies`付き） | ✅ |
| `ANTHROPIC_API_KEY` | Claudeのキー | ✅ |
| `YOUTUBE_API_KEY` | YouTubeのキー | ✅ |
| `GH_PAT` | トークン延命用 | ✅（延命を使うなら） |
| `NOTIFY_WEBHOOK_URL` | Discord/Slack | 任意 |

---

## 6. 自分用に設定を書き換える

| ファイル | 中身 |
|---|---|
| `config/account.json` | ハンドル・表示名・ジャンル・note誘導先（`account.example.json` を参考に） |
| `config/slots.json` | 投稿枠の時刻・テーマ・トーン（既定 9/15/21時 JST） |
| `config/ng_words.json` | 禁止表現 |
| `assets/persona.md` | キャラクター（声・口調・人称） |
| `assets/cta.md` | CTAの参考例 |
| `assets/youtube-channels.txt` | 素材にする定番YouTubeチャンネル |
| `kata-library/*.md` | 型（そのまま流用可。自分で `/kata-register` 相当を足すのも可） |

> ⚠️ **投稿枠の時刻を変えたら**、`config/slots.json` と `.github/workflows/publish.yml` の cron の
> **両方**を合わせてください（cronはUTC。JST 9/15/21時 = UTC 0/6/12時）。

---

## 7. 状態を初期化して反映

```bash
.venv/bin/python scripts/init_account.py    # 前運用者の在庫・履歴を空に
git add -A && git commit -m "setup: my account" && git push
```

`.env` は `.gitignore` で除外されるので、キーがコミットされる心配はありません。

---

## 8. 動作確認 → 本番

- GitHubの **Actions** タブで `Generate Threads Drafts` を **Run workflow → dry_run: true**（不足枠の確認）
- 次に dry_run なしで `Generate` → `posts/queue.json` に連投が入る
- `Publish` は枠時刻の前後に自動実行。最初はActionsのログで成否を確認

---

## 運用の注意（各自の責任で）

- **GitHub Actions無料枠**：publishは「枠の前後2時間だけ15分間隔」に調整済み（`publish.yml` の cron）。
  枠を増やす・頻度を上げると private の無料枠（月2000分）を超えることがあります。
- **費用**：Claude生成はアカウントごとに課金。連投×3枠だと相応にかかります。
- **Threads/Metaの規約**：自動投稿・高頻度・連投・CTAは規約グレーで、アカウント停止のリスクが
  あります。**必ず自分のアカウントで、自己責任で**運用してください。まず1アカウントで様子を見て、
  問題なければ調整するのが安全です。
- **返信の読み取り**（/replies edge）にはさらに別権限が要ります。投稿だけなら不要です。

---

## トラブル早見表

| 症状 | 原因・対処 |
|---|---|
| 連投が繋がらず単体投稿になる | トークンに `threads_manage_replies` が無い → 権限追加＋トークン取り直し |
| 返信作成が HTTP 500（unknown error） | 同上（返信権限不足） |
| `redirect_uri` エラー | アプリ登録値と `.env` の `THREADS_REDIRECT_URI` を完全一致させる |
| 生成が全滅 | YouTubeキー未設定／チャンネルが空／字幕なし動画ばかり |
| Actionsが動かない | Secrets未登録／ワークフローがdisabled／無料枠切れ |
