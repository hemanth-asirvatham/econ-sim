from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from pathlib import Path
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from ..config import AppSettings

ParsedModel = TypeVar("ParsedModel", bound=BaseModel)


class OpenAIGateway:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.client = OpenAI()
        self.live = not settings.dummy_openai

    async def parse(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        text_format: type[ParsedModel],
        reasoning_effort: str | None = None,
        previous_response_id: str | None = None,
        prompt_cache_key: str | None = None,
        max_output_tokens: int = 2400,
        verbosity: str | None = None,
        max_attempts: int = 2,
    ) -> tuple[ParsedModel, str | None]:
        if not self.live:
            raise RuntimeError("Structured parse is unavailable in dummy mode")

        last_error: Exception | None = None
        effort = self._normalize_reasoning_effort(reasoning_effort)
        service_tier = self._normalize_service_tier(self.settings.service_tier)
        for attempt in range(max_attempts):
            def _call() -> tuple[ParsedModel, str | None]:
                response = self.client.responses.parse(
                    model=model,
                    instructions=instructions,
                    input=input_text,
                    text_format=text_format,
                    reasoning={"effort": effort} if effort else None,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=prompt_cache_key,
                    max_output_tokens=max_output_tokens,
                    service_tier=service_tier,
                    text={"verbosity": verbosity} if verbosity else None,
                )
                parsed = response.output_parsed
                if parsed is not None:
                    return parsed, response.id
                if response.output_text:
                    try:
                        return text_format.model_validate_json(response.output_text), response.id
                    except Exception:
                        try:
                            return text_format.model_validate(json.loads(response.output_text)), response.id
                        except Exception as exc:
                            raise RuntimeError(
                                f"Structured parse returned no parsed object. status={response.status} "
                                f"incomplete={response.incomplete_details} text={response.output_text[:1200]!r}"
                            ) from exc
                raise RuntimeError(
                    f"Structured parse returned no parsed object. status={response.status} "
                    f"incomplete={response.incomplete_details} error={response.error}"
                )

            try:
                return await asyncio.to_thread(_call)
            except Exception as exc:
                last_error = exc
                effort = "medium" if effort == "high" else effort
        try:
            return await asyncio.to_thread(
                self._fallback_parse,
                model,
                instructions,
                input_text,
                text_format,
                effort or "low",
                previous_response_id,
                prompt_cache_key,
                max(max_output_tokens * 2, 4200),
                verbosity,
                service_tier,
            )
        except Exception as exc:
            cause = exc if last_error is None else exc
            raise RuntimeError("Structured parse failed after retries") from cause

    async def render_image(self, *, prompt: str, output_path: Path) -> None:
        if not self.live:
            svg = (
                "<svg xmlns='http://www.w3.org/2000/svg' width='1536' height='1024'>"
                "<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>"
                "<stop stop-color='#24170f' offset='0'/>"
                "<stop stop-color='#60412c' offset='1'/>"
                "</linearGradient></defs>"
                "<rect width='100%' height='100%' fill='url(#g)'/>"
                "<text x='64' y='150' font-size='52' fill='#f6d7a8' font-family='Georgia'>"
                "econ-sim preview frame"
                "</text>"
                f"<text x='64' y='240' font-size='28' fill='#ead0ab' font-family='Georgia'>{prompt[:120]}</text>"
                "</svg>"
            )
            output_path.write_text(svg, encoding="utf-8")
            return

        def _call() -> None:
            response = self.client.images.generate(
                model=self.settings.image_model,
                prompt=prompt,
                size=self.settings.image_size,
                quality=self.settings.image_quality,
                output_format="png",
            )
            first = response.data[0].model_dump()
            image_payload = first.get("b64_json")
            if image_payload:
                output_path.write_bytes(base64.b64decode(image_payload))
                return
            image_url = first.get("url")
            if image_url:
                import httpx

                image_response = httpx.get(image_url, timeout=120)
                image_response.raise_for_status()
                output_path.write_bytes(image_response.content)
                return
            raise RuntimeError("Image generation returned neither b64_json nor url")

        await asyncio.to_thread(_call)

    async def synthesize(self, *, text: str, output_path: Path) -> None:
        if not self.live:
            return

        def _call() -> bytes:
            audio = self.client.audio.speech.create(
                model=self.settings.speech_model,
                voice=self.settings.narration_voice,
                input=text,
                response_format="mp3",
            )
            return audio.read()

        output_path.write_bytes(await asyncio.to_thread(_call))

    async def synthesize_bytes(self, *, text: str, voice: str | None = None) -> bytes:
        if not self.live:
            return b""

        def _call() -> bytes:
            audio = self.client.audio.speech.create(
                model=self.settings.speech_model,
                voice=voice or self.settings.narration_voice,
                input=text,
                response_format="mp3",
            )
            return audio.read()

        return await asyncio.to_thread(_call)

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str = "audio.webm",
        content_type: str | None = None,
        prompt: str | None = None,
    ) -> str:
        if not self.live:
            return ""

        def _call() -> str:
            payload = io.BytesIO(audio_bytes)
            payload.name = filename
            result = self.client.audio.transcriptions.create(
                model=self.settings.realtime_input_transcription_model,
                file=(filename, payload, content_type or "application/octet-stream"),
                prompt=prompt or "Transcribe this short setup-chamber instruction faithfully.",
                response_format="text",
            )
            return str(result).strip()

        return await asyncio.to_thread(_call)

    async def create_realtime_session(
        self,
        *,
        instructions: str,
        tools: list[dict],
        model: str | None = None,
        voice: str | None = None,
        max_output_tokens: int | None = None,
        create_response: bool = True,
    ) -> tuple[str, str]:
        if not self.live:
            return "dummy-client-secret", model or self.settings.realtime_model

        def _build_payload() -> tuple[dict, str]:
            selected_model = model or self.settings.realtime_model
            selected_voice = voice or self.settings.realtime_voice
            session_payload = {
                "type": "realtime",
                "model": selected_model,
                "instructions": instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {"model": self.settings.realtime_input_transcription_model},
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "medium",
                            "create_response": create_response,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": selected_voice,
                        "speed": 1.0,
                    },
                },
                "tools": tools,
                "tool_choice": "auto",
            }
            if max_output_tokens is not None:
                session_payload["max_output_tokens"] = max_output_tokens
            return session_payload, selected_model

        def _call() -> tuple[str, str]:
            session_payload, selected_model = _build_payload()
            session = self.client.with_options(timeout=6.0).realtime.client_secrets.create(
                session=session_payload
            )
            return session.value, selected_model

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await asyncio.to_thread(_call)
            except Exception as exc:
                last_error = exc
                if attempt >= 1:
                    break
                await asyncio.sleep(0.45 * (attempt + 1))
        assert last_error is not None
        raise RuntimeError(
            "Realtime session setup failed after retries. Try the mic again in a moment."
        ) from last_error

    def _normalize_reasoning_effort(self, effort: str | None) -> str | None:
        if effort is None:
            return None
        normalized = effort.strip().lower()
        return None if normalized in {"", "none"} else normalized

    def _normalize_service_tier(self, tier: str | None) -> str | None:
        if tier is None:
            return None
        normalized = tier.strip().lower()
        return None if normalized in {"", "none"} else normalized

    def _fallback_parse(
        self,
        model: str,
        instructions: str,
        input_text: str,
        text_format: type[ParsedModel],
        effort: str,
        previous_response_id: str | None,
        prompt_cache_key: str | None,
        max_output_tokens: int,
        verbosity: str | None,
        service_tier: str | None,
    ) -> tuple[ParsedModel, str | None]:
        schema = json.dumps(text_format.model_json_schema(), ensure_ascii=True)
        response = self.client.responses.create(
            model=model,
            instructions=(
                f"{instructions}\n\n"
                "Your last attempt did not validate. Return ONLY a valid JSON object with no markdown, no prose, and no surrounding commentary. "
                "Every required field must be present and match the schema exactly."
            ),
            input=f"{input_text}\n\nSchema:\n{schema}",
            reasoning={"effort": effort} if effort else None,
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
            max_output_tokens=max_output_tokens,
            service_tier=service_tier,
            text={"verbosity": verbosity} if verbosity else None,
        )
        try:
            parsed = self._coerce_text_to_model(response.output_text, text_format)
            return parsed, response.id
        except Exception:
            repair_response = self.client.responses.create(
                model=model,
                instructions=(
                    "Repair the invalid JSON draft into one complete valid JSON object matching the schema exactly. "
                    "Do not omit required fields. Do not add markdown or commentary."
                ),
                input=(
                    f"Schema:\n{schema}\n\n"
                    f"Original task:\n{input_text}\n\n"
                    f"Invalid draft to repair:\n{response.output_text}"
                ),
                reasoning={"effort": effort} if effort else None,
                max_output_tokens=max(max_output_tokens, 4200),
                service_tier=service_tier,
                text={"verbosity": verbosity} if verbosity else None,
            )
            parsed = self._coerce_text_to_model(repair_response.output_text, text_format)
            return parsed, repair_response.id

    def _coerce_text_to_model(self, text: str, text_format: type[ParsedModel]) -> ParsedModel:
        candidates: list[str] = []
        stripped = text.strip()
        if stripped:
            candidates.append(stripped)
        fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL).strip()
        if fenced and fenced not in candidates:
            candidates.append(fenced)
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])
        for candidate in candidates:
            try:
                return text_format.model_validate_json(candidate)
            except Exception:
                try:
                    return text_format.model_validate(json.loads(candidate))
                except Exception:
                    continue
        raise RuntimeError(f"Could not coerce response text into {text_format.__name__}")
