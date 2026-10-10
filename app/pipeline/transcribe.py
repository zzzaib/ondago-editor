"""Speech-to-text with word timestamps (faster-whisper) + Silero VAD speech map."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.gpu import free_vram
from ..core.paths import MODELS

# Whisper is trained to drop disfluencies. A filler-heavy prompt nudges it to keep them.
FILLER_PROMPT = "Umm, so, uh, I was like, hmm, thinking... Uh, yeah. Okay, um, let's, uh, start."
FILLER_PROMPT_HI = "अं, तो, उम्म... मतलब, हम्म, ठीक है। Okay, so आज हम video edit करेंगे, uh, चलो start करते हैं।"

# best -> worst; first one CTranslate2 actually supports on this machine wins
CUDA_PREF = ["int8_float16", "float16", "int8_bfloat16", "bfloat16", "int8_float32", "int8", "float32"]
CPU_PREF = ["int8", "int8_float32", "float32"]


HINGLISH_REPO = "Oriserve/Whisper-Hindi2Hinglish-Apex"       # Apache-2.0, outputs Roman Hinglish directly
HINGLISH_CT2_FALLBACK = "Hub84/faster-whisper-hinglish-prime"  # pre-converted older sibling
HINGLISH_DIR = MODELS / "whisper" / "hinglish-apex"
FILLER_PROMPT_HINGLISH = "Umm, toh aaj hum, uh, video edit karenge... matlab, hmm, okay, chalo start karte hain."


def whisper_model_path(name: str) -> str:
    local = MODELS / "whisper" / name
    return str(local) if (local / "model.bin").exists() else name


def hinglish_model_ready() -> bool:
    return (HINGLISH_DIR / "model.bin").exists()


def ensure_hinglish_model(log=None) -> bool:
    """Download + convert the Hindi->Hinglish Whisper fine-tune to CTranslate2 once (~1.6 GB)."""
    if hinglish_model_ready():
        return True
    HINGLISH_DIR.parent.mkdir(parents=True, exist_ok=True)
    try:
        log and log(f"Preparing Hinglish speech model ({HINGLISH_REPO}) - first time only, a few minutes")
        from ctranslate2.converters import TransformersConverter
        try:
            conv = TransformersConverter(HINGLISH_REPO, copy_files=["tokenizer.json", "preprocessor_config.json"],
                                         load_as_float16=True, low_cpu_mem_usage=True)
            conv.convert(str(HINGLISH_DIR), quantization="float16", force=True)
        except Exception:  # noqa: BLE001  (repo without tokenizer.json)
            conv = TransformersConverter(HINGLISH_REPO, load_as_float16=True, low_cpu_mem_usage=True)
            conv.convert(str(HINGLISH_DIR), quantization="float16", force=True)
        if hinglish_model_ready():
            return True
    except Exception as e:  # noqa: BLE001
        log and log(f"Hinglish model conversion failed ({e}); trying pre-converted copy")
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(HINGLISH_CT2_FALLBACK, local_dir=str(HINGLISH_DIR))
        return hinglish_model_ready()
    except Exception as e:  # noqa: BLE001
        log and log(f"Hinglish model unavailable ({e}); using standard Whisper + transliteration")
        return False


def _candidates(requested: str, log) -> list[tuple[str, str]]:
    """(device, compute_type) options in order of preference, based on what CTranslate2 reports."""
    import ctranslate2

    out: list[tuple[str, str]] = []
    try:
        n_gpu = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        n_gpu = 0
    if n_gpu > 0:
        try:
            sup = set(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:  # noqa: BLE001
            sup = set()
        log and log(f"CTranslate2 {ctranslate2.__version__}: {n_gpu} CUDA device(s), compute types: {sorted(sup)}")
        order = [requested] + [c for c in CUDA_PREF if c != requested]
        out += [("cuda", c) for c in order if not sup or c in sup]
    else:
        log and log(f"CTranslate2 {ctranslate2.__version__}: no CUDA device visible - Whisper will run on CPU (slow)")
    try:
        sup_cpu = set(ctranslate2.get_supported_compute_types("cpu"))
    except Exception:  # noqa: BLE001
        sup_cpu = {"int8", "float32"}
    out += [("cpu", c) for c in CPU_PREF if c in sup_cpu]
    return out


def load_whisper(model_name: str, compute_type: str, log=None, path: str | None = None):
    from faster_whisper import WhisperModel

    errors = []
    for dev, ct in _candidates(compute_type, log):
        try:
            m = WhisperModel(path or whisper_model_path(model_name), device=dev, compute_type=ct,
                             download_root=str(MODELS / "whisper"))
            log and log(f"Whisper {model_name} loaded on {dev} ({ct})")
            return m
        except Exception as e:  # noqa: BLE001
            errors.append(f"{dev}/{ct}: {e}")
            log and log(f"Whisper could not use {dev}/{ct}: {e}")
            free_vram()
    raise RuntimeError("Whisper failed to load:\n" + "\n".join(errors))


def transcribe(wav: Path, model_name: str = "large-v3-turbo", language: str = "auto", compute_type: str = "int8_float16",
               on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
               hinglish_asr: bool = True, vocabulary: str = "") -> dict:
    """language: 'auto', an ISO code, or 'hinglish'. Hindi/Hinglish speech goes to the dedicated
    Hinglish model (Roman-script output, far fewer mistakes than stock Whisper on Indian speech)."""
    use_hinglish = language == "hinglish"
    model = None
    if language in ("", "auto") and hinglish_asr:
        from faster_whisper.audio import decode_audio
        model = load_whisper(model_name, compute_type, log)
        audio = decode_audio(str(wav), sampling_rate=16000)
        lang, prob, hi_share = detect_language_robust(model, audio, log)
        # Hinglish speakers often get labelled "en" with low confidence; stock Whisper then TRANSLATES the Hindi
        # parts into English. Any real Hindi share -> use the Hinglish model, which writes what was actually said.
        if lang in ("hi", "ur") or hi_share >= 0.2 or (lang == "en" and prob < 0.8 and hi_share >= 0.08):
            use_hinglish = True
            log and log(f"Hindi/Hinglish speech detected (Hindi share {hi_share:.0%}) - using the Hinglish model")
        else:
            language = lang
    if use_hinglish and hinglish_asr and ensure_hinglish_model(log):
        if model is not None:
            del model
            free_vram()
        model = load_whisper("hinglish-apex", compute_type, log, path=str(HINGLISH_DIR))
        lang_arg, prompt, label = "en", FILLER_PROMPT_HINGLISH, "hinglish"
    else:
        if use_hinglish:
            language = "hi"
        model = model or load_whisper(model_name, compute_type, log)
        lang_arg = None if language in ("", "auto") else language
        prompt = FILLER_PROMPT_HI if language in ("hi", "ur") else FILLER_PROMPT
        label = None
    try:
        kw = {}
        if vocabulary.strip():
            kw["hotwords"] = vocabulary.strip()
        segments, info = model.transcribe(
            str(wav),
            language=lang_arg,
            word_timestamps=True,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            beam_size=5,
            vad_filter=False,
            no_speech_threshold=0.6,
            **kw,
        )
        out_segs = []
        total = max(1e-6, info.duration)
        for seg in segments:
            words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}
                     for w in (seg.words or []) if w.word.strip()]
            if not words:
                continue
            out_segs.append({"id": len(out_segs), "s": words[0]["s"], "e": words[-1]["e"],
                             "text": seg.text.strip(), "words": words})
            if on_progress:
                on_progress(min(1.0, seg.end / total))
        lang_out = label or info.language
        log and log(f"Transcribed {len(out_segs)} segments, language={lang_out}")
        return {"language": lang_out, "duration": info.duration, "segments": out_segs}
    finally:
        del model
        free_vram()


def detect_language_robust(model, audio, log=None) -> tuple[str, float, float]:
    """Language vote over up to 5 windows spread through the recording (not just the first 30 s).
    Returns (language, its average probability, average Hindi+Urdu probability)."""
    sr = 16000
    n = len(audio)
    win = 30 * sr
    starts = [0] if n <= win else sorted({int(x) for x in [0, n * 0.2, n * 0.45, n * 0.7, max(0, n - win)]})
    totals: dict[str, float] = {}
    used = 0
    for st in starts:
        chunk = audio[st:st + win]
        if len(chunk) < 5 * sr:
            continue
        try:
            _, _, probs = model.detect_language(chunk)
        except Exception:  # noqa: BLE001
            continue
        used += 1
        for code, p in probs[:10]:
            totals[code] = totals.get(code, 0.0) + p
    if not used:
        return "en", 0.0, 0.0
    avg = {k: v / used for k, v in totals.items()}
    lang = max(avg, key=avg.get)
    hi = avg.get("hi", 0.0) + avg.get("ur", 0.0)
    if log:
        top = ", ".join(f"{k} {v:.0%}" for k, v in sorted(avg.items(), key=lambda x: -x[1])[:3])
        log(f"Detected spoken language over {used} parts: {top}")
    return lang, avg[lang], hi


def speech_regions(wav: Path) -> list[tuple[float, float]]:
    """Voice-activity regions in seconds (Silero VAD bundled with faster-whisper, runs on CPU)."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(wav), sampling_rate=16000)
    opts = VadOptions(threshold=0.45, min_speech_duration_ms=120, min_silence_duration_ms=120, speech_pad_ms=30)
    ts = get_speech_timestamps(audio, opts)
    return [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]
