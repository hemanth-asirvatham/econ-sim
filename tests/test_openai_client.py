from __future__ import annotations

from pydantic import BaseModel

from econ_sim.config import AppSettings
from econ_sim.services.openai_client import OpenAIGateway


class _ExampleStructuredOutput(BaseModel):
    title: str
    count: int


def test_coerce_text_to_model_repairs_nearly_json(tmp_path):
    gateway = OpenAIGateway(AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare())

    parsed = gateway._coerce_text_to_model(
        '{"title": "Cheap Cognition", "count": 3,}',
        _ExampleStructuredOutput,
    )

    assert parsed.title == "Cheap Cognition"
    assert parsed.count == 3


def test_coerce_text_to_model_without_repair_raises(tmp_path):
    gateway = OpenAIGateway(AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare())

    try:
        gateway._coerce_text_to_model(
            '{"title": "Cheap Cognition", "count": 3,}',
            _ExampleStructuredOutput,
            allow_repair=False,
        )
    except RuntimeError as exc:
        assert "Could not coerce response text" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected coercion without repair to fail")
