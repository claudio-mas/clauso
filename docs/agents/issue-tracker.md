# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)

### Exact header format

Every ticket starts with its title and then these lines, each in its own paragraph and in this exact form, so that agents and scripts can parse them:

```markdown
# NN: Título

**What to build:** ...

**Blocked by:** None

**Status:** ready-for-agent
```

- `**Blocked by:**` is `None` or a comma-separated list of ticket references. A reference in the same feature is the number (`04`); a reference to another feature is `<prefix>/<NN>`, where the prefix is the branch prefix from `workflow.md` (`m0/11`). A pending human decision is written after a semicolon, in prose (`10; decisão humana sobre hospedagem`), and the ticket stays `blocked` until it is recorded under `## Comments`.
- `**Status:**` takes one value from `triage-labels.md` or from the status cycle in `workflow.md`.
- A spec uses the same bold form, with `**Type:** spec` before `**Status:**`.
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
