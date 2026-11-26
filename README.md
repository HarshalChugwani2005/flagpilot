# FlagPilot

An AI agent that turns a plain-English request into a Flagsmith A/B experiment,
waits for human approval before changing anything, runs it, summarises the results
and rolls out the winner after a second approval.

Built for the Flagsmith "Toggle Wars" hackathon, Track 2 (AI Agents + Feature Flags).

> Work in progress: the repository structure is in place and features land step by step.

## Setup

Requirements: Python 3.11 and GNU Make.

```bash
git clone <this repo> flagpilot
cd flagpilot
make install
cp .env.example .env   # then fill in the values (see comments in the file)
make test
make run-ui            # http://localhost:8501
```

Windows without Make: `winget install ezwinports.make`, then open a new terminal.

| Command | What it does |
| --- | --- |
| `make run-ui` | Streamlit app on port 8501 |
| `make run-store` | Demo store on <http://localhost:8000> |
| `make sim` | Traffic simulator |
| `make seed` / `make reset` | Prepare / clean up the Flagsmith demo project |
| `make test` | Run the test suite |

### Where to get the keys

| Variable | Where |
| --- | --- |
| `NVIDIA_API_KEY` | Free key from <https://build.nvidia.com> (starts with `nvapi-`) |
| `FLAGSMITH_ADMIN_API_KEY` | Flagsmith dashboard → Organisation Settings → API Keys |
| `FLAGSMITH_PROJECT_ID` | The number in the dashboard URL: `/project/<id>/...` |
| `FLAGSMITH_ENVIRONMENT_KEY` | Flagsmith dashboard → Environment → SDK Keys (client-side) |

## License

MIT
