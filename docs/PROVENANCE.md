# Verifiable Provenance (prototype)

> "Which agent mission declared this change, and has the record been
> tampered with since?"

The provenance subsystem (`sao/provenance/`) is a **tamper-evident,
git-native audit framework for recording and governing declared AI-agent
work**. It turns Special Agent Ops mission recordings into checkable
statements about which mission *declared* which change, when, and under
what declared scope — and makes any later tampering with those records
detectable. It is a working prototype: everything runs locally,
stdlib-only, on top of the existing black box recorder and seals.

What it is **not**: cryptographic proof that an agent (rather than a
human at the same keyboard) wrote the code, or that the record truthfully
describes what happened at creation time. The seals and proofs guarantee
integrity *since sealing*, not truthfulness *at creation* — the recorder
and the agent share one trust domain. Read
[docs/THREAT_MODEL.md](THREAT_MODEL.md) before relying on any of this.

Five pieces work together:

| Piece | Command | What it gives you |
|---|---|---|
| Transparency ledger | `sao ledger root` / `sao ledger verify` | Rewrites of the recorded-mission log are detectable against any previously pinned root |
| Attestations | `sao attest`, `sao run --attest` | A commit is bound to a declared, sealed mission recording (including the commit's git object IDs) |
| Flight plans | `sao flight-plan` | The mission declared its scope *before* running; drift is detectable |
| PR gate | `sao verify-pr` | Every commit in a PR is checked for consistent, untampered declared provenance |
| Line provenance | `sao blame` | A derived, best-effort view mapping surviving lines to missions (see caveats below) |

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
consistent with the one you saw. Without an externally pinned root, a
malicious operator can regenerate the whole log — see the split-view entry
in the [threat model](THREAT_MODEL.md#attack-catalogue).

## Attestations

`sao attest <mission_id>` (or `--attest` on `sao run` / `sao wrap`) builds a
versioned statement (`"sao-attestation/2"`) binding together:

- mission id/name and the agent command that ran,
- repo, branch, `head_before` → `head_after`,
- `diff_sha256` (hash of the recorded `git_diff.patch` — kept as a review
  artifact),
- `git_objects` — the result commit bound to **git object IDs**, not just
  diff text: the parent commit OID, the result commit OID, the result
  **tree OID** (`git rev-parse <commit>^{tree}`), and for each changed
  path its **blob OID and file mode**. Omitted when the mission did not
  end on a new commit,
- the seal's `manifest_sha256`,
- the ledger position `{leaf_index, leaf_hash, tree_size, root}`,
- `flightplan_sha256` when a flight plan was consumed,
- `parent_attestation_sha256` — the SHA256 of the *previous* attestation's
  canonical JSON, forming a hash chain across missions,
- `created_at`.

Canonical JSON is `json.dumps(sort_keys=True, separators=(",", ":"))`.
Verifiers accept both `sao-attestation/1` (which predates `git_objects`)
and `sao-attestation/2`. The statement is stored two ways:

1. `provenance.json` in the session folder (always) — the **durable
   store**. It is written *after* sealing — it references the seal — so it
   is excluded from the seal's directory hash (`_DIR_HASH_EXCLUDE` in
   `sao/blackbox/seal.py`).
2. A **git note** on the commit the mission produced:
   `git notes --ref=refs/notes/sao add -f -m <note-json> <head_after>` —
   attached only when the mission ended on a new commit. The note body is
   the canonical statement plus a `payload_sha256` field (SHA256 of the
   statement's canonical JSON) so the note can be cross-checked against
   the session copy.

   **The note is a discovery index, not the durable security store.**
   Git notes can be force-replaced without changing the commit SHA, and
   they are **not pushed or fetched by default** — "provenance travels
   with clones" only when `refs/notes/sao` is explicitly pushed and
   fetched:

   ```bash
   git push origin refs/notes/sao
   git fetch origin '+refs/notes/sao:refs/notes/sao'
   ```

   The CI template ([`templates/verify-pr.yml`](../templates/verify-pr.yml))
   fetches the notes ref explicitly. A note whose session folder is gone
   is unverifiable discovery metadata, and `sao verify-pr` says so (WARN).

### Optional signing

If `SAO_SIGNING_KEY_FILE` points to an SSH private key and `ssh-keygen -Y
sign` is available, the canonical JSON is signed (namespace
`sao-attestation`) into `provenance.json.sig`. Verification uses
`ssh-keygen -Y check-novalidate`, or `-Y verify` against an allowed-signers
file when `SAO_ALLOWED_SIGNERS` is set (identity from
`SAO_SIGNER_IDENTITY`, default `sao`). Everything works unsigned; signatures
are purely additive. Unsigned attestations carry no identity at all —
see the [assurance tiers](THREAT_MODEL.md#graduated-assurance-tiers).

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

- **attestation** — the statement version is supported (v1 or v2),
- **hash-chain** — `parent_attestation_sha256` links to the previous
  attestation (located via the ledger's previous leaf) where discoverable,
- **ledger-inclusion** — the recorded leaf verifies against the *current*
  ledger root,
- **ledger-consistency** — the root recorded at attestation time is
  append-only-consistent with the current root,
- **diff** — `diff_sha256` matches the session's `git_diff.patch`,
- **git-objects** — the recorded tree OID matches the commit's actual
  tree, and each recorded changed-path blob OID/mode matches
  `git ls-tree` reality (v2; v1 statements SKIP this check),
- **session-copy** — the git note matches the session's `provenance.json`,
  including the note's `payload_sha256` cross-check against the full
  canonical JSON of the session copy. When the session folder is gone
  this is a WARN: a note alone is unverifiable discovery metadata,
- **signature** — `provenance.json.sig` verifies when present,
- **scope** — files changed in the commit match the flight-plan globs
  (drift is WARN, or FAIL with `--strict-scope`).

Unattested commits WARN by default and FAIL with `--require-attestation`.
Exit code is 0/1; `--markdown` writes a table suitable for a GitHub check
summary. A copy-paste GitHub Actions workflow for consumer repos lives at
[`templates/verify-pr.yml`](../templates/verify-pr.yml) — it checks out
full history and explicitly fetches `refs/notes/sao`.

## Line-level provenance: `sao blame`

```bash
sao blame src/greeter.py          # annotated listing
sao blame src/greeter.py --json   # machine-readable
```

Runs `git blame --line-porcelain` and maps each line's commit to a mission
through its `refs/notes/sao` attestation. Attested lines show the mission
id; human / pre-provenance lines show `-`.

**Line attribution is a derived, best-effort view.** git blame maps the
*surviving textual line* to the commit that last touched it — code
movement, copying, reformatting, and merge-conflict resolution all distort
attribution. A reformat commit "takes over" every line it re-wraps; a
conflict resolution can attribute an agent's line to the human who
resolved it (and vice versa). **Commit/patch-level provenance —
attestations with their recorded diff and git object IDs — is canonical**;
blame output is a convenience layered on top. Both the human output
(footer `NOTE:` line) and the `--json` output
(`"confidence": "derived-best-effort"`) carry this caveat.

## Hash domains — exactly what each hash covers

Every hash below is SHA256 over a precisely bounded byte domain. Knowing
what is *inside* and *outside* each domain is what makes the seal
meaningful.

| Hash | Inside the domain | Outside the domain |
|---|---|---|
| `manifest_sha256` (seal.json) | The raw bytes of `manifest.json` | Everything else |
| `archive_sha256` (seal.json) | The raw bytes of the `.zip` file exactly as written — including zip container metadata (member order, per-entry headers, stored timestamps) | The seal itself; any file written after compression |
| `session_directory_sha256` (seal.json) | For every raw data file in the session folder, the line `"<relative/posix/path>:<file_sha256>\n"`, in sorted path order — i.e. **file paths and file contents only** | **File timestamps, permissions/modes, ownership, symlinks, and empty directories are NOT covered.** Also excluded by name: the seal files themselves and derived views (`seal.json`, `seal.txt`, cards, QR payloads/images, `mission_summary.md`, `pr_report.md`, `provenance.json[.sig]`) — see `_DIR_HASH_EXCLUDE` in `sao/blackbox/seal.py` |
| `diff_sha256` (attestation) | The raw bytes of the recorded `git_diff.patch` | The working tree itself; anything git did not put in that diff |
| `payload_sha256` (git note) | The attestation statement's canonical JSON bytes | The note envelope; the session folder |
| ledger `leaf_hash` | `SHA256(0x00 ‖ manifest_sha256-as-text)` per RFC 6962 leaf hashing | Everything not committed to by `manifest_sha256` |
| attestation identity | The statement's canonical JSON (`sort_keys`, `(",", ":")` separators), hashed as UTF-8 | The `.sig` file; the git note's `payload_sha256` envelope field |

Two structural consequences worth stating plainly:

- **The companion seal lives *outside* the archive precisely to avoid
  circularity.** `seal.json` records `archive_sha256`, so it cannot be
  inside the archive it hashes; likewise the seal files are excluded from
  the directory hash they record. When distributing an archive, ship
  `<archive>.zip` together with `<archive>.zip.seal.json`.
- **The archive hash covers bytes, not semantics.** Matching
  `archive_sha256` proves the zip file is bit-identical, including
  timestamps stored in zip headers. Content-level verification of an
  extracted archive goes through `manifest_sha256` +
  `session_directory_sha256`, which deliberately ignore timestamps and
  permissions — so a verified archive proves *paths and contents*, not
  modes or mtimes.

Archives from untrusted sources are validated before extraction:
entries with absolute names, `..` segments (either separator), duplicate
names, or symlink entries are rejected, and a decompression budget
(total-size cap plus per-entry compression-ratio cap) makes archive bombs
fail cleanly (`sao/blackbox/compressor.py::validate_archive_members`).

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
| `blame_file` | path | line → mission mapping (derived, best-effort) |

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
#             diff, git-objects, session-copy, signature, scope — Result: PASS

# 5. Line-level attribution (derived, best-effort — see caveats above).
sao blame src/greeter.py
#  1  20260715_072822_add_greeter  bce7150927  def greet(n):

# 6. Everything is also queryable by a live agent over MCP:
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | sao mcp
```

Negative paths to try: edit `blackbox/ledger.jsonl` and rerun
`sao ledger verify`; edit a session's `git_diff.patch` or `provenance.json`
and rerun `sao verify-pr`; rewrite an attestation's recorded tree or blob
OIDs — each tamper is reported as a FAIL.

## Trust model and limitations (prototype)

The full analysis — trust boundaries, guarantees and non-guarantees, the
attack catalogue, and graduated assurance tiers — lives in
[docs/THREAT_MODEL.md](THREAT_MODEL.md). The short version:

- **The seal proves integrity since sealing, not truthfulness at
  creation.** The agent, the recorder, and any signing key share one user
  account on the workstation; nothing recorded there is testimony from an
  independent observer.
- The ledger, sessions, and notes live in the same repo they attest. The
  Merkle tree makes history rewrites *detectable by anyone who saw an
  earlier root* — publish roots somewhere you don't control (PR comments,
  chat, QR printouts) to get real transparency.
- Unsigned attestations prove integrity, not identity. Use SSH signing plus
  an allowed-signers file for identity — and note the key usually lives in
  the same trust domain as the agent.
- `git notes` are a discovery index: replaceable without changing the
  commit SHA and only transferred when `refs/notes/sao` is explicitly
  pushed/fetched. The durable record is the session's `provenance.json`;
  CI needs full history and the notes ref (see the workflow template).
- Sealing binds to a **quiesced snapshot**: on POSIX the recorder kills
  any surviving process-group members of the wrapped command before
  capturing after-state and sealing.
- Scope checking is advisory glob matching, not a sandbox — it detects
  drift after the fact; it does not prevent it.
- Recorded session content (summaries, stdout, "lessons") is untrusted
  retrieved data; never feed it into privileged instructions for future
  agents without review (persistent prompt-injection risk).
- One pending flight plan at a time; it applies to the *next* recorded
  mission in the repo.
