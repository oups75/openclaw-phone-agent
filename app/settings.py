from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    phone_agent_env: str = "development"
    phone_agent_host: str = "0.0.0.0"
    phone_agent_port: int = 8080
    phone_agent_log_level: str = "INFO"
    phone_agent_db_path: Path = REPO_ROOT / "data/phone-agent.sqlite3"
    phone_agent_recordings_dir: Path = REPO_ROOT / "recordings"
    phone_agent_mixmonitor_dir: Path = Path("/var/lib/asterisk/openclaw-recordings")

    asterisk_base_url: str = "http://127.0.0.1:8088"
    asterisk_ari_app: str = "openclaw-phone-agent"
    asterisk_ari_username: str = "openclaw_agent"
    asterisk_ari_password: str = "change-me"
    asterisk_ari_ws_path: str = "/ari/events"
    asterisk_playback_sound: str = "custom/openclaw-hold"
    asterisk_recording_format: str = "wav"
    asterisk_recording_max_duration: int = 3600
    asterisk_transfer_context: str = "call-human-softphone"
    asterisk_transfer_extension: str = "700"
    asterisk_transfer_priority: int = 1

    softphone_endpoint: str = "PJSIP/human-softphone"
    phonebook_path: Path = REPO_ROOT / "data/phonebook.json"
    outbound_pstn_context: str = "call-ht813-pstn"
    outbound_pstn_endpoint: str = "PJSIP/ht813"
    outbound_number_template: str = "Local/{number}@{context}"
    transfer_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    enable_ari_listener: bool = True
    mcp_server_name: str = "openclaw-phone-agent"
    phone_agent_public_base_url: str = "http://127.0.0.1:8080"

    openclaw_enabled: bool = False
    openclaw_command: str = "openclaw"
    openclaw_timeout_seconds: int = 45
    openclaw_use_local_agent: bool = False
    openclaw_agent_id: str | None = None
    openclaw_session_id: str | None = None
    openclaw_model: str | None = None
    openclaw_config_path: Path | None = None
    openclaw_state_dir: Path | None = None

    tts_enabled: bool = False
    tts_backend: str = "piper"
    tts_piper_binary: str = "/home/soloway/.local/bin/piper"
    tts_piper_model_path: Path | None = None
    tts_piper_config_path: Path | None = None
    tts_output_dir: Path = REPO_ROOT / "recordings" / "tts"

    orpheus_ollama_model: str = "legraphista/Orpheus:latest"
    orpheus_voice: str = "tara"
    orpheus_snac_model_id: str = "hubertsiuzdak/snac_24khz"
    orpheus_timeout_seconds: int = 120

    stt_enabled: bool = True
    stt_whisper_binary: str = "/home/soloway/.local/bin/whisper"
    stt_whisper_model: str = "medium"
    stt_whisper_model_dir: Path = Path("/home/soloway/.cache/whisper")
    stt_whisper_language: str = "en"
    stt_ld_library_path: str = "/media/soloway/workspace/Devel/Tools/ai/xtts/venv/lib/python3.14/site-packages/nvidia/cusparselt/lib"

    llm_backend: str = "openclaw"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: int = 60

    cli_llm_command: str = ""
    cli_llm_timeout_seconds: int = 45

    dialog_max_turns: int = 3
    asterisk_turn_recording_max_duration: int = 30
    asterisk_ari_recording_dir: str = "/var/spool/asterisk/recording"
    asterisk_playback_timeout: int = 30


settings = Settings()
