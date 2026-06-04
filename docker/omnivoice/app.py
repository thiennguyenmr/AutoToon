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
    instruct: Optional[str] = None        # voice description for Voice Design mode
    language: Optional[str] = None        # e.g. "vi", "Vietnamese"
    speed: Optional[float] = None         # 1.0 = normal
    seed: Optional[int] = None
    sample_rate: Optional[int] = None
    pad_ms: Optional[int] = 0             # silence pad đầu + cuối (mili-giây). Default 0 = no pad.
    # legacy compatibility — if dict provided, convert to instruct string
    voice_design: Optional[Dict[str, Any]] = None


def _resolve_ref(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(REF_DIR, p)
    return cand if os.path.exists(cand) else None


def _resolve_ref_text(t: Optional[str]) -> Optional[str]:
    """If t ends with .txt and exists in REF_DIR, read its contents; else return as-is."""
    if not t:
        return None
    t = t.strip()
    if t.endswith('.txt'):
        cand = t if os.path.isabs(t) else os.path.join(REF_DIR, t)
        if os.path.exists(cand):
            try:
                with open(cand, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception:
                return t
    return t


def _to_wav_bytes(audio, sample_rate: int, pad_ms: int = 0) -> bytes:
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
    # Pad silence ở đầu và cuối nếu client yêu cầu (per-request, default 0 = no pad)
    if pad_ms > 0:
        pad_frames = int(sample_rate * pad_ms / 1000)
        silence = torch.zeros((audio.shape[0], pad_frames), dtype=audio.dtype)
        audio = torch.cat([silence, audio, silence], dim=1)
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

    # Mode 1: voice clone if both ref_audio + ref_text given
    resolved_ref_text = _resolve_ref_text(req.ref_text)
    if ref_path and resolved_ref_text:
        kwargs["ref_audio"] = ref_path
        kwargs["ref_text"] = resolved_ref_text
    else:
        # Mode 2: voice design with instruct text
        instruct = req.instruct
        if not instruct and req.voice_design:
            # back-compat: stitch dict into a description string
            vd = req.voice_design
            bits = []
            if vd.get("gender"): bits.append(str(vd["gender"]))
            if vd.get("age"): bits.append(str(vd["age"]))
            if vd.get("pitch"): bits.append(f"{vd['pitch']} pitch")
            if vd.get("emotion"): bits.append(str(vd["emotion"]))
            if vd.get("style"): bits.append(str(vd["style"]))
            instruct = ", ".join(bits) if bits else None
        if instruct:
            kwargs["instruct"] = instruct

    if req.language:
        kwargs["language"] = req.language
    if req.speed is not None:
        kwargs["speed"] = float(req.speed)
    if req.seed is not None:
        # seed lives inside generation_config in newer OmniVoice; pass as kwarg too
        try:
            from omnivoice import OmniVoiceGenerationConfig
            kwargs["generation_config"] = OmniVoiceGenerationConfig(seed=int(req.seed))
        except Exception:
            kwargs["seed"] = int(req.seed)

    try:
        out = model.generate(**kwargs)
    except TypeError as e:
        # log mismatch and retry with just text + instruct/language
        print(f"[tts] kwargs mismatch: {e}; retrying minimal", flush=True)
        minimal = {"text": req.text}
        if req.language: minimal["language"] = req.language
        if "instruct" in kwargs: minimal["instruct"] = kwargs["instruct"]
        out = model.generate(**minimal)

    audio = out
    sr = req.sample_rate or DEFAULT_SAMPLE_RATE
    if isinstance(out, tuple) and len(out) >= 2:
        audio, sr = out[0], int(out[1])
    elif isinstance(out, dict):
        audio = out.get("audio") or out.get("waveform") or out.get("samples")
        sr = int(out.get("sample_rate") or out.get("sr") or sr)

    if audio is None:
        raise HTTPException(500, "model returned no audio")

    wav = _to_wav_bytes(audio, sr, pad_ms=int(req.pad_ms or 0))
    return Response(content=wav, media_type="audio/wav", headers={"X-Sample-Rate": str(sr)})
