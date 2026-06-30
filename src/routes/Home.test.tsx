import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { I18nProvider } from "../lib/i18n";

// Tauri APIs aren't available in jsdom — stub the webview drag/drop listener and
// the file-picker IPC. isSupportedAudio is a pure extension check.
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: () => Promise.resolve(() => {}),
  }),
}));
vi.mock("../lib/ipc", () => ({
  pickAudioFile: vi.fn(),
  isSupportedAudio: (p: string) => /\.(mp3|wav|m4a|aiff|aif)$/i.test(p),
}));

import { Home } from "./Home";
import { pickAudioFile } from "../lib/ipc";

function renderHome(onPick = vi.fn()) {
  localStorage.setItem("paperecho.lang", "ja");
  render(
    <I18nProvider>
      <Home onPick={onPick} />
    </I18nProvider>,
  );
  return onPick;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Home", () => {
  it("renders the dropzone and all four transcribe chips, checked by default", () => {
    renderHome();
    expect(screen.getByRole("heading", { name: "音声ファイルをドロップ" })).toBeInTheDocument();
    const chips = screen.getAllByRole("checkbox");
    expect(chips).toHaveLength(4);
    expect(chips.every((c) => (c as HTMLInputElement).checked)).toBe(true);
  });

  it("passes the picked file and the default parts to onPick", async () => {
    vi.mocked(pickAudioFile).mockResolvedValue("/music/song.mp3");
    const onPick = renderHome();
    await userEvent.click(screen.getByRole("button"));
    expect(onPick).toHaveBeenCalledExactlyOnceWith("/music/song.mp3", [
      "bass",
      "vocals",
      "guitar",
      "piano",
    ]);
  });

  it("omits a deselected part from the parts passed to onPick", async () => {
    vi.mocked(pickAudioFile).mockResolvedValue("/music/song.wav");
    const onPick = renderHome();
    await userEvent.click(screen.getByRole("checkbox", { name: "ピアノ" }));
    await userEvent.click(screen.getByRole("button"));
    expect(onPick).toHaveBeenCalledExactlyOnceWith("/music/song.wav", [
      "bass",
      "vocals",
      "guitar",
    ]);
  });

  it("rejects an unsupported file with an error and does not call onPick", async () => {
    vi.mocked(pickAudioFile).mockResolvedValue("/music/song.flac");
    const onPick = renderHome();
    await userEvent.click(screen.getByRole("button"));
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByText(/対応していないファイル/)).toBeInTheDocument();
  });

  it("opens the picker via keyboard (Enter on the dropzone)", async () => {
    vi.mocked(pickAudioFile).mockResolvedValue(null);
    renderHome();
    screen.getByRole("button").focus();
    await userEvent.keyboard("{Enter}");
    expect(pickAudioFile).toHaveBeenCalledOnce();
  });
});
