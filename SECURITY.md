# Security Policy

## Scope

This repository contains templates, prompts, documentation, and scripts. It does not run as a service or handle user data.

Security concerns relevant to this repo:

- **Scripts with unintended behaviour** — `safety-gate.py`, `check-secrets.sh`, `check-secrets.ps1`
- **Prompts that could be weaponised** — instructions that could be used to make agents bypass safety checks or exfiltrate data
- **Documentation that gives dangerously wrong advice** — guidance that would lead users to expose secrets or skip critical review steps

## Reporting a vulnerability

If you find a security issue in this repo, please do not open a public issue.

Email: see the repo owner's GitHub profile for contact details.

Please include:
- A clear description of the issue
- Which file(s) are affected
- What the impact is if the issue is followed as written
- A suggested fix if you have one

We will respond within 5 business days and aim to resolve confirmed issues within 14 days.

## What this repo does NOT cover

This repo does not:
- Store secrets
- Connect to external services
- Handle authentication or user data
- Execute agent commands on your behalf

The scripts in `scripts/` are designed to be read and run locally by the user. They do not transmit data.

## General guidance on secrets

The single most important rule in this repo: **never put secrets in a git repository, public or private.**

If you discover a secret has been committed, treat it as compromised immediately:
1. Revoke and rotate the credential
2. Check git history for other leaks
3. Then clean the history

Cleaning git history after a leak does not make the secret safe — it was already exposed. Revoke first.
