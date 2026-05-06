# Special Agent Ops 60-Second Demo Script

Use this from the repository root when demoing the GitHub README flow.

## Setup

```bash
pip install -e .
```

## Flow

| Time | Action | Command |
|---|---|---|
| 0-10s | Run a mission and copy the printed Mission ID. | `python -m sao.cli run --name "demo mission" --command "python --version"` |
| 10-20s | Show the mission index. | `python -m sao.cli list` |
| 20-30s | Inspect the mission details. | `python -m sao.cli show <mission_id>` |
| 30-40s | Verify the tamper-evident seal. | `python -m sao.cli verify <mission_id>` |
| 40-50s | Open the standalone HTML card. | `python -m sao.cli open <mission_id>` |
| 50-60s | Start the local dashboard. | `python -m sao.cli dashboard --port 8765` |

Replace `<mission_id>` with the ID printed by the first command.

## Copy/Paste Version

```bash
python -m sao.cli run --name "demo mission" --command "python --version"
python -m sao.cli list
python -m sao.cli show <mission_id>
python -m sao.cli verify <mission_id>
python -m sao.cli open <mission_id>
python -m sao.cli dashboard --port 8765
```

The dashboard runs at `http://127.0.0.1:8765`. Press `Ctrl+C` in the terminal to stop it.
