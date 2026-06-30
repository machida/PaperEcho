# Paper Echo の配布（macOS）

決定事項:

| 項目 | 選択 |
|------|------|
| 対象 | **macOS Apple Silicon（arm64）のみ** — Intel は非対応（ML スタックに x86_64 macOS wheel が無い。「ユニバーサル」参照） |
| 署名 | **既定では未署名**（自分用 / 内部用——受け取った人は初回に右クリック →「開く」）。Developer ID 署名 + 公証も配線済みで環境変数で有効化（「署名と公証」参照）。 |
| Python バックエンド | **スリム化したランタイム + オフラインモデルを同梱し、初回起動時に GitHub Releases からダウンロード**（`.app` には同梱しない） |

**なぜ同梱せずダウンロードなのか。** Python の ML パイプライン（torch, scipy,
llvmlite, …）は約 1 GB あります。同梱すると DMG は 384 MB、`.app` は 1.2 GB に
なりました。現在は小さなアプリシェルだけを配り、初回起動時に一度だけランタイムを
取得します:

- DMG は **数十 MB** に縮小（Tauri シェル + ffmpeg + チェックサムのみ）。
- アプリの更新（シェル/バグ修正）は小さく、重いランタイムはそのバージョンが
  変わったときだけ再取得されます。
- 新規ユーザーが転送する総バイト数はほぼ同じですが、小さなインストール + 進捗画面
  付きの一度きりのバックグラウンドダウンロードになります。

ランタイムはパック前に **スリム化** されます（読み込まれないペイロード約 170 MB を
削除）——下記「スリム化」参照。

---

## 初回ダウンロードの仕組み

1. `scripts/build-python-dist.sh arm64` が自己完結 CPython + ロック済み依存を
   ステージし（「ランタイム」参照）、**スリム化** したうえで
   `dist-runtime/paperecho-runtime-<ver>-arm64.tar.zst` としてパックし、その
   sha256 をサイドカーと同梱リソース `src-tauri/resources-arm64/runtime.sha256` の
   両方に書き出します。**`runtime.sha256` は git 追跡対象**です（65 バイト。アプリが
   検証に使う値なので、クローンや CI でも正しいビルドができるよう repo に含めます。
   `resources-arm64/` の重い成果物 `bin/ffmpeg`・`runtime/`・`model-cache/` は除外）。
   ターボールを再パックしたら、更新された `runtime.sha256` をコミットしてください。
2. そのターボールを `v<ver>` タグの **GitHub Release** にアップロードします。
3. `.app` には `resources-arm64/bin/ffmpeg` + `resources-arm64/runtime.sha256`
   だけを同梱します。
4. 起動時（`src-tauri/src/runtime.rs`）:
   - `runtime_status` が使用可能なインタプリタを解決できるか報告します（dev の
     `.venv`、環境変数による上書き、またはインストール済みのダウンロード）。
   - 無ければフロントエンド（`src/routes/RuntimeSetup.tsx`）が「初回セットアップ」
     画面を表示し、`download_runtime` を呼びます。これはターボールをストリーミング
     し、**同梱チェックサムに対して sha256 を検証** し、
     `<app_data>/runtime-<app_version>/python/` に展開して、パイプラインをそこに
     向けます。
   - インストール先はアプリのバージョンで分かれるため、新しいアプリバージョンは
     専用のランタイムを取得し、古いものは単に再取得されます。

**URL は設定可能**（`runtime.rs`）: 既定は
`<RELEASE_BASE>/v<ver>/paperecho-runtime-<ver>-<arch>.tar.zst`。`RELEASE_BASE` は
本物のリポジトリ（`https://github.com/machida/PaperEcho/releases/download`）に
設定済みです。URL 全体は `PAPER_ECHO_RUNTIME_URL`、期待ハッシュは
`PAPER_ECHO_RUNTIME_SHA256` で上書きできます——本物の Release が無い段階での
テストや、別の場所でターボールをホストするのに便利です。

---

## ランタイム（`build-python-dist.sh`）

通常の `python/.venv` は **同梱できません**: その `bin/python` は dev ツール
チェーンへのシンボリックリンクだからです。そこで **uv 管理の *standalone* CPython**
（python-build-standalone、完全に再配置可能）をステージツリーにコピーし、ロック済み
依存をそのコピーにインストールします——外部を指すものは何もありません。uv は
standalone python を "externally managed" とマークするため、
`uv pip install --break-system-packages` の前に
**`runtime/lib/python3.11/EXTERNALLY-MANAGED` を削除** します。
`python.rs::venv_python` は dev の `<dir>/.venv` より
`<dir>/runtime/bin/python3.11` を優先します。

**ステージしたランタイムには必ず、表示される検証ステップを実行してください**
（インタプリタを `env -i` の下で実行するため、PATH の漏れが壊れたバンドルを
隠せません）。

### スリム化（約 170 MB 削除、各々 import パスに対して検証済み）

`build-python-dist.sh` はパイプラインが読み込まないペイロードを刈り取ります:

- **`music21/corpus` の楽譜データ**（約 66 MB）— 同梱コーパスを解析しません。
  `corpus` *パッケージ*（その `*.py`）は music21 初期化時に import されるため、
  コードは残し、作曲家の楽譜データのサブディレクトリ + `_metadataCache` だけを
  削除します。
- **`torchcrepe/assets/full.pth`**（約 85 MB）— `transcribe.py` は CREPE
  `model="tiny"` を固定しており、フルチェックポイントは読み込まれません。
- **`coremltools`**（約 19 MB）— basic_pitch は import 可能なものでバックエンドを
  選びます（tf > coreml > tflite > **onnx**）。coremltools を削除すると
  `ICASSP_2022_MODEL_PATH` が同梱の `nmp.onnx` に解決されます（onnxruntime あり）。
  basic_pitch の `__init__` は import をガードしているので、import 自体は通ります。

**残すもの（要・削除禁止）:** `matplotlib`
（`piano_transcription_inference/models.py` がモジュール冒頭で `pyplot` を即時
import）、`sympy`（torch）、`numba`/`llvmlite`（librosa）、
`scipy`/`scikit-learn`（librosa）。`torch`（406 MB）はサイズの主因ですが mps に
必須——手を付けられません。

### ffmpeg（小さいので引き続き同梱）

Homebrew の ffmpeg は動的リンク（62 個の dylib）です。**静的** ビルドを
`src-tauri/resources-arm64/bin/ffmpeg` に同梱します。
`lib.rs::wire_bundled_resources` が `PAPER_ECHO_FFMPEG` をそれに設定し、
`audio.ffmpeg_bin()` が PATH より優先します。

バイナリは **コミットしていません**（大きく、第三者製のため）。`npm install` 時に
`ffmpeg-static` devDependency 経由でダウンロードされ、`scripts/stage-ffmpeg.mjs` が
所定の場所にコピーします（npm の `postinstall` に配線。必要なら
`npm run stage:ffmpeg` で再実行）。アーキは `PAPER_ECHO_ARCH` で上書きできます。

---

## リリース手順（ビルドを切る）

```sh
# 1. ランタイムをステージ + スリム化 + パックし、同梱チェックサムを書き出す
./scripts/build-python-dist.sh arm64
#    -> dist-runtime/paperecho-runtime-<ver>-arm64.tar.zst (+ .sha256)
#    -> src-tauri/resources-arm64/runtime.sha256   （同梱、検証元）

# 2. アプリシェルをビルド + アドホック署名（未署名配布でも、同梱 ffmpeg を
#    読み込むには署名が必要）
npm run tauri build -- --target aarch64-apple-darwin
codesign --force --deep -s - \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Paper Echo.app"

# 3. 公開: v<ver> タグの GitHub Release を作り、ターボールを
#    paperecho-runtime-<ver>-arm64.tar.zst（runtime.rs が期待する名前）として
#    アップロード。その後 DMG を配布する。
```

パックには `zstd`（`brew install zstd`）が必要です。

**アプリだけ更新する場合（ランタイム不変）:** フロント/Rust の修正だけでランタイム
依存が変わっていないなら、ステップ 1 は不要です。`runtime.sha256` は同じ値のまま
なので、既存の Release ターボールとの整合は保たれます。アプリ（DMG）を再ビルドして
`gh release upload v<ver> "…/Paper Echo_<ver>_aarch64.dmg" --clobber` で差し替える
だけで配布更新できます。チェックサムは `runtime.sha256` ＝ Release のターボールで
一致している必要があります。

**受け取る人の初回起動の流れ:** DMG 内のアプリを右クリック →「開く」（未署名 →
Gatekeeper が一度警告）。するとアプリがランタイム（約 430 MB——スリム化された
パイプライン + 同梱オフラインモデル、進捗表示あり）をダウンロードし、準備完了です。
以降の起動はダウンロードを省略し、**初回解析も完全オフライン** で動きます
（モデルのダウンロードなし）。公証済みビルドなら右クリック →「開く」は不要です。

---

## 署名と公証（任意、環境変数駆動）

既定のビルドは **未署名**（アドホック）で、Apple アカウントは不要です。
**Developer ID + 公証** ビルドにすると右クリック →「開く」が不要になります。
*未署名パスは変わらない* ように配線されており、署名は環境変数があるときだけ
有効になります:

- `src-tauri/entitlements.plist` — **アプリプロセス** 用の最小限の Hardened
  Runtime エンタイトルメント（WebKit のための `allow-jit` のみ）。
  `tauri.conf.json`（`bundle.macOS.entitlements`）から参照されます。ダウンロード
  された Python ランタイムはアプリ署名の対象外の **別の子プロセス** なので、
  エンタイトルメントは不要です。arm64 では既にアドホック署名済み
  （python-build-standalone）で実行に十分であり、子プロセスとして自由に JIT
  （torch）できます。
- `src-tauri/src/runtime.rs::strip_quarantine` がインストール後に展開済み
  ランタイムから `com.apple.quarantine` を消すため、公証済み / Gatekeeper 配下の
  アプリでも初回実行ブロックなしで mach-o ファイルが動きます。（アプリが書き込んだ
  ファイルは通常検疫されません——これは念のための措置で、未署名でも無害です。）

**公証済みビルドを切るには**（Apple Developer Program のメンバーシップ + ログイン
キーチェーンの *Developer ID Application* 証明書が必要）:

```sh
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
# 公証クレデンシャル — アプリ固有パスワードを使う場合:
export APPLE_ID="you@example.com"
export APPLE_PASSWORD="abcd-efgh-ijkl-mnop"   # アプリ固有パスワード
export APPLE_TEAM_ID="TEAMID"
# …または App Store Connect API キーを使う場合:
#   APPLE_API_KEY / APPLE_API_ISSUER / APPLE_API_KEY_PATH
npm run tauri build -- --target aarch64-apple-darwin
```

`APPLE_SIGNING_IDENTITY` が設定されていると、`tauri build` は Hardened Runtime +
エンタイトルメントで署名し、（公証クレデンシャルがあれば）Apple に提出して
チケットを **ステープル** します。**注意——あとから再署名しないこと:** 上記の
未署名ワークフローは手動の `codesign --force --deep -s -` + `hdiutil` DMG を
行いますが、公証済みビルドではその手動再署名を **絶対に実行しない** でください
（Developer ID 署名 + ステープルが剥がれます）。`tauri build` に署名済みの
`.app`/`.dmg` を直接作らせるか、*署名・ステープル済み* のアプリから DMG を再生成
してください。検証:
`codesign -dv --verbose=4 "Paper Echo.app"`、`spctl -a -vvv "Paper Echo.app"`、
`xcrun stapler validate "Paper Echo.app"`。

*ここではまだエンドツーエンドで実行していません*（ビルドマシンに証明書なし）。
設定・エンタイトルメント・検疫処理は整っており、証明書が手に入れば上記コマンドが
残りの手作業です。

---

## ユニバーサル（Intel）— ❌ 実現不可（arm64 のみと決定）

ML スタックは Intel macOS wheel を廃止しました。uv で検証済み
（`--python-platform x86_64-apple-darwin`）: `torch==2.12.0` と
`onnxruntime==1.27.0` は **`macosx_*_arm64` のみ** を配布。Intel ビルドには torch
（≤2.2）+ onnxruntime + その連鎖のダウングレードが必要で、EOL プラットフォームの
ために挙動が変わる退行になります。`runtime.rs`/`lib.rs` はアーキ選択と
`$UV` パラメータ化スクリプトを残していますが（無害で、x86_64 macOS wheel が復活
すれば対応可能）、配布は arm64 のみです。

---

## モデル（ランタイムターボールに同梱——完全オフライン）

AI モデルの重みは **ランタイムターボール内** の `python/model-cache/` に同梱
されるため、**初回解析でもダウンロードしません**——ネットワークアクセスは一度きりの
ランタイムダウンロードだけです。`build-python-dist.sh` がステージします（約 294 MB）:

| モデル | ファイル | 解決経路 |
|--------|----------|----------|
| Demucs `htdemucs_6s` | `model-cache/torch/hub/checkpoints/5c90dfd2-34c22ccb.th`（約 55 MB） | `TORCH_HOME` → `torch.hub` |
| beat_this `final0` | `model-cache/torch/hub/checkpoints/beat_this-final0.ckpt`（約 81 MB） | `TORCH_HOME` → `torch.hub` |
| ByteDance piano | `model-cache/piano/note_F1=0.9677_pedal_F1=0.9186.pth`（約 172 MB） | `PAPER_ECHO_MODEL_CACHE` → 明示的 `checkpoint_path` |

CREPE-tiny（ベース/ボーカル）は既に `torchcrepe` 内に同梱、ギターは同梱の
`nmp.onnx` を使用——どちらもステージ不要です。

**配線**（`src-tauri/src/python.rs::model_cache_envs`）: 起動するインタプリタの
ディレクトリに `model-cache/` があるとき、Rust は
`TORCH_HOME=<dir>/model-cache/torch`（Demucs + beat_this はどちらも
`torch.hub.load_state_dict_from_url` 経由で取得し、これは `$TORCH_HOME/hub` を
読む）と `PAPER_ECHO_MODEL_CACHE=<dir>/model-cache`（`transcribe.py::transcribe_piano`
が読む。ByteDance ライブラリはそうしないと `~/piano_transcription_inference_data` を
ハードコードして `wget` する）を設定します。ユーザーホームへの書き込みはありません。
dev（`.venv`、`model-cache/` なし）では環境変数は未設定で、ライブラリは通常の
`~/.cache` ダウンロードにフォールバックします——dev は影響を受けません。

**ステージング**（`build-python-dist.sh` ステップ 5.5）: ビルドマシンの
`~/.cache/torch/hub/checkpoints` + `~/piano_transcription_inference_data` があれば
再利用します（高速）。無ければステージしたインタプリタが `torch.hub` 経由で
Demucs + beat_this を取得し、ピアノチェックポイントは Zenodo から `curl` します。

**オフライン検証**（ホームへの書き込みなし、モデルのネットワークなし）:
```sh
env -i HOME=/tmp/emptyhome PATH=/opt/homebrew/bin:/usr/bin:/bin \
  PAPER_ECHO_FFMPEG=src-tauri/resources-arm64/bin/ffmpeg \
  TORCH_HOME=<…>/python/model-cache/torch \
  PAPER_ECHO_MODEL_CACHE=<…>/python/model-cache \
  PAPER_ECHO_SHIFTS=0 PAPER_ECHO_DEVICE=cpu \
  python/.venv/bin/python -m paperecho.pipeline analyze --input clip.wav --job-dir /tmp/j
# その後 /tmp/emptyhome/.cache/torch と /tmp/emptyhome/piano_transcription_inference_data が存在しないことを確認
```

## メモ / 落とし穴

- `targets` は `[app, dmg]`（macOS）。`all` は使わない（deb/rpm を試みる）。
- **未署名** アプリ: 受け取る人は右クリック →「開く」（または `xattr -dr
  com.apple.quarantine "Paper Echo.app"`）。
- **公証とダウンロードされるランタイム:** ランタイムはインストール *後* に
  ダウンロードされるため、その mach-o ファイル（インタプリタ、torch dylib）は
  **アプリ署名の対象外** です。それでも動くのは、(a) アプリに読み込まれない
  *別の子プロセス* であり、arm64 では python-build-standalone により既にアドホック
  署名されているため、(b) `runtime.rs::strip_quarantine` が展開時に検疫ビットを
  消すため Gatekeeper が初回実行をブロックしないため、です。上記「署名と公証」
  参照。（Developer ID 署名で配る必要はありません。）
- `assetProtocol` を有効化するには Cargo.toml に
  `tauri = { features = ["protocol-asset"] }` が必要。無いとビルドスクリプトが
  失敗します。
- **DMG の前に署名するか、署名後に DMG を作り直す。** `tauri build` は
  *codesign 前* のアプリから `.dmg` をバンドルするため、あとからスタンドアロンの
  `.app` を再署名すると DMG 内に古いアプリが残ります。対処: `codesign --force
  --deep -s - "Paper Echo.app"` の後、署名済みアプリから DMG を再生成する——
  `hdiutil create -volname "Paper Echo" -srcfolder "…/Paper Echo.app" -ov -format
  UDZO "Paper Echo_<ver>_aarch64.dmg"`。（tauri の `bundle_dmg.sh` の Finder
  レイアウトは FinderSync 拡張が多いマシンで非常に遅いことがあります。素の
  `hdiutil` 形式は速く、内部ビルドには十分です——ドラッグ→Applications の
  レイアウトが付かないだけです。）
- MuseScore（PDF 書き出し）は **同梱しません**——PDF は任意のまま。MusicXML/MIDI は
  無くても動きます。
