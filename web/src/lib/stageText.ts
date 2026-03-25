import type { StagePackage } from "../types";

export function stageRoomBrief(stage?: Pick<StagePackage, "room_briefing" | "authored_room_briefing"> | null) {
  return stage?.authored_room_briefing?.trim() || stage?.room_briefing?.trim() || "";
}

export function stagePolicyAxes(
  stage?: Pick<StagePackage, "policy_notes" | "suggested_policy_axes" | "authored_policy_axes"> | null,
  limit = 4,
) {
  const source = stage?.policy_notes?.length
    ? stage.policy_notes
    : stage?.authored_policy_axes?.length
      ? stage.authored_policy_axes
      : (stage?.suggested_policy_axes ?? []);
  return source
    .map((entry) => entry.trim())
    .filter(Boolean)
    .slice(0, limit);
}
