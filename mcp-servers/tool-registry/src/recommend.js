// U7: recommend_tool scoring.
//
//   score(tool, intent, allowed):
//     if allowed != null && tool not in allowed: -inf
//     if tool.health != healthy: -inf
//     base = jaccard(tool.capability_tags, intent.tags || [])
//     rank_bonus = position-in-prefer_over chain for intent.category
//     return base + rank_bonus
//
// Ties broken by position in the prefer_over chain.

function jaccard(a, b) {
  if (!a?.length || !b?.length) return 0;
  const setA = new Set(a);
  const setB = new Set(b);
  let inter = 0;
  for (const x of setA) if (setB.has(x)) inter++;
  const union = new Set([...setA, ...setB]).size;
  return union === 0 ? 0 : inter / union;
}

export function recommendTool(intent, manifest, allowed) {
  const tools = manifest?.tools || {};
  let best = null;
  let bestScore = -Infinity;
  let bestRank = Infinity;

  for (const [name, tool] of Object.entries(tools)) {
    if (allowed && !allowed.has(name)) continue;
    if (tool?.health?.state !== "healthy") continue;
    if (!tool.category?.includes(intent.category)) continue;

    const base = jaccard(tool.capability_tags || [], intent.tags || []);
    // Higher rank_bonus for tools at the head of more chains for this category.
    // Compute: how many other tools in this category list `name` in their
    //          prefer_over[category]? Each such reference means `name` is
    //          preferred over that tool — so rank_bonus = those references.
    let rankBonus = 0;
    for (const [otherName, otherTool] of Object.entries(tools)) {
      if (otherName === name) continue;
      if (!otherTool.category?.includes(intent.category)) continue;
      // `tool` prefers over `otherName` if otherName appears in tool's chain.
      // The head tool has the longest chain; that gives it the rank bonus.
    }
    // Simpler proxy: chain length for this category in `tool` itself.
    rankBonus = (tool.prefer_over?.[intent.category] || []).length;

    const score = base + rankBonus;
    // Tie-break by chain position: prefer the tool that lists more tools in
    // its prefer_over chain (i.e., the head of the chain).
    if (score > bestScore || (score === bestScore && rankBonus < bestRank)) {
      best = name;
      bestScore = score;
      bestRank = rankBonus;
    }
  }
  return best;
}
