"""Unit tests for the slash-command parser/router in commands.py."""
import pytest

import commands


def test_non_command_message_returns_none():
    assert commands.resolve("Qual o preço da PETR4?") is None


def test_empty_and_whitespace_are_not_commands():
    assert commands.resolve("") is None
    assert commands.resolve("   ") is None


def test_help_command_returns_direct_reply_listing_all_commands():
    resolved = commands.resolve("/help")
    assert resolved is not None
    assert resolved.prompt_for_agent is None
    for spec in commands.COMMANDS.values():
        assert spec.usage in resolved.direct_reply


def test_unknown_command_returns_direct_reply_with_help():
    resolved = commands.resolve("/naoexiste PETR4")
    assert resolved is not None
    assert resolved.prompt_for_agent is None
    assert "não reconhecido" in resolved.direct_reply
    assert "/price" in resolved.direct_reply


@pytest.mark.parametrize(
    "message,expected_substring",
    [
        ("/price PETR4", "PETR4"),
        ("/fundamentals VALE3", "VALE3"),
        ("/technical AAPL", "AAPL"),
        ("/crypto BTC/USDT", "BTC/USDT"),
        ("/brazil selic", "selic"),
    ],
)
def test_single_arg_commands_build_a_prompt_mentioning_the_argument(message, expected_substring):
    resolved = commands.resolve(message)
    assert resolved is not None
    assert resolved.direct_reply is None
    assert expected_substring in resolved.prompt_for_agent


def test_price_without_ticker_is_a_usage_error_not_an_agent_call():
    resolved = commands.resolve("/price")
    assert resolved is not None
    assert resolved.prompt_for_agent is None
    assert "/price" in resolved.direct_reply


def test_compare_requires_at_least_two_comma_separated_tickers():
    bad = commands.resolve("/compare PETR4")
    assert bad.prompt_for_agent is None

    good = commands.resolve("/compare PETR4,VALE3,ITUB4")
    assert good.direct_reply is None
    assert "PETR4,VALE3,ITUB4" in good.prompt_for_agent


def test_simulate_accepts_optional_days_argument():
    no_days = commands.resolve("/simulate MSFT")
    assert no_days.direct_reply is None
    assert "MSFT" in no_days.prompt_for_agent
    assert "dias" not in no_days.prompt_for_agent.split("MSFT")[0]

    with_days = commands.resolve("/simulate MSFT 30")
    assert with_days.direct_reply is None
    assert "MSFT" in with_days.prompt_for_agent
    assert "30 dias" in with_days.prompt_for_agent


def test_simulate_rejects_non_numeric_days():
    resolved = commands.resolve("/simulate MSFT trinta")
    assert resolved.prompt_for_agent is None
    assert resolved.direct_reply is not None


def test_global_requires_arguments():
    resolved = commands.resolve("/global")
    assert resolved.prompt_for_agent is None


def test_command_is_case_insensitive():
    resolved = commands.resolve("/PRICE PETR4")
    assert resolved is not None
    assert resolved.direct_reply is None
    assert "PETR4" in resolved.prompt_for_agent
