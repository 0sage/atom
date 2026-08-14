import ast
import subprocess
import sys
from pathlib import Path

import pytest

import atom.channels._setup as channel_setup_module
import atom.channels.registry as registry_module
from atom.channels._manifest import DIRECT_GROUP_POLICIES, field, one_of, required
from atom.channels._setup import channel_setup_spec
from atom.channels.contracts import ChannelSetupSpec
from atom.channels.plugin import ChannelPlugin, load_channel_package
from atom.channels.registry import channel_default_enabled, discover_plugins

EXPECTED_CHANNELS = {
    "telegram",
}


def test_channel_setup_spec_derives_route_and_secret_metadata() -> None:
    telegram = channel_setup_spec("telegram")

    assert telegram is not None
    assert telegram.secrets == {"token", "proxy"}
    assert telegram.route_field_types == {
        "token": "secret",
        "proxy": "secret",
        "allowFrom": "list",
        "groupPolicy": ("enum", {"mention", "open", "allowlist"}),
    }
    assert telegram.simple_required_fields == ("token",)
    assert telegram.fields["groupPolicy"].default == "mention"
    group_policy = next(
        field
        for field in telegram.to_public_dict("telegram")["fields"]
        if field["field"] == "groupPolicy"
    )
    assert group_policy["default_value"] == "mention"


def test_setup_spec_requires_one_complete_login_method() -> None:
    """``one_of`` requirements accept any single complete alternative group."""
    spec = ChannelSetupSpec(
        fields={
            "homeserver": field("string"),
            "userId": field("string"),
            "password": field("secret"),
            "accessToken": field("secret"),
            "deviceId": field("string"),
        },
        required=(
            required("homeserver"),
            required("userId"),
            one_of(("password",), ("accessToken", "deviceId")),
        ),
    )

    base = {
        "homeserver": "https://home.example",
        "userId": "@atom:home.example",
    }
    assert spec.is_configured(base | {"password": "secret"})
    assert spec.is_configured(base | {"accessToken": "token", "deviceId": "DEVICE"})
    assert not spec.is_configured(base | {"accessToken": "token"})


def test_channel_setup_spec_separates_writable_and_snapshot_fields() -> None:
    snapshot_only = ChannelSetupSpec(
        fields={"allowFrom": field("list", writable=False, snapshot=True)},
    )
    writable_only = ChannelSetupSpec(
        fields={"allowFrom": field("list", writable=True, snapshot=False)},
    )

    assert "allowFrom" not in snapshot_only.route_field_types
    assert "allowFrom" in snapshot_only.snapshot_fields
    assert "allowFrom" in writable_only.route_field_types
    assert "allowFrom" not in writable_only.snapshot_fields


def test_setup_forms_expose_writable_field_kinds() -> None:
    spec = ChannelSetupSpec(
        fields={
            "serverUrl": field("string"),
            "token": field("secret"),
            "allowFrom": field("list"),
            "groupPolicy": field(
                "enum", choices=DIRECT_GROUP_POLICIES, default="mention"
            ),
        },
    )

    assert spec.route_field_types["serverUrl"] == "string"
    assert spec.route_field_types["token"] == "secret"
    assert spec.route_field_types["allowFrom"] == "list"
    assert spec.route_field_types["groupPolicy"] == (
        "enum",
        {"mention", "open"},
    )


def test_every_channel_is_a_self_contained_package() -> None:
    channel_dir = Path(channel_setup_module.__file__).parent
    package_names = {path.parent.name for path in channel_dir.glob("*/manifest.py")}

    assert not hasattr(channel_setup_module, "CHANNEL_SETUP_SPECS")
    assert package_names == EXPECTED_CHANNELS
    assert set(discover_plugins()) == EXPECTED_CHANNELS
    for name in EXPECTED_CHANNELS:
        package_dir = channel_dir / name
        assert (package_dir / "__init__.py").is_file()
        assert (package_dir / "manifest.py").is_file()
        assert (package_dir / "runtime.py").is_file()
        assert not (channel_dir / f"{name}.py").exists()

        plugin = load_channel_package(name)
        assert plugin is not None
        assert plugin.name == name
        assert plugin.runtime.startswith(f"atom.channels.{name}.runtime:")
        assert plugin.setup is channel_setup_spec(name)


def test_channel_manifests_only_import_contract_modules() -> None:
    channel_dir = Path(channel_setup_module.__file__).parent
    allowed_imports = {
        "atom.channels._manifest",
        "atom.channels.contracts",
        "atom.channels.plugin",
    }

    for name in EXPECTED_CHANNELS:
        manifest_path = channel_dir / name / "manifest.py"
        tree = ast.parse(manifest_path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        allowed_channel_imports = {
            module
            for module in imports
            if module.startswith(f"atom.channels.{name}.")
            and not module.endswith(".runtime")
        }
        unexpected = imports - allowed_imports - allowed_channel_imports
        assert not unexpected, f"{name} imports runtime dependencies: {unexpected}"


def test_runtime_classes_do_not_declare_persisted_management_hooks() -> None:
    channel_dir = Path(channel_setup_module.__file__).parent
    management_hooks = {
        "feature_instances",
        "instance_specs",
        "runtime_name",
        "supports_multiple_instances",
        "update_instance_config",
    }
    for name in EXPECTED_CHANNELS:
        tree = ast.parse((channel_dir / name / "runtime.py").read_text(encoding="utf-8"))
        declared = {
            item.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert declared.isdisjoint(management_hooks), f"{name} runtime owns {declared & management_hooks}"


def test_telegram_package_manifest_owns_runtime_metadata() -> None:
    plugin = load_channel_package("telegram")

    assert plugin is not None
    assert plugin.runtime == "atom.channels.telegram.runtime:TelegramChannel"
    assert [dep.split(";")[0].strip() for dep in plugin.dependencies] == [
        "python-telegram-bot[socks,webhooks]>=22.6,<23.0",
        "socksio>=1.0.0,<2.0.0",
        "python-socks[asyncio]>=2.8.0,<3.0.0",
    ]
    assert plugin.management.multi_instance is False


def test_package_manifests_do_not_import_runtimes() -> None:
    code = f"""
import sys
from atom.channels.plugin import load_channel_package

for name in {sorted(EXPECTED_CHANNELS)!r}:
    plugin = load_channel_package(name)
    assert plugin is not None
    assert f"atom.channels.{{name}}.runtime" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_channel_plugin_name_must_match_package_identifier() -> None:
    with pytest.raises(ValueError, match="letters, digits, or underscores"):
        ChannelPlugin(
            name="google-chat",
            display_name="Google Chat",
            runtime="example.google_chat.runtime:GoogleChatChannel",
        )


def test_channel_plugin_rejects_invalid_runtime_import_path() -> None:
    with pytest.raises(ValueError, match="absolute import path"):
        ChannelPlugin(
            name="demo",
            display_name="Demo",
            runtime="../runtime:DemoChannel",
        )


def test_channel_default_enabled_uses_package_manifest(monkeypatch) -> None:
    plugin = ChannelPlugin(
        name="demo",
        display_name="Demo",
        runtime="example.demo.runtime:DemoChannel",
        default_enabled=True,
    )
    monkeypatch.setattr(
        registry_module,
        "load_channel_plugin",
        lambda name: plugin if name == "demo" else (_ for _ in ()).throw(ImportError()),
    )

    assert channel_default_enabled("demo") is True
    assert channel_default_enabled("missing") is False


def test_no_channel_is_enabled_by_default() -> None:
    """Every channel is opt-in; the WebUI's websocket channel was the last default."""
    enabled = {
        name
        for name in EXPECTED_CHANNELS
        if (plugin := load_channel_package(name)) is not None and plugin.default_enabled
    }

    assert enabled == set()
