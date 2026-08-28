---
name: kata-list
description: 型ライブラリの一覧を表示する。kata-library/INDEX.md と各型ファイルの frontmatter を突き合わせ、不整合があれば修正する。今ある型を確認したいときに使う。
---

# /kata-list — 型ライブラリ一覧

`kata-library/` の型を一覧表示する。

## 手順

1. `kata-library/INDEX.md` を読む。
2. `kata-library/` 内の各 `<type-slug>.md`（`_common-modules.md` と `INDEX.md` を除く）の
   frontmatter を読む。
3. 両者を突き合わせ、一覧表を表示する。列:
   slug / 分類 / 連投数レンジ / フック技法 / 主エンゲージ源 / ファネル役割 / 使用回数 / 登録日
4. **不整合があれば修正する**:
   - INDEX にあるがファイルが無い、または逆
   - 使用回数（`uses`）が INDEX とファイルで食い違う → ファイル側を正として INDEX を合わせる
   - frontmatter の欠損項目を指摘
5. 修正した場合は何を直したか報告する。型が0件なら「まだ型がありません」と案内する。
