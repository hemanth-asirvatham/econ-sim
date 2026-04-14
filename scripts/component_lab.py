#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from econ_sim.app import build_director
from econ_sim.config import AppSettings
from econ_sim.models import (
    CouncilTurnRequest,
    ConversationTurn,
    PreparationPhase,
    SetupSessionCreateRequest,
    SetupSessionTurnRequest,
    SimulationStatus,
    SimulationState,
    StagePackage,
    StageProgress,
    new_id,
)


def _stage_payload(stage: StagePackage, *, include_featurettes: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "index": stage.index + 1,
        "phase_label": stage.phase_label,
        "year_label": stage.year_label,
        "title": stage.title,
        "montage_logline": stage.montage_logline,
        "world_brief": stage.world_brief,
        "macro_stats": {
            key: stat.model_dump(mode="json")
            for key, stat in stage.macro_stats.items()
        },
        "room_briefing": stage.room_briefing,
        "narrative_beats": [
            {
                "line": beat.line,
                "image_prompt": beat.image_prompt,
            }
            for beat in stage.narrative_beats
        ],
    }
    if include_featurettes:
        payload["featurettes"] = [
            {
                "subject": featurette.subject,
                "question": featurette.question,
                "title": featurette.title,
                "logline": featurette.logline,
                "narrative_beats": [
                    {
                        "line": beat.line,
                        "image_prompt": beat.image_prompt,
                    }
                    for beat in featurette.narrative_beats
                ],
            }
            for featurette in stage.featurettes
        ]
    return payload


def _print_stage(stage: StagePackage, *, include_featurettes: bool) -> None:
    print(f"\n=== Stage {stage.index + 1}: {stage.title} ===")
    print(f"{stage.phase_label} | {stage.year_label}")
    print(stage.montage_logline)
    print("\n[world brief]")
    print(stage.world_brief)
    if stage.macro_stats:
        print("\n[macro stats]")
        for stat in stage.macro_stats.values():
            detail = f" — {stat.detail}" if stat.detail else ""
            print(f"- {stat.label}: {stat.value}{detail}")
    print("\n[room briefing]")
    print(stage.room_briefing)
    print("\n[narrative beats]")
    for index, beat in enumerate(stage.narrative_beats, start=1):
        print(f"{index}. {beat.line}")
    if include_featurettes:
        print("\n[featurettes]")
        for featurette in stage.featurettes:
            print(f"* {featurette.title} — {featurette.question}")
            print(f"  {featurette.logline}")
            for beat in featurette.narrative_beats:
                print(f"  - {beat.line}")


def _print_council_turn(*, beat_index: int, request_text: str | None, response) -> None:
    label = f"Council beat {beat_index}"
    if request_text is not None:
        print(f"\n=== {label}: player ===")
        print(request_text)
    else:
        print(f"\n=== {label}: continuation ===")
    print(f"[floor] {response.lead}")
    if response.reason:
        print(f"[why] {response.reason}")
    if response.board_notes:
        print("[board]")
        for note in response.board_notes:
            print(f"- {note}")
    if response.turns:
        for turn in response.turns:
            print(f"[{turn.speaker_name or 'advisor'}] {turn.text}")
    else:
        print("[room] yields to player")


def _empty_state(director, config) -> SimulationState:
    simulation_id = new_id("lab")
    return SimulationState(
        simulation_id=simulation_id,
        incumbent_name=config.player_name,
        config=config,
        standard_questions=director.gabriel_service.standard_questions(
            config.player_name,
            config.opponent_name,
        ),
        progress=StageProgress(
            phase=PreparationPhase.stagewriting,
            label="Component lab",
            detail="Composing isolated orchestrator output.",
            percent=1,
        ),
    )


async def _build_director(args: argparse.Namespace):
    runs_dir = Path(args.runs_dir or ROOT / "runs" / "_component_lab")
    settings = AppSettings(
        runs_dir=runs_dir,
        dummy_openai=args.dummy_openai,
        default_persona_count=args.persona_count,
        max_stage_count=max(args.stages, args.stage_count),
        orchestrator_reasoning_effort=args.reasoning,
    ).prepare()
    return build_director(settings)


async def _config_from_setup(director, args: argparse.Namespace):
    session = await director.create_setup_session(
        SetupSessionCreateRequest(
            persona_count=args.persona_count,
            stage_count=max(args.stages, args.stage_count),
            orchestrator_reasoning_effort=args.reasoning,
        )
    )
    if args.setup.strip():
        session = await director.turn_setup_session(
            session.setup_session_id,
            SetupSessionTurnRequest(text=args.setup.strip()),
        )
    config = session.config.model_copy(
        update={"stage_count": max(args.stages, session.config.stage_count)}
    )
    return config, session


async def _compose_stages(
    director,
    state: SimulationState,
    count: int,
    *,
    include_featurettes: bool,
    stream_output: bool,
) -> list[StagePackage]:
    stages: list[StagePackage] = []
    for stage_index in range(count):
        if stream_output:
            print(f"\n>>> composing stage {stage_index + 1}/{count}", flush=True)
        state.active_stage_index = stage_index
        previous_stage = stages[-1] if stages else None
        tracking = previous_stage.tracking if previous_stage else None
        prior_polls = previous_stage.poll_summaries if previous_stage else []
        stage = await director.orchestrator.compose_stage(
            state=state,
            previous_stage=previous_stage,
            tracking=tracking,
            poll_summaries=prior_polls,
            queued_poll_questions=[],
        )
        if include_featurettes:
            stage.featurettes = await director.orchestrator.compose_stage_featurettes(
                state=state,
                stage=stage,
            )
        stages.append(stage)
        state.stages = list(stages)
        if stream_output:
            _print_stage(stage, include_featurettes=include_featurettes)
    return stages


async def _run_council_lab(
    director,
    state: SimulationState,
    stages: list[StagePackage],
    *,
    stage_index: int,
    council_turns: list[str],
    continue_beats: int,
    stream_output: bool,
) -> list[dict[str, Any]]:
    if not council_turns:
        return []
    bounded_stage_index = min(max(stage_index, 0), len(stages) - 1)
    state.active_stage_index = bounded_stage_index
    state.status = SimulationStatus.stage_ready
    state.stages = list(stages)
    await director.store.save(state)
    transcript: list[dict[str, Any]] = []
    beat_counter = 0
    for prompt in council_turns:
        beat_counter += 1
        response = await director.generate_council_turn(
            state.simulation_id,
            CouncilTurnRequest(text=prompt, mode="text", continue_dialogue=False),
        )
        transcript.append(
            {
                "kind": "player_turn",
                "prompt": prompt,
                "lead": response.lead,
                "reason": response.reason,
                "board_notes": response.board_notes,
                "turns": [turn.model_dump(mode="json") for turn in response.turns],
                "yield_after_turn": response.yield_after_turn,
            }
        )
        if stream_output:
            _print_council_turn(beat_index=beat_counter, request_text=prompt, response=response)
        state = response.simulation
        for _ in range(max(continue_beats, 0)):
            beat_counter += 1
            continued = await director.generate_council_turn(
                state.simulation_id,
                CouncilTurnRequest(text="", mode="text", continue_dialogue=True),
            )
            transcript.append(
                {
                    "kind": "continuation",
                    "lead": continued.lead,
                    "reason": continued.reason,
                    "board_notes": continued.board_notes,
                    "turns": [turn.model_dump(mode="json") for turn in continued.turns],
                    "yield_after_turn": continued.yield_after_turn,
                }
            )
            if stream_output:
                _print_council_turn(beat_index=beat_counter, request_text=None, response=continued)
            state = continued.simulation
            if continued.yield_after_turn:
                break
    return transcript


async def _run(args: argparse.Namespace) -> int:
    director = await _build_director(args)
    config, session = await _config_from_setup(director, args)
    state = _empty_state(director, config)
    stages = await _compose_stages(
        director,
        state,
        args.stages,
        include_featurettes=args.featurettes,
        stream_output=not args.json,
    )
    council_stage_index = args.council_stage - 1 if args.council_stage > 0 else len(stages) - 1
    council_transcript = await _run_council_lab(
        director,
        state,
        stages,
        stage_index=council_stage_index,
        council_turns=args.council_turn,
        continue_beats=args.continue_beats,
        stream_output=not args.json,
    )

    payload = {
        "setup": {
            "prompt": args.setup.strip(),
            "guidance_reply": session.guidance.chamber_reply if session.guidance else "",
            "applied_updates": session.guidance.applied_updates if session.guidance else [],
            "config": config.model_dump(mode="json"),
        },
        "stages": [
            _stage_payload(stage, include_featurettes=args.featurettes)
            for stage in stages
        ],
        "council": council_transcript,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("=== Setup ===")
    if args.setup.strip():
        print(args.setup.strip())
    else:
        print("(default setup)")
    if session.guidance:
        print(f"\n[setup reply]\n{session.guidance.chamber_reply}")
        if session.guidance.applied_updates:
            print("\n[applied updates]")
            for item in session.guidance.applied_updates:
                print(f"- {item}")

    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compose isolated stage outputs for manual tasting without running the full GUI."
    )
    parser.add_argument(
        "--setup",
        default="",
        help="Natural-language setup guidance to feed through the setup chamber before composing stages.",
    )
    parser.add_argument(
        "--stages",
        type=int,
        default=1,
        help="How many stages to compose in sequence.",
    )
    parser.add_argument(
        "--stage-count",
        type=int,
        default=5,
        help="Simulation stage count baked into the config for pacing context.",
    )
    parser.add_argument(
        "--persona-count",
        type=int,
        default=48,
        help="Persona count to bake into the setup config.",
    )
    parser.add_argument(
        "--reasoning",
        default="medium",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        help="Reasoning effort for orchestrator calls.",
    )
    parser.add_argument(
        "--featurettes",
        action="store_true",
        help="Also generate the optional side reels for each composed stage.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of the human-readable report.",
    )
    parser.add_argument(
        "--runs-dir",
        default="",
        help="Optional override for the runs directory used by the temporary director.",
    )
    parser.add_argument(
        "--dummy-openai",
        action="store_true",
        help="Use dummy content instead of live model calls.",
    )
    parser.add_argument(
        "--council-turn",
        action="append",
        default=[],
        help="Optional council prompt to taste the multi-advisor room after composing stages. May be repeated.",
    )
    parser.add_argument(
        "--continue-beats",
        type=int,
        default=0,
        help="How many continuation council beats to request after each council turn.",
    )
    parser.add_argument(
        "--council-stage",
        type=int,
        default=0,
        help="1-based stage number to use for council tasting; defaults to the last composed stage.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
