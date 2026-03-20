from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import AppSettings, get_settings
from .models import (
    CouncilTurnRequest,
    ConversationSyncRequest,
    TownHallQuestionRequest,
    QueuePollRequest,
    RealtimeRole,
    RealtimeSessionRequest,
    ResolveStageRequest,
    SetupSessionCreateRequest,
    SetupSessionPatchRequest,
    SetupSessionTurnRequest,
    SpeechSynthesisRequest,
    SimulationCreateRequest,
)
from .services.director import SimulationDirector
from .services.gabriel_service import GabrielService
from .services.openai_client import OpenAIGateway
from .services.orchestrator import OrchestratorService
from .services.realtime import RealtimePromptFactory
from .storage import SimulationStore


def build_director(settings: AppSettings) -> SimulationDirector:
    store = SimulationStore(settings.runs_dir)
    gateway = OpenAIGateway(settings)
    gabriel_service = GabrielService(settings, gateway)
    orchestrator = OrchestratorService(settings, gateway)
    realtime_prompts = RealtimePromptFactory()
    return SimulationDirector(
        settings=settings,
        store=store,
        gateway=gateway,
        gabriel_service=gabriel_service,
        orchestrator=orchestrator,
        realtime_prompts=realtime_prompts,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.director = build_director(settings)
    yield


app = FastAPI(title="econ-sim api", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"ok": "true"}


@app.get("/api/simulations/defaults")
async def simulation_defaults():
    director: SimulationDirector = app.state.director
    return director.build_create_defaults()


@app.get("/api/setup-sessions/defaults")
async def setup_session_defaults():
    director: SimulationDirector = app.state.director
    return director.build_setup_defaults()


@app.post("/api/setup-sessions")
async def create_setup_session(request: SetupSessionCreateRequest | None = None):
    director: SimulationDirector = app.state.director
    return await director.create_setup_session(request)


@app.get("/api/setup-sessions/{setup_session_id}")
async def get_setup_session(setup_session_id: str):
    director: SimulationDirector = app.state.director
    try:
        return await director.get_setup_session(setup_session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc


@app.patch("/api/setup-sessions/{setup_session_id}")
async def patch_setup_session(setup_session_id: str, request: SetupSessionPatchRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.patch_setup_session(setup_session_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc


@app.post("/api/setup-sessions/{setup_session_id}/turn")
async def turn_setup_session(setup_session_id: str, request: SetupSessionTurnRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.turn_setup_session(setup_session_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/setup-sessions/{setup_session_id}/voice-turn")
async def voice_turn_setup_session(setup_session_id: str, audio: UploadFile = File(...)):
    director: SimulationDirector = app.state.director
    try:
        audio_bytes = await audio.read()
        transcript_text = await director.gateway.transcribe_audio(
            audio_bytes=audio_bytes,
            filename=audio.filename or "setup-turn.webm",
            content_type=audio.content_type,
            prompt="Transcribe this short spoken setup instruction for the simulation orchestrator.",
        )
        if not transcript_text.strip():
            raise HTTPException(status_code=400, detail="no speech recognized")
        setup_session = await director.turn_setup_session(
            setup_session_id,
            SetupSessionTurnRequest(text=transcript_text.strip()),
        )
        return {"setup_session": setup_session, "transcript_text": transcript_text.strip()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/setup-sessions/{setup_session_id}/realtime/session")
async def setup_realtime_session(setup_session_id: str):
    director: SimulationDirector = app.state.director
    try:
        return await director.create_setup_realtime_session(setup_session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc


@app.post("/api/setup-sessions/{setup_session_id}/start")
async def start_setup_session(setup_session_id: str, request: SetupSessionPatchRequest | None = None):
    director: SimulationDirector = app.state.director
    try:
        return await director.start_setup_session(setup_session_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="setup session not found") from exc


@app.post("/api/simulations")
async def create_simulation(request: SimulationCreateRequest):
    director: SimulationDirector = app.state.director
    return await director.create_simulation(request)


@app.get("/api/simulations/{simulation_id}")
async def get_simulation(simulation_id: str):
    director: SimulationDirector = app.state.director
    try:
        return await director.get_simulation(simulation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="simulation not found") from exc


@app.post("/api/simulations/{simulation_id}/polls/queue")
async def queue_poll(simulation_id: str, request: QueuePollRequest):
    director: SimulationDirector = app.state.director
    return await director.queue_poll(simulation_id, request)


@app.post("/api/simulations/{simulation_id}/polls/run")
async def run_polls(simulation_id: str):
    director: SimulationDirector = app.state.director
    return await director.run_polls(simulation_id)


@app.post("/api/simulations/{simulation_id}/stage/resolve")
async def resolve_stage(simulation_id: str, request: ResolveStageRequest):
    director: SimulationDirector = app.state.director
    return await director.resolve_stage(simulation_id, request)


@app.post("/api/simulations/{simulation_id}/realtime/session")
async def realtime_session(simulation_id: str, request: RealtimeSessionRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.create_realtime_session(simulation_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/simulations/{simulation_id}/conversation/sync")
async def sync_conversation(simulation_id: str, request: ConversationSyncRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.sync_conversation(simulation_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/simulations/{simulation_id}/advisor/council-turn")
async def advisor_council_turn(simulation_id: str, request: CouncilTurnRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.generate_council_turn(simulation_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/simulations/{simulation_id}/debate/town-hall-question")
async def debate_town_hall_question(simulation_id: str, request: TownHallQuestionRequest):
    director: SimulationDirector = app.state.director
    try:
        return await director.generate_town_hall_question(simulation_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/simulations/{simulation_id}/realtime/{role}/tools/{tool_name}")
async def realtime_tool(simulation_id: str, role: RealtimeRole, tool_name: str, payload: dict):
    director: SimulationDirector = app.state.director
    try:
        return await director.execute_tool(simulation_id, role, tool_name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/audio/speech")
async def synthesize_speech(request: SpeechSynthesisRequest):
    director: SimulationDirector = app.state.director
    audio_bytes = await director.gateway.synthesize_bytes(text=request.text, voice=request.voice)
    return Response(content=audio_bytes, media_type="audio/mpeg")


def mount_static_files(fastapi_app: FastAPI, settings: AppSettings) -> None:
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    fastapi_app.mount("/assets", StaticFiles(directory=str(settings.runs_dir)), name="assets")
    web_dist = Path("web/dist")
    if web_dist.exists():
        fastapi_app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")


mount_static_files(app, get_settings())


def main() -> None:
    import uvicorn

    uvicorn.run("econ_sim.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
