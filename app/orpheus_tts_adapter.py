from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import torch

if TYPE_CHECKING:
    from snac import SNAC

logger = logging.getLogger(__name__)

_SPECIAL_CUTOFF = 6
_TOKEN_RE = re.compile(r"<custom_token_(\d+)>")
_SLOT_OFFSETS = [0, 4096, 8192, 12288, 16384, 20480, 24576]
_CODEBOOK_SIZE = 4096


def _extract_audio_codes(response_text: str) -> list[int]:
    return [int(m) for m in _TOKEN_RE.findall(response_text) if int(m) > _SPECIAL_CUTOFF]


def _decode_to_waveform(audio_tokens: list[int], snac) -> torch.Tensor:
    n_frames = len(audio_tokens) // 7
    tokens = audio_tokens[: n_frames * 7]

    l0, l1, l2 = [], [], []
    for i in range(n_frames):
        g = tokens[i * 7 : (i + 1) * 7]
        l0.append(max(0, min(_CODEBOOK_SIZE - 1, g[0] - _SLOT_OFFSETS[0])))
        l1.append(max(0, min(_CODEBOOK_SIZE - 1, g[1] - _SLOT_OFFSETS[1])))
        l2.append(max(0, min(_CODEBOOK_SIZE - 1, g[2] - _SLOT_OFFSETS[2])))
        l2.append(max(0, min(_CODEBOOK_SIZE - 1, g[3] - _SLOT_OFFSETS[3])))
        l1.append(max(0, min(_CODEBOOK_SIZE - 1, g[4] - _SLOT_OFFSETS[4])))
        l2.append(max(0, min(_CODEBOOK_SIZE - 1, g[5] - _SLOT_OFFSETS[5])))
        l2.append(max(0, min(_CODEBOOK_SIZE - 1, g[6] - _SLOT_OFFSETS[6])))

    codes = [
        torch.tensor([l0], dtype=torch.long),
        torch.tensor([l1], dtype=torch.long),
        torch.tensor([l2], dtype=torch.long),
    ]
    with torch.no_grad():
        return snac.decode(codes)


class OrpheusTTSAdapter:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._snac = None

    def _get_snac(self):
        if self._snac is None:
            from snac import SNAC
            model = SNAC.from_pretrained(self.settings.orpheus_snac_model_id)
            model.eval()
            self._snac = model
        return self._snac

    def is_configured(self) -> bool:
        return bool(self.settings.tts_enabled and self.settings.tts_backend == "orpheus")

    def synthesize(self, text: str, utterance_id: str) -> Path | None:
        if not self.is_configured():
            return None

        prompt = f"<|audio|>{self.settings.orpheus_voice}: {text}<|eot_id|>"
        try:
            response = httpx.post(
                f"{self.settings.ollama_base_url}/api/generate",
                json={"model": self.settings.orpheus_ollama_model, "prompt": prompt, "stream": False},
                timeout=self.settings.orpheus_timeout_seconds,
            )
            response.raise_for_status()
            response_text = response.json()["response"]
        except Exception as exc:
            logger.warning("Orpheus Ollama request failed: %s", exc)
            return None

        audio_tokens = _extract_audio_codes(response_text)
        if len(audio_tokens) < 7:
            logger.warning("Orpheus returned too few audio tokens (%d)", len(audio_tokens))
            return None

        try:
            waveform = _decode_to_waveform(audio_tokens, self._get_snac())
        except Exception as exc:
            logger.warning("SNAC decode failed: %s", exc)
            return None

        return self._save_wav(waveform, utterance_id)

    def _save_wav(self, waveform: torch.Tensor, utterance_id: str) -> Path | None:
        import scipy.io.wavfile as wav_io

        output_dir = self.settings.tts_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_np = waveform.squeeze().cpu().numpy()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            wav_io.write(str(tmp_path), 24000, audio_np)
            final_path = output_dir / f"{utterance_id}.wav"
            subprocess.run(
                [shutil.which("ffmpeg") or "ffmpeg", "-y",
                 "-i", str(tmp_path), "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le",
                 str(final_path)],
                check=True, capture_output=True,
            )
            return final_path
        except Exception as exc:
            logger.warning("WAV save/convert failed: %s", exc)
            return None
        finally:
            tmp_path.unlink(missing_ok=True)
