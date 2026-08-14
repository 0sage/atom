# Deployment

Use this page after `atom agent -m "Hello!"` works locally. Deployment keeps long-running surfaces online: chat apps, heartbeat, Dream, cron jobs, and channel connections.

## Before You Deploy

Check these once before installing a systemd unit or LaunchAgent:

| Check | Why it matters |
|---|---|
| `atom status` shows the expected config and workspace | Confirms the process will read the instance you meant to run |
| `atom agent -m "Hello!"` works | Proves install, config, provider, model, and workspace writes before adding a service layer |
| Secrets are in environment variables or protected config files | API keys, bot tokens, OAuth state, and chat credentials should not be world-readable |
| `~/.atom/` or your custom config/workspace path is persistent | Sessions, memory, channel login state, generated artifacts, and cron jobs live there |
| Channel access control is intentional | Use `allowFrom`, pairing, or private test channels before exposing the bot |
| Ports are planned | Gateway health defaults to local-only `127.0.0.1:18790`; `atom serve` defaults to `8900` |
| Logs are easy to reach | Use `journalctl`, LaunchAgent log files, or `atom gateway --verbose` while diagnosing startup |

Restart the deployed process after editing `config.json`. Long-running processes read config at startup.

## Install

atom is deployed with `uv` from this git repository. It is not published on PyPI,
and the PyPI name `atom` belongs to an unrelated project, so always install from
the git URL.

```bash
uv tool install "git+https://github.com/0sage/atom.git"
```

Pin a tag or commit for a reproducible deployment, and declare optional
dependencies up front with `--with` so `uv tool upgrade` cannot drop them:

```bash
uv tool install \
  --with "python-telegram-bot[socks,webhooks]>=22.6,<23.0" \
  --with "socksio>=1.0.0,<2.0.0" \
  --with "python-socks[asyncio]>=2.8.0,<3.0.0" \
  "git+https://github.com/0sage/atom.git@main[api]"
```

Any git ref works after `@` — a branch, a tag, or a full commit SHA. A SHA is the
only form that cannot move under you:

```bash
uv tool install "git+https://github.com/0sage/atom.git@0a810c9f"
```

Make sure the `uv` tool bin directory is on `PATH` (`uv tool update-shell`, or set
`UV_TOOL_BIN_DIR` explicitly in a service unit).

Upgrade in place:

```bash
uv tool upgrade atom
```

An unpinned git URL tracks the default branch, so `uv tool upgrade` fetches and
rebuilds whatever `main` currently points at. Pin a tag or commit when you want
upgrades to be an explicit decision.

Anything installed at run time — `atom plugins enable ...`, or channel
dependencies the gateway installs on first start — is not recorded in the tool
receipt, so `uv tool upgrade` rebuilds the environment without it. Declare those
requirements with `--with` for a deployment you intend to upgrade.

## Choose a Runtime

| Runtime | Use it for | State location | Useful first command |
|---|---|---|---|
| Foreground process | Testing a host before installing a service | `~/.atom` unless you pass explicit paths | `atom gateway --verbose` |
| systemd user service | Linux user-level gateway that restarts automatically | Host user's `~/.atom` unless you pass explicit paths | `systemctl --user status atom-gateway` |
| macOS LaunchAgent | macOS gateway that starts after login | Host user's `~/.atom` unless the plist passes explicit paths | `launchctl list \| grep ai.atom.gateway` |
| Background process | Hosts without systemd or launchd (Alpine/OpenRC) | `~/.atom` unless you pass explicit paths | `atom gateway --background` |

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

Preview the generated unit first:

```bash
atom gateway install-service --manager systemd --dry-run
```

Install, enable, and start it:

```bash
atom gateway install-service --manager systemd
```

`install-service` only writes and starts the unit; it does not check that the
gateway can actually run. With no provider configured the unit installs
successfully and then crashloops, since `Restart=always` retries a startup that
refuses every time:

```
Active: activating (auto-restart) (Result: exit-code)
Process: ... (code=exited, status=1/FAILURE)
```

Run `systemctl --user status atom-gateway` after installing. If you see that, run
`atom status` — the reason it refused is printed there, not in the unit's output.
This is why the checks above come first.

For a custom instance, pass the same config/workspace selector you use to run the gateway:

```bash
atom gateway install-service \
  --manager systemd \
  --name atom-telegram \
  --config ~/.atom-telegram/config.json \
  --workspace ~/.atom-telegram/workspace
```

Common operations:

```bash
systemctl --user status atom-gateway        # check status
systemctl --user restart atom-gateway       # restart after config changes
journalctl --user -u atom-gateway -f        # follow logs
atom gateway uninstall-service --manager systemd
```

If `journalctl --user` reports `No journal files were found`, the host is not
writing per-user journals (some minimal images ship without them). The unit's own
lines are still in the system journal, and `status` shows the most recent output:

```bash
sudo journalctl -u atom-gateway -f
systemctl --user status atom-gateway
```

The installer writes `~/.config/systemd/user/atom-gateway.service`, runs
`systemctl --user daemon-reload`, enables the unit, and restarts it. It uses the
current Python executable with `python -m atom gateway --foreground`, so the
service runs in the same environment you used to install atom.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## Hosts Without systemd

`install-service` needs `systemctl` or `launchd`; on Alpine or another OpenRC host
it fails with `No such file or directory: 'systemctl'`. Use the built-in
background mode there, supervised by whatever init the host actually runs:

```bash
atom gateway --background          # start detached
atom gateway status                # running?, PID, port
atom gateway logs --no-follow      # print recent output and exit
atom gateway logs                  # follow new output (Ctrl-C to stop)
atom gateway stop                  # stop it
```

`atom gateway logs` follows by default and does not return on its own; pass
`--no-follow` when you just want the tail, and `--tail N` to choose how much.

`--background` detaches from your shell but nothing supervises it: **it does not
survive a reboot.** After a restart, `atom gateway status` reports
`Reason: stale_state` and the gateway is not running. To start it at boot, have
the host's own init supervise `atom gateway --foreground`. An OpenRC service that
does this:

```sh
# /etc/init.d/atom-gateway
#!/sbin/openrc-run
name="atom-gateway"
command="/home/youruser/.local/bin/atom"
command_args="gateway --foreground"
command_user="youruser"
command_background=true
pidfile="/run/atom-gateway.pid"
output_log="/var/log/atom-gateway.log"
error_log="/var/log/atom-gateway.log"

depend() {
    need net
}

start_pre() {
    checkpath --file --owner "$command_user" --mode 0644 "$output_log"
}
```

The `start_pre` is required, not optional. `start-stop-daemon` drops to
`command_user` before opening the log, so without it the first start fails with
`unable to open the logfile for stdout ... Permission denied` and the service
reports `crashed` — on the first start only, since after that the file exists.

```bash
sudo chmod +x /etc/init.d/atom-gateway
sudo rc-update add atom-gateway default
sudo rc-service atom-gateway start
```

Use `--foreground` in the unit, not `--background`: OpenRC does its own
daemonizing via `command_background`, and a process that forks again would escape
supervision.

## macOS LaunchAgent

Use a LaunchAgent when you want `atom gateway` to stay online after you log in, without keeping a terminal open.

Preview the generated plist first:

```bash
atom gateway install-service --manager launchd --dry-run
```

Install, load, enable, and start it:

```bash
atom gateway install-service --manager launchd
```

For a custom instance:

```bash
atom gateway install-service \
  --manager launchd \
  --name atom-telegram \
  --config ~/.atom-telegram/config.json \
  --workspace ~/.atom-telegram/workspace
```

Common operations:

```bash
launchctl list | grep ai.atom.gateway
launchctl kickstart -k gui/$(id -u)/ai.atom.gateway
atom gateway uninstall-service --manager launchd
```

The installer writes `~/Library/LaunchAgents/ai.atom.gateway.plist`, uses the
current Python executable with `python -m atom gateway --foreground`, and
writes LaunchAgent logs under `~/.atom/logs/`.

> **Note:** if startup fails with "address already in use", stop the manually started `atom gateway` process first.
