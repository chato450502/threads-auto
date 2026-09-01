# はじめての方へ — 完全セットアップガイド（省略なし）

このシステムは、**あなたのThreadsアカウントに、毎日自動で投稿してくれる仕組み**です。
パソコンやプログラミングの知識がなくても、この通りに進めれば使えます。
**パソコンにコマンドを打つ作業は一切ありません。すべてWebサイトのボタン操作だけ**で完結します。

読みながら1つずつ進めてください。所要時間はだいたい1時間ほどです。

---

## 0. まず「仕組み」を理解する（ここ大事）

### これは何をするもの？
- AIが「型（バズる文章の骨組み）」とYouTube動画の内容をもとに、**Threadsの文章を自動で作ります**。
- そして **毎日 朝9時・昼15時・夜21時 に、あなたのThreadsへ自動で投稿**します（連投＝スレッド形式）。

### なぜ「GitHub」が必要なの？
プログラムは、どこかで「動かし続ける場所」が必要です。あなたのパソコンでやると、
パソコンを閉じたら止まってしまいます。

そこで **GitHub（ギットハブ）** を使います。GitHubは、
- プログラムを**置いておく場所**（＝あなた専用のフォルダをクラウドに持てる）
- 決まった時刻にプログラムを**自動で動かしてくれるタイマー**
- APIキーなどの秘密を**安全に保管する金庫**

これらを**無料**で提供してくれるサービスです。つまりGitHubが、あなたの代わりに
「クラウド上の作業員」として、決まった時刻に投稿を実行してくれます。
だから**あなたのパソコンは閉じていてOK**。寝ていても投稿されます。

### 図でイメージ
```
   GitHub（クラウド）＝あなたの代わりに働く作業員
   ├─ プログラム一式（文章を作る・投稿する）
   ├─ タイマー：毎日3時=下書き作成 / 9・15・21時=投稿
   └─ 金庫（Secrets）：APIキーを安全に保管
        ↓ 決まった時刻に自動実行
   あなたのThreadsアカウントに投稿される
```

### 登場する「鍵（キー）」の意味
このシステムは、外部のサービスを使うために「鍵」がいくつか要ります。難しく考えず、
「〇〇を使うための許可証」と思ってください。

| 鍵 | 何のため |
|---|---|
| **Threadsトークン** | あなたのThreadsに「投稿していいよ」という許可証 |
| **Anthropicキー** | 文章を書くAI（Claude）を使う鍵 |
| **YouTubeキー** | 素材にする動画を探す鍵 |
| **GH_PAT** | GitHubが自分の金庫を更新するための鍵（トークンの自動更新に使う） |

---

## 全体の地図（迷子にならないために）

1. GitHubアカウントを作る
2. テンプレから「自分のコピー」を作る
3. Metaでアプリを作り、Threadsの鍵を用意
4. 他の鍵（Anthropic・YouTube・GH_PAT）を用意
5. 4〜6個の鍵を、GitHubの金庫（Secrets）に入れる
6. **ボタンだけ**でThreadsトークンを取得
7. 設定ファイルを自分用に書き換える
8. 「初期化」ボタンを押す → 完成、あとは自動

では1つずつ。

---

## ステップ1：GitHubアカウントを作る

1. [github.com](https://github.com/) を開く
2. 右上 **「Sign up」** をクリック
3. メールアドレス・パスワード・ユーザー名を入れて登録（無料プランでOK）
4. 届いたメールで本人確認

> すでにアカウントがあればログインするだけでOK。

---

## ステップ2：テンプレから「自分のコピー」を作る

1. 配布された **テンプレのURL** を開く（例: `https://github.com/＜配布元＞/threads-auto`）
2. 緑色の **「Use this template」→「Create a new repository」** をクリック
3. **Repository name**：好きな名前（例 `my-threads`）
4. **Private** を選ぶ（⚠️ 公開にしない）
5. **「Create repository」** をクリック

これで、あなた専用のコピー（リポジトリ）ができました。以降の作業は、この
**自分のリポジトリのページ**で行います。

---

## ステップ3：Metaでアプリを作り、Threadsの鍵を用意

Threadsに自動投稿するには、Meta（Threadsの運営会社）で「アプリ」を作る必要があります。

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) を開いてログイン
2. **「アプリを作成」** → ユースケースで **「Threads」** を選んで作成
3. 権限（アクセス許可）に、次の**4つ**を追加する：
   - `threads_basic`
   - `threads_content_publish`
   - **`threads_manage_replies`** ← ⚠️**これが無いと連投（返信でつなぐ）が失敗します。必ず追加**
   - **`threads_manage_insights`** ← 週1の「型の自動入れ替え」で投稿の成績を読むのに必要
4. **「アプリの設定 → ベーシック」** の画面で、次の2つを控える（後で使う）：
   - **アプリID**（数字）
   - **app secret**（「表示」ボタンを押すと見える文字列）
5. Threadsユースケースの設定で、**Redirect Callback URLs** に `https://localhost/` を登録して保存
6. 同じ設定内の **Threads testers** に、あなたの運用アカウントを追加 → そのアカウント側で承認
   （ウェブ版Threadsの設定から、届いた招待を承認します）

> 「アプリID」「app secret」「`https://localhost/`」の3つを、次のステップでGitHubに入れます。

---

## ステップ4：他の鍵（Anthropic・YouTube・GH_PAT）を用意

**Anthropic（AIの鍵）**
1. [console.anthropic.com](https://console.anthropic.com/) に登録・ログイン
2. API Keys の画面で新しいキーを作成 → `sk-ant-...` をコピー（課金設定が必要な場合あり）

**YouTube（動画を探す鍵）**
1. [Google Cloud Console](https://console.cloud.google.com/) にログイン
2. プロジェクトを作成 →「APIとサービス → ライブラリ」で **YouTube Data API v3** を有効化
3. 「認証情報 → APIキーを作成」→ キーをコピー

**GH_PAT（GitHubが自分の金庫を更新する鍵）**
1. GitHubの [Personal access tokens (Fine-grained)](https://github.com/settings/personal-access-tokens) を開く
2. 「Generate new token」→ **Repository access** で、ステップ2で作った自分のリポジトリを選ぶ
3. **Permissions** で次を **Read and write** にする：
   - **Contents**
   - **Secrets**
4. 「Generate token」→ 出てきた `github_pat_...` をコピー

---

## ステップ5：鍵を「金庫（Secrets）」に入れる

自分のリポジトリのページで：

1. 上のメニュー **「Settings」** をクリック
2. 左メニュー **「Secrets and variables」→「Actions」**
3. **「New repository secret」** ボタンで、下の表を**1つずつ**登録する
   （Name＝名前、Secret＝中身。1つ入れるごとに「Add secret」）

| Name（名前） | Secret（中身） |
|---|---|
| `THREADS_APP_ID` | ステップ3のアプリID |
| `THREADS_APP_SECRET` | ステップ3のapp secret |
| `THREADS_REDIRECT_URI` | `https://localhost/` |
| `ANTHROPIC_API_KEY` | Anthropicのキー |
| `YOUTUBE_API_KEY` | YouTubeのキー |
| `GH_PAT` | GitHubのトークン（github_pat_...） |
| `APIFY_TOKEN` | Apifyのトークン（週1の型リサーチ用。[apify.com](https://apify.com/) で無料登録→Settings→APIから取得） |
| `NOTIFY_WEBHOOK_URL` | （任意）Discord/SlackのWebhook。なければ登録しなくてOK |

> ⚠️ `THREADS_ACCESS_TOKEN` と `THREADS_USER_ID` は、次のステップ6で**自動的に入ります**。
> ここでは登録しなくてOKです。

---

## ステップ6：Threadsトークンを取得（ボタンだけ・パソコン不要）

ここが少し独特ですが、**ボタンを2回押すだけ**です。

1. リポジトリ上のメニュー **「Actions」** をクリック
   - （初回は「I understand my workflows, go ahead and enable them」が出たら押して有効化）
2. 左の一覧から **「1. Threadsトークン取得（パソコン不要）」** を選ぶ
3. 右の **「Run workflow」** を押す → **mode を `url`** のまま **「Run workflow」**（緑ボタン）
4. 少し待つと実行が終わる。その**実行結果をクリック**すると、ページに
   **「① この認可URLを開いてください」** としてURLが表示される
5. そのURLをコピーして、**運用アカウントでログイン中のブラウザ**で開く →「許可」を押す
6. `https://localhost/?code=XXXXX#_` という画面に飛ぶ（エラー画面でOK）。
   アドレス欄の **`code=` の後ろ〜`#`の前**（XXXXX）をコピー
7. もう一度 **「Run workflow」** を押し、今度は **mode を `exchange`** に変え、
   **code の欄にさっきのXXXXXを貼って** 実行
8. 実行結果に **「② トークン保存 完了 🎉」** と出れば成功。
   `THREADS_ACCESS_TOKEN` と `THREADS_USER_ID` が自動でSecretsに入りました

> うまくいかないとき：`code` は数十秒で無効になります。3〜6をやり直せば何度でもOK。
> 「redirect_uri」系のエラーが出たら、ステップ3の `https://localhost/` とSecretの
> `THREADS_REDIRECT_URI` が完全一致しているか確認。

---

## ステップ7：設定を自分用に書き換える

自分のキャラクターやテーマに変えます。ファイルは**GitHubの画面上で直接編集**できます。

編集のやり方（共通）：
- リポジトリのトップでファイル名をクリック → 右上の **鉛筆アイコン（Edit）** → 書き換え →
  緑の **「Commit changes」** を押す

書き換えるファイル：

| ファイル | 中身 | 注意 |
|---|---|---|
| `config/account.json` | ハンドル・表示名・ジャンル・note誘導先 | `config/account.example.json` が見本 |
| `assets/persona.md` | キャラクターの声・口調・性格 | ここが投稿の「人格」になります |
| `assets/cta.md` | CTA（プロフ誘導）の参考例 | |
| `assets/youtube-channels.txt` | 素材にするYouTubeチャンネル（1行1つ） | |
| `config/slots.json` | 投稿する時刻とテーマ・トーン | 既定は9/15/21時 |
| `config/ng_words.json` | 使ってほしくない表現 | |

> ⚠️ `.json` ファイルは記号（`{ } " ,`）を壊さないよう、**「" "（ダブルクォート）の中の文字だけ**
> 書き換えてください。記号を消すと動かなくなります。
>
> ⚠️ 投稿時刻を変えたい場合は、`config/slots.json` と `.github/workflows/publish.yml` の中の
> `cron` の**両方**を直す必要があります（難しければそのままの時刻を推奨）。

---

## ステップ8：初期化して完成

テンプレには見本のデータ（前の人の投稿在庫）が入っているので、それを空にします。

1. **「Actions」** タブ → **「2. 初期化（前の人のデータを空にする）」** を選ぶ
2. **「Run workflow」** → 実行

これで完成です！ 以降は自動で、
- **毎日3時**：AIが3日分の連投を作って貯める
- **9時・15時・21時**：貯めた連投を1つずつ投稿

してくれます。**あなたは何もしなくてOK**です。

さらに **毎週月曜の早朝**、システムが自分の投稿の成績（表示回数・いいね）を測り、
**伸びていない「型」を引退させ、新しくリサーチした型と自動で入れ替え**ます
（急に総入れ替えしないよう、データが十分たまった型だけを1週1つずつ、慎重に）。
これも全部自動なので、放っておくほど良い型に最適化されていきます。

> ⚠️ この「型の入れ替え」を使うには、ステップ3で `threads_manage_insights` 権限を、
> ステップ5で `APIFY_TOKEN` を登録しておく必要があります（未登録なら投稿は動きますが入れ替えは行われません）。

---

## 最初の動作確認（やっておくと安心）

- 「Actions」→ **「Generate Threads Drafts」** →「Run workflow」で **dry_run を true** にして実行
  → エラーが出なければキー類は正しく入っています
- 続けて dry_run なしで実行すると、`posts/queue.json` に連投が作られます
- 「Publish Threads Posts」は投稿時刻の前後に自動で動きます。最初は「Actions」のログで
  成功/失敗を確認しましょう

---

## 大切な注意（必ず読んでください）

- **費用は自己負担**：AI（Anthropic）は投稿を作るたびに少額かかります。連投×1日3回だと
  相応の金額になります。使いすぎが不安なら、Anthropicの利用上限を設定してください。
- **規約リスク**：SNSへの自動投稿・高頻度・毎回の宣伝リンクは、Threads/Metaの規約上グレーで、
  **アカウント停止のリスク**があります。**必ず自分のアカウントで、自己責任で**行ってください。
  まずは様子を見ながら、問題なければ続ける、が安全です。
- **GitHub無料枠**：このシステムは無料枠に収まるよう調整済みですが、投稿回数を増やすと
  超えることがあります。

---

## 困ったとき（症状 → 対処）

| 症状 | 原因・対処 |
|---|---|
| 連投がバラバラの投稿になる（返信でつながらない） | トークンに `threads_manage_replies` が無い → ステップ3で権限追加し、ステップ6でトークンを取り直す |
| トークン取得で「redirect_uri」エラー | ステップ3の `https://localhost/` とSecretの `THREADS_REDIRECT_URI` を完全一致させる |
| `code` を貼っても失敗する | codeは数十秒で失効。ステップ6の3〜7をやり直す |
| 何も投稿されない | ①ステップ8の初期化をしたか ②Secretsが全部入っているか ③Actionsが有効か を確認 |
| 生成が全部失敗する | YouTubeキーが未登録／`youtube-channels.txt` が空／字幕の無い動画ばかり |

わからないところは、その画面のスクリーンショットを撮って質問すると解決が早いです。
