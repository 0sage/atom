# Automations

<!-- Meta description: Create, run, and manage atom scheduled automations, local triggers, and heartbeat-backed background checks. -->

Automations are agent turns that run later in a linked topic. Use them
when atom should do work without someone actively typing: reminders,
recurring checks, nightly summaries, CI follow-ups, local script reports, or
webhook-driven events.

Create automations from the chat channel or session where the
result should appear. That lets atom keep the right session history,
workspace, and reply target.

## Choose an Automation Type

| Type | Starts from | Best for | Created with |
|---|---|---|---|
| Scheduled automation | Time, interval, or cron expression | Recurring reminders, scheduled summaries, one-time future tasks | Ask atom in the target topic to schedule it with the `cron` tool |
| Local trigger | A local `atom trigger ...` command | CI jobs, webhooks, shell scripts, generated reports | `/trigger <name>` in the target topic |
| Heartbeat | Protected system schedule | Quiet recurring checks that should only report useful results | Edit `<workspace>/HEARTBEAT.md` |

The two user-created automation types are scheduled automations and local
triggers. Heartbeat uses the same background service but is system-managed and
protected from normal automation edits.

## Before You Create One

Keep `atom gateway` running. The gateway owns background delivery for chat
apps, scheduled automations, local triggers, heartbeat, and Dream jobs.

Use the same workspace and config for the gateway and any process that sends
local trigger messages. If you run multiple atom instances, pass the matching
`--config` or `--workspace` option to `atom trigger`.

Create each automation from the target topic. An automation without a linked
topic cannot run, because atom would not know where to deliver the turn.

## Scheduled Automations

Scheduled automations are created by the agent's `cron` tool. In practice, ask
atom from the target chat or session:

```text
Every weekday at 9am, check open pull requests and summarize blockers here.
```

or:

```text
Tomorrow at 4pm, remind me to send the release notes.
```

The cron tool supports interval schedules, cron expressions, and one-time
scheduled tasks. Cron expressions can include an IANA timezone such as
`America/Vancouver`; otherwise atom uses the runtime default timezone.

Scheduled automations normally deliver the result back to the session where they
were created. Use them for work that should run on a predictable schedule and
report each run.

For background checks that should stay quiet unless there is something useful to
report, use heartbeat instead of a user-created scheduled automation.

## Local Triggers

Local triggers let a local script or external service send a message into a
specific atom session later.

Create the trigger from the chat or session where future messages should
arrive:

```text
/trigger PR review
```

atom replies with a trigger ID and a command shaped like:

```bash
atom trigger trg_8K4P2Q9X "Review PR #4502"
```

Replace the quoted text with the message atom should receive. For generated
or longer content, pipe stdin:

```bash
generate-report | atom trigger trg_8K4P2Q9X
```

For multiple instances, use the same config or workspace selector as the
gateway:

```bash
atom trigger --config ./bot-a/config.json trg_8K4P2Q9X "Nightly report"
atom trigger --workspace ./bot-a/workspace trg_8K4P2Q9X "Nightly report"
```

atom does not provide a built-in public webhook receiver for local triggers.
If GitHub, CI, or another external system should wake atom, run your own
small webhook service and have it call `atom trigger` after it builds the
final message.

## Heartbeat

Heartbeat is for recurring workspace checks that should usually stay quiet. It
reads `<workspace>/HEARTBEAT.md`, executes active tasks, and sends only useful or
actionable results to the most recently active chat target.

Use heartbeat for checks such as "watch this repo for important failures" or
"periodically inspect this workspace and only tell me when action is needed." Use
a scheduled automation instead when every run should produce a visible reminder
or report.

Heartbeat is enabled by default when `atom gateway` starts. Configure it in
[`configuration.md#gateway-heartbeat`](./configuration.md#gateway-heartbeat).

## Manage Automations

Ask atom in a chat to use its `cron` tool: `list` shows scheduled automations
with their IDs, and `remove` deletes one by ID. Heartbeat appears in that list as
a protected system job that the tool cannot remove.

Local triggers are delivered by running the `atom trigger ...` command that
`/trigger` printed when you created the trigger, with `"message"` replaced by the
content to deliver.

## Delivery and Reliability

Automation delivery is workspace-local. Scheduled jobs and local trigger
deliveries use the same workspace as the gateway.

Local trigger messages are written to a durable queue. If the gateway is not
running yet, the message waits in that workspace. If the linked topic is
already running a turn, the trigger waits until the session becomes idle instead
of being injected into the active turn.

The local trigger queue is at-least-once, not exactly-once. If the gateway exits
after claiming a delivery but before the linked turn completes, the next gateway
start requeues that delivery. External scripts should make repeated trigger
messages safe. If the delivery reaches the agent and the turn fails, the
delivery is marked failed instead of retrying forever.

Each local trigger delivery writes an audit record under
`<workspace>/triggers/runs`. Run one gateway consumer per workspace; the local
queue is not a distributed multi-consumer queue.

## Common Patterns

For a nightly report, ask from the target topic:

```text
Every night at 9pm, review today's workspace changes and summarize anything I should handle tomorrow.
```

For a CI follow-up, create a trigger once:

```text
/trigger CI follow-up
```

Then have your CI or webhook adapter call:

```bash
atom trigger <trigger-id> "Build failed on main. Inspect the logs and suggest the next fix."
```

For a local report script:

```bash
generate-report | atom trigger <trigger-id>
```

## Troubleshooting

If an automation does not run, check that `atom gateway` is running, the
automation is enabled, and it was created from a linked topic.

If a local trigger waits forever, confirm the command uses the same workspace or
config as the gateway.

If a trigger message appears twice after a restart, treat it as expected
at-least-once delivery and make the external message idempotent.

## Related Docs

- [`chat-commands.md#local-triggers`](./chat-commands.md#local-triggers) for `/trigger`
- [`cli-reference.md#local-triggers`](./cli-reference.md#local-triggers) for `atom trigger`
- [`configuration.md#gateway-heartbeat`](./configuration.md#gateway-heartbeat) for heartbeat settings
- [`guides/long-running-ai-agent.md`](./guides/long-running-ai-agent.md) for long-running agent work
