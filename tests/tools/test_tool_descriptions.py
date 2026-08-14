
from atom.agent.tools.apply_patch import ApplyPatchTool
from atom.agent.tools.exec_session import ListExecSessionsTool, WriteStdinTool
from atom.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from atom.agent.tools.search import FindFilesTool, GrepTool
from atom.agent.tools.shell import ExecTool


def test_coding_tool_descriptions_steer_editing_priority() -> None:
    apply_patch = ApplyPatchTool().description.lower()
    edit_file = EditFileTool().description.lower()
    write_file = WriteFileTool().description.lower()

    assert "default tool for code edits" in apply_patch
    assert "multi-file" in apply_patch
    assert "dry_run=true" in apply_patch
    assert "edit_file only for small exact replacements" in apply_patch

    assert "small, exact replacement" in edit_file
    assert "copied from read_file" in edit_file
    assert "prefer apply_patch" in edit_file

    assert "replace an entire file" in write_file
    assert "prefer apply_patch" in write_file


def test_coding_tool_descriptions_steer_discovery_and_shell_usage() -> None:
    read_file = ReadFileTool().description.lower()
    find_files = FindFilesTool().description.lower()
    grep = GrepTool().description.lower()
    exec_tool = ExecTool().description.lower()
    write_stdin = WriteStdinTool().description.lower()
    list_sessions = ListExecSessionsTool().description.lower()

    assert "find_files/list_dir first" in read_file
    assert "before editing" in read_file
    assert "uploaded non-image attachments are referenced by path" in read_file
    assert "only when their contents are needed" in read_file
    assert "prefer it over shell find/ls" in find_files
    assert "prefer this over shell grep" in grep

    assert "tests, builds" in exec_tool
    assert "prefer read_file/find_files/grep" in exec_tool
    assert "apply_patch/write_file/edit_file" in exec_tool
    assert "yield_time_ms" in exec_tool

    assert "do not use this to start new commands" in write_stdin
    assert "wait_for" in write_stdin
    assert "recover a session_id" in list_sessions


def test_exec_tool_shell_guidance_is_unix_only() -> None:
    description = ExecTool().description.lower()
    assert "on unix" in description
    assert "powershell" not in description
    assert "cmd-specific" not in description

    shell_parameter = ExecTool().parameters["properties"]["shell"]["description"].lower()
    assert "override the unix shell only when needed" in shell_parameter
    assert "omit to use bash by default" in shell_parameter
    assert "unix" in shell_parameter
    assert "powershell" not in shell_parameter
    assert "cmd" not in shell_parameter
