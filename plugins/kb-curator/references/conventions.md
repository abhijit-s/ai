# Vault conventions

These are the rules the `kb-curator` skill enforces. They reflect what the vault already does in 80%+ of files — the skill exists to push the remaining 20% into line and to keep new material consistent from day one.

## Directory shape

```
KnowledgeBase/
  01-Languages & Runtimes/
    Golang/
      README.md                    # area index
      01-Language/                 # optional ordered sub-area
        README.md
        Crash Courses/             # topic group (no number prefix)
        Idioms & Features/
      02-Concurrency/
      03-Runtime/
      99-Reference/                # always last
```

- **Pillars** are the seven top-level numbered directories (`01-…07-`). The number is part of the name and encodes top-level reading order.
- **Areas** sit one level below a pillar. Each area maps 1:1 to a `category` slug.
- **Sub-areas** are optional. When used, they get a numeric prefix (`01-`, `02-`, …) to encode a learning sequence. A `99-` prefix is the convention for "reference, look this up later".
- **Topic groups** (no number prefix) cluster related notes inside an area or sub-area. They don't get their own category slug — notes in `Golang/01-Language/Crash Courses/` still have `category: golang`.

## Slugs

| Layer       | Style                                  | Example                       |
| ----------- | -------------------------------------- | ----------------------------- |
| Pillar dir  | Title Case with `&`, `NN-` prefix      | `02-Architecture & System Design` |
| Pillar slug | kebab-case                             | `architecture-system-design`  |
| Area dir    | Title Case                             | `Distributed Systems`         |
| Area slug   | kebab-case (= `category` value)        | `distributed-systems`         |
| Tag         | kebab-case, lowercase, ASCII           | `deep-dive`                   |

## Frontmatter

Every `.md` file inside the vault (except those in `zAttachments/` and `.obsidian/`) carries:

```yaml
---
title: Buffered vs Unbuffered Channels
category: golang
tags:
  - golang
  - concurrency
  - channels
---
```

- `title` matches the H1 in the body. When they disagree, the H1 is authoritative.
- `category` is exactly one slug from `taxonomy.yaml > categories`. Not inferred at query time; written explicitly.
- `tags` is a list, never a string. The first element mirrors `category`. Subsequent tags express cross-cutting concerns or sub-topics.
- READMEs additionally carry the `index` tag.

Optional fields tolerated (do not strip if present): `aliases`, `kind`, `spec`, `metadata`.

## Tag vocabulary

The `taxonomy.yaml > tags` list defines the **controlled** vocabulary — these are the recall scaffolding, the ones worth typing into the search bar. Tags outside the controlled list are permitted but the audit flags any tag that appears only once across the whole vault.

**Rule of thumb:** if you'd never search by it, don't tag by it. `gotcha`, `interesting`, `weird` are bad tags — they slice the vault into unmemorable subsets.

## READMEs as indexes

Every directory that contains notes should also contain a `README.md` whose body links to its peers via `[[wiki-links]]` and a one-line description per link. The README is the area's table of contents; it is the page a reader lands on when they navigate into the area.

Minimal README:

```md
---
title: <Area Name>
category: <area-slug>
tags:
  - <area-slug>
  - index
---

# <Area Name>

<One-paragraph orientation: what question this area answers, what a reader should expect to find here.>

## <Group / Sub-area>

- [[Note Title]] — one-line description
- [[Another Note]] — one-line description
```

## When to add a new area

The threshold is roughly:

- **≥5 notes** with a clearly shared central question that isn't answered by an existing area, OR
- **≥3 notes** plus a near-term plan to write more.

Below that bar, place notes in the closest existing area and tag them. A premature split fragments recall.

## When to add a new pillar

Almost never. Pillars are decade-scale. If you genuinely need one (a domain entirely outside the seven), discuss it explicitly with the user before editing `taxonomy.yaml`.
