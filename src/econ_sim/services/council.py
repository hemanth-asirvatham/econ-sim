from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import CouncilAdvisorProfile


@dataclass(frozen=True)
class CouncilAdvisorSpec:
    key: str
    name: str
    room_role: str
    country_role: str
    remit: str
    voice: str = "alloy"
    viewpoint: str = ""


COUNCIL_ADVISORS: tuple[CouncilAdvisorSpec, ...] = (
    CouncilAdvisorSpec(
        key="capacity",
        name="Rowan",
        room_role="Economy",
        country_role="national economic and industrial strategy director",
        remit="tracks prices, household purchasing power, machine-income flows, capability diffusion, industrial buildout, competition, compute access, and which concrete gains households or smaller firms would fight to keep if politics tried to choke useful capacity",
        voice="cedar",
        viewpoint="leans pro-diffusion when the gains are real, but can back restraint around bottlenecks or concentrated capture",
    ),
    CouncilAdvisorSpec(
        key="innovation",
        name="Leila",
        room_role="Innovation",
        country_role="science, innovation, and frontier systems advisor",
        remit="tracks research speed, robotics rollout, compute bottlenecks, talent pipelines, laboratory and startup diffusion, and which institutional changes, standards, or public interfaces would unlock more real capability instead of merely protecting incumbents",
        voice="marin",
        viewpoint="pushes capability forward first, but worries about cartelized chokepoints and frozen frontier access",
    ),
    CouncilAdvisorSpec(
        key="politics",
        name="Mateo",
        room_role="Politics",
        country_role="political strategy director",
        remit="tracks coalition mood, legitimacy, polling movement, debate framing, public tolerance for change, and which lines the player can actually defend in public without sounding evasive, bloodless, abstract, or overconfident",
        voice="ash",
        viewpoint="sharp on voter interpretation, willing to defend speed when people see gains and punishing caution when it sounds fake",
    ),
    CouncilAdvisorSpec(
        key="state",
        name="Amina",
        room_role="Security",
        country_role="national security and state-capacity advisor",
        remit="tracks infrastructure, strategic dependence, war and coercion risk, resilience, defense-industrial readiness, alliances, and what the state can actually execute, procure, secure, ration, or defend in the real world",
        voice="shimmer",
        viewpoint="starts from resilience and execution, but can favor openness when it clearly makes the state stronger",
    ),
)

COUNCIL_VOICE_POOL: tuple[str, ...] = (
    "cedar",
    "marin",
    "ash",
    "shimmer",
    "sage",
    "verse",
)


def council_roster_block(country: str, roster: Sequence[CouncilAdvisorProfile | CouncilAdvisorSpec] | None = None) -> str:
    roster_items = roster or COUNCIL_ADVISORS
    return "\n".join(
        f"- {advisor.key}: {advisor.name} ({advisor.room_role}, voice {advisor.voice}) for {country}; {advisor.country_role}; {advisor.remit}"
        + (f"; viewpoint: {advisor.viewpoint}" if str(getattr(advisor, 'viewpoint', '')).strip() else "")
        for advisor in roster_items
    )
