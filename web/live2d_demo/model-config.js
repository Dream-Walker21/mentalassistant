export const HIYORI_FREE_MODEL_URL =
  "../../hiyori_zh-Hans/hiyori_free/runtime/hiyori_free_t08.model3.json";

// These names come directly from hiyori_free_t08.model3.json. Keep business
// semantics here so LangGraph never needs to know a model-specific motion name.
export const HIYORI_ACTIONS = {
  idle: { group: "Idle", index: 0 },
  greet: { group: "Flick", index: 0 },
  listen: { group: "Idle", index: 1 },
  comfort: { group: "Tap", index: 0 },
  think: { group: "FlickDown", index: 0 },
  encourage: { group: "Flick@Body", index: 0 },
  alert: { group: "Tap@Body", index: 0 },
  goodbye: { group: "Idle", index: 2 },
};

export const EXPRESSION_PARAMETERS = {
  neutral: { ParamMouthForm: 0, ParamCheek: 0, ParamEyeLSmile: 0, ParamEyeRSmile: 0 },
  gentle_smile: { ParamMouthForm: 0.75, ParamCheek: 0.35, ParamEyeLSmile: 0.55, ParamEyeRSmile: 0.55 },
  calm: { ParamMouthForm: 0.25, ParamCheek: 0, ParamEyeLSmile: 0.1, ParamEyeRSmile: 0.1 },
  concerned: { ParamMouthForm: -0.45, ParamCheek: 0, ParamBrowLForm: -0.4, ParamBrowRForm: -0.4 },
  serious: { ParamMouthForm: -0.25, ParamCheek: 0, ParamBrowLForm: -0.25, ParamBrowRForm: -0.25 },
};

export const DEFAULT_COMMAND = {
  version: 1,
  action: "listen",
  expression: "calm",
  gesture: "nod",
  intensity: 0.3,
  duration_ms: 1200,
};
