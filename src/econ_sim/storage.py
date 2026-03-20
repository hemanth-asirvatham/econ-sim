from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .models import SetupSessionState, SimulationState


class SimulationStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def simulation_dir(self, simulation_id: str) -> Path:
        path = self.root / simulation_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def setup_sessions_root(self) -> Path:
        path = self.root / "_setup_sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def setup_session_dir(self, setup_session_id: str) -> Path:
        path = self.setup_sessions_root() / setup_session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, simulation_id: str) -> Path:
        return self.simulation_dir(simulation_id) / "simulation.json"

    def setup_session_path(self, setup_session_id: str) -> Path:
        return self.setup_session_dir(setup_session_id) / "setup_session.json"

    def asset_dir(self, simulation_id: str, stage_index: int) -> Path:
        path = self.simulation_dir(simulation_id) / f"stage-{stage_index + 1:02d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def asset_url(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        return f"/assets/{relative.as_posix()}"

    def persona_path(self, simulation_id: str) -> Path:
        return self.simulation_dir(simulation_id) / "personas.csv"

    def poll_dir(self, simulation_id: str, stage_index: int) -> Path:
        path = self.simulation_dir(simulation_id) / "polls" / f"stage-{stage_index + 1:02d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def persona_update_dir(self, simulation_id: str, stage_index: int) -> Path:
        path = self.simulation_dir(simulation_id) / "persona_updates" / f"stage-{stage_index + 1:02d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def save(self, state: SimulationState) -> None:
        async with self._lock:
            self.state_path(state.simulation_id).write_text(
                json.dumps(state.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )

    async def load(self, simulation_id: str) -> SimulationState:
        async with self._lock:
            payload = json.loads(self.state_path(simulation_id).read_text(encoding="utf-8"))
        return SimulationState.model_validate(payload)

    async def exists(self, simulation_id: str) -> bool:
        return self.state_path(simulation_id).exists()

    async def save_setup_session(self, state: SetupSessionState) -> None:
        async with self._lock:
            self.setup_session_path(state.setup_session_id).write_text(
                json.dumps(state.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )

    async def load_setup_session(self, setup_session_id: str) -> SetupSessionState:
        async with self._lock:
            payload = json.loads(self.setup_session_path(setup_session_id).read_text(encoding="utf-8"))
        return SetupSessionState.model_validate(payload)

    async def setup_session_exists(self, setup_session_id: str) -> bool:
        return self.setup_session_path(setup_session_id).exists()

    async def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        async with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
