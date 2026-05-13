import io
import os
from typing import Optional, Dict, Any

import numpy as np
import torch
import torchaudio
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="OmniVoice TTS")

MODEL_ID = os.environ.get("OMNIVOICE_MODEL_ID", "k2-fsa/OmniVoice")
DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
REF_DIR = os.environ.get("REF_DIR", "/refs")
DEFAULT_SAMPLE_RATE = int(os.environ.get("DEFAULT_SAMPLE_RATE", "24000"))

_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model
    from omnivoice import OmniVoice  # imported lazily so /health works pre-load
    _model = OmniVoice.from_pretrained(MODEL_ID, device_map=DEVICE)
    return _model


class TTSRequest(BaseModel):
    text: str
    ref_audio: Optional[str] = None       # path inside container or filename in /refs
    ref_text: Optional[str] = None
    voice_design: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    sample_rate: Optional[int] = None


def _resolve_ref(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(REF_DIR, p)
    return cand if os.path.exists(cand) else None


def _to_wav_bytes(audio, sample_rate: int) -> bytes:
    if isinstance(audio, (list, tuple)):
        audio = np.asarray(audio, dtype=np.float32)
    if isinstance(audio, np.ndarray):
        audio = torch.from_numpy(audio.astype(np.float32, copy=False))
    if not isinstance(audio, torch.Tensor):
        raise RuntimeError(f"unexpected audio type: {type(audio)}")
    audio = audio.detach().to("cpu", dtype=torch.float32)
    if audio.dim() == 1:
        audio = audio.unsqueeze(0)
    elif audio.dim() == 3:
        audio = audio.squeeze(0)
    elif audio.dim() == 0:
        raise RuntimeError("audio tensor is a scalar")
    buf = io.BytesIO()
    torchaudio.save(buf, audio, sample_rate, format="wav")
    return buf.getvalue()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "model_id": MODEL_ID,
        "model_loaded": _model is not None,
        "cuda_available": torch.cuda.is_available(),
    }


@app.post("/load")
def load():
    _load_model()
    return {"status": "loaded", "model_id": MODEL_ID, "device": DEVICE}


@app.post("/tts")
def tts(req: TTSRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(400, "text is required")
    model = _load_model()

    kwargs: Dict[str, Any] = {"text": req.text}
    ref_path = _resolve_ref(req.ref_audio)
    if ref_path and req.ref_text:
        kwargs["ref_audio"] = ref_path
        kwargs["ref_text"] = req.ref_text
    elif req.voice_design:
        kwargs["voice_design"] = req.voice_design
    if req.seed is not None:
        kwargs["seed"] = int(req.seed)

    try:
        out = model.generate(**kwargs)
    except TypeError:
        # some versions return (audio, sr); pass minimal kwargs as fallback
        out = model.generate(req.text)

    audio = out
    sr = req.sample_rate or DEFAULT_SAMPLE_RATE
    if isinstance(out, tuple) and len(out) >= 2:
        audio, sr = out[0], int(out[1])
    elif isinstance(out, dict):
        audio = out.get("audio") or out.get("waveform") or out.get("samples")
        sr = int(out.get("sample_rate") or out.get("sr") or sr)

    if audio is None:
        raise HTTPException(500, "model returned no audio")

    wav = _to_wav_bytes(audio, sr)
    return Response(content=wav, media_type="audio/wav", headers={"X-Sample-Rate": str(sr)})
