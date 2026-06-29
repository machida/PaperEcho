import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type Lang = "ja" | "en";

type Dict = Record<string, string>;
type Params = Record<string, string | number>;

// UI strings keyed by a dotted name. Japanese is the primary locale; English is
// the fallback when a key is missing from `ja`.
const ja: Dict = {
  "app.tagline": "音声を編集可能な楽譜に",
  "app.home": "ホーム",
  "app.starting": "起動中…",
  "lang.switch": "English",

  // Parts
  "part.bass": "ベース",
  "part.bass_treble": "ベース（ト音 15ma）",
  "part.vocals": "ボーカル",
  "part.guitar": "ギター",
  "part.piano": "ピアノ",
  "part.drums": "ドラム",
  "part.other": "その他",
  "part.click": "クリック",

  // Home
  "home.unsupported": "対応していないファイルです。mp3 / wav / m4a / aiff を使用してください。",
  "home.drop": "音声ファイルをドロップ",
  "home.browse": "またはクリックして選択",
  "home.notate": "楽譜化:",
  "home.hint":
    "Paper Echo は各パートを分離し、編集可能な楽譜の下書きを作成します。仕上げは MuseScore / Dorico / Sibelius で行ってください。楽譜化が不要なパート（例：ピアノ）のチェックを外すと解析が速くなります。すべてのパートは分離され再生できます。",

  // Analyze — stages
  "stage.decode": "音声をデコード中",
  "stage.separate": "パートを分離中",
  "stage.rhythm": "テンポを推定中",
  "stage.transcribe": "音符を採譜中",
  "stage.preview": "プレビューをエンコード中",
  // Analyze
  "analyze.failed": "解析に失敗しました: {error}",
  "analyze.starting": "開始中…",
  "analyze.analyzing": "解析中",
  "analyze.firstRunHint":
    "初回は AI モデル（分離・ピッチ・ピアノ・ビート）をダウンロードします。次回以降はキャッシュを使うため高速です。",
  "analyze.detectedParts": "検出されたパート",
  "analyze.meta": "{n} パート · 約 {bpm} BPM · {ts}",
  "analyze.export": "書き出し →",
  "analyze.startOver": "← 最初からやり直す",
  // Mixer
  "mixer.loadingStems": "ステム音声を読み込み中…",
  "mixer.solo": "ソロ",
  "mixer.mute": "ミュート",
  "mixer.audioOnly": "音声のみ",
  "mixer.exportMix": "ミックスを書き出し (mp3)",
  "mixer.exporting": "書き出し中…",
  "mixer.mixFailed": "ミックスに失敗しました: {error}",
  "mixer.audioLoaded": "音声: {loaded}/{total} ステム読み込み済み",
  "mixer.audioFailed": " — 失敗: {list}",
  "mixer.pressPlay": " · ▶ を押すと再生状態を表示",
  "mixer.playing": "再生中 {n} ステム, コンテキスト {state} @ {rate}Hz",
  "mixer.audioError": "音声エラー: {error}",

  // Export
  "export.title": "書き出し",
  "export.parts": "パート",
  "export.formats": "フォーマット",
  "export.notationOnly": "楽譜のみ",
  "export.audioOnly": "音声のみ",
  "format.needsMuseScore": "MuseScore が必要",
  "export.tempoGrid": "テンポグリッド",
  "seg.fixed": "固定",
  "seg.variable": "可変",
  "export.gridFixed": "一定のメトロノームグリッド",
  "export.gridVariable": "ライブのテンポに追従",
  "export.gridHint":
    "「固定」は各小節を一定のテンポにスナップします。スタジオ録音向きで、ビート検出のゆらぎに強いです。テンポが実際に揺れるライブ／ルバート録音には「可変」を使ってください。",
  "export.tempo": "テンポ",
  "export.detected": "（検出値 {n}）",
  "export.tempoHint":
    "テンポのオクターブ（例：80 と 160）は曖昧です。音価が倍／半分に見える場合は ½×／2× を切り替えてください。",
  "export.beatNudge": "拍のずらし",
  "export.beats": "拍",
  "export.beatHint":
    "全体が 1 拍ずれている場合（音符が裏拍に乗る／小節頭が休符になる等）は、グリッドを ±¼ または ±½ 拍ずらして合わせてください。",
  "export.key": "調",
  "export.keyAuto": "自動（検出）",
  "export.keyEstimated": "音符から推定",
  "export.keyFixed": "固定",
  "export.keyHint":
    "自動の調検出はよく外れます。調号が違う場合はここで正しい調を選んでください。",
  "export.octave": "オクターブ",
  "export.octaves": "オクターブ",
  "export.octaveHint":
    "書かれる音符をオクターブ単位で上下させ、読みやすい音域にします（例：譜表の下に来る低いボーカル）。書き出すパートに適用されます。複数パートをまとめて書き出すときは 0 にしてください。",
  "export.previewSingle": "プレビュー · {label}",
  "export.chooseFolder": "フォルダを選択して書き出し…",
  "export.exporting": "書き出し中…",
  "export.failed": "書き出しに失敗しました: {error}",
  "export.savedCount": "{n} 個のファイルを保存しました",
  "export.openFolder": "フォルダを開く",
  "export.skippedCount": "{n} 件スキップ",

  // Common
  "common.back": "← 戻る",
  "common.reveal": "表示",
  "common.done": "完了",
  "score.rendering": "描画中…",

  // RuntimeSetup
  "runtime.download": "ダウンロード中",
  "runtime.verify": "検証中",
  "runtime.extract": "展開中",
  "runtime.done": "完了",
  "runtime.title": "初回セットアップ",
  "runtime.desc":
    "音楽解析エンジン（約 350 MB）を一度だけダウンロードします。次回からはこの画面は出ません。",
  "runtime.failed": "セットアップに失敗しました: {error}",
  "runtime.retry": "再試行",
  "runtime.preparing": "準備中",
};

const en: Dict = {
  "app.tagline": "Turn audio into editable sheet music",
  "app.home": "Home",
  "app.starting": "Starting…",
  "lang.switch": "日本語",

  "part.bass": "Bass",
  "part.bass_treble": "Bass (treble 15ma)",
  "part.vocals": "Vocal",
  "part.guitar": "Guitar",
  "part.piano": "Piano",
  "part.drums": "Drums",
  "part.other": "Other",
  "part.click": "Click",

  "home.unsupported": "Unsupported file. Use mp3, wav, m4a, or aiff.",
  "home.drop": "Drop an audio file",
  "home.browse": "or click to browse",
  "home.notate": "Notate:",
  "home.hint":
    "Paper Echo separates the parts and drafts editable notation — finish it in MuseScore, Dorico, or Sibelius. Uncheck parts you don't need to notate (e.g. piano) to analyse faster — every part is still separated and playable.",

  "stage.decode": "Decoding audio",
  "stage.separate": "Separating parts",
  "stage.rhythm": "Estimating tempo",
  "stage.transcribe": "Transcribing notes",
  "stage.preview": "Encoding previews",
  "analyze.failed": "Analysis failed: {error}",
  "analyze.starting": "Starting…",
  "analyze.analyzing": "Analyzing",
  "analyze.firstRunHint":
    "First run downloads the AI models (separation, pitch, piano, beats); later runs use the cache and are faster.",
  "analyze.detectedParts": "Detected parts",
  "analyze.meta": "{n} parts · ~{bpm} BPM · {ts}",
  "analyze.export": "Export →",
  "analyze.startOver": "← Start over",
  "mixer.loadingStems": "Loading stem audio…",
  "mixer.solo": "Solo",
  "mixer.mute": "Mute",
  "mixer.audioOnly": "audio only",
  "mixer.exportMix": "Export mix (mp3)",
  "mixer.exporting": "Exporting…",
  "mixer.mixFailed": "Mix failed: {error}",
  "mixer.audioLoaded": "audio: {loaded}/{total} stems loaded",
  "mixer.audioFailed": " — failed: {list}",
  "mixer.pressPlay": " · press ▶ for playback state",
  "mixer.playing": "playing {n} stems, context {state} @ {rate}Hz",
  "mixer.audioError": "audio error: {error}",

  "export.title": "Export",
  "export.parts": "Parts",
  "export.formats": "Formats",
  "export.notationOnly": "notation only",
  "export.audioOnly": "audio only",
  "format.needsMuseScore": "needs MuseScore",
  "export.tempoGrid": "Tempo grid",
  "seg.fixed": "Fixed",
  "seg.variable": "Variable",
  "export.gridFixed": "steady metronomic grid",
  "export.gridVariable": "follow live tempo",
  "export.gridHint":
    "Fixed snaps every bar to a steady tempo — cleaner for studio takes and immune to beat-tracking wobble. Use Variable for live/rubato recordings where the tempo genuinely drifts.",
  "export.tempo": "Tempo",
  "export.detected": "(detected {n})",
  "export.tempoHint":
    "Tempo octave (e.g. 80 vs 160) is ambiguous — if the note values look doubled or halved, switch ½×/2×.",
  "export.beatNudge": "Beat nudge",
  "export.beats": "beats",
  "export.beatHint":
    "If everything sits a beat off (e.g. notes land on the off-beat / measure heads are rests), nudge the grid by ±¼ or ±½ beat to line it up.",
  "export.key": "Key",
  "export.keyAuto": "Auto (detect)",
  "export.keyEstimated": "estimated from the notes",
  "export.keyFixed": "fixed",
  "export.keyHint":
    "Auto key detection often misses — if the key signature is wrong, pick the real key here.",
  "export.octave": "Octave",
  "export.octaves": "octaves",
  "export.octaveHint":
    "Shifts the written notes up/down by octaves to read them in a comfortable register (e.g. a low vocal that sits under the staff). Applies to the parts you export — set 0 when exporting several parts together.",
  "export.previewSingle": "Preview · {label}",
  "export.chooseFolder": "Choose folder & Export…",
  "export.exporting": "Exporting…",
  "export.failed": "Export failed: {error}",
  "export.savedCount": "{n} file(s) saved",
  "export.openFolder": "Open folder",
  "export.skippedCount": "{n} skipped",

  "common.back": "← Back",
  "common.reveal": "Reveal",
  "common.done": "Done",
  "score.rendering": "Rendering…",

  "runtime.download": "Downloading",
  "runtime.verify": "Verifying",
  "runtime.extract": "Extracting",
  "runtime.done": "Done",
  "runtime.title": "First-time setup",
  "runtime.desc":
    "Download the music-analysis engine (~350 MB) once. You won't see this screen again.",
  "runtime.failed": "Setup failed: {error}",
  "runtime.retry": "Retry",
  "runtime.preparing": "Preparing",
};

const MESSAGES: Record<Lang, Dict> = { ja, en };
const STORAGE_KEY = "paperecho.lang";

function initialLang(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "ja" || saved === "en") return saved;
  return navigator.language.toLowerCase().startsWith("ja") ? "ja" : "en";
}

export type TFunc = (key: string, params?: Params) => string;

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: TFunc;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    localStorage.setItem(STORAGE_KEY, l);
    setLangState(l);
  }, []);

  const t = useCallback<TFunc>(
    (key, params) => {
      let s = MESSAGES[lang][key] ?? MESSAGES.en[key] ?? key;
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          s = s.split(`{${k}}`).join(String(v));
        }
      }
      return s;
    },
    [lang],
  );

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
