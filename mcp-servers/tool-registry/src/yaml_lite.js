// Minimal YAML subset parser for the shapes this registry uses:
//   - top-level mapping of string -> object
//   - object fields: string scalars, inline `[a, b, c]` lists,
//     nested mappings indented by 2 spaces, multi-line block lists ("- foo")
//   - top-level list of scalars ("- foo")
//
// This is deliberately not a full YAML parser. The annotation files committed
// to this repo are small and human-authored; bringing in js-yaml just for this
// would balloon the committed dependency surface (KTD3 keeps node_modules
// minimal). Errors include the offending line for easy diagnosis.

export function parseSimpleYaml(text) {
  const lines = text.split(/\r?\n/);
  // Strip comments and blank lines while tracking original line numbers.
  const rows = [];
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const stripped = raw.replace(/\s+#.*$/, "").replace(/^\s*#.*$/, "");
    if (!stripped.trim()) continue;
    rows.push({ line: i + 1, raw: stripped });
  }
  if (rows.length === 0) return {};

  // Decide top-level shape.
  if (rows[0].raw.trim().startsWith("- ")) {
    // Top-level list of scalars.
    return rows.map((r) => parseScalar(r.raw.trim().slice(2).trim()));
  }
  return parseMapping(rows, 0, 0).value;
}

function indent(raw) {
  let n = 0;
  while (n < raw.length && raw[n] === " ") n++;
  return n;
}

function parseScalar(value) {
  if (value === "null" || value === "~") return null;
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+$/.test(value)) return parseInt(value, 10);
  if (/^-?\d+\.\d+$/.test(value)) return parseFloat(value);
  // Inline list: [a, b, c]
  if (value.startsWith("[") && value.endsWith("]")) {
    const inner = value.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(",").map((s) => parseScalar(stripQuotes(s.trim())));
  }
  // Inline map: { a: 1 } — not currently used; not supported.
  return stripQuotes(value);
}

function stripQuotes(value) {
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

// Parses a mapping block starting at row index `start` with indentation
// `baseIndent`. Returns { value, nextIndex }.
function parseMapping(rows, start, baseIndent) {
  const out = {};
  let i = start;
  while (i < rows.length) {
    const row = rows[i];
    const ind = indent(row.raw);
    if (ind < baseIndent) break;
    if (ind > baseIndent) throw new Error(`yaml_lite: unexpected indent at line ${row.line}`);

    const content = row.raw.slice(baseIndent);
    const colonIdx = content.indexOf(":");
    if (colonIdx === -1) {
      throw new Error(`yaml_lite: expected 'key: value' at line ${row.line}: ${content}`);
    }
    const key = content.slice(0, colonIdx).trim();
    const rest = content.slice(colonIdx + 1).trim();

    if (rest === "") {
      // Nested block: look at the next non-blank row to decide list vs map.
      if (i + 1 < rows.length) {
        const next = rows[i + 1];
        const nextInd = indent(next.raw);
        if (nextInd <= baseIndent) {
          // Empty block — treat as empty mapping.
          out[key] = {};
          i++;
          continue;
        }
        const nextContent = next.raw.slice(nextInd);
        if (nextContent.startsWith("- ")) {
          const { value, nextIndex } = parseBlockList(rows, i + 1, nextInd);
          out[key] = value;
          i = nextIndex;
          continue;
        }
        const { value, nextIndex } = parseMapping(rows, i + 1, nextInd);
        out[key] = value;
        i = nextIndex;
        continue;
      } else {
        out[key] = null;
        i++;
        continue;
      }
    }

    out[key] = parseScalar(rest);
    i++;
  }
  return { value: out, nextIndex: i };
}

function parseBlockList(rows, start, baseIndent) {
  const out = [];
  let i = start;
  while (i < rows.length) {
    const row = rows[i];
    const ind = indent(row.raw);
    if (ind < baseIndent) break;
    if (ind > baseIndent) throw new Error(`yaml_lite: unexpected indent at line ${row.line}`);
    const content = row.raw.slice(baseIndent);
    if (!content.startsWith("- ")) break;
    const value = content.slice(2).trim();
    out.push(parseScalar(value));
    i++;
  }
  return { value: out, nextIndex: i };
}
