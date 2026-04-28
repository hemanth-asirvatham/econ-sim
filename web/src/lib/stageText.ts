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
  const sentences = firstParagraph.split(/(?<=[.!?])\s+/).map((sentence) => sentence.trim()).filter(Boolean);
  const firstSentence = sentences[0] || firstParagraph;
  if (firstSentence.length > maxChars) {
    return clippedLine(firstSentence, maxChars);
  }
  const firstTwoSentences = sentences.slice(0, 2).join(" ").trim();
  if (firstTwoSentences && firstTwoSentences.length <= maxChars) {
    return firstTwoSentences;
  }
  return firstSentence;
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
    /machine[- ]linked income/,
    /machine[- ]income/,
    /machine checks?/,
    /machine surplus/,
    /service floor/,
    /public[- ]service utility/,
    /public capacity/,
    /capability account/,
    /model credits?/,
    /compute credits?/,
    /shorter paid weeks?/,
    /paid hours/,
    /old workweek/,
    /time dividend/,
    /expert help/,
    /service guarantee/,
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
      /machine surplus/,
      /machine[- ]linked income/,
      /service floor/,
      /public[- ]service utility/,
      /who gets/,
      /who owns/,
      /who controls/,
      /who gets compute/,
      /who pays/,
      /who captures/,
      /who rents/,
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
      /public model/,
      /ownership share/,
      /productive floor/,
      /machine account/,
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
      /datacenter/,
      /data center/,
      /interconnect/,
      /energy/,
      /housing/,
      /water/,
      /substation/,
      /battery/,
      /materials?/,
      /warehouse/,
      /port/,
      /robot depot/,
      /robotics depot/,
      /model queue/,
      /compute queue/,
      /queue rule/,
      /bottleneck/,
      /scarcity/,
      /dependence/,
      /queue/,
      /premium/,
      /paywall/,
      /license/,
      /licensing/,
      /platform toll/,
      /ownership concentration/,
    ])
    || stageWorldOpening(stage, maxChars);
  return clippedLine(source, maxChars);
}

export function stagePolicyAxes(
  stage?: Pick<StagePackage, "policy_notes" | "world_brief"> | null,
  limit = 4,
) {
  return (stage?.policy_notes ?? [])
    .map((entry) => entry.trim())
    .filter(Boolean)
    .slice(0, limit);
}
