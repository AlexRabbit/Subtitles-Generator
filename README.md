If this helped you, consider starring the repo ⭐

# Subtitles Generator - Generate .srt Both Locally or Let it Run on your PC and use it On other Devices.
<img width="1410" height="1041" alt="image" src="https://github.com/user-attachments/assets/213cf61d-8d39-4266-994d-67982d1913bf" />

> **Turn any video into professional `.srt` subtitle files** — locally, on your PC, powered by [OpenAI Whisper](https://github.com/openai/whisper).  
> No cloud upload. No subscription. Drag, drop, pick languages, done.

Portable, queue-based desktop app with a glassmorphism UI, GPU acceleration, phone/tablet support, and cinema-style phrase timing.

---

## Start here — double-click `run.bat`

**This is the only step you need to remember.**

1. Open the project folder on your PC.
2. **Double-click `run.bat`.**
3. Leave the black console window open while the app runs.
4. When the app window appears, you are ready to add videos.

That is it. You do **not** need to install Python manually, run pip commands, or open a terminal — `run.bat` handles everything on first launch and every launch after.

> **Keep the console window open.** Closing it stops the app.  
> **First run takes longer** (venv, packages, FFmpeg, Whisper model download). Later launches are much faster.

---

## 60-second workflow (after `run.bat` opens the app)

| Step | Action |
|------|--------|
| 1 | **Drag & drop** videos or folders onto the window — or click **Browse files** / **Browse folder** |
| 2 | Set **Source language** (spoken in the video). `Auto` works for most content. |
| 3 | Check every **Subtitle language** you want (e.g. `en`, `es`, `fr`) |
| 4 | Click **Process All** |
| 5 | Wait for the progress bar. SRT files appear **next to each video** on disk |

**Example result:**

```
MyVacation.mp4
MyVacation - (en).srt
MyVacation - (es).srt
MyVacation - (fr).srt
```

---

## Full tutorial — PC workflow

### Step 1 — Launch

Double-click **`run.bat`**. Wait until the app window appears.

- First launch: **5–20 minutes** depending on internet speed (PyTorch + Whisper model).
- Later launches: usually **under 30 seconds**.

### Step 2 — Add videos

Three ways to add content:

| Method | How |
|--------|-----|
| **Drag & drop** | Drop video files or entire folders anywhere on the app window |
| **Browse files** | Click the button in the drop zone; pick one or more videos |
| **Browse folder** | Pick a folder — all supported videos inside are scanned recursively |

### Step 3 — Configure languages

**Global defaults** (sidebar → Languages) apply to every video unless you override one card.

1. **Source language** — what is spoken in the video.  
   - Use **Auto** for mixed or unknown content.  
   - Set explicitly (e.g. `Japanese`) if detection is wrong.

2. **Target languages** — check every language you want subtitles in.  
   - Tap **☆** next to a language to pin it as a **favorite** (sorted to the top).  
   - Favorites are saved in `user_settings.json` on your PC.

3. **Per-video override** — click **Select** on a card, then change languages for that file only.

---

## Subtitle output

### Naming convention

```
{video-filename} - ({language-code}).srt
```

| Video | Language | Output file |
|-------|----------|-------------|
| `lecture.mp4` | English | `lecture - (en).srt` |
| `lecture.mp4` | Spanish | `lecture - (es).srt` |
| `clip.mkv` | Japanese | `clip - (ja).srt` |

### Standard SRT format

```srt
1
00:00:01,080 --> 00:00:03,180
Hello and welcome.

2
00:00:04,500 --> 00:00:07,200
Today we will learn something new.
```

Compatible with VLC, MPC-HC, Plex, DaVinci Resolve, Premiere, YouTube upload, etc.

### Smart re-run behavior

If `Video - (en).srt` **already exists**, that language is **skipped** automatically — safe to re-run after adding new target languages.

---

## Languages & translation

### Source language (transcription)

Whisper supports **90+ languages** including Auto-detect. The UI lists every language Whisper can transcribe.

### Target language (translation)

When target ≠ source, segments are translated via **Google Translate** (`deep-translator`). Translation runs in batches with automatic retries.

| Scenario | What happens |
|----------|--------------|
| Target = source (e.g. `en` → `en`) | Transcription only — no translation |
| Target ≠ source (e.g. `es` → `en`) | Transcribe in source, translate to target |
| Source = Auto | Whisper detects spoken language first |

> **Tip:** For best accuracy, set source language explicitly when you know it.

---

## Whisper models — which one to pick

Change the model in **Settings → Engine → Whisper model**. The model file is cached in `models/` after first download.

| Model | Speed | Quality | VRAM (approx.) | Best for |
|-------|-------|---------|------------------|----------|
| `tiny` / `tiny.en` | Fastest | Lowest | ~1 GB | Quick drafts, English-only with `.en` |
| `base` / `base.en` | Fast | Good | ~1 GB | **Default — balanced daily use** |
| `small` / `small.en` | Medium | Better | ~2 GB | Cleaner dialogue |
| `medium` / `medium.en` | Slow | High | ~5 GB | Podcasts, interviews |
| `large-v3` | Slowest | **Best** | ~10 GB | Final exports, GPU recommended |
| `large-v2` / `large` | Very slow | Excellent | ~10 GB | Legacy large models |
| `turbo` | Fast | High (EN-focused) | ~6 GB | English content, speed/quality balance |

Changing model or CUDA setting **unloads and reloads** Whisper automatically.

---


## Optional: word-level timestamps

Enable **Word-level timestamps** in Settings when you need:

- Karaoke-style highlighting
- Per-word precision for editing in a DAW/NLE
- Linguistic analysis

Each word becomes its own SRT cue. Best when output language matches spoken language.

---


## Phone & tablet (LAN access)

1. Double-click **`run.bat`** on the PC (app must be running).
2. In the app sidebar, enable **Allow LAN access (phone/tablet)**.
3. Connect phone to the **same Wi‑Fi** as the PC (not guest network).
4. Expand **📱 Same Wi‑Fi** in the sidebar — open the URL shown (e.g. `http://192.168.1.42:8765`).  
   Or tap **▣** to scan the **QR code**.
5. On the phone: use **Browse files** to upload videos from that device. Processing still runs on the PC.

### Download subtitles on phone

When processing completes, tap **↓ en.srt** (or other language) on the video card.

### Turn LAN off when done

Disable **Allow LAN access** — remote devices on your Wi‑Fi get **403 Forbidden** immediately. Your PC still works at `127.0.0.1`.

### Firewall blocked?

Right-click PowerShell → **Run as Administrator**:

```powershell
cd path\to\Subtitles-Generator
.\scripts\allow_lan_firewall.ps1
```

This adds an inbound rule for TCP port **8765**.

### Phone can't connect?

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Page never loads | **AP / client isolation** on router | Disable isolation in router admin; avoid guest Wi‑Fi |
| Page never loads | Windows Firewall | Run `allow_lan_firewall.ps1` as Admin |
| Connection refused | App not running | Double-click `run.bat` again |
| 403 Forbidden | LAN toggle OFF | Enable **Allow LAN access** in Settings |
| Blank page | Wrong URL | Use LAN IP from sidebar, not `127.0.0.1` |

**Quick ping test:** From the phone, ping the PC's LAN IP. No reply = network isolation or firewall — not an app bug.

---

## Security & privacy

### What leaves your PC?

| Data | Leaves PC? |
|------|------------|
| Video files | **No** — processed locally |
| Audio for Whisper | **No** — extracted to `logs/audio_cache/`, deleted after use |
| Translation text | **Yes** — text segments sent to Google Translate API when target ≠ source |
| QR code | **Yes** — generated via `api.qrserver.com` (URL only, when you open QR modal) |

---

## Supported video formats

`.mp4` · `.mkv` · `.avi` · `.mov` · `.wmv` · `.flv` · `.webm` · `.m4v` · `.mpg` · `.mpeg` · `.3gp`

Any format FFmpeg can decode works. FFmpeg is auto-installed by `run.bat` if missing.

---

## Advanced configuration (`.env`)

On first run, `run.bat` copies `.env.example` → `.env`. Edit `.env` only when you need non-default behavior.

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Local URL host label |
| `PORT` | `8765` | Web UI port |
| `BIND_HOST` | `0.0.0.0` | Network bind address (LAN toggle enforces access) |
| `LAN_ONLY` | `true` | Block public internet IPs |
| `WHISPER_MODEL` | `base` | Starting model (also changeable in UI) |
| `WHISPER_DEVICE` | `cuda` | Preferred device hint |
| `USE_CUDA` | `true` | Default GPU toggle state in UI |
| `MODELS_DIR` | `models` | Whisper model cache folder |
| `FFMPEG_PATH` | *(empty)* | Custom `ffmpeg.exe` path; auto-detect if empty |
| `LOG_LEVEL` | `DEBUG` | Logging verbosity |
| `LOG_DIR` | `logs` | Log file directory |
| `MAX_QUEUE_SIZE` | `1000` | Maximum queued jobs |
| `TRANSLATION_BATCH_SIZE` | `20` | Segments per translation batch |
| `TRANSLATION_RETRY_COUNT` | `3` | Retries on translation failure |
| `DEBUG` | `false` | Flask debug mode (dev only) |

> **Note:** `Allow LAN access` and `Word-level timestamps` are **UI toggles only** — always **OFF** when the app opens. They are not controlled by `.env`.

---

## Troubleshooting

### `run.bat` says Python 3.10+ not found

1. Install Python from [python.org](https://www.python.org/downloads/) — check **“Add Python to PATH”**.
2. Double-click **`run.bat`** again.

### App window does not open (pywebview error)

The app falls back to your **default browser** at `http://127.0.0.1:8765`. Functionality is identical.

Install [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) for the native window experience.

### `pip install` failed

1. Check internet connection.
2. Delete `venv/` folder.
3. Double-click **`run.bat`** to recreate the environment.

### FFmpeg not found / transcription fails immediately

1. Let `run.bat` auto-download FFmpeg.
2. Or install manually from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) into `ffmpeg\bin\`.
3. Or set `FFMPEG_PATH` in `.env`.

### CUDA not detected / very slow processing

1. Update NVIDIA drivers.
2. Re-run `run.bat` (triggers CUDA PyTorch install).
3. Try `scripts\install_cuda_torch.ps1` manually.
4. As last resort: turn **Use CUDA** OFF and use a smaller model (`tiny` / `base`).

### Subtitles appear too early / too late

Default **phrase timing** is tuned for natural viewing. For per-word precision, enable **Word-level timestamps** and re-process.

### Out of memory (large model)

Switch to `base` or `small`, or disable CUDA to use system RAM instead of VRAM.

### Port 8765 already in use

Change `PORT=8766` in `.env`, restart via **`run.bat`**.

### Where are the logs?

| File | Contents |
|------|----------|
| `logs/app.log` | Full debug trail |
| `logs/errors.log` | Errors only |
| `logs/events.jsonl` | Structured JSON events (one per line) |
| `logs/startup.log` | `run.bat` launch history |

---



## How it works (architecture)

```mermaid
flowchart TB
    subgraph UI["Desktop UI (pywebview)"]
        DROP[Drag & drop / Browse]
        SETTINGS[Languages & Engine settings]
    end

    subgraph API["Flask API (localhost:8765)"]
        QUEUE[Job queue]
        GUARD[LAN network guard]
    end

    subgraph Worker["Background worker (1 thread)"]
        FF[FFmpeg → WAV 16 kHz mono]
        WH[Whisper transcribe]
        TR[Google Translate]
        REF[Phrase timing refine]
        SRT[Write .srt next to video]
    end

    DROP --> API
    SETTINGS --> API
    API --> QUEUE
    QUEUE --> Worker
    FF --> WH --> TR --> REF --> SRT
```

## Requirements

| Requirement | Details |
|-------------|---------|
| **OS** | Windows 10 or later |
| **Python** | 3.10 – 3.13 (3.10 recommended; auto-detected by `run.bat`) |
| **Internet** | Required on first run (packages + model download) |
| **GPU** | Optional — NVIDIA + CUDA for 5–20× faster transcription |
| **Disk** | ~2 GB for venv + base model; up to ~10 GB for `large-v3` |

---

## FAQ

**Do I need to install Python?**  
`run.bat` finds it automatically. Install Python 3.10+ only if the script reports it missing.

**Do videos get uploaded to the cloud?**  
No. Whisper runs entirely on your PC.

**Can I process multiple videos at once?**  
They queue automatically, but transcribe **one at a time** for stability.

**Can I use this on Mac or Linux?**  
YES The Python script can run manually on other OS with adjustments.

**Why are LAN and word timestamps off by default?**  
Security (LAN) and readability (phrase timing). Both are opt-in.

**Can I change the port?**  
Yes — set `PORT` in `.env` and restart via **`run.bat`**.

**Where do phone uploads go?**  
`uploads/` folder on the PC. SRT output still saves next to the uploaded file path.

---

## Credits

- [OpenAI Whisper](https://github.com/openai/whisper) — speech recognition
- [auto-subtitle](https://github.com/m1guelpf/auto-subtitle) — original inspiration
- [deep-translator](https://github.com/nidhaloff/deep-translator) — subtitle translation
- [pywebview](https://pywebview.flowrl.com/) — native desktop window
- [driver.js](https://driverjs.com/) — interactive onboarding tour

---

<p align="center">
  <strong>Remember: double-click <code>run.bat</code> — everything else is optional.</strong>
</p>
