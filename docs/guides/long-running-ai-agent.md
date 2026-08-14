# How to Run a Long-Running AI Agent with atom

atom can keep agent work alive across turns through persistent sessions,
scheduled automations, local triggers, and a gateway process that stays running.

## What you will build

- a working local agent
- a persistent chat session
- a scheduled automation or local trigger
- a gateway process for background delivery

## When to use this

Use this when the task is not a one-shot answer: project work, recurring checks,
scheduled summaries, file maintenance, multi-step research, or local triggers
from scripts and build jobs.

## Install

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
atom agent -m "Hello!"
```

## Minimal working example

Start a gateway:

```bash
atom gateway
```

From a chat session, ask atom to schedule recurring work:

```text
Every weekday at 9am, review this workspace for missing tests and report the
smallest next fix.
```

atom creates the cron job from that chat, so it stays linked to the correct
session and workspace. For trigger-based runs, create the trigger from the same
chat and use the `atom trigger ...` command it prints.

## Production notes

- Keep the gateway running for chat apps, automations, and local triggers.
- Use stable session keys or chat sessions for work that should preserve context.
- Keep each scheduled task bounded and explicit about done-ness.
- Ask atom to `list` its cron jobs before relying on a schedule.

## Security notes

- Treat scheduled and triggered runs as delegated work with real tool access.
- Restrict workspaces and shell execution before scheduling unattended tasks.
- Keep chat access narrow so unknown users cannot create automations.

## Troubleshooting

- If a run appears stuck, inspect the active session and gateway logs.
- If an automation does not run, check that it is linked to a chat/session and
  that the gateway is still running.
- If a local trigger fails, check the `atom trigger ...` command that
  `/trigger` printed when the trigger was created.

## Related atom docs

- [Automations](../automations.md)
- [Chat Commands](../chat-commands.md)
- [Memory](../memory.md)
- [Deployment](../deployment.md)
