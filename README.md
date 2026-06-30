# Paper Echo

音声を編集可能な楽譜に変換します。Paper Echo は音声ファイルを各パート（ベース・
ボーカル・ギター・ピアノ・ドラム・その他）に分離し、編集可能な楽譜（MusicXML /
MIDI）の下書きを作成します。仕上げは MuseScore / Dorico / Sibelius で行えます。
これは最終的な楽譜ではなく **下書き** を作るツールで、耳コピの時間を約 80% 短縮
することを目的としています。

## ダウンロード（macOS / Apple Silicon）

最新版は [Releases](https://github.com/machida/PaperEcho/releases/latest) から
**`PaperEcho-<ver>-arm64.dmg`** をダウンロードできます。Apple Silicon（arm64）
専用で、Intel Mac には非対応です。

1. DMG を開き、**Paper Echo** を Applications にドラッグします。
2. 初回起動時、解析エンジン（約 430 MB）を一度だけ自動ダウンロードします。以降は
   オフラインで動作します。

> **未署名アプリの起動について。** このアプリは Apple の公証（notarization）を
> 受けていないため、初回起動時に Gatekeeper がブロックします。ターミナルで検疫
> 属性を外すのが確実です:
>
> ```sh
> xattr -dr com.apple.quarantine "/Applications/Paper Echo.app"
> ```
>
> または、起動して「開けません」と出たら **システム設定 → プライバシーとセキュリティ**
> の「このまま開く」をクリックしてください。

## アーキテクチャ

3 層構成です。

- **`src/`** — React + TypeScript + Vite のフロントエンド（Home → Analyze →
  Export）。
- **`src-tauri/`** — Tauri 2 / Rust のバックエンド。Python パイプラインをサブ
  プロセスとして起動し、その JSON 進捗を UI にストリーミングします。
- **`python/paperecho/`** — ML パイプライン（uv 管理の venv）:
  デコード (ffmpeg) → 分離 (Demucs `htdemucs_6s`、6 ステム) → リズム → 採譜
  → 楽譜化 (music21) → 書き出し。さらにアプリ内再生用の圧縮音声プレビューを生成
  します。
  - **採譜はパートごと:** **ベースとボーカル** はオンセット駆動のトラッカー
    （アタックが音符の境界を決め、ピッチは各区間ごとに CREPE ニューラル f0 モデルで
    補完——同じ音高の連続音も保持される）；**ピアノ** は ByteDance の高解像度ピアノ
    モデル（ポリフォニック、きれいな和音）；**ギター** は Spotify Basic Pitch を
    使用します。楽譜化するパートは最初に選べます（選ばなくても全パートは分離・再生
    可能）。
  - **リズム** は `beat_this`（Transformer によるビート＋ダウンビート、librosa
    フォールバックあり）を使い、局所的なオクターブジャンプ補正により、音数の多い
    区間でテンポが倍にならないようにしています。

Rust↔Python の契約: 常駐する `python -m paperecho.pipeline serve` プロセスが、
stdin の 1 行ごとに 1 つの JSON リクエストを受け取り、stdout に 1 行ごとに 1 つの
JSON オブジェクト（`progress` / `done` / `error`）を出力します。同じ `analyze` /
`export` / `preview` サブコマンドは CLI 用に単独でも実行できます。成果物は
`app_data_dir/jobs/<id>/` に書き出されます。

## 操作の流れ

1. **Home** — 音声ファイルをドロップします。楽譜化する音程付きパート（ベース /
   ボーカル / ギター / ピアノ）を選びます。不要なパート（例：最も遅いピアノ）の
   チェックを外すと解析が速くなります。チェックを外しても全パートは分離・試聴
   できます。
2. **Analyze** — 進捗を確認し、同期ミキサーで分離されたステムを試聴します
   （パートごとのミュート / ソロ / 音量、クリックトラック付き）。ミックスダウンの
   書き出しも可能です。
3. **Export** — パートとフォーマット（MusicXML / MIDI / PDF）を選びます。ライブ
   楽譜プレビューと、手動の読み取り補助があります: **テンポグリッド**（固定の
   メトロノーム / 可変のライブテンポ）、**テンポ** ½×/1×/2×、**拍のずらし**
   （±拍）、**調** の上書き、**オクターブ** シフト。すべてキャッシュ済みジョブに
   対して書き出し時に適用されます（再解析不要）。

## 必要なもの

- Node 20+、Rust（stable）、Python 3.11、[uv](https://docs.astral.sh/uv/)、ffmpeg。

## セットアップ

```sh
# フロントエンドの依存（postinstall で静的 ffmpeg を
# src-tauri/resources-arm64/bin に自動ダウンロードします）
npm install

# Python パイプラインの依存（python/.venv を作成）
cd python && uv sync && cd ..
```

> 同梱する静的 `ffmpeg` はリポジトリにコミットしていません。`npm install` 時に
> `ffmpeg-static` パッケージ経由で取得し、`scripts/stage-ffmpeg.mjs` が配置します。
> 必要に応じて `npm run stage:ffmpeg` で再実行できます。

### テスト / チェック

CI（`.github/workflows/ci.yml`）と同じものをローカルで実行できます。

```sh
npm run typecheck                       # TypeScript 型チェック
npm run lint                            # ESLint
npm test                                # Vitest（フロントの単体テスト）
cd python && ./.venv/bin/python -m pytest tests/ -q && ./.venv/bin/ruff check .
cd src-tauri && cargo test --lib
```

## 実行

```sh
npm run tauri dev
```

ソースから実行する場合、初回解析時に AI モデルをダウンロードします——Demucs の
分離モデル（数百 MB）、CREPE ピッチモデル、ByteDance ピアノモデル（約 170 MB）、
`beat_this`——いずれも以降はキャッシュされます。（パッケージ済みの `.dmg`
ビルドはダウンロードされるランタイムに **これらのモデルを同梱** するため、初回解析
も完全オフラインです——「配布」を参照。）

### 便利な環境変数

- `PAPER_ECHO_PYTHON_DIR` — `python/` プロジェクトの場所を上書きします。
- `PAPER_ECHO_DEVICE` — 分離・CREPE ピッチ検出・ByteDance ピアノモデルで使うデバイス。
  **既定では GPU（Apple `mps` / CUDA）を自動検出** し、なければ CPU にフォール
  バックします。`cpu`/`mps`/`cuda` で上書き可能。Apple Silicon では GPU がピアノ
  モデル（最も遅い工程）を CPU の約 1.75 倍速で処理します。
- `PAPER_ECHO_SHIFTS` — Demucs のテスト時オーグメンテーション回数（既定 `2`）。
  大きいほど分離がきれい（ギター/ピアノのアタックのにじみが減る）ですが、約 (1+N)
  倍遅くなります。最速にするには `0`。
- `PAPER_ECHO_PIPELINE_TIMEOUT_SECS` — バックエンドが Python パイプラインの無応答を
  ハングと見なして再起動するまでの待ち時間（既定 `600`）。分離処理は無出力で数分
  かかるため、この値はそれより長く設定します。
- `PAPER_ECHO_RUNTIME_URL` / `PAPER_ECHO_RUNTIME_SHA256` — 初回起動時のランタイム
  ダウンロード URL / 期待されるチェックサムを上書きします（パッケージ済みビルド
  のみ。下記参照）。

## 配布

パッケージ済み（`.dmg`）ビルドは約 1 GB の Python パイプラインを同梱しません。
代わりに、スリム化された自己完結ランタイムを **初回起動時に一度だけ**（進捗付きの
「初回セットアップ」画面で）GitHub Releases から `<app_data>/runtime-<version>/`
にダウンロードします。これにより DMG は数十 MB に収まります。ランタイムは AI
モデルの重みも同梱するため、**初回解析も完全オフライン** で動作します（追加の
ダウンロードなし）。ビルドは既定で未署名（未署名のため初回は検疫属性の削除か
「このまま開く」が必要——上記「ダウンロード」参照）；Developer ID 署名 + 公証も
環境変数で有効化できます。ビルド/リリース手順・スリム化・公証は
[`DISTRIBUTION.md`](DISTRIBUTION.md) を参照。macOS Apple Silicon（arm64）のみ対応。

## パイプラインを直接テストする

```sh
cd python
# --transcribe-parts で楽譜化する音程付きパートを限定（既定: 全部）
./.venv/bin/python -m paperecho.pipeline analyze --input song.mp3 --job-dir /tmp/job \
    --transcribe-parts bass,vocals
# 書き出しの読み取り補助: --tempo-mode fixed|variable, --tempo-mult, --beat-offset,
# --key-sharps, --octave-shift
./.venv/bin/python -m paperecho.pipeline export  --job-dir /tmp/job \
    --parts bass,vocals --formats musicxml,midi --tempo-mode fixed
```

## ステータス / スコープ

MVP: 音程付きパート（ベース/ボーカル/ギター/ピアノ）が楽譜化され、ドラムは音声
のみです。PDF 書き出しには PATH 上の MuseScore CLI が必要です。スコープ外: クラウド
同期、アカウント、DAW 機能。
