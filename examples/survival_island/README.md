# Survival Island

A deterministic 30×30 agent simulation demonstrating long-running plans,
replanning, bounded tools, partial observability, communication, and conflicting
survival needs. Five autonomous survivors explore a generated island, find water
and food, gather materials, craft tools, and build shelters.

The included policy is deliberately deterministic and explainable. It can later be
replaced with a model-backed decision policy without changing the world tools or
letting model output mutate simulation state directly.

From the repository root:

```sh
python -m pip install -e '.[game]'
python -m examples.survival_island.app
```

Controls:

- `Space`: pause/resume
- `Right arrow`: advance one tick while paused
- `+` / `-`: simulation speed

The headless simulation does not require pygame:

```python
from examples.survival_island import Simulation

simulation = Simulation(seed=7)
for _ in range(200):
    simulation.step()
```

### Model policy

With an Ollama server already running and a model already downloaded:

```sh
python -m examples.survival_island.app --model YOUR_INSTALLED_MODEL
```

Use `--endpoint URL` for another Ollama-compatible `/api/chat` endpoint.
No provider SDK or API key is required for a local server. A model is not
downloaded automatically. Requests have a 90-second timeout and run on one
background worker so rendering continues. One request per agent is allowed in
flight. Pending agents observe/rest; urgent needs and failures use the reference
policy. Use `ollama list` to choose an installed model, such as `qwen3.5:4b`.

The model proposes a goal, a short plan, and one action. Proposed plans appear in
the sidebar; each subsequent model response revises that proposal. These are
advisory plans, not automatically executed action sequences. The reference policy
retains its own executable plan. Model responses pass tool-name and argument
validation, then the world applies its ordinary resource and location checks.
`ContextBuilder` budgets snapshots. `DecisionRuntime` validates a tool-specific
schema, retries a failed response once, and records standard decision trace events.
There is no tick-based response expiry. The UI counts accepted proposals and
failures; per-agent status and the terminal expose errors. Trace events `model.decision` and
`model.failed` distinguish actual model responses from fallback actions.

The baseline supports resting by observing while stationary. Requests include explicit legal actions and current-tile
resources. Framework validation rejects choices unavailable in the snapshot, and
the main thread checks preconditions again against the live world before acceptance.
The last eight observations include tool results, failures, and rejected actions
so the next model request can correct mistakes. Observing depletion removes the
resource location from personal memory.
The most recent failure is retained separately from the recent-observation buffer.

The baseline replenishes
forest berries each day. This makes sustained survival possible. Weather,
disease, predators, and voice are not implemented yet.
