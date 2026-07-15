"""sao.provenance — verifiable provenance for AI-written code.

Modules:
    ledger      RFC 6962-style Merkle transparency log (blackbox/ledger.jsonl).
    attest      Git-native attestation statements (git notes + provenance.json).
    flightplan  Pre-declared mission scope (blackbox/flightplan.pending.json).
    verify_pr   PR enforcement gate over attested commits.
    blame       Line-level attribution via git blame + sao notes (best-effort).
    mcp_server  Stdio MCP server exposing provenance tools to live agents.

Stdlib only — no external dependencies (QR output reuses sao.blackbox.qr_image).
"""
