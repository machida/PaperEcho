# リファクタリング計画

採譜エンジン・再生・リズム修正・書き出しの読み取り補助（テンポグリッド、
オクターブ、パート選択、…）を追加する過程で溜まった重複 / パラメータの肥大化を
整理します。各タスクは挙動を変えず、`pytest` / `tsc --noEmit` / `cargo check` で
グリーンを確認済みです。

ステータス: `[ ]` 未着手 · `[x]` 完了 — **すべて完了（2026-06-24）**。
`pytest`（46 件）、`tsc --noEmit`、`cargo check` でグリーン確認済み。

## タスク

- [x] **R1 — デバイス検出の統一。** `separate._device()` と
  `transcribe._crepe_device()` はほぼ同一の GPU 自動検出ヘルパでした。共有の
  `paperecho.device.resolve_device()` を 1 つ抽出し、両方がそれを呼ぶようにします。
  *対象:* `python/paperecho/device.py`（新規）、`separate.py`、`transcribe.py`。

- [x] **R2 — 楽譜整形パラメータを `ScoreOptions` にまとめる。** `cmd_export` と
  `cmd_preview` はそれぞれ 5 つのばらけたパラメータ（`tempo_multiplier`,
  `beat_offset`, `key_sharps_override`, `tempo_mode`, `octave_shift`）を取り、それらは
  serve ディスパッチ・`argparse`・`main()` でも再掲されています。`from_request` /
  `from_args` ビルダ付きの単一 `@dataclass ScoreOptions` にまとめて、重複を無くし、
  将来のオプション追加を 1 行で済むようにします。
  *対象:* `python/paperecho/pipeline.py`。

- [x] **R3 — `_resolve_grid()` を抽出。** `cmd_export` と `cmd_preview` は同じ約 8 行
  を繰り返しています（`bpm/time_sig/beats/beats_per_bar/downbeat_phase` を読み、
  （固定時に）`to_fixed_grid` を適用、その後 `apply_tempo_multiplier`（ダウンビート
  位相をリセット））。解決済みグリッドを返す 1 つのヘルパに引き出します。
  *対象:* `python/paperecho/pipeline.py`。

- [x] **R4 — `<Segmented>` コントロールコンポーネント。** `Export.tsx` は同じ
  `.tempo-toggle` + `.seg` のボタン map を 4 回手書きしています（テンポグリッド、
  テンポ、拍のずらし、オクターブ）。1 つの
  `<Segmented options value onChange>` コンポーネントに抽出します。
  *対象:* `src/components/Segmented.tsx`（新規）、`src/routes/Export.tsx`。

- [x] **R5 — 書き出し/プレビュー IPC 呼び出しのオプションオブジェクト化。**
  `exportParts`（位置引数 8 個）と `previewScore`（7 個）は呼び出し側でミスを
  招きやすいです。TS 側で楽譜整形引数を 1 つの `ScoreControls` オブジェクトに
  まとめます（`invoke` ペイロードのキーは同じなので、Rust コマンドは変更不要）。
  *対象:* `src/lib/ipc.ts`、`src/routes/Export.tsx`。

## スコープ外（記録のみ、今はやらない）

- Rust の `export`/`preview` コマンド引数を serde 構造体にまとめる——実利なく
  ワイヤフォーマットを変えるだけ。コマンドは薄いパススルーです。
- `transcribe.MONO_PARTS` と `score.MONOPHONIC_PARTS` を 1 つの名前に統一する——
  見た目だけの問題で、ホットな採譜パスに触れるためスキップ。
