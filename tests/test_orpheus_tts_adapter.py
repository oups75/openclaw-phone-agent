import unittest
from unittest.mock import MagicMock

from app.orpheus_tts_adapter import _extract_audio_codes, _decode_to_waveform


FAKE_RESPONSE = (
    "<custom_token_4><custom_token_5><custom_token_1>"
    "<custom_token_3929><custom_token_5371><custom_token_9118>"
    "<custom_token_13341><custom_token_18079><custom_token_20651><custom_token_24747>"
    "<custom_token_2><custom_token_6><custom_token_3>"
)


class TestExtractAudioCodes(unittest.TestCase):
    def test_skips_special_tokens_and_returns_audio_values(self):
        codes = _extract_audio_codes(FAKE_RESPONSE)
        self.assertEqual(codes, [3929, 5371, 9118, 13341, 18079, 20651, 24747])


class TestOrpheusTTSAdapterIsConfigured(unittest.TestCase):
    def _settings(self, enabled=True, backend="orpheus"):
        from types import SimpleNamespace
        from pathlib import Path
        return SimpleNamespace(
            tts_enabled=enabled,
            tts_backend=backend,
            orpheus_voice="tara",
            orpheus_ollama_model="legraphista/Orpheus:latest",
            orpheus_snac_model_id="hubertsiuzdak/snac_24khz",
            orpheus_timeout_seconds=60,
            ollama_base_url="http://127.0.0.1:11434",
            tts_output_dir=Path("/tmp/tts_test"),
        )

    def test_is_configured_true_when_enabled_and_backend_orpheus(self):
        from app.orpheus_tts_adapter import OrpheusTTSAdapter
        adapter = OrpheusTTSAdapter(self._settings())
        self.assertTrue(adapter.is_configured())


class TestOrpheusTTSAdapterSynthesize(unittest.TestCase):
    def _settings(self):
        from types import SimpleNamespace
        from pathlib import Path
        return SimpleNamespace(
            tts_enabled=True,
            tts_backend="orpheus",
            orpheus_voice="tara",
            orpheus_ollama_model="legraphista/Orpheus:latest",
            orpheus_snac_model_id="hubertsiuzdak/snac_24khz",
            orpheus_timeout_seconds=60,
            ollama_base_url="http://127.0.0.1:11434",
            tts_output_dir=Path("/tmp/tts_test"),
        )

    def test_synthesize_returns_wav_path_on_success(self):
        import torch
        from pathlib import Path
        from unittest.mock import patch
        from app.orpheus_tts_adapter import OrpheusTTSAdapter

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"response": FAKE_RESPONSE}

        fake_snac = MagicMock()
        fake_snac.decode.return_value = torch.zeros(1, 1, 512)

        adapter = OrpheusTTSAdapter(self._settings())
        adapter._snac = fake_snac

        with patch("app.orpheus_tts_adapter.httpx.post", return_value=fake_resp), \
             patch("app.orpheus_tts_adapter.subprocess.run"), \
             patch("scipy.io.wavfile.write"):
            result = adapter.synthesize("hello", "test-utterance")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "test-utterance.wav")


class TestDecodeToWaveform(unittest.TestCase):
    def test_decode_produces_three_code_levels(self):
        import torch
        snac = MagicMock()
        snac.decode.return_value = torch.zeros(1, 1, 512)

        tokens = [3929, 5371, 9118, 13341, 18079, 20651, 24747]
        _decode_to_waveform(tokens, snac)

        codes = snac.decode.call_args[0][0]
        self.assertEqual(len(codes), 3)
        self.assertEqual(codes[0].shape[1], 1)  # L0: 1 per frame
        self.assertEqual(codes[1].shape[1], 2)  # L1: 2 per frame
        self.assertEqual(codes[2].shape[1], 4)  # L2: 4 per frame


if __name__ == "__main__":
    unittest.main()
