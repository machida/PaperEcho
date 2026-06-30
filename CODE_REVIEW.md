# コードレビュー — Paper Echo

レビュー日: 2026-06-30 / 対象: 全レイヤ（`src/` フロントエンド、`src-tauri/` Rust、
`python/paperecho/` ML パイプライン、約 5,000 行）。

検証結果（レビュー時点、すべてグリーン）:

- `pytest tests/ -q` → **47 passed**
- `cargo test --lib` → **5 passed**
- `tsc --noEmit` → **エラーなし**
- i18n キー整合: ja 88 / en 88（欠落なし）

**対応後（2026-06-30 時点、すべてグリーン）:**

- `pytest tests/ -q` → **63 passed**（+16: `test_pipeline.py`）
- `cargo test --lib` → **5 passed**（`python.rs` 改修後も維持）
- `npm test`（Vitest）→ **12 passed**（新規）
- `npm run typecheck` / `npm run lint` / `npm run build` → グリーン
- `ruff check .`（Python lint, 新規）→ グリーン
- 進捗の詳細は末尾の「タスクリスト」を参照（High 2 / Medium 4 完了 + ruff、
  Low は L6 のみ保留）。

## 総評

**設計・実装の質は高い。** レイヤ分離（React UI / Rust IPC / Python ML）が明快で、
Rust⇄Python の 1 行 JSON 契約、`ScoreOptions` への整形パラメータ集約、共有
`device.resolve_device()` など、過去のリファクタリング（`REFACTORING.md`）の成果が
効いている。`pipeline.py` 以下のドメインロジック（採譜・量子化・ゴースト除去・拍グリッド）
にはコメントで「なぜそうしたか」が丁寧に残されており、保守性が高い。

明確なバグは発見できなかった。以下は **品質向上・保守性・テスト網羅・堅牢性** の観点での
指摘で、機能を壊している問題ではない。重大度（High / Medium / Low）を付けた。

---

## 指摘事項

### High（着手推奨）

- **H1 — `pipeline.py` のオーケストレーションがほぼ未テスト。** 採譜/採点の純粋関数は
  `tests/` で手厚く検証されているが、`pipeline.py` の分岐ロジックには直接テストがない。
  特にリスクが高いのは:
  - `_resolve_grid()`: `tempo_multiplier != 1.0` のとき `downbeat_phase` を 0 にリセット
    する副作用、`fixed`/`variable` の分岐。
  - `_build_part_score()`: 派生パート（`bass_treble`）の transpose と `octave_shift` の
    **加算スタック**、クレフ上書き（`guitar` = treble8vb）。
  - `_scoreable()` / `_resolve_key()` / `ScoreOptions.from_request`/`from_args`。
  これらは export/preview の出力を直接左右するため、回帰テストの価値が大きい。

- **H2 — `python.rs::request` にタイムアウト/ウォッチドッグがない。** 常駐 Python が
  ハング（巨大ファイルや GPU デッドロック等）すると、`read_line` がブロックし続け、
  リクエストを直列化している `Mutex` を握ったままになる。以降の全コマンドが固まり、
  アプリ再起動以外に回復手段がない。読み取りタイムアウト or ハートビート監視で、
  デッドプロセスを検知して再生成できるようにしたい。

### Medium

- **M1 — デッドコード: `transcribe_mono` とその専用ヘルパ。** `transcribe_part` の
  ルーティングは bass/vocals→`transcribe_onset`、piano→`transcribe_piano`、その他→
  `transcribe`(Basic Pitch) で、`transcribe_mono`（pYIN 版、約 77 行）は**どこからも
  呼ばれていない**。専用の `_median_smooth` / `_close` も同様。docstring には「軽量
  フォールバックとして残す」とあるが、実呼び出しがないため将来の読者を惑わせる。
  → 削除するか、本当にフォールバックとして使うなら `transcribe_part` に経路を足す。

- **M2 — `tempfile.mktemp()` の使用（非推奨・競合の余地）。** `transcribe.py:364` と
  `pipeline.py:390`。`mktemp` はファイル名予約後に作成までの隙があり、Python でも
  非推奨。`tempfile.mkstemp()` か `NamedTemporaryFile` に置換（挙動はほぼ同じ）。

- **M3 — フロントエンドにテストランナーがない。** `StemMixer` のソロ/ミュートの
  実効ゲイン計算（`applyGains` のソロ優先ロジック）、i18n の `{token}` 置換、
  `effectiveGains()` などは純粋ロジックで単体テスト可能。Vitest 導入を推奨。

- **M4 — Lint / 型チェックのスクリプトと CI が未整備。** `package.json` に `lint` /
  `typecheck` スクリプトがなく、ESLint 設定も Python の ruff 設定も無い。`.github/
  workflows/` も無いため、PR でテストが自動実行されない。最低限 `tsc --noEmit` +
  `pytest` + `cargo test` を回す CI と、`npm run typecheck` の追加を推奨。
  （公開リポジトリなので外部コントリビュータ対策としても効く。）

### Low

- **L1 — デッドフィールド: `PART_META[*].label`。** i18n 化（`t(\`part.${name}\`)`）後、
  `label` は読まれておらず `color` のみ使用。`types.ts` のコメントは「`scoreable`
  parts can produce notation」とあるが `scoreable` フィールド自体も存在しない（コメントの
  古い名残）。`label` 削除＋コメント修正で整理。

- **L2 — ユーティリティ重複: `formatTime`(Analyze.tsx) と `fmtTime`(Export.tsx)。**
  同一の mm:ss フォーマッタが 2 箇所。共有ヘルパ（例 `lib/format.ts`）へ抽出。

- **L3 — `cmd_mixdown` が CLI から到達不能。** `serve` ループは dispatch するが、
  `main()` の argparse には `mixdown` サブコマンドがない。他コマンドは CLI でも叩けて
  テスト/デバッグできるのに mixdown だけ非対称。サブコマンド追加 or 意図的なら明記。

- **L4 — `estimate_rhythm` の握りつぶし。** `_rhythm_via_beat_this` の失敗を
  `except Exception` で無言に librosa フォールバックへ。beat_this の実エラーが
  見えなくなる。`progress.log(...)` で stderr に理由を残すと診断しやすい。
  （GPU op フォールバックの `except` は意図的で妥当。）

- **L5 — `assetProtocol.scope` が広い。** `tauri.conf.json` のスコープに `$HOME/**` が
  含まれるが、ジョブ成果物は `$APPDATA/jobs/...` 配下。`$HOME/**` は不要に広く、
  webview からホーム全体が読める。`$APPDATA`/`$APPLOCALDATA`/`$TEMP` に絞るのが安全。

- **L6 — `csp: null`（CSP 無効）。** ローカルアプリでは一般的だが、OSMD やバンドル
  資産に合わせた制限的 CSP を設定するとハードニングになる。

- **L7 — Home ドロップゾーンのキーボード操作不可。** `role="button" tabIndex={0}` だが
  `onKeyDown`（Enter/Space）ハンドラがなく、キーボードのみのユーザがファイル選択を
  開けない。アクセシビリティの小改善。

---

## タスクリスト

ステータス: `[ ]` 未着手 · `[x]` 完了

### High
- [x] **H1** `pipeline.py` のテスト追加（`tests/test_pipeline.py`、16 件）:
  `ScoreOptions.from_request/from_args`、`_resolve_grid`（fixed/variable・downbeat
  リセット）、`_scoreable`、`_resolve_key`（override 優先）、`_build_part_score`
  （bass_treble +24・octave スタック・guitar 8vb クレフ）。pytest 47→63。
- [x] **H2** `python.rs` を読み取りスレッド + `recv_timeout` 方式に変更。各行が
  ハートビートをリセットし、`PAPER_ECHO_PIPELINE_TIMEOUT_SECS`（既定 600s）無出力で
  ハングと判断 → プロセス kill して mutex を解放、次リクエストで再生成。環境変数は
  CLAUDE.md にも追記。

### Medium
- [x] **M1** `transcribe_mono` + `_median_smooth` + `_close` を削除（約 95 行）。
  docstring の「fallback」記述も削除。
- [x] **M2** `tempfile.mktemp()` を `mkstemp()`+`os.close(fd)` に置換
  （`transcribe.py`, `pipeline.py`）。
- [x] **M3** Vitest 導入（12 件）。ソロ/ミュート優先ロジックを純粋関数
  `lib/mixer-gain.ts` に抽出し `StemMixer`/`Analyze` から共有（重複も解消）、i18n の
  `translate` を抽出してテスト、`formatTime` もテスト。`npm test` 追加。
- [x] **M4** `package.json` に `typecheck`/`lint`/`test` を追加。ESLint 9 flat config
  （typescript-eslint + react-hooks）を整備し `npm run lint` グリーン。
  `.github/workflows/ci.yml` で web(typecheck/lint/test)・python(ruff+pytest)・rust
  (cargo test) を実行。ruff（E/F/I/UP/B）も `pyproject.toml` に追加し `ruff check`
  グリーン（`l`→`lower`、`zip(..., strict=False)`、`from_request`/`from_args` の型
  注釈の引用符除去など軽微な是正）。

### Low
- [x] **L1** `PART_META` を `{ color }` のみに縮小し、古いコメントを修正。
- [x] **L2** `formatTime` を `src/lib/format.ts` に抽出し、Analyze/Export から共有。
- [x] **L3** `mixdown` を CLI サブコマンド（`--gains` JSON / `--dest`）として追加。
- [x] **L4** `estimate_rhythm` のフォールバック時に `progress.log` で理由を stderr 出力。
- [x] **L5** `assetProtocol.scope` から `$HOME/**` を除外。
- [ ] **L6** 制限的な CSP を設定。**保留** — CSP を誤ると dev(Vite HMR)/本番ビルドの
  両方が壊れるが、本環境では GUI スモークテストができない。適用前に下記の候補を
  実機で検証すること:
  `default-src 'self'; connect-src 'self' ipc: http://ipc.localhost asset: http://asset.localhost; img-src 'self' asset: http://asset.localhost data:; media-src 'self' asset: http://asset.localhost; style-src 'self' 'unsafe-inline'; font-src 'self' data:`
  （webview は外部 URL を読み込まない＝XSS 面は MusicXML→OSMD のみで限定的。優先度低。）
- [x] **L7** Home ドロップゾーンに `onKeyDown`（Enter/Space）を追加。

## 残課題（任意・優先度低）

- **L6（CSP）** — 上記のとおり実機スモークテスト後に適用（唯一の未対応項目）。

## スコープ外（記録のみ）

- arm64 限定・未署名配布は `DISTRIBUTION.md` / メモリで決定済み（意図的）。
- 採譜の精度向上（モデル差し替え等）は MVP スコープ外。`htdemucs_6s` は唯一の 6 ステム
  モデルで差し替え不可（CLAUDE.md 記載）。
- beat_this を CPU 固定にしている点は意図的（GPU は分離/CREPE/ピアノに割り当て）。
