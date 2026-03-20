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

function normalize(text: string) {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function hasNeedle(text: string, needles: string[]) {
  const normalized = normalize(text);
  return needles.some((needle) => normalized.includes(needle));
}

function latestPlayerDebateCase(turns: ConversationTurn[]) {
  return turns
    .filter((turn) => turn.speaker === "user")
    .map((turn) => turn.text.trim())
    .filter(Boolean)
    .slice(-4)
    .join(" ");
}

function questionForCitizen(stage: StagePackage, playerCase: string, index: number) {
  const citizen = stage.sample_citizens[index];
  if (!citizen) {
    return null;
  }

  const playerCaseNormalized = normalize(playerCase);
  const restrictionHeavy = hasNeedle(playerCaseNormalized, ["tax", "taxes", "slow", "pause", "license", "licensing", "cap", "caps", "permit", "permits", "regulate"]);
  const speedHeavy = hasNeedle(playerCaseNormalized, ["build faster", "ship faster", "diffuse", "open access", "faster rollout", "move faster", "accelerate"]);
  const lowExposure = hasNeedle(citizen.ai_exposure, ["low", "minimal", "rare", "barely"]);
  const positiveCitizen = hasNeedle(citizen.support_label, ["approve", "open", "hopeful", "support"]);
  const skepticalCitizen = hasNeedle(citizen.support_label, ["disapprove", "angry", "against", "skeptical"]);
  const teacherLike = hasNeedle(citizen.role, ["teacher", "student", "professor", "school"]);
  const careLike = hasNeedle(citizen.role, ["nurse", "doctor", "care", "hospital", "therapist", "clinic"]);
  const builderLike = hasNeedle(citizen.role, ["owner", "founder", "manager", "developer", "engineer", "contractor", "shop"]);
  const familyCue = citizen.current_hopes || citizen.current_worries || citizen.current_update || citizen.summary;

  let question = "";
  let cue = "";

  if (restrictionHeavy && positiveCitizen) {
    question = "If these tools are already helping me, what exactly are you willing to slow down?";
    cue = "This voter likes some of the gains already and wants to know what your brake would cost them.";
  } else if (speedHeavy && skepticalCitizen) {
    question = "If you move faster, who is actually protecting people when the gains pile up at the top?";
    cue = "This voter wants a real answer on fairness and leverage before they trust a speed-first line.";
  } else if (teacherLike) {
    question = "What changes for students who can suddenly do expert-level work, and what still needs a real teacher in the room?";
    cue = "Good education question: capability jump plus the human role that does not vanish.";
  } else if (careLike) {
    question = "If AI keeps getting better in care, what becomes safer or cheaper, and what do you refuse to automate away?";
    cue = "Good care question: visible upside first, then the limit you would defend.";
  } else if (builderLike && positiveCitizen) {
    question = "How do you keep smaller firms and ordinary people getting the upside, instead of letting a few giants lock it up?";
    cue = "This voter is open to diffusion but worried about concentration.";
  } else if (lowExposure) {
    question = "I barely touch AI myself, so why should I believe your plan changes my life at all?";
    cue = "Good grounding question when the voter feels outside the current wave.";
  } else if (skepticalCitizen) {
    question = "What would you say to someone who thinks this mostly makes life feel shakier, not better?";
    cue = "This voter wants you to answer the fear directly instead of talking past it.";
  } else {
    question = "What feels better because of AI right now, and what still needs a real public guardrail?";
    cue = "Balanced audience question: keep the gains visible and force a real limit.";
  }

  const livedLine = familyCue.replace(/\s+/g, " ").trim();
  if (livedLine) {
    cue = `${cue} Their current read: ${livedLine.slice(0, 110)}${livedLine.length > 110 ? "..." : ""}`;
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
    question,
    cue,
  } satisfies TownHallQuestion;
}

export function buildTownHallQuestions(stage: StagePackage, debateTurns: ConversationTurn[]) {
  const playerCase = latestPlayerDebateCase(debateTurns);
  return stage.sample_citizens
    .slice(0, 6)
    .map((_, index) => questionForCitizen(stage, playerCase, index))
    .filter((item): item is TownHallQuestion => Boolean(item));
}

export function townHallPrompt(question: TownHallQuestion) {
  return `Town hall question from ${question.displayName}, ${question.role} in ${question.region}: ${question.question} Answer the voter directly in one short sentence and then stop so I can answer too.`;
}
