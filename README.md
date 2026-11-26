# FlagPilot

An AI agent that turns a plain-English request into a Flagsmith A/B experiment,
waits for human approval before changing anything, runs it, summarises the results
and rolls out the winner after a second approval.

Built for the Flagsmith "Toggle Wars" hackathon, Track 2 (AI Agents + Feature Flags).

## Setup

Requirements: Python 3.11, [uv](https://docs.astral.sh/uv/) (FlagPilot starts the official
Flagsmith MCP server with `uvx`), Node.js 20+ (demo-store tests) and GNU Make.

```bash
git clone <this repo> flagpilot
cd flagpilot
make install
cp .env.example .env   # then fill in the values (see comments in the file)
make test
.venv/Scripts/python -m agent.llm --smoke               # checks NVIDIA_API_KEY
.venv/Scripts/python -m agent.flagsmith_client --smoke   # checks Flagsmith, lists projects
make run-ui            # http://localhost:8501
```

Windows without Make: `winget install ezwinports.make`, then open a new terminal.

| Command | What it does |
| --- | --- |
| `make run-ui` | Streamlit app on port 8501 |
| `make run-store` | Demo store on <http://localhost:8000> (its `config.js` is generated from `.env`) |
| `make sim ARGS="--rates control=0.08,green_buy_now=0.11"` | Simulate 500 visitors (`--visitors 2000` for the final demo only; 1 API request each) |
| `make seed` / `make reset` | Prepare / clean up the Flagsmith demo project |
| `make test` | Run the test suite |

### Using the app

1. **New experiment**: describe a test in plain English (or click an example), review the
   plan, the exact Flagsmith changes and the guardrail checks, then approve, edit or reject.
2. **Experiments**: every approved experiment with its status and traffic split.
3. **Results**: verdict, conversion rates with 95% confidence intervals, an AI summary, and
   a second approval (type the flag name) to roll out the winner.
4. **Audit log**: every plan, approval and Flagsmith call; filter by flag, export as CSV.

Set `MANUAL_BASELINE_MINUTES` in `.env` to the time a manual setup takes you; the sidebar's
"Time saved" compares it with FlagPilot's measured prompt-to-live time.

### Where to get the keys

| Variable | Where |
| --- | --- |
| `NVIDIA_API_KEY` | Free key from <https://build.nvidia.com> (starts with `nvapi-`) |
| `FLAGSMITH_ADMIN_API_KEY` | Flagsmith dashboard → Organisation Settings → API Keys |
| `FLAGSMITH_PROJECT_ID` | The number in the dashboard URL: `/project/<id>/...` |
| `FLAGSMITH_ENVIRONMENT_KEY` | Flagsmith dashboard → Environment → SDK Keys (client-side) |

## License

MIT
