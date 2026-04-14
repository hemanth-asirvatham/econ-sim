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
        remit="prices, household security, machine-income flows, compute access, industrial buildout, and which gains ordinary people would defend",
        voice="cedar",
        viewpoint="leans pro-diffusion, but turns hard against choke points or concentrated capture",
    ),
    CouncilAdvisorSpec(
        key="innovation",
        name="Leila",
        room_role="Innovation",
        country_role="science, innovation, and frontier systems advisor",
        remit="research speed, robotics rollout, compute bottlenecks, talent pipelines, and which interfaces or standards unlock real capability",
        voice="marin",
        viewpoint="pushes capability forward first, but hates frozen frontier access and cartel bottlenecks",
    ),
    CouncilAdvisorSpec(
        key="politics",
        name="Mateo",
        room_role="Politics",
        country_role="political strategy director",
        remit="coalition mood, legitimacy, polling movement, debate framing, and which lines the player can actually defend in public",
        voice="ash",
        viewpoint="sharp on voter interpretation, willing to defend speed when people feel gains and punishing when caution sounds fake",
    ),
    CouncilAdvisorSpec(
        key="state",
        name="Amina",
        room_role="Security",
        country_role="national security and state-capacity advisor",
        remit="infrastructure, strategic dependence, coercion risk, resilience, alliances, and what the state can actually execute or defend",
        voice="shimmer",
        viewpoint="starts from resilience and execution, but favors openness when it clearly makes the state stronger",
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
        f"- {advisor.key}: {advisor.name} ({advisor.room_role}, voice {advisor.voice}) for {country}; watches {advisor.remit}"
        + (f"; instinct: {advisor.viewpoint}" if str(getattr(advisor, 'viewpoint', '')).strip() else "")
        for advisor in roster_items
    )
