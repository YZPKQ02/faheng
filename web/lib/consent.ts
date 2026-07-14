import type { ModelConsent } from "./api";

export type ModelPurpose = "intake" | "analysis" | "simulation";

const requiredCategories: Record<ModelPurpose, string[]> = {
  intake: ["conversation", "facts", "evidence_metadata"],
  analysis: ["facts", "evidence_metadata", "legal_analysis"],
  simulation: ["conversation", "facts", "evidence_metadata"],
};

export function mergeConsentScope(existing: ModelConsent | null, purpose: ModelPurpose) {
  return {
    purposes: Array.from(new Set([...(existing?.purposes ?? []), purpose])),
    data_categories: Array.from(
      new Set([...(existing?.data_categories ?? []), ...requiredCategories[purpose]]),
    ),
  };
}
