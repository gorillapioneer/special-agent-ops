# Verifiable Provenance (prototype)

> "Which agent mission wrote this line, and can you prove it?"

The provenance subsystem (`sao/provenance/`) turns Special Agent Ops mission
recordings into cryptographically checkable claims about who (which agent
mission) changed what, when, and under what declared scope. It is a working
prototype: everything runs locally, stdlib-only, on top of the existing black
box recorder and seals.

Five pieces work together:

| Piece | Command | What it proves |
|---|---|---|
| Transparency ledger | `sao ledger root` / `sao ledger verify` | The set of recorded missions is append-only; nothing was rewritten or dropped |
| Attestations | `sao attest`, `sao run --attest` | A specific commit was produced by a specific sealed mission |
| Flight plans | `sao flight-plan` | The mission declared its scope *before* running |
| PR gate | `sao verify-pr` | Every commit in a PR carries verifiable provenance |
| Line provenance | `sao blame` | Each line of a file maps to the mission that wrote it |

Plus a live-agent interface: `sao mcp` exposes all of it over the Model
Context Protocol so an agent can file flight plans and verify missions while
it works.

---

## The transparency ledger

`blackbox/ledger.jsonl` is an append-only log, one JSON entry per attested
mission:

```json
{"index": 0, "mission_id": "20260715_072822_add_greeter", "leaf_hash": "d69b…d6a2", "timestamp": "…"}
```

The leaf material is the mission seal's `manifest_sha256` (from the session's
`seal.json`), so each ledger entry commits to the full sealed recording.
Entries are hashed into an RFC 6962-style Merkle tree — the same construction
as Certificate Transparency:

```
leaf     = SHA256(0x00 || data)
interior = SHA256(0x01 || left || right)
```

That structure gives two proofs any auditor can check without trusting the
repo owner:

- **Inclusion proof** — "mission X is in the log under root R"
  (`Ledger.inclusion_proof` / `verify_inclusion`).
- **Consistency proof** — "the log at size 12 is a strict prefix of the log
  at size 20" (`consistency_proof` / `verify_consistency`). If anyone edits
  or removes a historical entry, consistency with previously observed roots
  breaks — history rewrites are detectable, not just discouraged.

```bash
sao ledger root                 # {"tree_size": 2, "root_hash": "…"}
sao ledger root --qr root.png   # QR image of the root payload (share/pin it)
sao ledger verify               # recompute leaves from session seals + verify
                                # every inclusion proof; exit 1 on any mismatch
```

Publishing the root (in a PR comment, a pinned issue, a printed QR on the
wall) is what makes the log *transparent*: any future root must be provably
consistent with the one you saw.

## Attestations

`sao attest <mission_id>` (or `--attest` on `sao run` / `sao wrap`) builds a
versioned statement (`"sao-attestation/1"`) binding together:

- mission id/name and the agent command that ran,
- repo, branch, `head_before` → `head_after`,
- `diff_sha256` (hash of the recorded `git_diff.patch`),
- the seal's `manifest_sha256`,
- the ledger position `{leaf_index, leaf_hash, tree_size, root}`,
- `flightplan_sha256` when a flight plan was consumed,
- `parent_attestation_sha256` — the SHA256 of the *previous* attestation's
  canonical JSON, forming a hash chain across missions,
- `created_at`.

Canonical JSON is `json.dumps(sort_keys=True, separators=(",", ":"))`. The
statement is stored two ways:

1. `provenance.json` in the session folder (always). It is written *after*
   sealing — it references the seal — so it is excluded from the seal's
   directory hash (`_DIR_HASH_EXCLUDE` in `sao/blackbox/seal.py`).
2. A **git note** on the commit the mission produced:
   `git notes --ref=refs/notes/sao add -f -m <canonical-json> <head_after>` —
   attached only when the mission ended on a new commit. Notes travel with
   the repo: `git push origin refs/notes/sao`.

### Optional signing

If `SAO_SIGNING_KEY_FILE` points to an SSH private key and `ssh-keygen -Y
sign` is available, the canonical JSON is signed (namespace
`sao-attestation`) into `provenance.json.sig`. Verification uses
`ssh-keygen -Y check-novalidate`, or `-Y verify` against an allowed-signers
file when `SAO_ALLOWED_SIGNERS` is set (identity from
`SAO_SIGNER_IDENTITY`, default `sao`). Everything works unsigned; signatures
are purely additive.

## Flight plans

Declare scope *before* the mission runs:

```bash
sao flight-plan --name "add greeter" \
  --intent "Add a greeting module" \
  --scope "src/**" --scope "tests/**"
```

This writes `blackbox/flightplan.pending.json`. The next recorded mission
consumes it: the plan is copied into the session as `flightplan.json`
**before sealing** (so the seal and archive cover it — a plan cannot be
swapped in afterwards) and its sha256 lands in the attestation. Scope globs
are fnmatch patterns over repo-relative paths of files changed during the
mission; the recorder's own `blackbox/` artefacts are always in scope.

## The PR gate: `sao verify-pr`

```bash
sao verify-pr --base origin/main --head HEAD \
  [--require-attestation] [--strict-scope] [--markdown report.md]
```

Walks every commit in `base..head`. For attested commits it verifies:

- **hash-chain** — `parent_attestation_sha256` links to the previous
  attestation (located via the ledger's previous leaf) where discoverable,
- **ledger-inclusion** — the recorded leaf verifies against the *current*
  ledger root,
- **ledger-consistency** — the root recorded at attestation time is
  append-only-consistent with the current root,
- **diff** — `diff_sha256` matches the session's `git_diff.patch`,
- **session-copy** — the git note matches the session's `provenance.json`,
- **signature** — `provenance.json.sig` verifies when present,
- **scope** — files changed in the commit match the flight-plan globs
  (drift is WARN, or FAIL with `--strict-scope`).

Unattested commits WARN by default and FAIL with `--require-attestation`.
Exit code is 0/1; `--markdown` writes a table suitable for a GitHub check
summary. A copy-paste GitHub Actions workflow for consumer repos lives at
[`templates/verify-pr.yml`](../templates/verify-pr.yml).

## Line-level provenance: `sao blame`

```bash
sao blame src/greeter.py          # annotated listing
sao blame src/greeter.py --json   # machine-readable
```

Runs `git blame --line-porcelain` and maps each line's commit to a mission
through its `refs/notes/sao` attestation. Attested lines show the mission
id; human / pre-provenance lines show `-`.

## Live agent access: `sao mcp`

`sao mcp` serves a dependency-free Model Context Protocol server over stdio
(newline-delimited JSON-RPC 2.0, protocol version `2025-06-18`). Tools:

| Tool | Arguments | Returns |
|---|---|---|
| `file_flight_plan` | name, intent, scope[] | writes the pending flight plan |
| `list_missions` | — | recorded missions |
| `get_mission` | mission_id | manifest + attestation |
| `verify_mission` | mission_id | seal + ledger inclusion verification |
| `ledger_root` | — | current tree size + root hash |
| `blame_file` | path | line → mission mapping |

Example client registration (Claude Code):

```bash
claude mcp add sao-provenance -- sao mcp
```

---

## Copy-paste demo

From any git repo with `special-agent-ops` installed:

```bash
# 1. Declare what the next mission is allowed to touch.
sao flight-plan --name "add greeter" --intent "Add a greeting module" \
  --scope "src/**"

# 2. Record the agent's work with attestation on. The mission writes
#    src/greeter.py and commits it. (Substitute your real agent command.)
sao run --name "add greeter" --attest --command \
  'mkdir -p src && printf "def greet(n):\n    return f\"hi {n}\"\n" > src/greeter.py \
   && git add src/greeter.py && git commit -q -m "feat: add greeter"'
# banner now shows:  Flight Plan: consumed…  Ledger Leaf: #0  Git Note: refs/notes/sao -> …

# 3. Inspect and verify the transparency log.
sao ledger root            # {"tree_size": 1, "root_hash": "…"}
sao ledger verify          # Result: VERIFIED

# 4. Gate the change like a PR reviewer would.
sao verify-pr --base main~1 --head main --require-attestation --strict-scope
# per-commit: attestation, hash-chain, ledger-inclusion, ledger-consistency,
#             diff, session-copy, signature, scope — Result: PASS

# 5. Prove line-level authorship.
sao blame src/greeter.py
#  1  20260715_072822_add_greeter  bce7150927  def greet(n):

# 6. Everything is also queryable by a live agent over MCP:
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | sao mcp
```

Negative paths to try: edit `blackbox/ledger.jsonl` and rerun
`sao ledger verify`; edit a session's `git_diff.patch` or `provenance.json`
and rerun `sao verify-pr` — each tamper is reported as a FAIL.

## Trust model and limitations (prototype)

- The ledger, sessions, and notes live in the same repo they attest. The
  Merkle tree makes history rewrites *detectable by anyone who saw an
  earlier root* — publish roots somewhere you don't control (PR comments,
  chat, QR printouts) to get real transparency.
- Attestations are only as honest as the machine that recorded the mission;
  unsigned attestations prove integrity, not identity. Use SSH signing plus
  an allowed-signers file for identity.
- `git notes` must be pushed/fetched explicitly (`refs/notes/sao`); CI needs
  full history and the notes ref (see the workflow template).
- Scope checking is advisory glob matching, not a sandbox — it detects
  drift after the fact; it does not prevent it.
- One pending flight plan at a time; it applies to the *next* recorded
  mission in the repo.
