"""Tests for cross-platform shell execution.

Verifies that ExecTool selects the correct shell, environment, path-append
strategy, and sandbox behaviour per platform — without actually running
platform-specific binaries (all subprocess calls are mocked).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from atom.agent.tools.shell import ExecTool

# ---------------------------------------------------------------------------
# _build_env
# ---------------------------------------------------------------------------

class TestBuildEnvUnix:

    def test_expected_keys(self):
        env = ExecTool()._build_env()
        expected = {"HOME", "LANG", "TERM", "PYTHONUNBUFFERED"}
        assert set(env) == expected

    def test_home_from_environ(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/dev")
        env = ExecTool()._build_env()
        assert env["HOME"] == "/Users/dev"

    def test_secrets_excluded(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("ATOM_TOKEN", "tok-secret")
        env = ExecTool()._build_env()
        assert "OPENAI_API_KEY" not in env
        assert "ATOM_TOKEN" not in env
        for v in env.values():
            assert "secret" not in v.lower()


class TestSpawnUnix:

    @pytest.mark.asyncio
    async def test_uses_bash(self):
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = AsyncMock()
            await ExecTool._spawn("echo hi", "/tmp", {"HOME": "/tmp"})

        args = mock_exec.call_args[0]
        assert "bash" in args[0]
        assert "-l" not in args
        assert "-c" in args
        assert "echo hi" in args

        kwargs = mock_exec.call_args[1]
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL

    @pytest.mark.asyncio
    async def test_process_tree_starts_new_session(self):
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = AsyncMock()
            await ExecTool._spawn(
                "echo hi",
                "/tmp",
                {"HOME": "/tmp"},
                process_tree=True,
            )

        assert mock_exec.call_args.kwargs["start_new_session"] is True


class TestPathAppendPlatform:

    @pytest.mark.asyncio
    async def test_unix_uses_env_var_in_fixed_export(self):
        """On Unix, path_append must not be interpolated into shell source."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        captured_cmd = None
        captured_env = {}

        async def capture_spawn(
            cmd, cwd, env, shell_program=None, login=True, *, process_tree=False,
        ):
            nonlocal captured_cmd
            captured_cmd = cmd
            captured_env.update(env)
            return mock_proc

        with (
            patch("atom.agent.tools.shell.os.pathsep", ":"),
            patch.object(ExecTool, "_spawn", side_effect=capture_spawn),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool(path_append="/opt/bin; echo INJECTED")
            await tool.execute(command="ls")

        assert captured_cmd == 'export PATH="$PATH:$ATOM_PATH_APPEND"; ls'
        assert captured_env["ATOM_PATH_APPEND"] == "/opt/bin; echo INJECTED"
        assert "INJECTED" not in captured_cmd

    @pytest.mark.asyncio
    async def test_unix_path_prepend_uses_env_var_in_fixed_export(self):
        """On Unix, path_prepend must not be interpolated into shell source."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        captured_cmd = None
        captured_env = {}

        async def capture_spawn(
            cmd, cwd, env, shell_program=None, login=True, *, stdin=None, process_tree=False,
        ):
            nonlocal captured_cmd
            captured_cmd = cmd
            captured_env.update(env)
            return mock_proc

        with (
            patch("atom.agent.tools.shell.os.pathsep", ":"),
            patch.object(ExecTool, "_spawn", side_effect=capture_spawn),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool(path_prepend="/venv/bin; echo INJECTED")
            await tool.execute(command="python --version")

        assert captured_cmd == 'export PATH="$ATOM_PATH_PREPEND:$PATH"; python --version'
        assert captured_env["ATOM_PATH_PREPEND"] == "/venv/bin; echo INJECTED"
        assert "INJECTED" not in captured_cmd

    @pytest.mark.asyncio
    async def test_unix_path_prepend_and_append_order(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        captured_cmd = None
        captured_env = {}

        async def capture_spawn(
            cmd, cwd, env, shell_program=None, login=True, *, stdin=None, process_tree=False,
        ):
            nonlocal captured_cmd
            captured_cmd = cmd
            captured_env.update(env)
            return mock_proc

        with (
            patch("atom.agent.tools.shell.os.pathsep", ":"),
            patch.object(ExecTool, "_spawn", side_effect=capture_spawn),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool(path_prepend="/venv/bin", path_append="/usr/sbin")
            await tool.execute(command="python --version")

        assert captured_cmd == (
            'export PATH="$ATOM_PATH_PREPEND:$PATH:$ATOM_PATH_APPEND"; python --version'
        )
        assert captured_env["ATOM_PATH_PREPEND"] == "/venv/bin"
        assert captured_env["ATOM_PATH_APPEND"] == "/usr/sbin"

class TestSandboxPlatform:

    @pytest.mark.asyncio
    async def test_bwrap_applied_on_unix(self):
        """On Unix, sandbox wrapping should still happen normally."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"sandboxed", b"")
        mock_proc.returncode = 0

        with (
            patch("atom.agent.tools.shell.wrap_command", return_value="bwrap -- sh -c ls") as mock_wrap,
            patch.object(ExecTool, "_spawn", return_value=mock_proc) as mock_spawn,
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool(sandbox="bwrap", working_dir="/workspace")
            await tool.execute(command="ls")

        mock_wrap.assert_called_once()
        spawned_cmd = mock_spawn.call_args[0][0]
        assert "bwrap" in spawned_cmd

    @pytest.mark.asyncio
    async def test_bwrap_receives_configured_bind_roots(self, tmp_path):
        """Configured bwrap bind roots should be forwarded to the sandbox wrapper."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"sandboxed", b"")
        mock_proc.returncode = 0
        tool_bin = tmp_path / "tool-bin"
        tool_cache = tmp_path / "tool-cache"

        with (
            patch("atom.agent.tools.shell.wrap_command", return_value="bwrap -- sh -c ls") as mock_wrap,
            patch.object(ExecTool, "_spawn", return_value=mock_proc),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool(
                sandbox="bwrap",
                working_dir="/workspace",
                sandbox_ro_binds=[str(tool_bin)],
                sandbox_rw_binds=[str(tool_cache)],
            )
            await tool.execute(command="ls")

        kwargs = mock_wrap.call_args.kwargs
        assert kwargs["sandbox_ro_binds"] == [
            str(tool_bin.resolve(strict=False))
        ]
        assert kwargs["sandbox_rw_binds"] == [
            str(tool_cache.resolve(strict=False))
        ]


# ---------------------------------------------------------------------------
# end-to-end (mocked subprocess, full execute path)
# ---------------------------------------------------------------------------

class TestExecuteEndToEnd:

    @pytest.mark.asyncio
    async def test_unix_full_path(self):
        """Full execute() flow on Unix: env, spawn, output formatting."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"hello world\n", b"")
        mock_proc.returncode = 0

        with (
            patch.object(ExecTool, "_spawn", return_value=mock_proc),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool()
            result = await tool.execute(command="echo hello world")

        assert "hello world" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_execute_defaults_to_non_login_shell(self):
        """The public execute path must not silently request a login shell."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok\n", b"")
        mock_proc.returncode = 0
        captured_login = []

        async def capture_spawn(
            cmd, cwd, env, shell_program=None, login=None, *, stdin=None, process_tree=False,
        ):
            captured_login.append(login)
            return mock_proc

        with (
            patch.object(ExecTool, "_spawn", side_effect=capture_spawn),
            patch.object(ExecTool, "_guard_command", return_value=None),
        ):
            tool = ExecTool()
            await tool.execute(command="echo ok")
            await tool.execute(command="echo ok", login=True)

        assert captured_login == [False, True]


# ---------------------------------------------------------------------------
# _extract_absolute_paths
# ---------------------------------------------------------------------------

class TestExtractAbsolutePaths:
    """Tests for absolute path extraction in shell commands."""

    def test_posix_absolute_path(self):
        """Absolute POSIX paths are extracted."""
        cmd = "ls /var/log/syslog"
        paths = ExecTool._extract_absolute_paths(cmd)
        assert "/var/log/syslog" in paths

    def test_multiple_posix_paths(self):
        """Every absolute path in a chained command is extracted."""
        cmd = "cp /etc/hosts /tmp/hosts.bak && ls /tmp"
        paths = ExecTool._extract_absolute_paths(cmd)
        assert "/etc/hosts" in paths
        assert "/tmp/hosts.bak" in paths
        assert "/tmp" in paths

    def test_home_path(self):
        """Test extraction of home directory shortcuts."""
        cmd = "cat ~/config.txt"
        paths = ExecTool._extract_absolute_paths(cmd)
        assert "~/config.txt" in paths

    def test_no_paths(self):
        """Test command with no absolute paths."""
        cmd = "echo hello"
        paths = ExecTool._extract_absolute_paths(cmd)
        assert paths == []
