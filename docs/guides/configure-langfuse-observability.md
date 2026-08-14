# How to Configure Langfuse Observability for atom

atom can trace supported OpenAI-compatible provider calls through Langfuse's
OpenAI SDK wrapper.

## What you will build

- Langfuse installed in the same Python environment as atom
- Langfuse environment variables set before startup
- one traced atom model call

## When to use this

Use Langfuse when you need observability for model requests, latency, errors,
cost, or prompt behavior during development or production operation.

## Install

Install atom and prove the agent works:

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
atom agent -m "Hello!"
```

Install Langfuse:

```bash
atom plugins enable langfuse
```

## Minimal working example

Set credentials before starting atom:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
atom agent -m "Hello!"
```

## Production notes

- Langfuse is configured with environment variables, not `config.json`.
- Start services from an environment that exports the same variables.
- Add tracing after the provider works; it should not be the first setup step.
- Native providers that do not use the OpenAI-compatible client path may not
  produce Langfuse OpenAI-wrapper traces.

## Security notes

- Treat Langfuse projects as observability stores for sensitive prompts and
  outputs.
- Use separate projects for personal, staging, and production traffic.
- Keep Langfuse keys out of committed service files.

## Troubleshooting

- If no traces appear, confirm the service process sees the environment
  variables.
- Confirm the provider path is OpenAI-compatible.
- Run one local `atom agent -m "Hello!"` call before debugging service logs.

## Related atom docs

- [Configuration: Langfuse Observability](../configuration.md#langfuse-observability)
- [Provider Cookbook: Langfuse Tracing](../provider-cookbook.md#recipe-langfuse-tracing)
- [Deployment](../deployment.md)
