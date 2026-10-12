# OnDaGo Editor

**Offline AI video editor for YouTube & Instagram Reels. Raw recording in, finished edit out, on your own GPU.**

OnDaGo Editor is a one-click desktop app for **Windows and macOS** (Apple Silicon M1/M2/M3/M4 and Intel Macs). It cuts silences, filler words and retakes, adds animated captions (English or **Hinglish**), B-roll, **hand-drawn animated doodles**, zooms, sound effects, thumbnails, titles and descriptions. It exports a 16:9 YouTube video and a 9:16 Reel, and can upload both. All the AI runs **locally on your own computer** (NVIDIA GPU on Windows, Apple Silicon on Mac). It's free, with no subscriptions and no cloud processing.

**New in v2:** leave the laptop on and **edit from your phone** (Android, iPhone or any browser) from anywhere, no static IP needed, plus **animated doodles** that draw themselves next to you.

![platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011%20%7C%20macOS%2012%2B-blue)
![gpu](https://img.shields.io/badge/GPU-NVIDIA%208%20GB-76b900)
![offline](https://img.shields.io/badge/AI-100%25%20local-purple)

---

## Features

### Editing
- **Removes dead air automatically:** silences, "um/uh" (including ones the speech model didn't write down), false starts and repeated takes.
- **Never chops real content:** a sentence is only removed as a "retake" when you actually say it again right after; your opening hook and closing lines are protected.
- **Word-safe cuts:** every word keeps a safety margin and cut points land on the quietest audio, so words don't get clipped.
- **Jump-cut punch-ins, emphasis zooms** and slow camera push-ins keep the frame moving.
- **Sideways phone videos are fixed automatically:** face detection works out the real orientation.
- **One recording, two outputs:** YouTube **16:9** and Instagram **9:16**. The Reel either follows your face or uses a blurred fill. Vertical recordings get blurred side bars on YouTube.
- **Reel length:** videos up to 3 minutes become a full-length Reel; longer ones get the most engaging self-contained 60–90 s with a hook title.
- **Picture quality:** iPhone/Android HDR clips are tone-mapped to proper colours, outputs carry correct colour tags, and encoding uses higher quality settings.

### Captions & text
- **Word-by-word animated captions** with keyword highlights, in three styles (Bold Pop, Clean box, Minimal).
- **Hinglish support:** a dedicated Hindi→Hinglish speech model writes captions in Roman script ("aaj hum video edit karenge"). English words stay in English.
- **AI proof-reading** fixes misheard names and brands. Add your own terms (e.g. *Meta Quest, Ray-Ban*) in Settings.
- **Pop-up callouts, an opening hook title** and an end card ("Follow for more").

### B-roll
- **Offline:** your own clip/image folder, searched by meaning with SigLIP.
- **Online, out of the box:** openly-licensed photos from Openverse with no key needed; add a free Pexels or Pixabay key (also from the phone app) for real video clips.
- **Decides for you:** when nothing fits it uses an animated keyword card instead of stopping to ask (choose "Ask me" if you prefer to pick).
- **A vision model checks every clip** (Qwen 3.5 VL) and rejects ones that don't match.
- **Animated text cards** when no footage fits.
- **When it can't find B-roll, it asks you.** For each gap it shows what to film, what to search for, and an upload button.

### Split-screen "push aside" moments (new in v2)
- For B-roll moments you **glide to one side** and the footage, a photo or an animated keyword card (with a big doodle) slides in next to you, then you glide back.
- **Vertical phone recordings on YouTube** become a rounded card on a soft backdrop instead of plain blurred bars, and use the empty sides for split-screen moments.
- **Reels** keep you on screen as a picture-in-picture card over the B-roll.
- Hand-drawn arrows point from you to the footage; whooshes play as you move.

### Doodles & motion graphics (new in v2)
- **Hand-drawn doodles that draw themselves on screen**, stroke by stroke, with a wobbly marker line and the "boil" shimmer of hand-made animation: 💡 a lightbulb above your head for an idea, 👉 an arrow pointing at you, 👑 a crown, ❤️ hearts, 💰 ₹/$ scribbles, 📈 growth graphs, 🚀 rockets, ❓ question marks, 💬 WOW / BOOM bursts and 25+ more.
- **The editor AI picks the moments** from what you say; a keyword fallback covers runs without the LLM.
- **Placed around your face**, using face tracking, never on your face, the captions or a pop-up callout, separately for the YouTube and Reel framing.
- **Glowing sticker outline around you** on key moments, drawn on from the top of your head.
- **Marker underline / circle** on pop-up callouts.
- Every doodle gets a matching sound (pencil scribble, ding, sparkle chime).
- **AI custom doodles (RTX only):** FLUX draws a doodle of anything ("a VR headset"), then it's traced and animated the same way. Greyed out on GPUs that can't run it.
- Change, add or switch off any doodle in **Review → Doodles** (or on your phone).

### Phone app (new in v2)
- **Edit from your phone:** pick a video from the phone (resumable chunked upload) or from the laptop's folders, choose options, watch progress live, pick B-roll, change doodles and text, choose a thumbnail, watch a phone-sized preview, download the HD file and publish to YouTube / Instagram.
- **Works from anywhere without a static IP:** Tailscale (free, private) or a Cloudflare link (public, password-protected).
- **Same Wi-Fi = direct and fast:** when your phone is on the same network as the laptop, uploads, video playback and downloads go straight over your Wi-Fi at full speed, with no internet data used. It switches automatically (⚡ Wi-Fi badge); through a public link it offers a one-tap switch.
- **Installable** on the home screen (Android & iPhone), with **push notifications** through the free ntfy app when an edit is ready or needs you.
- Runs in the **system tray**, can **start with Windows**, and keeps the laptop awake while the phone app is on.

### Audio
- Noise removal (RNNoise), compression, loudness normalised to −14 LUFS.
- Background music folder with automatic ducking under your voice.
- **Sound effects** generated by the app (whoosh, pop, riser), placed on cuts, B-roll and text.

### Publishing
- **3 thumbnail options:** the most expressive frames, background removal, AI or blurred background, and bold text.
- **AI-written metadata:** titles, description, chapters, tags, Instagram caption and hashtags.
- **One-click upload** to YouTube (with custom thumbnail and scheduling) and Instagram Reels.
- **Review screen** to toggle any cut, edit text, swap B-roll, pick a thumbnail and re-render.

---

## GPU compatibility

| GPU | VRAM | What works |
|---|---|---|
| **RTX 20 / 30 / 40 / 50 series** (e.g. 2060 Super, 3060, 3070 Ti, 4060, 4070, 5070) | 8 GB+ | **Everything**, including AI-generated B-roll and thumbnail backgrounds |
| **GTX 16 series** (1660, 1660 Super/Ti) | 6 GB | Everything; use the smaller editor model `qwen3.5:4b` (Settings); AI images are slow |
| **GTX 10 series** (1070, 1080, 1080 Ti, laptop Max-Q) | 8 GB+ | Everything **except AI images** (turned off automatically). **Tested on a GTX 1070 Max-Q 8 GB.** |
| GTX 1060 6 GB and similar | 6 GB | Works with `qwen3.5:4b`; slower |
| < 6 GB, AMD, Intel, no GPU | — | Runs on CPU (very slow) |

The installer detects your GPU and picks the right PyTorch build automatically: CUDA 12.8 for RTX cards, CUDA 12.6 for GTX 10-series.

### Minimum requirements
- **OS:** Windows 10 (1803+) or Windows 11, 64-bit
- **GPU:** NVIDIA with **8 GB VRAM** recommended (6 GB works, see above), driver **560 or newer**
- **RAM:** 16 GB (32 GB recommended for AI image generation)
- **Disk:** ~45 GB free for the full install, ~25 GB with `-Lite` (an SSD is strongly recommended)
- **Internet:** only for the first install, online B-roll and uploads. Editing itself is fully offline.

---

## Install

> The download is small (under 1 MB). The installer downloads the AI models, about **35 GB** in total, the first time you run it.

1. **Download** `OnDaGoEditor.zip` from the [**Releases**](../../releases/latest) page. Don't use the green "Code" button.
2. **Extract** it to a short path, e.g. `C:\OnDaGo`. Long paths can break model downloads.
3. **Unblock the files.** Windows marks downloaded scripts and Smart App Control may block them. Open PowerShell and run:
   ```powershell
   Get-ChildItem C:\OnDaGo -Recurse | Unblock-File
   ```
4. **Double-click `install.bat`**, or from PowerShell:
   ```powershell
   Set-Location C:\OnDaGo
   powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\bootstrap.ps1
   ```
   Leave the window alone while it runs. Clicking inside a PowerShell window pauses it; press **Esc** to resume.
5. **Launch** from the **OnDaGo Editor** desktop shortcut, or `run.bat`.

Everything installs **inside the folder**. You don't need admin rights, a system Python, or a system-wide Ollama. Re-running `install.bat` is safe: it resumes or repairs and skips what's already done. To uninstall, delete the folder.

### What the installer downloads

| Component | Size | Purpose |
|---|---|---|
| Python 3.11 + PyTorch (CUDA) + libraries | ~4 GB | Runtime |
| FFmpeg (NVENC build) | ~150 MB | Cutting, rendering, hardware encoding |
| Ollama (portable) | ~1.5 GB | Runs the editor LLM locally |
| Qwen 3.5 9B (`qwen3.5:9b`) | 6.6 GB | Editor brain: retakes, B-roll ideas, captions proof-reading, titles; vision checks |
| Whisper large-v3-turbo | ~1.6 GB | Speech-to-text with word timings |
| Whisper Hindi→Hinglish (Oriserve Apex) | ~1.6 GB | Hinglish captions in Roman script |
| SigLIP base | ~0.8 GB | Searching your offline B-roll library |
| BiRefNet portrait | ~1 GB | Thumbnail background removal |
| U²-Net human segmentation | ~170 MB | The glowing outline doodle around you |
| YuNet, RNNoise, fonts | < 5 MB | Face tracking, noise removal, captions |
| FLUX.2 [klein] 4B | ~16 GB | AI B-roll stills & thumbnail backgrounds (*skipped with `-Lite` and on GTX 10-series*) |

### Install options
```powershell
install.bat -Lite                 # skip the 16 GB image model (AI images off)
install.bat -Llm qwen3.5:4b       # smaller editor model for 6 GB GPUs
```

### Install on a Mac (Apple Silicon or Intel)

Needs **macOS 12 Monterey or newer** and about **25 GB** free space. 16 GB memory or more is recommended (8 GB Macs get the smaller editor model automatically).

1. **Get the folder:** download the ZIP from GitHub (green **Code** button → *Download ZIP*) and unzip it to e.g. `~/OnDaGo`, or clone it:
   ```bash
   git clone https://github.com/zzzaib/ondago-editor.git ~/OnDaGo
   ```
2. **Open Terminal** and run the installer:
   ```bash
   cd ~/OnDaGo
   bash install.command
   ```
   (Double-clicking `install.command` also works. If macOS says it's from an unidentified developer, right-click it → **Open** → **Open**.)
3. **Launch** *OnDaGo Editor* from your Desktop, Launchpad or `~/Applications`, or run `bash run.command`.
4. The first time the phone app is turned on, macOS asks whether OnDaGo may accept incoming connections: click **Allow**.

| | Apple Silicon (M1, M2, M3, M4…) | Intel Mac |
|---|---|---|
| Video rendering | Apple VideoToolbox (hardware, fast) | Apple VideoToolbox (hardware) |
| Speech to text (Whisper) | CPU, all cores | CPU (slower) |
| Editor AI (Ollama) | Apple GPU (Metal) | CPU (slow; use `--llm qwen3.5:4b`) |
| B-roll library search | Apple GPU (Metal) | CPU |
| Doodles, captions, split-screen, phone app | ✓ | ✓ |
| AI-generated images (FLUX) | not available (needs an NVIDIA RTX GPU) | not available |

Mac install options:
```bash
bash install.command --llm qwen3.5:4b    # smaller editor model (8 GB Macs, Intel Macs)
```

---

## Quick start

1. **Create** tab: drop a video, choose captions, B-roll mode, zoom style and outputs, then click **Auto-Edit My Video**.
2. If some moments have no fitting B-roll, the **Review → B-roll** tab lists them with suggestions. Add your own clip, search online, use a text card, or skip.
3. Click **Render**. Output lands in `<your video>_OnDaGo\` next to the original:
   - `_youtube.mp4`, `_reel.mp4`
   - `_thumbnail.jpg`, `_reel_cover.jpg`
   - `_metadata.txt` (title, description, tags, caption)
4. To publish, use **Upload to YouTube / Instagram**, or upload the files yourself.

Test with a 1–2 minute clip first. The first run loads every model cold, so it's slower than later runs.

### Use it from your phone

1. **Laptop:** open **Phone & remote**, set a **password**, tick **Turn on the phone app** → **Save & apply**. Allow access if Windows Firewall asks.
2. **Same Wi-Fi:** scan the **QR code** with your phone camera. It opens the app and logs you in.
3. **From anywhere (mobile data, another city):** pick one:
   - **Tailscale (recommended, private):** install [Tailscale](https://tailscale.com/download) on the laptop **and** the phone and log in with the same account. Then scan the QR again: it now uses the Tailscale address. For full app install / HTTPS, enable HTTPS in the Tailscale admin console (DNS page) and tick **Also give it an HTTPS address**.
   - **Public link (any browser):** *Public link → Quick* gives a free `https://….trycloudflare.com` link with no account; it changes when the app restarts. *My own address* uses a Cloudflare tunnel token for a fixed `https://edit.yourdomain.com`. Both are protected by your password.
4. **Add to Home Screen:** iPhone Safari → Share → *Add to Home Screen*; Android Chrome → ⋮ → *Install app*.
5. **Notifications (optional):** install the free [ntfy](https://ntfy.sh) app, tap **Make one** on the laptop to create a secret topic, subscribe to it on the phone, then **Send test**. You'll get pinged when an edit is ready, needs B-roll, or the public link changes.
6. **Keep it reachable:** tick *Start OnDaGo in the background when I log in*. Keep the laptop plugged in; to work with the lid closed set *Control Panel → Power Options → Choose what closing the lid does → Plugged in: Do nothing*.

**At home it's automatic:** when the phone and laptop share a Wi-Fi/router, the app talks to the laptop directly. The header shows **⚡ Wi-Fi**, uploads use bigger chunks and videos play in full quality. Away from home it falls back to Tailscale or the public link by itself. With Tailscale, traffic between devices on the same network is also direct (it never goes out to the internet). If the switch doesn't happen, check that Windows Firewall allows OnDaGo on Private networks and that the phone isn't on a "guest" Wi-Fi (those block devices from seeing each other).

Videos uploaded from the phone are exported to `Videos\OnDaGo\` on the laptop (or your Output folder). YouTube must be connected once on the laptop before you can publish from the phone.

### Optional one-time setup (Settings tab)

| What | Why | How |
|---|---|---|
| **Names / terms** | Correct spelling of brands, products, your name | Type them comma-separated |
| **B-roll library** | Offline B-roll from your own footage | Pick a folder of clips/images |
| **Pexels / Pixabay keys** | Online stock B-roll (Pexels has the best catalogue) | Free at [pexels.com/api](https://www.pexels.com/api/) and [pixabay.com/api/docs](https://pixabay.com/api/docs/) |
| **Music folder** | Background music with ducking | Folder of royalty-free tracks |
| **YouTube** | One-click upload | Google Cloud project → enable *YouTube Data API v3* → OAuth client (*Desktop app*) → select the JSON → **Connect** |
| **Instagram** | One-click Reel upload | Professional account + Meta app with *Instagram API* → paste long-lived token + user ID → **Test** |

> YouTube API uploads from an **unverified** Google Cloud project are forced to **Private** until Google audits the project. You can publish them from YouTube Studio.

---

## How it works

```
raw video
  ├─ orientation check (YuNet face detector)
  ├─ speech → text with word timings (Whisper / Hinglish Whisper) + voice activity (Silero VAD)
  ├─ editor LLM (Qwen 3.5 via Ollama): proof-read, retakes, B-roll plan, callouts, zooms, doodles, highlight, metadata
  ├─ cuts: silences, fillers, retakes → word-safe, quiet-point edges
  ├─ B-roll: library (SigLIP) / Pexels / Pixabay → vision-model check → text cards / ask you
  ├─ thumbnails: frame scoring → vision pick → background removal → text
  └─ render (FFmpeg + NVENC): cuts + zooms → outline → B-roll → doodles → captions/callouts → SFX + music + loudness
        ├─ YouTube 16:9
        └─ Reel 9:16
```

Only one large model sits in VRAM at a time; each is unloaded before the next loads. That's how the whole pipeline fits in 8 GB.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| *"Smart App Control blocked a file"* | Run the `Unblock-File` command from step 3. If it still blocks downloaded tools, turn Smart App Control off (Windows Security → App & browser control). Since the April 2026 update it can be turned back on without reinstalling. |
| Installer looks frozen | You probably clicked inside the window; press **Esc**. Otherwise press Ctrl+C and run `install.bat` again; it resumes. |
| `sm_61 is not compatible` (GTX 10-series) | Re-run `install.bat` (it now picks the CUDA 12.6 build automatically) |
| App closes without an error | Check `data\crash.log` and `data\app.log` |
| Words sound clipped | Settings → raise *Safety margin after words* |
| Captions misspell names | Settings → *Names / terms in your videos* |
| See what's happening | Start with `run.bat debug` (console) and check the **Log** tab |
| Phone can't connect on Wi-Fi | Phone and laptop on the same network? Allow *pythonw.exe* in Windows Defender Firewall (Private networks), or use Tailscale HTTPS / the public link, which don't need a firewall rule |
| Phone can't connect on mobile data | Use Tailscale (both devices logged in and connected) or a public link |
| Public link changed | Quick links change on every restart. Turn on ntfy notifications to receive the new one, or use *My own address* |
| "Port already in use" | OnDaGo is already running (check the tray), or change the port in Phone & remote |

---

## Folder layout

```
install.bat / run.bat      start here (Windows)
install.command / run.command   start here (macOS)
app/                       source (pipeline, GUI, phone app, uploaders)
installer/                 bootstrap.ps1 + model downloader
config/                    default settings
tools/  models/  data/     created by the installer (not in git)
```

---

## Credits & licences

OnDaGo Editor's own code is released under the MIT License (see `LICENSE`). The installer downloads these third-party tools and models from their original sources, each under its own licence. Check each one before commercial use:

- [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2)
- [Oriserve Whisper-Hindi2Hinglish-Apex](https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Apex) (Apache-2.0)
- [Qwen 3.5](https://ollama.com/library/qwen3.5) via [Ollama](https://ollama.com)
- [FLUX.2 [klein] 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (Apache-2.0)
- [SigLIP](https://huggingface.co/google/siglip-base-patch16-224), [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) via [rembg](https://github.com/danielgatis/rembg), [YuNet](https://github.com/opencv/opencv_zoo), [RNNoise models](https://github.com/richardpl/arnndn-models)
- [FFmpeg](https://ffmpeg.org), [uv](https://github.com/astral-sh/uv), [PySide6](https://doc.qt.io/qtforpython-6/), [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org)
- Phone access: [Tailscale](https://tailscale.com), [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared`, downloaded only if you turn on a public link), notifications via [ntfy](https://ntfy.sh)
- Outline effect: [U²-Net](https://github.com/xuebinqin/U-2-Net) human segmentation via rembg
- Fonts: Anton, Poppins, Noto Sans Devanagari (SIL Open Font License)
- Stock footage: [Pexels](https://www.pexels.com/license/) and [Pixabay](https://pixabay.com/service/license-summary/), under their own licences

Built by **Syed Zaib Farooq**, with the help of Claude (Anthropic).
