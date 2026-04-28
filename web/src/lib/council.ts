import type { CouncilAdvisorProfile } from "../types";

export interface CouncilAdvisorSpec {
  key: string;
  name: string;
  role: string;
  voice: string;
}

export interface CouncilTurnContext {
  policyNotes?: string[];
  dominantMechanism?: string;
  dominantUpside?: string;
  mainSplit?: string;
  pollTakeaways?: string[];
}

export const DEFAULT_COUNCIL_ADVISORS: CouncilAdvisorSpec[] = [
  { key: "capacity", name: "Rowan", role: "Economy", voice: "cedar" },
  { key: "innovation", name: "Leila", role: "Innovation", voice: "marin" },
  { key: "politics", name: "Mateo", role: "Politics", voice: "ash" },
  { key: "state", name: "Amina", role: "Security", voice: "shimmer" },
  { key: "labor", name: "Iris", role: "Labor", voice: "sage" },
  { key: "markets", name: "Nova", role: "Markets", voice: "verse" },
];

export const COUNCIL_ADVISORS = DEFAULT_COUNCIL_ADVISORS;

type CouncilRosterInput = Array<CouncilAdvisorProfile | CouncilAdvisorSpec> | undefined;

function escapeRegex(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function normalizeCouncilRoster(roster?: CouncilRosterInput): CouncilAdvisorSpec[] {
  const normalized = (roster ?? [])
    .map((advisor) => {
      const key = String(advisor.key ?? "").trim();
      const name = String(advisor.name ?? "").trim();
      const role = "room_role" in advisor
        ? String(advisor.room_role ?? "").trim()
        : String(advisor.role ?? "").trim();
      const voice = String(advisor.voice ?? "").trim();
      if (!key || !name) {
        return null;
      }
      return {
        key,
        name,
        role: role || "Advisor",
        voice: voice || "cedar",
      } satisfies CouncilAdvisorSpec;
    })
    .filter(Boolean) as CouncilAdvisorSpec[];
  return normalized.length > 0 ? normalized : DEFAULT_COUNCIL_ADVISORS;
}

function councilSpeakerPattern(roster?: CouncilRosterInput) {
  const advisors = normalizeCouncilRoster(roster);
  return new RegExp(
    `^(${advisors.map((advisor) => escapeRegex(advisor.name)).join("|")}):\\s*(.+)$`,
    "i",
  );
}

export function councilVoiceForSpeaker(speaker?: string | null, roster?: CouncilRosterInput) {
  const normalized = (speaker ?? "").trim().toLowerCase();
  const advisor = normalizeCouncilRoster(roster).find((entry) => entry.name.toLowerCase() === normalized);
  return advisor?.voice ?? "cedar";
}

export function parseCouncilCaption(text?: string | null, roster?: CouncilRosterInput) {
  const cleaned = text?.trim() ?? "";
  const firstLine = cleaned
    .split(/\n+/)
    .map((line) => line.trim())
    .find(Boolean) ?? "";
  const pattern = councilSpeakerPattern(roster);
  const match = firstLine.match(pattern);
  if (!match) {
    return {
      speaker: undefined,
      text: cleaned,
    };
  }
  const remainder = cleaned
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => (index === 0 ? match[2].trim() : line.replace(pattern, "$1: $2")))
    .join(" ");
  return {
    speaker: match[1][0].toUpperCase() + match[1].slice(1),
    text: remainder.trim(),
  };
}

export function splitCouncilLines(text?: string | null, roster?: CouncilRosterInput) {
  const cleaned = text?.replace(/\s+/g, " ").trim() ?? "";
  if (!cleaned) {
    return [];
  }
  const advisors = normalizeCouncilRoster(roster);
  const inlinePattern = new RegExp(
    `\\b(${advisors.map((advisor) => escapeRegex(advisor.name)).join("|")}):`,
    "gi",
  );
  const matches = [...cleaned.matchAll(inlinePattern)];
  if (matches.length > 0) {
    const segments: Array<{ speaker?: string; text: string }> = [];
    let cursor = 0;
    for (let index = 0; index < matches.length; index += 1) {
      const match = matches[index];
      const matchIndex = match.index ?? 0;
      if (matchIndex > cursor) {
        const prelude = cleaned.slice(cursor, matchIndex).trim();
        if (prelude) {
          segments.push({ speaker: undefined, text: prelude });
        }
      }
      const speaker = match[1][0].toUpperCase() + match[1].slice(1);
      const nextIndex = matches[index + 1]?.index ?? cleaned.length;
      const segmentText = cleaned.slice(matchIndex + match[0].length, nextIndex).trim();
      if (segmentText) {
        segments.push({ speaker, text: segmentText });
      }
      cursor = nextIndex;
    }
    if (segments.length > 0) {
      return segments;
    }
  }
  const lines = cleaned
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parsed = parseCouncilCaption(line, roster);
      return {
        speaker: parsed.speaker,
        text: parsed.text || line,
      };
    });
  if (lines.some((line) => line.speaker)) {
    return lines;
  }
  return [{ speaker: undefined, text: cleaned }];
}
