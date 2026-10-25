// One crop per study's CardArt, as [x, y, width, height] viewBox units. The crops
// wrap the measured drawn bounds (getBBox, margins 20-146 units of empty canvas on
// the old shared 400x250 frame) plus an 8-unit breath per side so centered strokes
// (width 1.5 = ~0.75 beyond the geometry bbox) never clip. CardArt renders the
// viewBox from this map, and the study exhibit derives its width cap from the same
// numbers, so the diagram fills any box it is given at any viewport width.
export const artFrames: Record<string, [number, number, number, number]> = {
  'code-review-orchestrator': [12, 28, 376, 194],
  'ai-development-harness': [16, 40, 380, 148],
  'nasa-mission-rag': [12, 36, 363, 118],
  'semantic-retrieval-benchmark': [16, 36, 246, 183],
  'ai-budget-predicter': [28, 28, 344, 176],
  'multimodal-moderation': [12, 40, 340, 200],
};

export const frameOf = (slug: string): [number, number, number, number] =>
  artFrames[slug] ?? [0, 0, 400, 250];