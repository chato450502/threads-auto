# Threads 自動投稿システム

Python + GitHub Actions で動く、サーバーレスの Threads 自動投稿システム。
Claude が毎晩3日分の下書きを生成してキューに貯め、毎日 **9:00 / 15:00 / 21:00（JST）** の
3枠で自動投稿します。

> ※ このリポジトリには別途「Threads投稿 AI自動生成システム」（`CLAUDE.md` / `kata-*`）も
> 同居していますが、本 README が説明するのは **自動投稿（生成＋配信）** の仕組みです。両者は独立しています。

---

## 全体像

```
                 ┌─ generate.yml（JST 3:00）─ src/generate.py
                 │     Claude で下書き生成 → 品質ゲート → queue.json に3日分(9本)補充
                 ▼
   posts/queue.json（下書き在庫。枠キー付き）
                 │
                 ▼
   publish.yml（15分おき）─ src/publish.py
        「予定時刻を過ぎた枠」で1本ずつ投稿（state.json で枠状態を管理）
                 │
                 ▼
   Threads Graph API（コンテナ作成 → 30秒待機 → 公開）

   refresh.yml（月1回）─ src/refresh_token.py … 長期トークンを延命＋Secret更新
```

### 投稿枠（スロット）

`config/slots.json` で1日の枠とテーマ・トーンを定義（既定 09:00 / 15:00 / 21:00 JST）。
publish は「未投稿 かつ 予定時刻を経過」した枠だけを投稿し、**cron の遅延・スキップに耐えます**
（既定6時間の catch-up 窓。`CATCHUP_MINUTES` で調整）。

### 冪等性（二重投稿しない）

- **枠単位** … `posts/state.json` に枠ごとの `posted/failed/missing` を記録。投稿済みの枠は再投稿しない。
- **下書き単位** … `posts/processed.json` に投稿済み下書きIDを即時記録。state 書き戻し失敗時も再投稿しない。

### 品質ゲート（generate.py 内）

生成した下書きを次の順にチェックし、通ったものだけキューへ:

1. **機械チェック** … 500文字以内 / URL5個以内 / CTAブロック有無 / 禁止表現（`config/ng_words.json`）
2. **AI採点** … 別の Claude 呼び出しで 0-100点（トーン一致・4段構成〈共感→痛み→視点転換→実践Tips〉・直近30投稿との類似度）
3. **80点未満なら再生成**（最大3回）
4. **3回落ちたら** `fallback/evergreen.json` の未使用の定型投稿を1本使う

> 文字数は書記素クラスタ基準（絵文字=1文字）で500字。`LENGTH_MODE`（graphemes/codepoints/bytes）で切替可。
> 生成時は直近30投稿の本文をプロンプトに渡し、内容の重複を避けさせます。枠ごとのテーマ・トーンは `config/slots.json`。

### 監視

投稿失敗・在庫切れ・生成全滅・**トークン期限30日切れ** を検知したら、GitHub Actions の
**Summary に警告**を出し、`NOTIFY_WEBHOOK_URL`（Discord/Slack）が設定されていれば通知も送ります。

> ⚠️ 仕様にあった **LINE Notify は 2025-03-31 に提供終了**したため実装していません。
> 代替として Discord / Slack の Incoming Webhook に対応しています（同一ペイロードで両対応）。

---

## セットアップ

### 1. Threads トークンとユーザーID（Meta 開発者設定）

1. **Meta for Developers** [developers.facebook.com](https://developers.facebook.com/) でアプリ作成 →
   ユースケース **「Threads API へのアクセス」**。
2. 権限に **`threads_basic`** と **`threads_content_publish`** を追加。
3. Threads の OAuth で短期トークンを取得し、**長期トークン（約60日）** に交換:
   ```
   GET https://graph.threads.net/access_token?grant_type=th_exchange_token
     &client_secret=＜app secret＞&access_token=＜短期トークン＞
   ```
   返る `access_token` が **`THREADS_ACCESS_TOKEN`**。
4. ユーザーID:
   ```
   GET https://graph.threads.net/v1.0/me?fields=id,username&access_token=＜長期トークン＞
   ```
   返る `id` が **`THREADS_USER_ID`**。

### 2. Claude API キー

[console.anthropic.com](https://console.anthropic.com/) で API キーを発行 → `ANTHROPIC_API_KEY`。
生成モデルは既定 `claude-sonnet-4-6`（`GENERATE_MODEL` で変更可）。

### 3. GitHub Secrets

**Settings → Secrets and variables → Actions** に登録:

| Secret 名 | 用途 | 必須 |
|-----------|------|------|
| `THREADS_USER_ID` | 投稿先ユーザーID | ✅ |
| `THREADS_ACCESS_TOKEN` | 長期アクセストークン | ✅ |
| `ANTHROPIC_API_KEY` | 下書き生成（generate） | ✅ |
| `NOTIFY_WEBHOOK_URL` | Discord/Slack 通知（任意） | － |
| `GH_PAT` | トークン自動更新（refresh）で Secret を書き換える PAT | 自動更新を使うなら |

`GH_PAT` は Fine-grained PAT で対象リポジトリに **Secrets: Read and write** を付与。

### 4. ワークフロー

| ファイル | cron | 内容 |
|----------|------|------|
| `.github/workflows/generate.yml` | `0 18 * * *`（JST 3:00） | 下書きを3日分に補充 |
| `.github/workflows/publish.yml` | `*/15 * * * *`（15分おき） | 予定を過ぎた枠を投稿 |
| `.github/workflows/refresh.yml` | `0 0 1 * *`（毎月1日） | トークン延命 |

> cron は UTC。15分間隔は時差の影響なし。`0 18 * * *` は JST 3:00、`0 0 1 * *` は JST 毎月1日9:00。
> schedule 実行は数分〜十数分遅延することがありますが、15分ポーリング＋catch-up 窓で吸収します。

---

## 使い方

### 通常運用

Secrets を入れてワークフローを有効化すれば、あとは自動です。手を入れるのは
`config/slots.json`（枠のテーマ・トーン）、`config/ng_words.json`（禁止表現）、
`fallback/evergreen.json`（定型投稿の補充）くらいです。

### 手動での下書き生成・投稿

GitHub の Actions 画面から各ワークフローを **Run workflow**（`dry_run` 入力あり）。

### ローカルで試す（ドライラン）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 値を編集（ドライランなら未設定でも可）

# 生成：不足枠の算出だけ表示（Claude を呼ばない）
.venv/bin/python src/generate.py --dry-run

# 投稿：予定時刻を疑似指定して、どの枠が発火するか確認（Threadsへは投稿しない）
.venv/bin/python src/publish.py --dry-run --now "2026-08-26 09:05"
```

`--now` を省くと現在時刻で判定します。`DRY_RUN=true` 環境変数でも同じです。

---

## データ構造

**queue.json**（下書き在庫。1要素=1下書き）

```json
{
  "id": "gen-2026-08-26T0900",
  "slot_key": "2026-08-26 09:00",
  "slot_time": "09:00",
  "theme": "…", "tone": "…", "source": "ai",
  "text": "本文＋CTA（これがそのまま投稿される）",
  "body": "…", "cta": "…", "score": 86,
  "status": "pending",         // pending | posted | failed
  "posted_id": null, "error": null,
  "created_at": "2026-08-26T03:00:00+09:00"
}
```

**state.json**（枠の状態）

```json
{ "slots": { "2026-08-26 09:00": { "status": "posted", "post_id": "…", "posted_id": "…", "posted_at": "…" } } }
```

---

## ファイル構成

```
src/
  threads_common.py   共通（.env・文字数/URL/バリデーション・JST・監視通知）
  slots.py            枠の時刻計算（due_slots / upcoming_slots）
  publish.py          投稿本体（スロット判定・2段階投稿・リトライ・冪等性）
  generate.py         下書き生成（Claude）＋品質ゲート＋監視
  refresh_token.py    トークン延命 + GitHub Secret 更新 + 期限記録
config/
  slots.json          投稿枠（時刻・テーマ・トーン）
  ng_words.json       禁止表現リスト
fallback/
  evergreen.json      生成全滅時の定型投稿
posts/
  queue.json          下書き在庫
  state.json          枠の投稿状態（冪等性）
  processed.json      処理済み下書きID（冪等性）
  token_meta.json     トークン期限（refresh が記録／30日監視が参照）
.github/workflows/
  generate.yml  publish.yml  refresh.yml
requirements.txt  .env.example  README.md
```

---

## 安全設計メモ

- **トークン直書き禁止**：全スクリプトは環境変数からのみ読む。Actions は Secrets、ローカルは `.env`（コミット不可）。
- **二重投稿防止**：枠単位（state.json）＋下書き単位（processed.json）の二重ガード。成功時に即 flush。
- **リトライ**：コンテナ作成・公開を各3回。公開リトライは同一 creation_id を再利用。3回失敗で failed＋警告。
- **cron 耐性**：予定時刻を過ぎた枠を catch-up 窓（既定6時間）内で拾うので、多少の遅延・スキップで欠落しない。
- **同時実行の抑止**：各ワークフローに `concurrency` を設定。
