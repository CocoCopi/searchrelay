# Examples

All examples talk to a running SearchRelay server through the Python SDK
(`clients/python`) and use an OpenAI-compatible chat endpoint for the final
answer.

```bash
.venv/bin/pip install -e clients/python
export SEARCHRELAY_BASE_URL=http://localhost:8000
export SEARCHRELAY_API_KEY=sk-your-key
export ANSWER_API_URL=https://your-endpoint/v1/chat/completions
export ANSWER_API_KEY=sk-your-answer-key
export ANSWER_MODEL=your-model
```

| File | Description |
|---|---|
| `agent.py` | Cited-answer agent: search, fetch the best pages, answer with `[1]`-style citations. |
| `deep_research.py` | Deep-research loop: search, fetch, ask the model whether the evidence is enough, and issue follow-up queries. |
| `streamlit_app.py` | Streamlit demo: run `streamlit run examples/streamlit_app.py`. |

```bash
.venv/bin/python examples/agent.py "What is the DeepSeek API search contract?"
.venv/bin/python examples/deep_research.py "Compare SearXNG and Tavily for self-hosting"
.venv/bin/pip install streamlit && .venv/bin/streamlit run examples/streamlit_app.py
```

Retrieved pages are untrusted third-party content. Every example keeps the
retrieved text inside explicit document boundaries and instructs the answer
model to ignore instructions found inside them.
