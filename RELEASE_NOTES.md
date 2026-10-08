## OnDaGo Editor v1.0.0: first public release

Offline AI video editor for Windows. Drop in a raw recording, get back a YouTube video, an Instagram Reel, thumbnails and titles, all generated on your own NVIDIA GPU.

### Download & install
1. Download **`OnDaGoEditor.zip`** below. Don't use "Source code".
2. Extract to a short path, e.g. `C:\OnDaGo`.
3. In PowerShell: `Get-ChildItem C:\OnDaGo -Recurse | Unblock-File`
4. Run **`install.bat`**. On first run it downloads about 35 GB of AI models into the folder. Use `install.bat -Lite` to skip the 16 GB image model.
5. Start **OnDaGo Editor** from the desktop shortcut.

**Requirements:** Windows 10/11 64-bit · NVIDIA GPU with 8 GB VRAM recommended (RTX 20-series or newer for all features; GTX 10-series supported without AI images) · driver 560+ · 16 GB RAM · ~45 GB free disk.

### Highlights
- Removes silences, fillers and retakes with word-safe cuts
- Animated word-by-word captions, including **Hinglish in Roman script**
- B-roll from your own library or Pexels/Pixabay, checked by a vision model, with animated text cards as a fallback. It asks you when nothing fits.
- Jump-cut zooms, push-ins, slide transitions, sound effects, colour grade, end card
- YouTube 16:9 and Reel 9:16 from one recording, with automatic orientation fix and face-tracked vertical crop
- AI thumbnails, titles, descriptions, chapters, tags and captions
- One-click upload to YouTube and Instagram

### Known limitations
- Hinglish transcription is much better than stock Whisper but not perfect. Add your brand and product names in Settings.
- AI image generation needs an RTX 20-series or newer GPU.
- YouTube API uploads stay Private until your Google Cloud project is audited.
