# rentcheck

An open-source CLI that A/B tests each section of `AGENTS.md`/`CLAUDE.md`, each
agent skill, and each MCP server — so you can see which ones actually help a
coding agent and which only waste tokens.

> Placeholder. Commands are stubs for now.

## Install

```bash
pip install -e .
```

## Usage

```bash
rentcheck --version
rentcheck scan     # inventory agent config (sections, skills, MCP servers)
rentcheck mine     # mine historical agent runs for candidate tasks
rentcheck ablate   # A/B test each element on/off and measure impact
rentcheck report   # render results as HTML/Markdown
```

## License

MIT
