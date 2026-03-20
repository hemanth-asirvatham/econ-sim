from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CouncilAdvisorSpec:
    key: str
    name: str
    room_role: str
    country_role: str
    remit: str


COUNCIL_ADVISORS: tuple[CouncilAdvisorSpec, ...] = (
    CouncilAdvisorSpec(
        key="capacity",
        name="Rowan",
        room_role="Economy",
        country_role="national economic council director",
        remit="tracks capability frontier, diffusion, prices, investment, competition, and the gains households or smaller firms would fight to keep if politics tried to slow them",
    ),
    CouncilAdvisorSpec(
        key="households",
        name="Leila",
        room_role="Households",
        country_role="household and labor advisor",
        remit="tracks wages, bargaining power, service quality, legitimacy, fairness, and what strain or relief feels concrete in ordinary life rather than abstract in policy language",
    ),
    CouncilAdvisorSpec(
        key="politics",
        name="Mateo",
        room_role="Politics",
        country_role="political strategy director",
        remit="tracks coalition mood, polling movement, debate framing, what wins votes, and which lines the player can actually defend in public without sounding evasive or overconfident",
    ),
    CouncilAdvisorSpec(
        key="state",
        name="Amina",
        room_role="Security",
        country_role="national security and state-capacity advisor",
        remit="tracks infrastructure, resilience, allied or rival pressure, strategic dependence, and what the state can actually execute, procure, secure, or defend in the real world",
    ),
)


def council_roster_block(country: str) -> str:
    return "\n".join(
        f"- {advisor.name}: {advisor.country_role} for {country}; {advisor.remit}"
        for advisor in COUNCIL_ADVISORS
    )
