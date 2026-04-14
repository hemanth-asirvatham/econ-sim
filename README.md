# econ-sim

`econ-sim` is a voice-first political economy simulation for navigating an AGI transition. The backend keeps long-horizon world modeling, Gabriel personas, polling, and stage resolution authoritative; the frontend turns that state into a cinematic room-based experience with an advisor channel, citizen interviews, and a debate/election loop.

## What is implemented

- FastAPI backend with persisted run folders under `runs/<simulation_id>/`
- Gabriel persona seeding via `gabriel.poll(..., questions=[])`
- Per-stage citizen updates via `gabriel.whatever(...)`
- Stage tracking polls and election polls via `gabriel.poll(...)` over the existing personas
- Structured stage orchestration and debate generation through the OpenAI Responses API
- Parallel image and TTS asset generation for narrative briefing beats
- Browser Realtime session setup with ephemeral client secrets for advisor and citizen rooms
- React/Vite frontend with a cinematic briefing room, advisor room, citizen room, and debate room
- Dummy mode for local development without live OpenAI calls

## Architecture

The repo is split along latency boundaries:

- `src/econ_sim/services/orchestrator.py`
  Heavy stage generation and debate writing. This is the master model layer.
- `src/econ_sim/services/gabriel_service.py`
  Persona creation, persona updates, public polling, and metric aggregation.
- `src/econ_sim/services/realtime.py`
  Realtime prompts and tool definitions for low-latency spoken interaction.
- `web/src/hooks/useRealtimeSession.ts`
  Browser WebRTC client for Realtime audio sessions and backend tool-call round trips.

The OpenAI-side design follows current guidance:

- Realtime voice uses browser WebRTC plus short-lived client secrets.
- Long-running stage generation stays off the voice path.
- Structured outputs are used for the orchestrator and debate writer.
- Static prompt prefixes are stable so prompt caching can help on repeated stage generation.

## Local setup

### 1. Python backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### 2. Frontend

```bash
cd web
npm install
cd ..
```

### 3. Environment

Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.

For a non-live local run:

```bash
export ECON_SIM_DUMMY_OPENAI=true
```

## Running the app

Start the API:

```bash
python -m econ_sim.app
```

In another terminal:

```bash
cd web
npm run dev
```

The frontend defaults to `http://localhost:5173` and the API to `http://localhost:8000`.

## Tests

Backend smoke tests:

```bash
pytest
```

Frontend production build:

```bash
cd web
npm run build
```

## Component Lab

For faster iteration on content without waiting for a full browser playthrough, use the component lab harness:

```bash
python scripts/component_lab.py --dummy-openai --stages 2
python scripts/component_lab.py --dummy-openai --json --stages 1
python scripts/component_lab.py --setup "Start five years ahead with AI already embedded in most office work." --stages 2 --reasoning medium
python scripts/component_lab.py --dummy-openai --stages 1 --council-turn "What should we do first?" --continue-beats 2
```

Live runs use the configured OpenAI API by default and write temporary simulation files under `runs/_component_lab/` unless `--runs-dir` is supplied. Use `--dummy-openai` for deterministic smoke checks and `--json` when you want machine-readable output for inspection. Content notes are tasting notes for the reviewer, not pass/fail tests. The `--setup` text is fed through the same natural-language setup path as the opening orchestrator room, so later-world, country, state, or education-board tests should be expressed as normal instructions rather than mode flags.

## Notes

- The repo defaults several non-Realtime models to `gpt-5.4` because that matches the intended sim design. If your project exposes a different GPT-5 variant, override the model names in `.env`.
- The Realtime model defaults to `gpt-realtime-1.5`.
- Dummy mode keeps the entire loop runnable without live API access, including persisted stage files and placeholder briefing art.
