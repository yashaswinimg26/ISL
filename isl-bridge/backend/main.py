"""
ISL Bridge - Stage 1 backend
Kannada speech -> text.

Providers (set STT_PROVIDER in .env):
  sarvam   Sarvam AI Saaras (hosted API, same engine as sarvam.ai's Kannada STT).
           Needs SARVAM_API_KEY. This is the default when a key is present.
  whisper  Local faster-whisper (offline fallback, lower Kannada accuracy).
           Needs: pip install -r requirements-whisper.txt

Run:  uvicorn main:app --reload --port 8000
"""
import os
import tempfile
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).parent / ".env")

# ---- Config -----------------------------------------------------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_URL = os.getenv("SARVAM_URL", "https://api.sarvam.ai/speech-to-text")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "saaras:v4")  # or saaras:v3
SARVAM_MODE = os.getenv("SARVAM_MODE", "").strip()  # transcribe|verbatim|codemix... (blank = API default)
LANGUAGE_CODE = os.getenv("SARVAM_LANGUAGE", "kn-IN")  # Kannada
SARVAM_MAX_SECONDS = 30  # REST endpoint limit per request

# Kannada -> English translation (Sarvam Translate API)
SARVAM_TRANSLATE_URL = os.getenv("SARVAM_TRANSLATE_URL", "https://api.sarvam.ai/translate")
SARVAM_TRANSLATE_MODEL = os.getenv("SARVAM_TRANSLATE_MODEL", "mayura:v1")  # or sarvam-translate:v1
SARVAM_TRANSLATE_MAX_CHARS = 2000 if SARVAM_TRANSLATE_MODEL.startswith("sarvam-translate") else 1000

PROVIDER = os.getenv("STT_PROVIDER", "sarvam" if SARVAM_API_KEY else "whisper").lower()

# Whisper fallback settings
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
WHISPER_LANGUAGE = os.getenv("ASR_LANGUAGE", "kn")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

app = FastAPI(title="ISL Bridge API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Sarvam provider --------------------------------------------------------
def _clean_mime(content_type: str | None, filename: str) -> str:
    """Sarvam wants a plain MIME type; MediaRecorder sends 'audio/webm;codecs=opus'."""
    base = (content_type or "").split(";")[0].strip().lower()
    if base.startswith("audio/") or base.startswith("video/webm"):
        return "audio/webm" if base == "video/webm" else base
    ext = Path(filename).suffix.lower()
    return {
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
    }.get(ext, "audio/webm")


def _sarvam_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", body)
        if isinstance(err, dict):
            return err.get("message") or str(err)
        return str(err)
    except Exception:  # noqa: BLE001
        return resp.text[:300] or f"HTTP {resp.status_code}"


async def transcribe_sarvam(data: bytes, filename: str, content_type: str | None):
    if not SARVAM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SARVAM_API_KEY is not set. Add it to backend/.env and restart.",
        )

    form = {"model": SARVAM_MODEL, "language_code": LANGUAGE_CODE}
    if SARVAM_MODE:
        form["mode"] = SARVAM_MODE

    files = {"file": (filename, data, _clean_mime(content_type, filename))}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                SARVAM_URL,
                headers={"api-subscription-key": SARVAM_API_KEY},
                data=form,
                files=files,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach Sarvam API: {exc}"
        ) from exc

    if resp.status_code in (401, 403):
        raise HTTPException(
            status_code=502,
            detail="Sarvam rejected the API key (check SARVAM_API_KEY in backend/.env).",
        )
    if resp.status_code == 429:
        raise HTTPException(
            status_code=429, detail="Sarvam rate limit / quota reached. Try again shortly."
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Sarvam error ({resp.status_code}): {_sarvam_error_message(resp)}",
        )

    j = resp.json()
    return {
        "text": (j.get("transcript") or "").strip(),
        "language": j.get("language_code") or LANGUAGE_CODE,
        "provider": "sarvam",
        "model": SARVAM_MODEL,
    }


# ---- Whisper fallback provider ---------------------------------------------
_whisper = None
_whisper_lock = threading.Lock()
_whisper_error: str | None = None


def get_whisper():
    global _whisper, _whisper_error
    if _whisper is None:
        with _whisper_lock:
            if _whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    _whisper_error = (
                        "faster-whisper is not installed. "
                        "Run: pip install -r requirements-whisper.txt"
                    )
                    raise RuntimeError(_whisper_error) from exc
                _whisper = WhisperModel(
                    WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
                )
    return _whisper


def transcribe_whisper(data: bytes, filename: str):
    suffix = Path(filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        model = get_whisper()
        segments, _info = model.transcribe(
            tmp_path,
            language=WHISPER_LANGUAGE,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return {
        "text": text,
        "language": WHISPER_LANGUAGE,
        "provider": "whisper",
        "model": WHISPER_MODEL,
    }


@app.on_event("startup")
def warm_up():
    if PROVIDER == "whisper":
        # Load the model in the background so the first request isn't slow.
        def _load():
            try:
                get_whisper()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_load, daemon=True).start()


# ---- Routes -----------------------------------------------------------------
@app.get("/api/health")
def health():
    if PROVIDER == "sarvam":
        ready = bool(SARVAM_API_KEY)
        return {
            "status": "ok",
            "provider": "sarvam",
            "model": SARVAM_MODEL,
            "language": LANGUAGE_CODE,
            "model_loaded": ready,
            "error": None if ready else "SARVAM_API_KEY is not set in backend/.env",
            "max_seconds": SARVAM_MAX_SECONDS,
        }
    return {
        "status": "ok",
        "provider": "whisper",
        "model": WHISPER_MODEL,
        "language": WHISPER_LANGUAGE,
        "model_loaded": _whisper is not None,
        "error": _whisper_error,
        "max_seconds": 120,
    }


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload.")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large.")

    filename = audio.filename or "speech.webm"
    if PROVIDER == "sarvam":
        return await transcribe_sarvam(data, filename, audio.content_type)
    return transcribe_whisper(data, filename)


# ============================================================================
# Stage 2: Kannada -> English translation (Sarvam) and ISL linguistic processing
# ============================================================================
class TextIn(BaseModel):
    text: str = Field(..., max_length=2000)


async def translate_kn_to_en(text: str) -> dict:
    if not SARVAM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SARVAM_API_KEY is not set. Add it to backend/.env and restart.",
        )
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to translate.")
    if len(text) > SARVAM_TRANSLATE_MAX_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long for {SARVAM_TRANSLATE_MODEL} ({SARVAM_TRANSLATE_MAX_CHARS} characters max).",
        )
    payload = {
        "input": text,
        "source_language_code": LANGUAGE_CODE,   # kn-IN
        "target_language_code": "en-IN",
        "model": SARVAM_TRANSLATE_MODEL,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                SARVAM_TRANSLATE_URL,
                headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Sarvam API: {exc}") from exc

    if resp.status_code in (401, 403):
        raise HTTPException(status_code=502, detail="Sarvam rejected the API key (check SARVAM_API_KEY in backend/.env).")
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Sarvam rate limit / quota reached. Try again shortly.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Sarvam translate error ({resp.status_code}): {_sarvam_error_message(resp)}")
    j = resp.json()
    return {"english": (j.get("translated_text") or "").strip(), "model": SARVAM_TRANSLATE_MODEL}


# ---- ISL processor (spaCy) is loaded once, on first use ----------------------
_isl = None
_isl_lock = threading.Lock()


def get_isl():
    global _isl
    if _isl is None:
        with _isl_lock:
            if _isl is None:
                try:
                    from isl import ISLProcessor

                    _isl = ISLProcessor()
                except (ImportError, RuntimeError) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=f"ISL processing unavailable: {exc}. "
                        "Run: pip install -r requirements.txt && python -m spacy download en_core_web_sm",
                    ) from exc
    return _isl


def run_isl(english: str) -> dict:
    return get_isl().process(english)


@app.post("/api/translate")
async def translate(body: TextIn):
    """Kannada text -> English text (Sarvam)."""
    return await translate_kn_to_en(body.text)


@app.post("/api/isl")
def isl_from_english(body: TextIn):
    """English text -> ISL gloss / sign sequence. Needs no API key."""
    return run_isl(body.text)


@app.post("/api/pipeline")
async def pipeline(body: TextIn):
    """Kannada text -> English -> ISL sign sequence."""
    tr = await translate_kn_to_en(body.text)
    isl_result = run_isl(tr["english"])
    return {"kannada": body.text.strip(), "english": tr["english"], "translate_model": tr["model"], "isl": isl_result}
