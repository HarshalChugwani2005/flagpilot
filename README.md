# FlagPilot

An AI agent that turns a plain-English request into a Flagsmith A/B experiment,
waits for human approval before changing anything, runs it, summarises the results
and rolls out the winner after a second approval.

Built for the Flagsmith "Toggle Wars" hackathon, Track 2 (AI Agents + Feature Flags).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.11 for you).

```bash
git clone <this repo> flagpilot
cd flagpilot
uv sync
cp .env.example .env   # then fill in the values (see comments in the file)
uv run pytest
```

### Where to get the keys

| Variable | Where |
| --- | --- |
| `NVIDIA_API_KEY` | Free key from <https://build.nvidia.com> (starts with `nvapi-`) |
| `FLAGSMITH_ADMIN_API_KEY` | Flagsmith dashboard → Organisation Settings → API Keys |
| `FLAGSMITH_PROJECT_ID` | The number in the dashboard URL: `/project/<id>/...` |
| `FLAGSMITH_ENVIRONMENT_KEY` | Flagsmith dashboard → Environment → SDK Keys (client-side) |
