"""sao.provenance — verifiable provenance for AI-written code.

Modules:
    ledger      RFC 6962-style Merkle transparency log (blackbox/ledger.jsonl).
    attest      Git-native attestation statements (git notes + provenance.json).
    flightplan  Pre-declared mission scope (blackbox/flightplan.pending.json).
    envelope    in-toto Statements + DSSE envelopes with pluggable signers.
    ci_issue    CI-side attestation issuance/verification (ci-verified tier).
    checkpoint  Signed ledger checkpoints + witness cosignatures.
    witness     Independent, stateful checkpoint cosigner (anti-equivocation).
    anchor      External git-native anchoring of checkpoints (append-only ref).
    verify_pr   PR enforcement gate over attested commits (tier-aware).
    blame       Line-level attribution via git blame + sao notes (best-effort).
    mcp_server  Stdio MCP server exposing provenance tools to live agents.

Stdlib only — no external dependencies (QR output reuses sao.blackbox.qr_image).
"""
