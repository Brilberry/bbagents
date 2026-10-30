# bbagents

<a href="https://www.buymeacoffee.com/brilberry">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy me a coffee" width="217" height="60">
</a>

Python building blocks for agent systems: tools, planning, model selection, memory,
human approval, tracing, and evaluation. Works with LangGraph or your own runtime.

Companion code for *Agentic AI: Architecture, Theory, and Practice*.
[Buy on Brilberry Press](https://press.brilberry.com) or
[Buy on Barnes & Nobles](https://www.barnesandnoble.com/w/agentic-ai-marco-dinacci/1151816412?ean=2940185526651).

<a href="https://press.brilberry.com">
  <img src="assets/book-cover.png" alt="Agentic AI: Architecture, Theory, and Practice by Marco Dinacci" width="240" height="360">
</a>

Requires Python 3.11+.

Install from PyPI:

```sh
python -m pip install bbagents
```

For development, install from a repository checkout and run the tests:

```sh
python -m pip install -e '.[dev]'
pytest
```

## Complete application example

[Agent Stranded](https://github.com/Brilberry/agentstranded) is a more complete
example: an autonomous survival game using bbagents planning, tools, memory,
skills, communication, and tracing, with an optional local LLM. Its code,
documentation, and run instructions live in that separate repository.

## Features

- Typed tools, dependency-checked plans, structured model decisions and validation.
- Prioritized behavior controllers with explicit abstention and selection traces.
- Native behavior trees with running actions, cancellation and execution traces.
- Per-agent model selection, routing constraints, cost estimates and caching.
- Capability policies, human intervention, explicit delegation and LangGraph interrupts.
- Bounded async execution, retry policies, execution journals and effect reconciliation.
- Versioned checkpoints, SQLite persistence, task budgets and completion gates.
- Budgeted context, summarization contracts and provider-neutral retrieval (RAG).
- Scoped, revisioned memory and skills; evaluated and approved procedure promotion.
- Roles, messages, delegation and organizational governance.
- Structured traces, isolated evaluation trials and compaction-continuation checks.
- Optional Ollama, LangChain, LangGraph, LangSmith and Langfuse integrations.

No mandatory runtime dependencies. Applications own domain behavior and adapters.

See [examples](examples/), [demos](demos/) and the [guide](GUIDE.md).

Maintainers: see [release instructions](RELEASING.md) for publishing to PyPI.
