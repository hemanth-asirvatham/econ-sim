import type { ConversationTurn, StagePackage } from "../types";

export interface TownHallQuestion {
  id: string;
  citizenId: string;
  displayName: string;
  role: string;
  region: string;
  voice: string;
  supportLabel: string;
  aiExposure: string;
  question: string;
  cue: string;
}

function compactLine(text: string, max = 116) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= max) {
    return normalized;
  }
  const clipped = normalized.slice(0, max);
  const breakAt = clipped.lastIndexOf(" ");
  return `${clipped.slice(0, breakAt > 24 ? breakAt : max).trim()}...`;
}

function firstSentence(text: string, max = 132) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  const sentence = normalized.split(/(?<=[.!?])\s+/)[0]?.trim() ?? normalized;
  return compactLine(sentence.replace(/[.?!]+$/g, "").trim(), max).replace(/\.{3}$/, "");
}

function keywordBag(text: string) {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/g)
    .filter((word) => word.length >= 5)
    .filter((word) => !["their", "there", "about", "which", "where", "would", "could", "still", "while", "people", "because", "being"].includes(word));
}

function cueForCitizen(stage: StagePackage, citizen = stage.sample_citizens[0]) {
  if (!citizen) {
    return "";
  }
  if (citizen.town_hall_cue.trim()) {
    return citizen.town_hall_cue.trim();
  }
  const source =
    citizen.current_worries ||
    citizen.current_update ||
    citizen.current_hopes ||
    citizen.recent_ai_moment ||
    citizen.summary;
  if (source.trim()) {
    return compactLine(source);
  }
  if (stage.main_split.trim()) {
    return compactLine(stage.main_split);
  }
  return compactLine(citizen.support_label || citizen.role);
}

function fallbackQuestionForCitizen(stage: StagePackage, citizen = stage.sample_citizens[0]) {
  if (!citizen) {
    return "";
  }
  const focus = firstSentence(
    citizen.current_worries ||
      citizen.current_hopes ||
      citizen.recent_ai_moment ||
      citizen.summary ||
      citizen.current_update ||
      stage.main_split ||
      citizen.role,
    140,
  );
  if (!focus) {
    return "What does your plan actually do for people like me?";
  }
  const lead = focus.slice(0, 1).toUpperCase() + focus.slice(1);
  if (/^(?:i|my|we|our)\b/i.test(focus)) {
    return `${lead}. What does your plan do for people like me?`;
  }
  return `${lead}. What would you do about that?`;
}

function questionForCitizen(stage: StagePackage, citizen = stage.sample_citizens[0]) {
  if (!citizen) {
    return null;
  }

  return {
    id: `townhall-${citizen.citizen_id}`,
    citizenId: citizen.citizen_id,
    displayName: citizen.display_name,
    role: citizen.role,
    region: citizen.region,
    voice: citizen.voice,
    supportLabel: citizen.support_label,
    aiExposure: citizen.ai_exposure,
    question: (citizen.town_hall_question || "").split(/\s+/).filter(Boolean).join(" ").trim() || fallbackQuestionForCitizen(stage, citizen),
    cue: cueForCitizen(stage, citizen),
  } satisfies TownHallQuestion;
}

export function buildTownHallQuestions(stage: StagePackage, debateTurns: ConversationTurn[]) {
  const latestPlayerText = [...debateTurns]
    .reverse()
    .find((turn) => turn.speaker === "user" && turn.text.trim())
    ?.text ?? "";
  const liveKeywords = new Set(
    keywordBag([latestPlayerText, stage.main_split, ...stage.policy_notes].join(" ")).slice(0, 8),
  );
  const priorCitizenTurns = new Set(
    debateTurns
      .filter((turn) => turn.speaker === "assistant" && turn.speaker_name)
      .map((turn) => turn.speaker_name?.toLowerCase().trim() ?? ""),
  );

  return [...stage.sample_citizens]
    .sort((left, right) => {
      const scoreCitizen = (citizen: StagePackage["sample_citizens"][number]) => {
        const citizenText = [
          citizen.current_update,
          citizen.current_worries,
          citizen.current_hopes,
          citizen.recent_ai_moment,
          citizen.town_hall_question,
          citizen.summary,
          citizen.role,
          citizen.region,
        ].join(" ");
        const overlap = keywordBag(citizenText).filter((word) => liveKeywords.has(word)).length;
        const unseenBonus = priorCitizenTurns.has(citizen.display_name.toLowerCase()) ? 0 : 3;
        return unseenBonus + overlap;
      };
      return scoreCitizen(right) - scoreCitizen(left);
    })
    .slice(0, 6)
    .map((citizen) => questionForCitizen(stage, citizen))
    .filter((item): item is TownHallQuestion => Boolean(item));
}

export function townHallPrompt(question: TownHallQuestion) {
  return `Town hall question from ${question.displayName}, ${question.role} in ${question.region}: ${question.question} Answer this voter directly and concretely before you move on.`;
}
