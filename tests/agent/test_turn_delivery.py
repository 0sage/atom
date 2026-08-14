from pathlib import Path

from atom.agent.turn_delivery import TurnDeliveryFactory, TurnRoute
from atom.bus.events import InboundMessage
from atom.bus.queue import MessageBus
from atom.bus.runtime_events import RuntimeEventBus


def _factory(route_policy=None) -> TurnDeliveryFactory:
    return TurnDeliveryFactory(MessageBus(), RuntimeEventBus(), route_policy=route_policy)


def test_channel_message_routes_back_to_its_own_channel() -> None:
    msg = InboundMessage(
        channel="telegram",
        sender_id="user",
        chat_id="chat-a",
        content="hello",
        metadata={"origin_message_id": "m-1"},
    )

    delivery = _factory().create(msg, msg.session_key)

    assert delivery.route.channel == "telegram"
    assert delivery.route.chat_id == "chat-a"
    assert delivery.route.publish_lifecycle is True
    assert delivery.route.metadata == {"origin_message_id": "m-1"}
    assert delivery.delivery_message.channel == "telegram"
    assert delivery.lifecycle_message.chat_id == "chat-a"


def test_system_message_chat_id_is_split_into_channel_and_chat() -> None:
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="discord:777",
        content="Background research completed",
        metadata={"origin_message_id": "m-9", "injected_event": "subagent_result"},
    )

    route = _factory().create(msg, "discord:777").route

    assert route.channel == "discord"
    assert route.chat_id == "777"
    # System turns stay silent unless an edge policy opts them in.
    assert route.publish_lifecycle is False
    # Only the allow-listed origin id is forwarded; injected hints are dropped.
    assert route.metadata == {"origin_message_id": "m-9"}


def test_system_message_without_channel_prefix_falls_back_to_cli() -> None:
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="direct",
        content="done",
    )

    route = _factory().create(msg, "cli:direct").route

    assert route.channel == "cli"
    assert route.chat_id == "direct"


def test_system_route_carries_slack_thread_from_session_key() -> None:
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="slack:C123",
        content="done",
    )

    route = _factory().create(msg, "slack:C123:1700000000.001").route

    assert route.channel == "slack"
    assert route.metadata["slack"] == {"thread_ts": "1700000000.001"}


def test_route_policy_can_override_the_default_route() -> None:
    def policy(_msg: InboundMessage, _session_key: str, route: TurnRoute) -> TurnRoute:
        return TurnRoute(
            channel=route.channel,
            chat_id=route.chat_id,
            metadata={"tagged": True},
            publish_lifecycle=True,
        )

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="discord:777",
        content="done",
    )

    route = _factory(policy).create(msg, "discord:777").route

    assert route.publish_lifecycle is True
    assert route.metadata == {"tagged": True}


def test_unrouted_preserves_the_inbound_channel_and_metadata(tmp_path: Path) -> None:
    def policy(_msg: InboundMessage, _session_key: str, _route: TurnRoute) -> TurnRoute:
        raise AssertionError("unrouted() must not consult the route policy")

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="discord:777",
        content="done",
        metadata={"injected_event": "subagent_result"},
    )

    delivery = _factory(policy).unrouted(msg, "discord:777")

    assert delivery.route.channel == "system"
    assert delivery.route.chat_id == "discord:777"
    assert delivery.route.metadata == {"injected_event": "subagent_result"}
