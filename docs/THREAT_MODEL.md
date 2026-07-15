# Threat Model

Special Agent Ops (SAO) is a **tamper-evident, git-native audit framework
for recording and governing declared AI-agent work**. This document states
precisely what that buys you, what it does not, and which attacks are
mitigated versus open.

The one-sentence honest summary, from an external security review of the
provenance prototype:

> SAO can prove that someone controlling the workstation and relevant keys
> produced a consistent record. It cannot prove the record accurately
> describes what the agent did. The cryptography freezes the story, but the
> storyteller and the suspect share a keyboard.

Every seal, ledger proof, and attestation proves **integrity since
sealing**, not **truthfulness at creation**.

---

## Trust boundaries

There is essentially one trust domain on the recording side:

- The **agent** (the wrapped command), the **recorder** (`sao run` /
  `sao wrap`), and the **signing key** (when `SAO_SIGNING_KEY_FILE` is set)
  all run under the **same user account on the same workstation**.
- The agent can read and write the same files the recorder reads and
  writes, including `blackbox/`, the ledger, the sessions, and (usually)
  the signing key.
- **Nothing on the workstation is a trusted observer.** A record produced
  there is a *declaration* by that trust domain, not testimony from an
  independent witness.

Independence only enters the picture when something leaves the
workstation: a ledger root published where the operator cannot rewrite it,
a verifier running elsewhere (CI), or a future external witness.

## What the system DOES guarantee

- **Tamper-evidence since sealing.** Once a session is sealed
  (`seal.json`), any later change to the recorded manifest, outputs, diff,
  flight plan, or archive is detectable by `sao verify` /
  `sao verify-archive` / `sao verify-pr`.
- **Append-only ledger relative to a pinned root.** The Merkle
  transparency ledger (RFC 6962-style) yields inclusion and consistency
  proofs. Anyone who pinned an earlier root (PR comment, chat message,
  printed QR) can detect any rewrite, reorder, or removal of history
  relative to that root.
- **Scope-drift tripwire.** A flight plan filed *before* the mission is
  sealed into the session; `sao verify-pr` flags commits that touch files
  outside the declared globs. This detects drift after the fact.
- **Git-object binding (attestation v2).** An attestation records the
  result commit's tree OID and each changed path's blob OID + mode, so the
  claimed change is checked against git's own immutable objects, not just
  against recorded diff text.
- **Quiesced sealing snapshot.** On POSIX the wrapped command runs in its
  own process group and any surviving background children are killed
  *before* after-state capture and sealing, so a straggler process cannot
  mutate the tree between capture and seal.

## What the system does NOT guarantee

- **Truthful evidence creation.** The recorder runs in the same trust
  domain as the thing it records. A malicious or compromised operator (or
  agent with workstation control) can fabricate an internally consistent
  record from scratch.
- **Identity, unless signing is configured.** Unsigned attestations carry
  no identity at all. Signed ones prove "someone holding this key" — and
  on a shared workstation the agent may be able to read the key.
- **Protection against a malicious operator.** All local checks assume
  you are auditing honest-but-fallible work, or an attacker who arrived
  *after* sealing / root publication. The operator themself is outside the
  defended perimeter.
- **Sandboxing or prevention.** Flight-plan scope checking is advisory
  glob matching after the fact; it does not confine the agent. Nothing in
  SAO prevents an action — it records and (later) detects.

## Attack catalogue

| Attack | Status | Notes / mitigation |
|---|---|---|
| **Forged-from-scratch consistent history** — operator fabricates sessions, ledger, attestations, and commits that all cross-verify | **Unmitigated locally; final claim mitigated at the CI-verified tier** | By construction, a workstation-only verifier cannot distinguish this from honest history. Mitigations: pin ledger roots externally (publish early, publish often); the `CI-verified` tier (`sao ci-issue`) mints the *final* attestation outside the operator's domain — the workstation can still fabricate evidence, but it can no longer issue the authoritative claim |
| **Ledger split-view / fork / rollback / freshness attacks** — different verifiers are shown different logs, or an old log is replayed | **Partially mitigated** | Consistency proofs detect rewrites *relative to a root you already hold*. Publish roots externally — e.g. the QR export (`sao ledger root --qr`), PR comments, chat — so there is a widely held root to compare against. Future: independent witnesses/co-signing |
| **Git note replacement** — `refs/notes/sao` notes can be force-replaced (`git notes add -f`) without changing the commit SHA, and are not pushed/fetched by default | **Mitigated by design role** | Notes are a **discovery index, not the durable security store**. The durable copy is the session's `provenance.json` (sealed context, hash-chained, ledgered). v2 notes carry `payload_sha256` so `sao verify-pr` cross-checks note vs session copy; a note whose session is gone is reported as an unverifiable WARN. Push/fetch `refs/notes/sao` explicitly; the CI template does |
| **Background-process race** — the wrapped command leaves a child running that mutates files after after-state capture, so the seal covers a lie | **Mitigated** | On POSIX the command runs in its own session; surviving process-group members are SIGTERM'd (then SIGKILL'd) before after-state capture and sealing. Sealing binds to a quiesced snapshot. Windows falls back to previous behaviour (no group kill) |
| **Archive bombs / malicious zips** — crafted session archives with traversal names, symlinks, duplicates, or extreme compression ratios attack the verifier | **Mitigated** | The verifier validates every entry before extraction: absolute names, `..` segments (both separators), duplicates, and symlink entries are rejected; a total decompression budget and per-entry ratio cap make bombs fail cleanly |
| **Persistent prompt injection via memory/lessons** — a compromised mission plants instructions in recorded content (stdout, summaries, "lessons") that a future agent retrieves and obeys | **Unmitigated by tooling — handle by policy** | Memory/lessons content is **untrusted retrieved data**. Never feed recorded session content into privileged instructions (system prompts, tool policies) without human review. Treat it like any other untrusted input |
| **Tampering after sealing** — edit recorded files, ledger entries, notes, or archives post hoc | **Mitigated** | This is the core designed-for case: seals, directory hashes, ledger proofs, hash chain, and git-object checks each FAIL on their respective tamper |

## Graduated assurance tiers

Provenance claims should state which tier they were produced at. Higher
tiers move evidence creation or verification outside the operator's trust
domain.

| Tier | What it means | Who must you trust | Status |
|---|---|---|---|
| `self-recorded` | Sessions sealed and ledgered locally, unsigned | The workstation operator entirely | **Current default** |
| `locally-signed` | Attestations signed with an SSH key (`SAO_SIGNING_KEY_FILE`), verified against an allowed-signers file | The operator, minus post-hoc tampering by third parties; key may be agent-readable | **Available now (opt-in)** |
| `CI-verified` | A trusted CI job (`sao ci-issue`) verifies the local evidence bundle, independently recomputes the commit's git objects, applies policy, and *issues* the final DSSE attestation under an identity the coding agent cannot access; `sao verify-pr --min-tier ci-verified` enforces it | The CI control plane, its pinned workflow, and its signing secret | **Implemented (opt-in)** |
| `independently-witnessed` | Ledger roots co-signed / mirrored by parties the operator does not control | A quorum of witnesses | **Future** |

The current implementation provides the first three tiers:
**self-recorded** by default, **locally-signed** when signing is
configured, and **CI-verified** when a trusted CI job issues the final
attestation (see
[docs/PROVENANCE.md](PROVENANCE.md#ci-issued-attestations--the-ci-verified-tier)).
Everything a verifier reports should be read with its tier in mind.

Be precise about what `CI-verified` adds — and what it does not:

- It **closes workstation-side forgery of the final claim**. The
  workstation submits *evidence*; the authoritative attestation is
  minted by a CI identity (pinned workflow, secret-held key) the coding
  agent cannot access, after the CI job has independently recomputed the
  commit's tree and changed blobs from git itself and applied policy
  (flight-plan scope, recorded checks). An operator can no longer sign
  an authoritative "this passed" statement from the same keyboard the
  agent used, and a `ci-issue` run outside CI refuses to claim the tier.
- It does **NOT make the local evidence truthful**. The session
  recording is still produced inside the workstation trust domain; a
  fabricated-but-internally-consistent evidence bundle that also matches
  git reality still passes. The tier certifies *independent
  recomputation of git reality plus policy*, not testimony about what
  happened on the workstation.

## Related documents

- [`docs/PROVENANCE.md`](PROVENANCE.md) — how the ledger, attestations,
  flight plans, PR gate, and blame view work, including exact hash
  domains.
- [`docs/SECURITY_MODEL.md`](SECURITY_MODEL.md) — the recorder/black-box
  security notes.
- [`SECURITY.md`](../SECURITY.md) — reporting vulnerabilities.
