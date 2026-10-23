# bbagents

Python building blocks for agent systems: tools, planning, model selection, memory,
human approval, tracing, and evaluation. Works with LangGraph or your own runtime.

Companion code for *Agentic AI: Architecture, Theory, and Practice*.
[Buy the book](https://press.brilberry.com).

<a href="https://press.brilberry.com">
  <img src="assets/book-cover.png" alt="Agentic AI: Architecture, Theory, and Practice by Marco Dinacci" width="240" height="360">
</a>

Requires Python 3.11+.

```sh
pip install -e '.[dev]'
pytest
```

## Complete application example

[Agent Stranded](https://github.com/Brilberry/agentstranded) is a more complete
example: an autonomous survival game using bbagents planning, tools, memory,
skills, communication, and tracing, with an optional local LLM. Its code,
documentation, and run instructions live in that separate repository.

## Features

The previous README's approval/recovery example is preserved as
[recoverable_release.py](examples/recoverable_release.py).

- Typed tools, dependency-checked plans, structured model decisions and validation.
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
