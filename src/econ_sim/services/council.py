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
        country_role="national economic and industrial strategy director",
        remit="tracks prices, household purchasing power, machine-income flows, capability diffusion, industrial buildout, competition, compute access, and which concrete gains households or smaller firms would fight to keep if politics tried to choke useful capacity",
    ),
    CouncilAdvisorSpec(
        key="innovation",
        name="Leila",
        room_role="Innovation",
        country_role="science, innovation, and frontier systems advisor",
        remit="tracks research speed, robotics rollout, compute bottlenecks, talent pipelines, laboratory and startup diffusion, and which institutional changes, standards, or public interfaces would unlock more real capability instead of merely protecting incumbents",
    ),
    CouncilAdvisorSpec(
        key="politics",
        name="Mateo",
        room_role="Politics",
        country_role="political strategy director",
        remit="tracks coalition mood, legitimacy, polling movement, debate framing, public tolerance for change, and which lines the player can actually defend in public without sounding evasive, bloodless, abstract, or overconfident",
    ),
    CouncilAdvisorSpec(
        key="state",
        name="Amina",
        room_role="Security",
        country_role="national security and state-capacity advisor",
        remit="tracks infrastructure, strategic dependence, war and coercion risk, resilience, defense-industrial readiness, alliances, and what the state can actually execute, procure, secure, ration, or defend in the real world",
    ),
)


def council_roster_block(country: str) -> str:
    return "\n".join(
        f"- {advisor.name}: {advisor.country_role} for {country}; {advisor.remit}"
        for advisor in COUNCIL_ADVISORS
    )
