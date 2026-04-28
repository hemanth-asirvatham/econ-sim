from __future__ import annotations

import random
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CANDIDATE_TICKETS = (
    {"player_name": "President Lena Park", "opponent_name": "Governor Malcolm Pryce", "opponent_voice": "ash"},
    {"player_name": "President Daniel Reyes", "opponent_name": "Senator Julia Mercer", "opponent_voice": "shimmer"},
    {"player_name": "President Aisha Rahman", "opponent_name": "Governor Adrian Cole", "opponent_voice": "cedar"},
    {"player_name": "President Marcus Vale", "opponent_name": "Governor Priya Nandakumar", "opponent_voice": "marin"},
    {"player_name": "President Naomi Chen", "opponent_name": "Senator Jonah Bell", "opponent_voice": "verse"},
    {"player_name": "President Thomas Keene", "opponent_name": "Governor Elena Cross", "opponent_voice": "sage"},
)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECON_SIM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "econ-sim"
    api_title: str = "econ-sim api"
    runs_dir: Path = Path("runs")
    allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:5175",
            "http://localhost:5176",
            "http://localhost:5177",
            "http://localhost:5178",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
            "http://127.0.0.1:5175",
            "http://127.0.0.1:5176",
            "http://127.0.0.1:5177",
            "http://127.0.0.1:5178",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )
    allow_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    dummy_openai: bool = False
    default_population_description: str = (
        "A representative sample of the United States adult population, "
        "with realistic variation across region, class, education, industry, "
        "family structure, ideology, ethnicity, age, and AI exposure."
    )
    default_visual_style: str = (
        "Cezanne-Monet-Matisse civic impressionism; grounded people and institutions, thick impasto brushstrokes, "
        "planar Fauvist color, softened faces, warm civic light, occasional Monet haze or pointillist shimmer, "
        "selective abstraction, varied compositions from panoramas to intimate rooms, and no readable text, glossy CGI, photorealism, or cartoon stylization."
    )
    max_stage_count: int = 5
    default_persona_count: int = 64

    orchestrator_model: str = "gpt-5.5"
    orchestrator_reasoning_effort: str = "medium"
    narration_model: str = "gpt-5.5"
    narration_reasoning_effort: str = "low"
    debate_model: str = "gpt-5.5"
    debate_reasoning_effort: str = "low"
    council_draft_model: str = "gpt-5.5"
    council_decider_model: str = "gpt-5.4-nano"
    council_audio_model: str = "gpt-audio-1.5"
    persona_update_model: str = "gpt-5.5"
    persona_update_reasoning_effort: str = "low"
    poll_model: str = "gpt-5.5"
    poll_reasoning_effort: str = "low"
    poll_questions_per_run: int = 24
    tracking_poll_soft_timeout_seconds: float = 45.0
    service_tier: str = "priority"
    openai_response_timeout_seconds: float = 120.0

    realtime_model: str = "gpt-realtime-alpha-dolphin-11"
    realtime_reasoning_effort: str = "low"
    realtime_voice: str = "cedar"
    realtime_debate_voice: str = "ash"
    narration_voice: str = "ballad"
    realtime_input_transcription_model: str = "gpt-4o-mini-transcribe"
    realtime_semantic_vad_eagerness: str = "low"
    realtime_capture_vad_threshold: float = 0.93
    realtime_capture_vad_prefix_padding_ms: int = 260
    realtime_capture_vad_silence_duration_ms: int = 960
    speech_model: str = "gpt-4o-mini-tts"
    image_model: str = "gpt-image-2"
    image_size: str = "1536x1024"
    image_quality: str = "medium"
    image_output_format: str = "jpeg"
    image_output_compression: int = 85
    image_timeout_seconds: float = 90.0
    image_max_attempts: int = 2

    def prepare(self) -> "AppSettings":
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return self

    def random_candidate_ticket(self) -> dict[str, str]:
        return dict(random.choice(DEFAULT_CANDIDATE_TICKETS))


def get_settings() -> AppSettings:
    return AppSettings().prepare()
