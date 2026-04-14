import type { StagePackage } from "../types";

function worldSentences(stage?: Pick<StagePackage, "world_brief"> | null) {
  return (stage?.world_brief?.trim() || "")
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

function clippedLine(text: string, maxChars: number) {
  if (text.length <= maxChars) {
    return text.trim();
  }
  const clipped = text.slice(0, maxChars);
  const breakAt = clipped.lastIndexOf(" ");
  return `${clipped.slice(0, breakAt > 32 ? breakAt : maxChars).trim()}...`;
}

function firstMatchingLine(lines: string[], patterns: RegExp[]) {
  return lines.find((line) => patterns.some((pattern) => pattern.test(line.toLowerCase())))?.trim() || "";
}

export function stageRoomBrief(stage?: Pick<StagePackage, "room_briefing"> | null) {
  return stage?.room_briefing?.trim() || "";
}

export function stageWorldOpening(stage?: Pick<StagePackage, "world_brief"> | null, maxChars = 160) {
  const worldBrief = stage?.world_brief?.trim() || "";
  if (!worldBrief) {
    return "";
  }
  const firstParagraph = worldBrief.split(/\n\s*\n/)[0]?.trim() || worldBrief;
  const firstTwoSentences = firstParagraph.split(/(?<=[.!?])\s+/).slice(0, 2).join(" ").trim() || firstParagraph;
  return clippedLine(firstTwoSentences, maxChars);
}

export function stageGain(stage?: Pick<StagePackage, "world_brief"> | null, maxChars = 140) {
  const lines = worldSentences(stage);
  const preferred = firstMatchingLine(lines, [
    /productivity rebate/,
    /machine dividend/,
    /dividend/,
    /royalt(?:y|ies)/,
    /service credit/,
    /allowance/,
    /public model account/,
    /public ai account/,
    /public ai desk/,
    /agent-run/,
    /machine-run/,
    /household floor/,
    /old office week/,
    /free day/,
    /days? no longer sold/,
    /new routine/,
    /part time human work/,
    /machine systems/,
    /cheap/,
    /cheaper/,
    /purchasing power/,
    /security/,
    /more capable/,
    /less dependent on scarce experts/,
  ]);
  return clippedLine(preferred || stageWorldOpening(stage, maxChars), maxChars);
}

export function stageSplit(
  stage?: Pick<StagePackage, "world_brief"> | null,
  maxChars = 140,
) {
  const source =
    firstMatchingLine(worldSentences(stage), [
      /public utility/,
      /toll road/,
      /platform toll/,
      /platform royalty/,
      /who gets/,
      /who owns/,
      /who controls/,
      /who gets compute/,
      /priority lane/,
      /ration/,
      /queue/,
      /tier/,
      /platform/,
      /access/,
      /ownership/,
      /membership/,
      /protocol/,
      /public desk/,
    ])
    || stageWorldOpening(stage, maxChars);
  return clippedLine(source, maxChars);
}

export function stageConstraint(
  stage?: Pick<StagePackage, "world_brief"> | null,
  maxChars = 140,
) {
  const source =
    firstMatchingLine(worldSentences(stage), [
      /power/,
      /compute/,
      /chips?/,
      /grid/,
      /transmission/,
      /housing/,
      /water/,
      /substation/,
      /battery/,
      /materials?/,
      /warehouse/,
      /port/,
      /robot depot/,
      /bottleneck/,
      /scarcity/,
      /dependence/,
      /queue/,
      /premium/,
      /paywall/,
    ])
    || stageWorldOpening(stage, maxChars);
  return clippedLine(source, maxChars);
}

export function stagePolicyAxes(
  stage?: Pick<StagePackage, "policy_notes" | "world_brief"> | null,
  limit = 4,
) {
  const worldText = (stage?.world_brief || "").toLowerCase();
  const worldDerived = [
    /public ai account|public model account|household|small firm|school|clinic|access/.test(worldText)
      ? "Keep broad AI access open for households, schools, clinics, and small firms."
      : "",
    /platform|ownership|chokepoint|toll|rent|queue|priority/.test(worldText)
      ? "Break chokepoints and spread ownership of the AI upside."
      : "",
    /machine check|dividend|productivity rebate|income|household floor|security/.test(worldText)
      ? "Turn machine gains into a visible household floor."
      : "",
    /power|grid|compute|chip|transmission|substation|water|housing|logistics/.test(worldText)
      ? "Speed grid, compute, and deployment buildout."
      : "",
    /fraud|scam|appeal|liability|records|benefits|infrastructure/.test(worldText)
      ? "Require audits and appeals where AI can move money, records, benefits, or infrastructure."
      : "",
  ].filter(Boolean);
  const source = stage?.policy_notes?.length ? stage.policy_notes : worldDerived;
  return source
    .map((entry) => entry.trim())
    .filter(Boolean)
    .slice(0, limit);
}
