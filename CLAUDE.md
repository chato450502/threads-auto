# Threads投稿 AI自動生成システム

バズ投稿から「型（構造テンプレート）」を抽出・蓄積し、YouTube動画の書き起こしを素材に
Threads連投を自動生成するシステム。

---

## アカウント設定

- **運用アカウント**: @mirei_fondly（キャラクター「みれい」。人格設定は `assets/persona.md`）
- **発信ジャンル・趣旨**: 20〜30代女性向けの恋愛（男性心理・愛され・溺愛系）
- **素材にする定番YouTubeチャンネル**: `assets/youtube-channels.txt` で管理（後から追加可）
- **ベンチマークThreadsアカウント**: @naniwa_tyan、@saya189ne、@hareru_koi、@aco.renai.mentor、@luna.note02
- **CTA**: 固定文面の使い回しはしない。`assets/cta.md` の参考例を文脈にAIが投稿ごとに新規生成する
- **リンク誘導先（note）**: https://note.com/mirei_fondly/n/n84f1c8740a98
  （⚠️ 将来変更の可能性あり。CTA生成の都度この行を確認し、変更されていたら反映すること）

---

## 全体像（2系統のパイプライン）

- **登録系 `/kata-register`**: バズ投稿を収集 → 構造分析 → 型化 → 重複判定 → 型ライブラリに保存
- **制作系 `/kata-write`**: 素材収集 → 型選択 → リライト → 検証 → CTA連結 → 保存
- **一覧 `/kata-list`**: 型ライブラリの一覧表示

```
バズ投稿 ──/kata-register──▶ kata-library/（型の蓄積）
                                   │
YouTube字幕 ──素材──▶ /kata-write ─┴─▶ output/posts/（完成投稿）
```

---

## ディレクトリ構成

```
CLAUDE.md                    このファイル（使い方・設計上の決定事項）
.env                         APIキー（プレースホルダ）
APIセットアップ手順.md        キー取得手順書
.claude/skills/              kata-register / kata-write / kata-list の3スキル
kata-library/                型ライブラリ
  INDEX.md                   目次（重複判定・一覧の基準）
  _common-modules.md         全投稿に適用する共通固定文面
  <type-slug>.md             1型=1ファイル
assets/
  cta.md                     CTA参考例（AIが投稿ごとに新規生成する際の文脈）
  persona.md                 キャラクター設定（@mirei_fondly「みれい」の人格・口調）
  youtube-channels.txt       定番チャンネルリスト
scripts/
  common.py                  共通ユーティリティ
  fetch_youtube.py           YouTube検索・字幕取得・チャンネル選定
  fetch_threads.py           Threadsバズ収集（Apify経由）
  check_length.py            投稿の機械検証（500字/アスタリスク/ページラベル）
materials/                   素材置き場（buzz/ は収集したバズ投稿）
output/posts/                生成した投稿の保存先
.venv/                       Python仮想環境
```

---

## セットアップ状況

- [x] `.venv` 作成・`youtube-transcript-api` `requests` インストール済み
- [x] `.env` プレースホルダ作成済み
- [ ] APIキー取得（`APIセットアップ手順.md` 参照）→ 検索・Threads収集を使う段階で
- [x] `assets/cta.md` にCTA参考例を貼り付け（2026-08-23、AI生成方式に移行）
- [ ] `/kata-register` で型を3〜4個登録 → `/kata-write` で運用開始

## スクリプトの実行

Python は必ず venv のものを使う:

```
.venv/bin/python scripts/fetch_youtube.py transcript "<URL>"
.venv/bin/python scripts/fetch_youtube.py channel --list
.venv/bin/python scripts/fetch_threads.py --mode user --usernames @aaa --per-user-top 3 --with-replies
.venv/bin/python scripts/check_length.py output/posts/<file>.md
```

---

## ⚠️ 設計上の絶対ルール（変更禁止）

1. **CTAは投稿ごとにAIが新規生成する（固定文面の使い回し禁止）。** `assets/cta.md` の
   参考例を文体・構成の文脈として使い、フレーズを丸ごと使い回さず本文の内容に合わせて
   都度書き起こす（2026-08-23変更。理由: 固定文面の使い回しは型と同様スパム判定/既視感
   のリスクがあるため。フレーズの直接コピペは禁止、機能・構成だけ参考にする）。
2. **同じ型の高頻度使用は禁止。** 同型の連続使用でスパム判定されリーチが激減した実績あり
   → 型のローテーション必須（直近2投稿で使った型は選ばない・同日に同型2本は作らない）。
3. **型ファイルに元バズ投稿の本文をコピーしない。** 出典は要約1行のみ。
4. **バズ判定は絶対値でなく相対評価。** アカウント/チャンネルの普段との倍率で判断する。
5. **各投稿500字はスクリプトで機械検証する。** AIの目視だけに頼らない（`check_length.py`）。
6. **Threads収集は控えめに。** 週1回・20件程度（規約グレーのため）。

---

## 型化・制作の要点（詳細は各スキル定義を参照）

- 型は **フレーズ実例ではなく「機能」で記述** する
  （×「"エグい話するけど"で始める」 → ○「核心を明かす予告で始める。表現は自由」）。
- 数字（項目数など）は「素材から決める」と可変化する。
- リライトは **型プロンプト＋`_common-modules.md` の両方** を必ず適用。
  内容は素材から、型からは構造だけ。口調・フレーズ・テーマ・数字は引き継がない。
- 検証は `check_length.py`（機械）＋ 同一語尾3連続なし ＋ 素材と似た表現の混入なし。
