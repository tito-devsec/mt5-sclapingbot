"""Safety boundary: no order-execution capability anywhere in the shipped code."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "mt5bot"
BANNED = {
    "order_send",
    "OrderSend",
    "OrderSendAsync",
    "CTrade",
    "PositionClose",
    "PositionModify",
    "OrderModify",
    "OrderDelete",
    "order_check",
    "positions_get",
    "orders_get",
    "TRADE_ACTION_DEAL",
    "TRADE_ACTION_PENDING",
    "TRADE_ACTION_SLTP",
    "TRADE_ACTION_MODIFY",
    "TRADE_ACTION_REMOVE",
}
# The fake MT5 module defines trap methods with these names that raise if ever called.
ALLOWED_FILES = {SRC / "gateway" / "fake.py"}


def _names(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(re.findall(r"[A-Za-z_]+", node.value))
    return found


@pytest.mark.parametrize(
    "path", sorted(p for p in SRC.rglob("*.py") if p not in ALLOWED_FILES), ids=lambda p: str(p.relative_to(SRC))
)
def test_source_contains_no_execution_api(path):
    hits = _names(ast.parse(path.read_text(encoding="utf-8"))) & BANNED
    assert not hits, f"{path}: execution API reference(s) {hits}"


def test_no_mql5_trading_code():
    for p in list(ROOT.rglob("*.mq5")) + list(ROOT.rglob("*.mqh")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"\b(OrderSend|CTrade|PositionClose|OrderSendAsync)\b", text), p


def test_gateway_interface_has_no_trading_methods():
    from mt5bot.gateway.base import MarketGateway
    from mt5bot.gateway.mt5_gateway import ALLOWED_FUNCTIONS, MT5Gateway

    for cls in (MarketGateway, MT5Gateway):
        methods = {m for m in dir(cls) if not m.startswith("_")}
        assert not methods & {"order", "send_order", "buy", "sell", "close_position", "modify_order", "cancel_order"}
    assert not ALLOWED_FUNCTIONS & BANNED


def test_decision_statuses_have_no_execute_value():
    from mt5bot.core.types import DecisionStatus

    assert {s.value for s in DecisionStatus} == {"RECOMMEND", "NO_TRADE", "UNCERTAIN", "REJECTED"}


def test_fake_module_traps_trading_calls():
    from mt5bot.gateway.fake import FakeMT5Module

    with pytest.raises(AssertionError):
        FakeMT5Module().order_send({})
