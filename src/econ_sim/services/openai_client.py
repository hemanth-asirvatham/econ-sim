from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from pathlib import Path
from typing import Iterator, TypeVar

from openai import OpenAI
from pydantic import BaseModel
try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional recovery dependency
    repair_json = None

from ..config import AppSettings

ParsedModel = TypeVar("ParsedModel", bound=BaseModel)
REALTIME_TRANSCRIPTION_PROMPT_MAX_CHARS = 1000


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
        use_fallback_repair: bool = True,
    ) -> tuple[ParsedModel, str | None]:
        if not self.live:
            raise RuntimeError("Structured parse is unavailable in dummy mode")

        last_error: Exception | None = None
        effort = self._normalize_reasoning_effort(reasoning_effort)
        service_tier = self._normalize_service_tier(self.settings.service_tier)
        for attempt in range(max_attempts):
            def _call() -> tuple[ParsedModel, str | None]:
                response = self.client.with_options(
                    timeout=self.settings.openai_response_timeout_seconds,
                ).responses.parse(
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
                # Let the OpenAI client timeout own the request lifecycle. Wrapping a
                # blocking SDK call in wait_for cancels only the asyncio waiter, not
                # the worker thread, which can leave duplicate HTTPS reads alive.
                return await asyncio.to_thread(_call)
            except Exception as exc:
                last_error = exc
                effort = "medium" if effort == "high" else effort
        if not use_fallback_repair:
            detail = str(last_error) if last_error else "no parse detail available"
            if max_attempts <= 1:
                raise RuntimeError(f"Structured parse failed on first attempt: {detail}") from last_error
            raise RuntimeError(f"Structured parse failed after {max_attempts} attempts: {detail}") from last_error
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

    async def strict_json(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        text_format: type[ParsedModel],
        schema_text: str | None = None,
        reasoning_effort: str | None = None,
        previous_response_id: str | None = None,
        prompt_cache_key: str | None = None,
        max_output_tokens: int = 2400,
        verbosity: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[ParsedModel, str | None]:
        if not self.live:
            raise RuntimeError("Strict JSON generation is unavailable in dummy mode")

        effort = self._normalize_reasoning_effort(reasoning_effort)
        service_tier = self._normalize_service_tier(self.settings.service_tier)
        schema = schema_text or json.dumps(text_format.model_json_schema(), ensure_ascii=True)
        timeout = timeout_seconds or self.settings.openai_response_timeout_seconds

        def _call() -> tuple[ParsedModel, str | None]:
            response = self.client.with_options(
                timeout=timeout,
            ).responses.create(
                model=model,
                instructions=(
                    f"{instructions}\n\n"
                    "Return only one valid JSON object that matches the schema exactly. "
                    "No markdown. No prose outside the JSON object."
                ),
                input=f"{input_text}\n\nSchema:\n{schema}",
                reasoning={"effort": effort} if effort else None,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                max_output_tokens=max_output_tokens,
                service_tier=service_tier,
                text={"verbosity": verbosity} if verbosity else None,
            )
            output_text = response.output_text or ""
            if getattr(response, "status", None) != "completed":
                raise RuntimeError(
                    f"response status={response.status} incomplete={response.incomplete_details} "
                    f"text_len={len(output_text)} preview={output_text[:320]!r}"
                )
            try:
                parsed = self._coerce_text_to_model(
                    output_text,
                    text_format,
                    allow_repair=False,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"response status={response.status} incomplete={response.incomplete_details} "
                    f"text_len={len(output_text)} preview={output_text[:320]!r}"
                ) from exc
            return parsed, response.id

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            raise RuntimeError(f"Strict JSON generation failed on first attempt: {exc}") from exc

    async def render_image(self, *, prompt: str, output_path: Path) -> None:
        if not self.live:
            digest = base64.b16encode(prompt.encode("utf-8", errors="ignore"))[:24].decode("ascii").lower()
            hue_a = f"#{(digest + '8f6b45')[:6]}"
            hue_b = f"#{(digest[6:] + '2f241d')[:6]}"
            hue_c = f"#{(digest[12:] + 'd8b46e')[:6]}"
            svg = (
                "<svg xmlns='http://www.w3.org/2000/svg' width='1536' height='1024' viewBox='0 0 1536 1024'>"
                "<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>"
                "<stop stop-color='#21160f' offset='0'/>"
                f"<stop stop-color='{hue_a}' offset='0.55'/>"
                "<stop stop-color='#d9c09a' offset='1'/>"
                "</linearGradient>"
                "<filter id='soft'><feGaussianBlur stdDeviation='18'/></filter></defs>"
                "<rect width='1536' height='1024' fill='url(#g)'/>"
                f"<path d='M0 706 C236 624 432 656 656 718 C928 793 1148 750 1536 650 L1536 1024 L0 1024 Z' fill='{hue_b}' opacity='0.45'/>"
                f"<circle cx='302' cy='280' r='210' fill='{hue_c}' opacity='0.22' filter='url(#soft)'/>"
                "<circle cx='1204' cy='224' r='184' fill='#f0d594' opacity='0.18' filter='url(#soft)'/>"
                f"<rect x='170' y='472' width='372' height='176' rx='34' fill='{hue_c}' opacity='0.24' transform='rotate(-6 356 560)'/>"
                "<rect x='620' y='352' width='360' height='280' rx='42' fill='#ead0a4' opacity='0.18'/>"
                f"<rect x='1038' y='438' width='310' height='190' rx='34' fill='{hue_b}' opacity='0.22' transform='rotate(7 1192 532)'/>"
                "<path d='M0 770 C280 690 540 742 786 782 C1032 822 1230 808 1536 724' stroke='#f4d58e' stroke-width='28' opacity='0.18' fill='none'/>"
                "<path d='M88 260 C286 196 426 216 612 258 C848 312 1016 286 1258 204' stroke='#ffffff' stroke-width='20' opacity='0.08' fill='none'/>"
                "</svg>"
            )
            output_path.write_text(svg, encoding="utf-8")
            return

        def _call() -> None:
            output_format = (self.settings.image_output_format or "png").lower()
            if output_format == "jpg":
                output_format = "jpeg"
            generate_kwargs = {
                "model": self.settings.image_model,
                "prompt": prompt,
                "size": self.settings.image_size,
                "quality": self.settings.image_quality,
                "output_format": output_format,
            }
            if output_format in {"jpeg", "webp"}:
                generate_kwargs["output_compression"] = self.settings.image_output_compression
            response = self.client.with_options(timeout=self.settings.image_timeout_seconds).images.generate(
                **generate_kwargs,
            )
            first = response.data[0].model_dump()
            image_payload = first.get("b64_json")
            if image_payload:
                output_path.write_bytes(base64.b64decode(image_payload))
                return
            image_url = first.get("url")
            if image_url:
                import httpx

                image_response = httpx.get(image_url, timeout=self.settings.image_timeout_seconds)
                image_response.raise_for_status()
                output_path.write_bytes(image_response.content)
                return
            raise RuntimeError("Image generation returned neither b64_json nor url")

        await self._run_with_retries(_call, attempts=max(1, self.settings.image_max_attempts))

    async def synthesize(self, *, text: str, output_path: Path) -> None:
        if not self.live:
            return

        def _call() -> bytes:
            service_tier = self._normalize_service_tier(self.settings.service_tier)
            kwargs = {
                "model": self.settings.speech_model,
                "voice": self.settings.narration_voice,
                "input": text,
                "response_format": "mp3",
            }
            if service_tier:
                kwargs["service_tier"] = service_tier
            try:
                audio = self.client.audio.speech.create(**kwargs)
            except TypeError as exc:
                if "service_tier" not in str(exc):
                    raise
                kwargs.pop("service_tier", None)
                audio = self.client.audio.speech.create(**kwargs)
            return audio.read()

        output_path.write_bytes(await self._run_with_retries(_call))

    async def synthesize_bytes(self, *, text: str, voice: str | None = None) -> bytes:
        if not self.live:
            return b""

        def _call() -> bytes:
            service_tier = self._normalize_service_tier(self.settings.service_tier)
            kwargs = {
                "model": self.settings.speech_model,
                "voice": voice or self.settings.narration_voice,
                "input": text,
                "response_format": "mp3",
            }
            if service_tier:
                kwargs["service_tier"] = service_tier
            try:
                audio = self.client.audio.speech.create(**kwargs)
            except TypeError as exc:
                if "service_tier" not in str(exc):
                    raise
                kwargs.pop("service_tier", None)
                audio = self.client.audio.speech.create(**kwargs)
            return audio.read()

        return await self._run_with_retries(_call)

    def synthesize_stream(
        self,
        *,
        text: str,
        voice: str | None = None,
        response_format: str = "mp3",
        chunk_size: int = 4096,
    ) -> Iterator[bytes]:
        if not self.live:
            yield b""
            return

        def _iter_chunks() -> Iterator[bytes]:
            service_tier = self._normalize_service_tier(self.settings.service_tier)
            kwargs = {
                "model": self.settings.speech_model,
                "voice": voice or self.settings.narration_voice,
                "input": text,
                "response_format": response_format,
                "stream_format": "audio",
            }
            if service_tier:
                kwargs["service_tier"] = service_tier
            def yield_chunks(request_kwargs: dict) -> Iterator[bytes]:
                response_cm = self.client.with_options(timeout=20.0).audio.speech.with_streaming_response.create(**request_kwargs)
                with response_cm as response:
                    for chunk in response.iter_bytes(chunk_size):
                        if chunk:
                            yield chunk

            try:
                yield from yield_chunks(kwargs)
            except TypeError as exc:
                if "service_tier" not in str(exc):
                    raise
                kwargs.pop("service_tier", None)
                yield from yield_chunks(kwargs)

        yield from _iter_chunks()

    async def generate_chat_audio_reply(
        self,
        *,
        instructions: str,
        input_text: str,
        voice: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        max_completion_tokens: int = 140,
        include_audio: bool = True,
    ) -> tuple[str, bytes | None, str | None]:
        if not self.live:
            return input_text.strip(), None, None

        effort = self._normalize_reasoning_effort(reasoning_effort)
        service_tier = self._normalize_service_tier(self.settings.service_tier)

        def _call() -> tuple[str, bytes | None, str | None]:
            kwargs = {
                "model": model or self.settings.council_audio_model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": input_text},
                ],
                "modalities": ["text", "audio"] if include_audio else ["text"],
                "audio": {"voice": voice, "format": "mp3"} if include_audio else None,
                "prompt_cache_key": prompt_cache_key,
                "service_tier": service_tier,
                "max_completion_tokens": max_completion_tokens,
                "store": False,
            }
            if effort and not str(kwargs["model"]).startswith("gpt-audio"):
                kwargs["reasoning_effort"] = effort
            completion = self.client.chat.completions.create(**kwargs)
            message = completion.choices[0].message
            transcript = ""
            audio_bytes: bytes | None = None
            audio_format: str | None = None
            if getattr(message, "audio", None) is not None:
                transcript = str(message.audio.transcript or "").strip()
                if message.audio.data:
                    audio_bytes = base64.b64decode(message.audio.data)
                    audio_format = "mp3"
            if not transcript:
                transcript = str(message.content or "").strip()
            return transcript, audio_bytes, audio_format

        return await self._run_with_retries(_call)

    async def _run_with_retries(self, fn, *, attempts: int = 3):
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await asyncio.to_thread(fn)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._is_retryable_transport_error(exc):
                    raise
                await asyncio.sleep(0.8 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _is_retryable_transport_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "broken pipe",
                "connection",
                "timeout",
                "timed out",
                "temporarily unavailable",
                "transport",
                "stream closed",
                "connection reset",
            )
        )

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
        capture_only: bool = False,
        capture_prompt: str | None = None,
    ) -> tuple[str, str]:
        if not self.live:
            return "dummy-client-secret", model or self.settings.realtime_model

        def _build_payload() -> tuple[dict, str]:
            selected_model = model or self.settings.realtime_model
            selected_voice = voice or self.settings.realtime_voice
            if capture_only:
                transcription_prompt = self._realtime_transcription_prompt(capture_prompt)
                session_payload = {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "noise_reduction": {"type": "near_field"},
                            "transcription": {
                                "model": self.settings.realtime_input_transcription_model,
                                "prompt": transcription_prompt,
                            },
                            "turn_detection": self._capture_turn_detection(),
                        },
                    },
                }
                return session_payload, selected_model
            session_payload = {
                "type": "realtime",
                "model": selected_model,
                "instructions": instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {"model": self.settings.realtime_input_transcription_model},
                        "turn_detection": self._conversation_turn_detection(create_response),
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": selected_voice,
                        "speed": 1.08,
                    },
                },
                "tools": tools,
                "tool_choice": "auto",
            }
            reasoning = self._realtime_reasoning_payload(selected_model)
            if reasoning:
                session_payload["reasoning"] = reasoning
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

    def _realtime_transcription_prompt(self, prompt: str | None) -> str:
        text = (
            prompt
            or (
                "Transcribe only the live player's speech in this strategy room. "
                "Ignore synthetic playback, narration, and other room voices. "
                "Return only the player's words."
            )
        )
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= REALTIME_TRANSCRIPTION_PROMPT_MAX_CHARS:
            return text
        clipped = text[:REALTIME_TRANSCRIPTION_PROMPT_MAX_CHARS].rsplit(" ", 1)[0].strip()
        return clipped.rstrip(" ,;:") + "."

    def _normalize_reasoning_effort(self, effort: str | None) -> str | None:
        if effort is None:
            return None
        normalized = effort.strip().lower()
        return None if normalized in {"", "none"} else normalized

    def _realtime_reasoning_payload(self, model: str) -> dict[str, str] | None:
        effort = self._normalize_reasoning_effort(self.settings.realtime_reasoning_effort)
        if not effort:
            return None
        normalized_model = model.strip().lower()
        if (
            normalized_model.startswith("gpt-realtime-alpha-dolphin")
            or normalized_model in {"gpt-realtime-2", "gpt-realtime-2.0"}
        ):
            return {"effort": effort}
        return None

    def _normalize_service_tier(self, tier: str | None) -> str | None:
        if tier is None:
            return None
        normalized = tier.strip().lower()
        return None if normalized in {"", "none"} else normalized

    def _conversation_turn_detection(self, create_response: bool) -> dict[str, str | bool]:
        return {
            "type": "semantic_vad",
            "eagerness": self.settings.realtime_semantic_vad_eagerness,
            "create_response": create_response,
            "interrupt_response": True,
        }

    def _capture_turn_detection(self) -> dict[str, str | float | int]:
        return {
            "type": "server_vad",
            "create_response": False,
            "interrupt_response": False,
            "threshold": self.settings.realtime_capture_vad_threshold,
            "prefix_padding_ms": self.settings.realtime_capture_vad_prefix_padding_ms,
            "silence_duration_ms": self.settings.realtime_capture_vad_silence_duration_ms,
        }

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
        response = self.client.with_options(
            timeout=self.settings.openai_response_timeout_seconds,
        ).responses.create(
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
            repair_response = self.client.with_options(
                timeout=self.settings.openai_response_timeout_seconds,
            ).responses.create(
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

    def _coerce_text_to_model(
        self,
        text: str,
        text_format: type[ParsedModel],
        *,
        allow_repair: bool = True,
    ) -> ParsedModel:
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
        if allow_repair:
            for candidate in list(candidates):
                repaired_candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                if repaired_candidate != candidate and repaired_candidate not in candidates:
                    candidates.append(repaired_candidate)
        for candidate in candidates:
            try:
                return text_format.model_validate_json(candidate)
            except Exception:
                try:
                    return text_format.model_validate(json.loads(candidate))
                except Exception:
                    if not allow_repair or repair_json is None:
                        continue
                    try:
                        repaired = repair_json(candidate, return_objects=True)
                        return text_format.model_validate(repaired)
                    except Exception:
                        continue
        raise RuntimeError(f"Could not coerce response text into {text_format.__name__}")
