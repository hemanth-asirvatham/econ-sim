from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from econ_sim.config import AppSettings
from econ_sim.models import NarrativeBeat, StagePackage, StageTracking, TrackingMetric
from econ_sim.services.gabriel_service import GabrielService

@pytest.mark.asyncio
async def test_prepare_poll_question_keeps_one_sentence_requests(tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)

    prepared = await service.prepare_poll_question("What do people say in one sentence about whether AI is helping them?")

    assert prepared.lower().startswith("in one sentence")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Give me a brief reaction on whether people think AI is helping at work.",
        "Why are people upset about AI in one sentence?",
        "What do people say as a short quote about the jobs picture?",
    ],
)
async def test_prepare_poll_question_preserves_open_ended_qualitative_requests(prompt: str, tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)

    prepared = await service.prepare_poll_question(prompt)

    assert any(keyword in prepared.lower() for keyword in ("one sentence", "brief reaction", "short quote", "why"))


@pytest.mark.asyncio
async def test_update_personas_prompt_requests_spoken_first_person_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AppSettings(dummy_openai=False, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    personas = pd.DataFrame(
        {
            "seed_id": ["seed-001"],
            "seed": ["teacher in Ohio"],
            "persona": ["Melissa is a public school teacher trying to keep up with AI paperwork shifts."],
            "baseline_ai_instinct": ["cautiously curious"],
            "baseline_priority": ["income and job ladder"],
        }
    )
    recorded: dict[str, str] = {}

    async def fake_whatever(**kwargs):
        recorded["prompt"] = kwargs["df"]["prompt"].iloc[0]
        return pd.DataFrame(
            {
                "seed_id": ["seed-001"],
                "Response JSON": [
                    {
                        "display_name": "Melissa Anne Whitaker",
                        "role": "Public high school English teacher",
                        "region": "Portland, Oregon",
                        "mood": "wary",
                        "ai_exposure": "high",
                        "support_score": 53,
                        "summary": "Public high school English teacher in Portland trying to keep lesson prep manageable while AI changes the boring parts of the job faster than staffing or trust can catch up.",
                        "current_update": "The paperwork side of school got faster again this year, so now I spend more time checking AI-generated drafts than writing everything from scratch. That does save me hours, but it also means the district acts like fewer support staff are fine even when parents still need real people. The administrators and the best-resourced schools seem to be adapting first, and it makes me judge the incumbent on whether ordinary classrooms get any real protection.",
                    }
                ],
            }
        )

    class FakeGabrielModule:
        async def whatever(self, **kwargs):
            return await fake_whatever(**kwargs)

    monkeypatch.setattr("econ_sim.services.gabriel_service._gabriel", lambda: FakeGabrielModule())
    stage = _stub_stage("Cognitive Automation Surge")

    updated = await service.update_personas_for_stage(
        personas=personas,
        stage=stage,
        incumbent_name="President Morgan Hale",
        player_name="President Morgan Hale",
        opponent_name="Governor Elena Cross",
        save_dir=tmp_path / "persona-updates",
    )

    assert "current_update must usually be 1 sentence or 2 clipped first-person sentences" in recorded["prompt"]
    assert "pick at most 2 of these" in recorded["prompt"]
    assert "sound like something this person would actually say out loud" in recorded["prompt"]
    assert "avoid policy jargon, consultant phrasing, slogans, or tidy both-sides wrapups" in recorded["prompt"]
    assert "do not mention the incumbent, player, or opponent by name" in recorded["prompt"]
    assert "preserve a real spread" in recorded["prompt"]
    assert "do not let the population collapse into one repeating office-work story" in recorded["prompt"]
    assert "if AI is not the first thing this person would say, let it stay in the background or go unnamed" in recorded["prompt"]
    assert "do not force every update to explain AI directly" in recorded["prompt"]
    assert "do not lean on paperwork, queue relief, admin hassle, or office cleanup" in recorded["prompt"]
    assert "let some people first talk about feeling more capable, less dependent on scarce experts" in recorded["prompt"]
    assert "if the best update is mostly good news, let it be mostly good news" in recorded["prompt"]
    assert "if the best update is mostly indirect, let it stay indirect" in recorded["prompt"]
    assert "town_hall_question must be the one direct question this person would ask a candidate" in recorded["prompt"]
    assert "town_hall_cue should be one short backstage note" in recorded["prompt"]
    assert updated.loc[0, "display_name"] == "Melissa Anne Whitaker"


@pytest.mark.asyncio
async def test_ensure_personas_repairs_missing_seed_and_persona_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AppSettings(dummy_openai=False, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)

    async def fake_poll(**kwargs):
        return pd.DataFrame(
            {
                "seed_id": ["seed-001", "", "seed-003"],
                "seed": ["Teacher in Ohio", "", ""],
                "persona": ["Melissa is a teacher in Ohio.", "", None],
            }
        )

    class FakeGabrielModule:
        async def poll(self, **kwargs):
            return await fake_poll(**kwargs)

    async def fake_calibrate(personas: pd.DataFrame, save_dir: Path):
        out = personas.copy()
        out["voice"] = "marin"
        return out

    monkeypatch.setattr("econ_sim.services.gabriel_service._gabriel", lambda: FakeGabrielModule())
    monkeypatch.setattr(service, "_calibrate_personas", fake_calibrate)

    personas = await service.ensure_personas(
        simulation_id="sim-test",
        population_description="A representative sample of U.S. adults.",
        persona_count=4,
        save_dir=tmp_path / "personas",
    )

    assert len(personas) == 4
    assert personas["seed"].astype(str).str.strip().all()
    assert personas["persona"].astype(str).str.strip().all()
    assert personas["seed_id"].astype(str).str.strip().all()
    assert "Population frame:" in personas.iloc[-1]["persona"]


@pytest.mark.asyncio
async def test_run_tracking_polls_uses_gabriel_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=False, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    question_map = {
        spec.key: spec.question
        for spec in service._core_question_specs("President Morgan Hale", "Governor Elena Cross")
    }
    personas = pd.DataFrame(
        {
            "seed_id": ["seed-001", "seed-002", "seed-003"],
            "seed": ["teacher in Ohio", "nurse in Arizona", "owner in Texas"],
            "voice": ["marin", "sage", "cedar"],
            "display_name": ["Melissa Anne Whitaker", "Daniel Ross", "Priya Raman"],
            "role": ["Public high school English teacher", "Nurse practitioner", "Small manufacturer"],
            "region": ["Portland, Oregon", "Phoenix, Arizona", "Dallas, Texas"],
            "current_update": [
                "Melissa likes that AI now clears paperwork and parent emails faster.",
                "Daniel trusts the convenience but worries about uneven hospital staffing.",
                "Priya sees faster design cycles and more pressure from foreign rivals.",
            ],
        }
    )
    recorded: dict[str, object] = {}

    async def fake_poll(**kwargs):
        recorded.update(kwargs)
        df = kwargs["df"].copy()
        answers = {
            question_map["approval"]: [
                "somewhat approve",
                "mixed",
                "strongly approve",
            ],
            question_map["vote"]: [
                "President Morgan Hale",
                "Governor Elena Cross",
                "President Morgan Hale",
            ],
            question_map["vote_reason"]: [
                "He seems more willing to keep useful AI tools available.",
                "I do not trust either side enough yet to commit.",
                "She feels steadier on wages and local jobs.",
            ],
            question_map["better_off"]: [
                "somewhat better off",
                "about the same",
                "much better off",
            ],
            question_map["ai_comfort"]: [
                "very comfortable",
                "somewhat comfortable",
                "mixed",
            ],
            question_map["daily_role"]: [
                "a useful convenience",
                "a better work tool",
                "a chance to get ahead",
            ],
            question_map["capability_read"]: [
                "routine computer work",
                "useful expert guidance",
                "much of the office backlog",
            ],
            question_map["trusted_task"]: [
                "I trust it to clear the routine paperwork triage before I even look at it.",
                "I trust it to sort the routine patient messages and flag what needs a human now.",
                "I trust it to turn a rough design brief into a usable first draft.",
            ],
            question_map["still_human"]: [
                "I still want a real person deciding anything disciplinary or emotionally delicate at school.",
                "I still would not trust it to tell a family bad news or override a nurse on the floor.",
                "I still want a human signing off on final bids when the relationship really matters.",
            ],
            question_map["job_worry"]: [
                "slightly worried",
                "worried",
                "mixed",
            ],
            question_map["gov_trust"]: [
                "some trust",
                "mixed",
                "high trust",
            ],
            question_map["public_stability"]: [
                "somewhat better",
                "mixed",
                "somewhat more strained",
            ],
            question_map["main_pressure"]: [
                "job loss",
                "prices",
                "housing",
            ],
            question_map["ai_gain"]: [
                "School paperwork is quicker and I get more time with students.",
                "Triage support catches routine issues before they swamp the floor.",
                "Design tools let my shop quote and revise jobs much faster.",
            ],
            question_map["newly_normal"]: [
                "Parents now expect the school systems to answer routine questions almost instantly.",
                "Patients assume triage starts before a nurse has even opened the chart.",
                "Customers expect a first draft quote before I have finished my coffee.",
            ],
            question_map["keep_change"]: [
                "I want to keep the paperwork help because it gives me time back.",
                "I would keep the triage support because it makes my day less chaotic.",
                "I want to keep the design copilots because they let my team move faster.",
            ],
            question_map["barely_notice"]: [
                "I still barely notice AI once I am actually in the classroom with students.",
                "I barely notice it when the shift gets physical and the floor gets crowded.",
                "I still barely notice it when the actual fabrication work starts.",
            ],
            question_map["life_touchpoint"]: [
                "paperwork and admin help",
                "medical or care coordination",
                "work tasks",
            ],
            question_map["pace_read"]: [
                "about right",
                "a bit too fast",
                "too slow to deliver the gains",
            ],
            question_map["biggest_worry"]: [
                "job loss",
                "loss of human control",
                "other countries pulling ahead",
            ],
            question_map["econ_read"]: [
                "mixed but functioning",
                "split between winners and losers",
                "stronger",
            ],
            question_map["service_reliability"]: [
                "more reliable",
                "faster but less trusted",
                "cheaper but more confusing",
            ],
            question_map["household_security"]: [
                "somewhat secure",
                "mixed",
                "very secure",
            ],
            question_map["fairness"]: [
                "The biggest firms are capturing the gains before schools and towns catch up.",
                "You can feel the convenience, but the people losing status are carrying more of the uncertainty.",
                "Small businesses still pay the bottleneck costs while giants scale faster.",
            ],
            question_map["next_two_years"]: [
                "large firms",
                "skilled professionals",
                "ordinary households",
            ],
            "In one sentence, what happened this week that made AI feel different in your work, bills, errands, or family life?": [
                "School paperwork is cheaper and faster, but teaching itself still feels human.",
                "Hospital triage is quicker, though staffing feels less predictable.",
                "Manufacturing design cycles are faster than the local training pipeline.",
            ],
            "Which service feels least reliable right now?": [
                "local schools",
                "permitting and public paperwork",
                "entry-level hiring",
            ],
            "Choose one: AI now feels most like cheaper and faster services, stronger tools in your own work, more abundant expert help, more power for big firms, or still too uneven to judge.": [
                "cheaper and faster services",
                "more abundant expert help",
                "stronger tools in your own work",
            ],
        }
        for question in kwargs["questions"]:
            df[question] = answers.get(question, ["mixed", "mixed", "mixed"])
        return df

    class FakeGabrielModule:
        async def poll(self, **kwargs):
            return await fake_poll(**kwargs)

    monkeypatch.setattr("econ_sim.services.gabriel_service._gabriel", lambda: FakeGabrielModule())
    stage = _stub_stage("Cognitive Automation Surge")

    _, summaries, tracking = await service.run_tracking_polls(
        personas=personas,
        stage_index=0,
        stage=stage,
        player_name="President Morgan Hale",
        opponent_name="Governor Elena Cross",
        save_dir=tmp_path / "polls",
        extra_questions=["Which service feels least reliable right now?"],
    )

    assert recorded["column_name"] == "seed"
    assert recorded["model"] == settings.poll_model
    assert recorded["reasoning_effort"] == settings.poll_reasoning_effort
    assert recorded["n_questions_per_run"] == settings.poll_questions_per_run
    assert "Which service feels least reliable right now?" in recorded["questions"]
    assert any("what happened this week" in question.lower() for question in recorded["questions"])


@pytest.mark.asyncio
async def test_ensure_personas_recovers_from_partial_blank_persona_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AppSettings(dummy_openai=False, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    save_dir = tmp_path / "seed-pass"
    save_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "seed_id": ["entity-00000", "entity-00001"],
            "seed": ["A teacher in Ohio", "A warehouse worker in Nevada"],
            "persona": ["", "Detailed second persona"],
            "voice": ["marin", "cedar"],
        }
    ).to_csv(save_dir / "poll_personas.csv", index=False)

    class FakeGabrielModule:
        async def poll(self, **kwargs):
            raise ValueError("Column 'persona' contains empty values at row(s): 0")

    monkeypatch.setattr("econ_sim.services.gabriel_service._gabriel", lambda: FakeGabrielModule())

    result = await service.ensure_personas(
        simulation_id="sim-test",
        population_description="A broad representative sample of U.S. adults",
        persona_count=2,
        save_dir=save_dir,
    )

    assert len(result) == 2
    assert result.loc[0, "persona"].startswith("Citizen 1 is part of a representative population sample. A teacher in Ohio")
    assert result.loc[1, "persona"] == "Detailed second persona"


def test_standard_questions_expand_the_battery_for_richer_tracking(tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)

    questions = service.standard_questions("President Morgan Hale", "Governor Elena Cross")

    assert len(questions) >= 20
    assert "right now AI mostly feels able to handle" in questions[0]
    assert "biggest national effect of AI right now" in questions[1]
    assert "trust AI to handle" in questions[2]
    assert "still clearly needs a person" in questions[3]
    assert "easier, cheaper, or better" in questions[4]
    assert "hate to lose right now" in questions[5]
    assert any("barely notice AI" in question for question in questions)
    assert any("most shaping your life right now" in question for question in questions)
    assert any("easier, cheaper, or better" in question for question in questions)
    assert any("useful expertise now feels" in question for question in questions)
    assert any("school or learning around you" in question for question in questions)
    assert any("household finances feel very secure" in question for question in questions)
    assert any("everyday services now feel more reliable" in question for question in questions)
    assert any("daily life around you feels more capable and convenient" in question for question in questions)
    assert any("gains and disruptions from AI are being shared" in question for question in questions)


@pytest.mark.asyncio
async def test_call_gabriel_retries_without_service_tier_when_installed_package_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AppSettings(dummy_openai=False, runs_dir=tmp_path, service_tier="priority").prepare()
    service = GabrielService(settings)
    calls: list[dict[str, object]] = []

    async def fake_poll(**kwargs):
        calls.append(kwargs)
        if "service_tier" in kwargs:
            raise TypeError("Unknown keyword argument(s) for gabriel.poll: service_tier")
        return pd.DataFrame({"seed_id": ["seed-001"], "seed": ["teacher"], "persona": ["teacher persona"]})

    class FakeGabrielModule:
        async def poll(self, **kwargs):
            return await fake_poll(**kwargs)

    monkeypatch.setattr("econ_sim.services.gabriel_service._gabriel", lambda: FakeGabrielModule())

    result = await service._call_gabriel(
        "poll",
        df=pd.DataFrame({"seed_id": ["seed-001"], "seed": ["teacher"]}),
        questions=["One question"],
        column_name="seed",
        save_dir=str(tmp_path),
        model="gpt-test",
        n_questions_per_run=4,
        reasoning_effort="none",
        reset_files=False,
    )

    assert len(calls) == 2
    assert "service_tier" in calls[0]
    assert "service_tier" not in calls[1]
    assert list(result.columns) == ["seed_id", "seed", "persona"]


def test_reason_snippet_reads_like_a_person_not_metadata(tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    row = pd.Series(
        {
            "display_name": "Melissa Anne Whitaker",
            "role": "Public high school English teacher",
            "region": "Portland, Oregon",
            "current_update": "At school, Melissa has quietly folded AI into the boring parts of the job: generating parent email drafts and planning documents faster than before.",
        }
    )

    snippet = service._reason_snippet(
        row,
        answer="somewhat uncomfortable",
        question="How comfortable do you feel with AI showing up in work, services, and daily routines: very comfortable, somewhat comfortable, mixed, somewhat uncomfortable, or very uncomfortable?",
    )

    assert snippet.startswith('Melissa: "')
    assert "I'm somewhat uncomfortable with it right now." in snippet
    assert "I've quietly folded AI into the boring parts of the job" in snippet
    assert "Melissa has quietly folded" not in snippet
    assert "teacher in Portland" not in snippet


def test_open_ended_sample_reasons_are_named_quotes(tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    question = "In one sentence, what would you say is the single biggest pressure point in your life during this AI wave right now?"
    frame = pd.DataFrame(
        {
            "display_name": ["Melissa Anne Whitaker", "Daniel Ross"],
            question: [
                "Honestly, the biggest thing is that school got faster on paper while the real staffing gap never went away.",
                "It is the bills, because the software is cheaper than a person and employers know it.",
            ],
        }
    )

    summary = service._summarize_question(frame, question)

    assert summary.sample_reasons[0].startswith('Melissa: "')
    assert "Honestly, the biggest thing is that school got faster on paper" in summary.sample_reasons[0]


def test_pick_sample_citizens_reserves_stage_relevant_people(tmp_path: Path) -> None:
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    personas = pd.DataFrame(
        {
            "seed_id": [f"seed-{idx:03d}" for idx in range(6)],
            "voice": ["marin", "sage", "cedar", "ash", "alloy", "verse"],
            "display_name": ["Rosa Valez", "Ethan Park", "Joyce Miller", "Tariq Owens", "Mina Shah", "Ben Ortiz"],
            "role": ["Warehouse lead", "Teacher", "Grid engineer", "Home health aide", "Retail manager", "Delivery driver"],
            "region": ["Ohio", "California", "Texas", "Georgia", "Illinois", "Nevada"],
            "mood": ["wary", "hopeful", "tense", "mixed", "curious", "frayed"],
            "ai_exposure": ["high", "medium", "high", "medium", "medium", "high"],
            "support_label": ["lean player"] * 6,
            "approval_band": ["mixed", "approve", "mixed", "disapprove", "mixed", "approve"],
            "support_score": [52, 66, 54, 34, 49, 61],
            "summary": [""] * 6,
            "current_update": [
                "Warehouse robots now move overnight orders faster, and her crew is being retrained to supervise flows rather than carry boxes.",
                "AI tutoring makes lesson prep cheaper, but the school still cannot hire enough counselors.",
                "Grid upgrades for data centers are lifting overtime and local wages, while neighbors complain about power strain.",
                "Care robots cut errands and lifting, but aides fear the field is splitting between high-touch specialists and everyone else.",
                "Retail software cuts wait times, yet the store keeps trimming supervisory shifts.",
                "Autonomous delivery pilots are squeezing routes while making same-day service much cheaper for customers.",
            ],
        }
    )
    stage = _stub_stage("Embodied Rollout")
    stage.tension_points = [
        "Robotics is changing warehouses, care, and delivery unevenly across regions.",
        "Grid and logistics bottlenecks are deciding who captures the gains first.",
        "People want lower prices without losing local job ladders.",
        "Households can feel convenience rising before institutions catch up.",
    ]
    citizens = service.pick_sample_citizens(personas, stage=stage)

    selected_ids = {citizen.citizen_id for citizen in citizens}
    assert {"seed-000", "seed-002"} & selected_ids


def _stub_stage(phase_label: str) -> StagePackage:
    def metric(key: str, label: str, value: float) -> TrackingMetric:
        return TrackingMetric(key=key, label=label, value=value, display=f"{value:.0f}%")

    return StagePackage(
        index=0,
        phase_label=phase_label,
        year_label="2030",
        title="Test Stage",
        state_of_world="A test stage.",
        detailed_summary="A richer test stage.",
        room_briefing="A test room briefing.",
        economic_indicators=["Indicator one", "Indicator two", "Indicator three", "Indicator four", "Indicator five"],
        tension_points=["Tension one", "Tension two", "Tension three", "Tension four"],
        suggested_policy_axes=["Axis one", "Axis two", "Axis three", "Axis four"],
        narrative_beats=[NarrativeBeat(line="Beat", image_prompt="Prompt")],
        sample_citizens=[],
        tracking=StageTracking(
            approval=metric("approval", "Approval", 50),
            vote_share_player=metric("vote_player", "Vote Share", 50),
            vote_share_opponent=metric("vote_opponent", "Opponent Vote", 50),
            better_off=metric("better_off", "Better Off", 50),
            ai_comfort=metric("ai_comfort", "AI Comfort", 50),
            unemployment_anxiety=metric("job_security", "Job Security", 50),
            trust_in_government=metric("trust", "Gov Trust", 50),
            social_stability=metric("stability", "Social Stability", 50),
        ),
        poll_summaries=[],
        queued_poll_questions=[],
    )
