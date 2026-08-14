"""Operator-declared secrets the agent can use without being shown their values.

Values are stored outside the workspace and injected into the shell tool's
subprocess environment; the agent learns only the names and references them as
``$NAME``. This keeps values out of prompts and session history — it is not
containment, since a shell can print its own environment.

Design decisions and known gaps: ``.agent/privacy.md``.
"""
