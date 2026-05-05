# Example: Docs-Only Task

**Risk level:** 🟢 GREEN  
**Agent roles used:** Planner, Builder  
**Estimated time:** 15–20 minutes including review

---

## The scenario

Your project just shipped a new CSV export feature. The README still describes the old export behaviour and is missing the new feature entirely. You want an AI agent to update the README.

---

## Step 1: Write the mission brief

```markdown
# Mission Brief

## Mission title
Update README to document the new CSV export feature

## Risk level
GREEN

## Goal
Update README.md to describe the new CSV export feature added in v1.4.
The export is accessed via the "Export" button in the data table toolbar.
It downloads a CSV of the currently filtered rows.

## In scope
- README.md only

## Out of scope
- All code files
- All other documentation files
- Any changes to examples or screenshots beyond what is in README.md

## Done criteria
- [ ] README.md describes the CSV export feature accurately
- [ ] The description matches actual v1.4 behaviour
- [ ] No other files were modified

## Branch name
feature/docs-export-feature

## What to do if something unexpected comes up
Stop and note it. Do not modify any files other than README.md.
```

---

## Step 2: Planner review

Open Claude (web or mobile). Paste the Planner Agent system prompt from `prompts/planner-agent.md`. Provide the mission brief.

**Planner output (example):**

> Single sub-task: add a section to README.md describing the CSV export feature.
> Risk level: GREEN confirmed — documentation only, no code.
> No dependencies, no risky paths.
> Branch: `feature/docs-export-feature`
> Recommended: draft the section, human edits and commits.

Gate 1: The plan is clear and scoped. Approved.

---

## Step 3: Builder Agent

Open Claude (web or mobile). Paste the Builder Agent system prompt from `prompts/builder-agent.md`. Provide:
- The mission brief
- The current content of README.md
- A brief description of the CSV export feature

The agent will draft the updated README section.

---

## Step 4: Human review and commit

Read the draft. Edit as needed. Confirm it accurately describes the feature.

Create the branch and commit:

```bash
git checkout -b feature/docs-export-feature
# Apply the changes to README.md
git add README.md
git commit -m "docs: document CSV export feature (v1.4)"
```

---

## Step 5: PR and merge

Open a PR. The diff is one file, one new section. Read it. Merge it.

No safety gate script required for a docs-only change (though running it never hurts). No tests to run.

---

## What this teaches

- The branch + PR habit is fast even for a one-file docs change
- The mission brief prevents the agent from helpfully "improving" other parts of the README
- Gate 1 for a GREEN task is a 30-second read, not a committee meeting

---

## Common mistake to avoid

Giving the agent access to all your docs files "just in case it needs context." It will often improve things that weren't in scope. Keep the context window tight.
