## OnDaGo Editor v2.2.0: titles behind you, real tutorial editing, faster phone uploads

**🎬 Big titles behind you**
- Some pop-up texts now become big bold titles that appear BEHIND you: your head and shoulders stay in front
  of the letters, like professional edits. Words pop in one by one, the key word is yellow, with a whoosh.
- Placed automatically so you only cover a small part of the letters; works in the YouTube and the Reel version.
- On in "Talking head" and "Energetic Reel"; switch it in Advanced options ("Big titles behind you").

**💻 Tutorials and screen recordings**
- Screen recordings are detected automatically (a small webcam in the corner doesn't count as a talking head).
- Silent parts where something happens on screen (typing, clicking, scrolling) are fast-forwarded 2-8x with a
  small "▶▶ 3x" badge, instead of being cut, so viewers never miss a step. Silent parts where nothing happens
  are still cut.
- Zooms follow the on-screen action (auto zoom where you type or click), no more zooming on the middle of the
  screen or on a tiny webcam face; no slow push-in that crops the UI.
- Reels of screen recordings follow the action so the text is readable on a phone.
- New "Screen recording: Auto / Always / Never" option.

**🔄 Orientation fix**
- Videos were sometimes turned upside down or sideways. The check now looks at where the eyes are relative to the
  mouth (instead of how confident the face detector is) and only turns a video on a clear majority of big faces.
  Screen recordings are never turned. It is also much faster on long 4K clips.

**📱 Faster, steadier phone uploads**
- The phone now sends 3-4 pieces at once, in any order, so one slow or lost piece no longer holds up the upload.
  Resuming continues exactly where it stopped. Progress and speed show from the first second.
- A warning appears when the phone is on the laptop's Wi-Fi but the upload goes through the internet
  (on Windows usually the firewall: Phone app tab -> Allow through firewall).

## OnDaGo Editor v2.1.1: fast, reliable phone uploads on iPhone

- Uploads no longer wait for the laptop's reply to each piece. iPhone Safari on a direct Wi-Fi link often never
  delivers that reply, which made uploads crawl. The phone now confirms each piece with a tiny check and moves on
  right away (tested: full speed even when every reply is lost).
- A piece that never arrives is re-sent after 6 s instead of waiting and shrinking; quick retries on Wi-Fi.
- Keeps the iPhone screen from auto-locking during an upload (it pauses uploads when locked), and an upload
  paused in the background continues the moment you come back.
- The laptop now tells you (phone + Log) when OnDaGo was updated but is still running the old version: quit it
  completely and reopen. Turning only the phone app off and on keeps the old code.

## OnDaGo Editor v2.1.0: Vox-style explainers + "This computer" page

### What's new
**📰 Vox-style explainer graphics**
- New **Vox explainer** style. The editor AI adds explainer graphics where they help: title cards, highlighted quotes on torn paper, counting numbers (dot charts and bars), maps that push in to the country you mention, timelines of the years you mention, and B-roll photos as taped paper clippings.
- The look: paper texture with boiling grain, 12 fps cut-out animation, whip-pan transitions with motion blur and film grain.
- Only things you actually said become numbers, maps or timelines. Works offline on any computer (CPU).
- Also available as a single switch in Advanced options (desktop and phone).

**🖥 This computer**
- New page that detects your graphics card / Apple chip and shows what every part of the editor runs on (GPU, Apple GPU or CPU), how fast, and what isn't available here, e.g. AI images on a Mac or on AMD / Intel GPUs.
- Tips with one-click fixes (e.g. use the smaller editor model on 6 GB cards or 8 GB Macs), driver hints when the GPU encoder fails, and a "Copy report" button.
- The same report is in the phone app's Settings and at the end of the installer.

## OnDaGo Editor v2.0.0: edit from your phone + animated doodles

### What's new
**📱 Phone app: edit from anywhere**
- Leave the laptop on. From your phone (Android, iPhone or any browser) you can:
  - pick a video from the phone or from the laptop's folders
  - choose the options and watch progress live
  - fix B-roll, change doodles and text, and pick a thumbnail
  - watch a preview, download the HD file and publish
- **No static IP needed:** use Tailscale (free, private) or a password-protected Cloudflare link.
- **Pair by QR code**, add it to your home screen like an app, and get **push notifications** (ntfy) when an edit is ready or needs you.
- **Runs in the tray:** it can start with Windows and keeps the laptop awake while the phone app is on.
- Uploads are **resumable** (8 MB chunks), so flaky mobile data doesn't restart a 2 GB upload.
- **Same Wi-Fi = direct, full-speed transfers:** at home the phone automatically sends uploads and plays or downloads videos straight from the laptop over your router, with no internet data (⚡ Wi-Fi badge).

**✨ Animated doodles**
- More than 25 hand-drawn doodles that draw themselves on screen: lightbulb ideas, arrows pointing at you, crowns, hearts, ₹/$ money, graphs, rockets, WOW/BOOM bursts and more. Each one has a marker wobble, the hand-drawn "boil" and a matching sound.
- The editor AI picks the moments from what you say. Doodles are **placed around your face**, never on it or on the captions, separately for YouTube and Reel framing.
- **Glowing sticker outline** around you on key moments, and **marker underlines / circles** on pop-up text.
- **AI custom doodles** on RTX GPUs: FLUX draws anything, then the app animates it. The option is greyed out on GPUs that can't run it.
- New **Review → Doodles** tab: change the type, add one at any moment, or switch them off.

**🎬 Better edits with less work**
- **Split-screen push-aside moments:** you glide to one side while footage or an animated keyword card slides in beside you. Reels get a picture-in-picture card.
- **Vertical recordings on YouTube** look like a designed layout (rounded card, soft backdrop) instead of blurred bars.
- **Nothing important gets chopped:** retakes are only removed when you really repeat yourself; the opening and ending are protected; Reels keep the full video when it's under 3 minutes.
- **Better Hindi/Hinglish detection:** the language is checked across the whole recording, so Hinglish speech goes to the Hinglish model instead of being translated into English.
- **Online B-roll without a key** (openly-licensed photos), and the app decides for you instead of stopping to ask.
- **Higher quality:** HDR tone-mapping for phone clips, better encoder settings, 720p phone previews with an HD button.

**Fixes**
- The installer no longer fails when the install folder path contains a space (e.g. `C:\Users\Designer 1\...`).

### Upgrading from v1
Extract v2 over your v1 folder and run **`install.bat`** again. It only installs the new libraries and the small outline model (~170 MB). Your settings, projects and models are kept.

### Download & install (new users)
1. Download **`OnDaGoEditor.zip`** below. Don't use "Source code".
2. Extract to a short path, e.g. `C:\OnDaGo`.
3. In PowerShell: `Get-ChildItem C:\OnDaGo -Recurse | Unblock-File`
4. Run **`install.bat`**. The first run downloads about 35 GB of AI models; use `install.bat -Lite` to skip the 16 GB image model.
5. Start **OnDaGo Editor** from the desktop shortcut. For the phone app, open the **Phone & remote** tab.

**Requirements:** Windows 10/11 64-bit · NVIDIA GPU, 8 GB VRAM recommended (RTX 20-series or newer for every feature; GTX 10-series works without AI images and AI doodles) · driver 560+ · 16 GB RAM · ~45 GB free disk.

### Known limitations
- Publishing to YouTube from the phone needs YouTube connected once in the desktop app.
- A quick Cloudflare link changes every time the app restarts. Turn on ntfy to receive the new one, or use your own domain.
- Laptops still sleep when the lid closes unless you set the lid action to "Do nothing" while plugged in.
