# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, if it exists.
- **`docs/adr/`**, if it exists.
- The repository's existing documentation map and agent instructions.

If any of these files do not exist, proceed silently. Do not flag their absence or suggest creating them upfront.

## File structure

This is a single-context repo. Domain vocabulary and decisions apply to the whole project unless a future document says otherwise.

## Use the project's vocabulary

When output names a domain concept, use this repo's vocabulary: investment system, strategy, account, A/B/C combinations, funding transfer, small-cap stock strategy, multi-factor convertible bond strategy, cash pool, current source of truth, research material, and implementation gap.

Do not collapse asset class, strategy, and account into one concept. If a concept is missing or ambiguous, note it for later domain modeling rather than inventing a new synonym.

## Flag decision conflicts

If output contradicts an existing decision record or current source-of-truth document, surface the conflict explicitly instead of silently overriding it.
