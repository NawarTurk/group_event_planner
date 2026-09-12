from bot import normalize_message_for_agent, should_route_to_agent


def test_normalize_message_for_agent_removes_extra_whitespace():
    assert normalize_message_for_agent("  Hello   there   friend  ") == "Hello there friend"


def test_should_route_to_agent_only_for_non_empty_messages():
    assert should_route_to_agent("What should we do this weekend?") is True
    assert should_route_to_agent("   ") is False
    assert should_route_to_agent("") is False
