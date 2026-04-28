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
        remit="macro conditions, layoffs versus shorter workweeks, wages, prices, firm boundaries, household income, scarce goods such as housing and care, machine-income flows, abundance, and where cheap AI services actually raise living standards",
        voice="cedar",
        viewpoint="argues from prices, quantities, and income flows; often pro-diffusion and pro-abundance, but blunt about unemployment, missing income streams, housing scarcity, and platform rents",
    ),
    CouncilAdvisorSpec(
        key="innovation",
        name="Leila",
        room_role="Innovation",
        country_role="science, innovation, and frontier systems advisor",
        remit="capability diffusion, research speed, robotics rollout, compute bottlenecks, new firm forms, standards, and which useful services get cheaper or more reliable when deployment is not over-managed",
        voice="marin",
        viewpoint="pushes capability forward first and is willing to say leave useful systems alone; worries most about frozen frontier access, cartel bottlenecks, and policies that keep ordinary users from cheap expert help",
    ),
    CouncilAdvisorSpec(
        key="politics",
        name="Mateo",
        room_role="Politics",
        country_role="political strategy director",
        remit="coalition mood, legitimacy, visible winners and losers, voter anger over lost jobs or rationed gains, debate framing, and which lines the player can actually defend in public",
        voice="ash",
        viewpoint="cares about what voters feel in their bills, routines, status, and pride; willing to defend speed when people feel gains and ruthless when caution sounds like elites taking toys away",
    ),
    CouncilAdvisorSpec(
        key="state",
        name="Amina",
        room_role="Security",
        country_role="national security and state-capacity advisor",
        remit="security, privacy, infrastructure, strategic dependence, coercion risk, sabotage, allied supply, geopolitics, cyber-physical failure modes, and what the state can actually execute or defend",
        voice="shimmer",
        viewpoint="starts from security, privacy, and geopolitical leverage; favors openness when it strengthens the state, but draws hard lines around systems that can coerce people, move assets, or touch infrastructure",
    ),
    CouncilAdvisorSpec(
        key="labor",
        name="Iris",
        room_role="Labor",
        country_role="labor market and household security advisor",
        remit="layoffs, hiring freezes, career ladders, bargaining power, benefits, household budgets, and what replaces labor income when digital services get cheap faster than scarce goods",
        voice="sage",
        viewpoint="does not pretend everyone becomes an AI supervisor; presses how people buy rent, care, and power if the old job ladder thins out",
    ),
    CouncilAdvisorSpec(
        key="markets",
        name="Nova",
        room_role="Markets",
        country_role="competition and market-structure advisor",
        remit="firm entry, incumbent defense, platform tolls, public versus private provision, small-business reach, ownership claims, and when regulation would slow useful abundance",
        voice="verse",
        viewpoint="often argues for competition, diffusion, and leaving working private markets alone unless there is a real chokepoint or coercive gatekeeper",
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
