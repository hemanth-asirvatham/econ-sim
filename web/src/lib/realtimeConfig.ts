export type RealtimeTurnDetection = {
  type: "semantic_vad";
  eagerness: "low" | "medium" | "high";
  create_response: boolean;
  interrupt_response: boolean;
};

const REALTIME_VAD_EAGERNESS: RealtimeTurnDetection["eagerness"] = "low";
const REALTIME_INTERRUPT_RESPONSE = true;

export function makeRealtimeTurnDetection(createResponse = true): RealtimeTurnDetection {
  return {
    type: "semantic_vad",
    eagerness: REALTIME_VAD_EAGERNESS,
    create_response: createResponse,
    interrupt_response: REALTIME_INTERRUPT_RESPONSE,
  };
}

export const REALTIME_TURN_DETECTION = makeRealtimeTurnDetection(true);
