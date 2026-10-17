# Brain

You have a persistent memory layer: the store at `~/.agents/brain/`. Files
are plain markdown, loaded on demand - you decide when to read more.

## Where things live

- `short-memory.md` - already injected above. Durable, high-level facts.
- `long-memory/index.md` - map of every long-memory page. Read it first
  when you need something not in short-memory.
- `long-memory/log.md` - dated history of what the digest recorded.
- `long-memory/projects/<name>/` - per-project pages (entities, stories,
  tasks, glossary), created only when there's material for them.
- `long-memory/team/`, `long-memory/personal/`, `long-memory/misc/` - team,
  personal, and everything-else facts (same rule: only when there's material).
- `inbox/` - raw session dumps waiting to be folded in; not for you to read.

## How to use it

- Search with `grep -r` over `~/.agents/brain/long-memory/` - don't assume
  a page exists, check `index.md` or grep first.
- Don't write directly into `short-memory.md` or `long-memory/` on your own
  initiative; that's the digest's job. If something durable comes up
  mid-session, just say it naturally - it will be captured and folded in
  when the session ends.
- **Exception:** if the user explicitly asks you to update/write/fix memory
  now (not just states a fact in passing), edit the relevant file(s)
  directly, right away, following the same rules the digest follows
  (`~/.claude/skills/brain/references/digest.md`: merge, don't append; keep
  frontmatter; update `index.md`/`log.md` if a page changed).
- If the brain feels stale or wrong, tell the user; don't silently patch it.
