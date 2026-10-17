# Digest cookbook

Fold everything in `~/.agents/brain/inbox/` into long-term (and, if VERY
important, short-term) memory. No script - do this yourself with your own
file tools (list, grep, read, write, edit, delete). `~/.agents/brain/` is the
brain store root; if you were given a different "Brain store root", use that
path everywhere instead. Work only inside the store.

1. List `inbox/`. If it has no `.md` files, report "inbox empty, nothing to
   digest" and stop. Otherwise get the current local time ONCE, from the
   shell (`date '+%Y-%m-%dT%H:%M:%S%z'`, rewrite the offset as `+02:00`
   style), and use it for every timestamp below. Never guess the time.
2. Read every file in `inbox/`. For each durable fact (people, decisions +
   rationale, project state, preferences, commitments, pitfalls - not
   trivia, not code, not raw transcript):
   - Project material -> `long-memory/projects/<name>/` on the matching page
     (`entities.md`, `stories.md`, `tasks.md`, `glossary.md` - create only
     what the material needs, never empty stubs).
   - Team / personal / other durable facts -> `long-memory/team/`,
     `long-memory/personal/`, `long-memory/misc/` respectively.
   - Create a subfolder or page ONLY when you have real material for it;
     a fresh store has none of them. Never create placeholder pages.
   - **VERY IMPORTANT facts** (must be in every session's context, not just
     discoverable on demand - e.g. an active hard constraint, a standing
     preference that changes your behavior every time) -> also fold into
     `short-memory.md`.
3. **Merge, never append.** Read the existing page first; a new fact
   replaces or edits a stale line - a digest that only grows a file is a
   failed digest. Never invent facts: record only what the inbox files
   state. Never rewrite the user's stated opinions in your own voice. Use
   declarative phrasing (`Prefers X`, not `Always do X`).
4. `short-memory.md` is a flat body with NO frontmatter and a **hard limit
   of 100 lines, each at most 200 characters** - never exceed either.
   Before/while adding anything, count lines; if adding would push it over
   100, first evict or compress: drop the oldest, least-relevant facts
   (superseded, stale, or lowest-impact-if-forgotten first - not
   necessarily physically last) until there's room, compressing in place
   where possible. End with the file at <=100 lines of <=200 chars.
5. Page format. Every long-memory page (not `index.md`/`log.md`) starts with
   YAML frontmatter, exactly this shape:

   ```
   ---
   type: Concept
   title: Short title
   description: One-line summary.
   tags: [tag-a, tag-b]
   status: stable
   generated: { by: agent:claude-code, at: 2026-10-17T10:00:00+02:00 }
   ---
   ```

   `type` is non-empty (Concept | Project | Profile | Note ...); `status` is
   `stable` or `deprecated` (never `draft`); `at` is the current local time
   in ISO 8601 with offset. Keep the frontmatter on every page you touch and
   refresh `generated` (`by: agent:claude-code`, `at: <now>`) on each edit.
6. Cross-links between pages are standard markdown links. `/x/y.md` is
   relative to the `long-memory/` root; `y.md` or `../y.md` is relative to
   the page's own folder. Every link must resolve to an existing page.
7. Update `long-memory/index.md`: keep its existing frontmatter untouched
   (it has `okf_version` and no `type`), and make the body list EXACTLY every
   page under `long-memory/` except `index.md` and `log.md`, one line each:

   `* [Title](/projects/foo/tasks.md) - description`

   (bundle-relative link starting with `/`; title and description match the
   page's frontmatter). Add new pages, drop deleted ones, fix changed ones.
8. Prepend a dated section to `long-memory/log.md` body (newest-first,
   directly below the frontmatter, above older sections):

   ```
   ## YYYY-MM-DD HH:MM

   * what this digest changed (pages created/updated, short-memory changes)
   ```

   Header must include the time of day - there can be several digests per
   day. Keep log.md's own frontmatter untouched.
9. **Delete every digested file from `inbox/`** - it must end empty. Never
   edit inbox files, only read then delete.
10. Report a short summary: how many inbox files processed, which
    long-memory pages touched, whether anything was promoted to
    short-memory.
