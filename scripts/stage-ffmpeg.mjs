// Stage a static ffmpeg binary into src-tauri/resources-<arch>/bin/ffmpeg.
//
// The binary is NOT committed (it is large and third-party). It is downloaded
// by the `ffmpeg-static` devDependency during `npm install`; this script copies
// it into the Tauri resources tree so the app bundle can pick it up
// (tauri.conf.json bundles `resources-arm64/bin`). Runs automatically via the
// `postinstall` npm script, or on demand with `npm run stage:ffmpeg`.
//
// It exits 0 even when it can't stage (e.g. ffmpeg-static not installed yet, or
// an unsupported platform) so it never breaks `npm install`.
import { copyFileSync, mkdirSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ARCH = process.env.PAPER_ECHO_ARCH || "arm64";
const dest = join(ROOT, "src-tauri", `resources-${ARCH}`, "bin", "ffmpeg");

try {
  const { default: ffmpegPath } = await import("ffmpeg-static");
  if (!ffmpegPath) throw new Error("ffmpeg-static did not resolve a binary path");
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(ffmpegPath, dest);
  chmodSync(dest, 0o755);
  console.log(`staged ffmpeg -> ${dest}`);
} catch (err) {
  console.warn(`stage-ffmpeg: skipped (${err.message}).`);
  console.warn("Run `npm run stage:ffmpeg` after `npm install` to retry.");
}
