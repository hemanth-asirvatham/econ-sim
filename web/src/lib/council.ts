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

export const COUNCIL_ADVISORS: CouncilAdvisorSpec[] = [
  { key: "capacity", name: "Rowan", role: "Economic", voice: "cedar" },
  { key: "households", name: "Leila", role: "Households", voice: "marin" },
  { key: "politics", name: "Mateo", role: "Politics", voice: "ash" },
  { key: "state", name: "Amina", role: "Security", voice: "shimmer" },
];

const COUNCIL_SPEAKER_PATTERN = new RegExp(
  `^(${COUNCIL_ADVISORS.map((advisor) => advisor.name).join("|")}):\\s*(.+)$`,
  "i",
);

export function parseCouncilCaption(text?: string | null) {
  const cleaned = text?.trim() ?? "";
  const firstLine = cleaned
    .split(/\n+/)
    .map((line) => line.trim())
    .find(Boolean) ?? "";
  const match = firstLine.match(COUNCIL_SPEAKER_PATTERN);
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
    .map((line, index) => (index === 0 ? match[2].trim() : line.replace(COUNCIL_SPEAKER_PATTERN, "$1: $2")))
    .join(" ");
  return {
    speaker: match[1][0].toUpperCase() + match[1].slice(1).toLowerCase(),
    text: remainder.trim(),
  };
}

export function splitCouncilLines(text?: string | null) {
  const cleaned = text?.replace(/\s+/g, " ").trim() ?? "";
  if (!cleaned) {
    return [];
  }
  const inlinePattern = new RegExp(`\\b(${COUNCIL_ADVISORS.map((advisor) => advisor.name).join("|")}):`, "gi");
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
      const speaker = match[1][0].toUpperCase() + match[1].slice(1).toLowerCase();
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
      const parsed = parseCouncilCaption(line);
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
