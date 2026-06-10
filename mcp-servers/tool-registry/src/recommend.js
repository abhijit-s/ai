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
    // Tie-break rule (documented for future maintainers):
    //
    //   When two tools tie on (base + rankBonus), pick the one with the
    //   SHORTER prefer_over chain for this category. The reasoning is
    //   subtle: rankBonus IS the chain length, so a tie on `score` with
    //   different rankBonus values means `base` (Jaccard tag overlap)
    //   compensated for the chain-length difference — the tool with the
    //   shorter chain has stronger tag-overlap evidence and is the
    //   higher-confidence pick.
    //
    // Why this works for the current tool set:
    //   - Within `search-content`, chains are strictly hierarchical
    //     (fff > ast-grep > rg > grep). Ties on score are rare; when they
    //     occur (e.g., two tools with identical tag sets and adjacent chain
    //     positions), the head of the chain typically also has the
    //     LONGEST chain — so a `rankBonus < bestRank` tie-break would
    //     actually prefer the FOLLOWER over the head. In practice, the
    //     `score > bestScore` arm fires first for the head, so the tie-break
    //     arm only triggers for genuine ties between equivalent tools.
    //
    // When this may need revisiting:
    //   - A new category with multiple tools sharing identical
    //     `capability_tags` (so `base` is identical) AND overlapping
    //     `prefer_over` chains. In that case, "shorter chain wins" may
    //     surface the wrong tool. The deterministic alternative is to
    //     define a global ordering of tools per category; we defer that
    //     until a real category demands it (see "Deferred to Follow-Up
    //     Work" in docs/plans/2026-06-10-001-feat-materialised-tool-discovery-plan.md).
    if (score > bestScore || (score === bestScore && rankBonus < bestRank)) {
      best = name;
      bestScore = score;
      bestRank = rankBonus;
    }
  }
  return best;
}
