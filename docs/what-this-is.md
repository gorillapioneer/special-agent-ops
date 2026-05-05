# What Special Agent Ops Is

## The problem it solves

AI coding agents are becoming a standard part of the development workflow. Claude, Codex, Devin-style tools, and IDE copilots can generate useful code quickly. But most guidance on using them falls into two bad categories:

1. **Naïve optimism:** "Just give it access to the repo and let it go."
2. **Reflexive rejection:** "Never trust AI-generated code."

Neither is a useful operating posture. The real question is: how do you get the value of AI-assisted development without the specific failure modes that make it dangerous?

## The answer this repo gives

Treat AI agents the way a competent ops team treats any fast-moving, capable, but fallible process: with structured missions, clear boundaries, staged gates, and human sign-off at the right moments.

This is not about being slow or bureaucratic. A well-run ops team moves faster than a chaotic one, because everyone knows their role, mistakes are caught early, and there is a clear path forward when something goes wrong.

The same applies here.

## The five principles

**1. Every agent has a mission, not a mandate.**
An agent is given a specific task with defined inputs and outputs. It is not given free-ranging access to the whole codebase.

**2. Boundaries are set before the agent starts.**
What files can it touch? What can it not touch? This is written down in `SAFE_REPO_BOUNDARIES.md` before any agent session begins.

**3. Every change goes through a review gate.**
No direct pushes to `main`. Every agent-produced change lives on a branch, gets a pull request, and a human reads the diff before it merges.

**4. Sensitive areas are out of scope by default.**
Auth code, payment flows, secrets handling, and deletion logic are not handed to agents unless explicitly and carefully scoped, with extra review steps.

**5. There is always a rollback plan.**
Before any agent-produced change deploys, someone has identified how to revert it if it breaks something.

## What this is not trying to do

This repo is not trying to build a tool that makes agents safer in a technical sense. It is not a sandbox, an API wrapper, or a monitoring system.

It is a set of human workflow practices — supported by templates, checklists, and lightweight scripts — that reduce the chance of an agent making a mess and increase the chance of catching it when it does.

## Who this is for

- Solo developers who use AI assistants and want a more structured approach
- Small teams adding AI tools to an existing development workflow
- Engineering leads setting standards for how their team uses agents
- Anyone who has been burned by an agent making an unexpected change and wants a framework to prevent it

## Who this is not for

This is not for anyone looking for a way to fully automate their development process. That is not what this repo is, and it is not what we recommend.
