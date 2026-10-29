"""Safety boundary: no order-execution capability anywhere in the shipped code."""

import ast
import itertools
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


MQL5_BANNED = re.compile(
    r"\b(OrderSend|OrderSendAsync|OrderCheck|OrderModify|OrderDelete|OrderClose|CTrade|CPositionInfo|PositionOpen|"
    r"PositionClose|PositionModify|PositionCloseBy|TRADE_ACTION_[A-Z]+)\b|Trade\\Trade\.mqh|Trade/Trade\.mqh"
)
MQL5_FILES = sorted((ROOT / "mql5").rglob("*.mq[5h]"))


def test_mql5_components_exist():
    names = {p.name for p in MQL5_FILES}
    assert {"Bridge.mqh", "MT5Bot_Decisions.mq5", "MT5Bot_Observer.mq5", "MT5Bot_ExportTicks.mq5"} <= names


@pytest.mark.parametrize("path", MQL5_FILES, ids=lambda p: p.name)
def test_mql5_has_no_trading_code(path):
    text = path.read_text(encoding="utf-8")
    assert not MQL5_BANNED.search(text), f"{path.name} references a trading API"


def _strip_mql(text):
    text = re.sub(r'"(\\.|[^"\\])*"', '""', text)  # string literals
    text = re.sub(r"'(\\.|[^'\\])'", "''", text)  # char literals
    return re.sub(r"//[^\n]*", "", text)


@pytest.mark.parametrize("path", MQL5_FILES, ids=lambda p: p.name)
def test_mql5_structure(path):
    """Static sanity checks; real compilation requires MetaEditor on Windows (press F7)."""
    text = path.read_text(encoding="utf-8")
    code = _strip_mql(text)
    for o, c in ("{}", "()", "[]"):
        assert code.count(o) == code.count(c), f"{path.name}: unbalanced {o}{c}"
    assert "/*" not in text, "block comments are not allowed (single-line comments only)"
    lines = [ln.strip() for ln in text.splitlines()]
    for a, b in itertools.pairwise(lines):
        assert not (a.startswith("//") and b.startswith("//")), f"{path.name}: multi-line comment block"
    for inc in re.findall(r"#include\s+<([^>]+)>", text):
        if inc.startswith("MT5Bot/"):
            assert (ROOT / "mql5" / "Include" / inc).exists(), inc
    if path.suffix == ".mq5" and "Experts" in path.parts:
        assert "ACCOUNT_TRADE_MODE_DEMO" in text and "INIT_FAILED" in text  # demo-only guard


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


def test_python_and_mql5_file_formats_agree():
    from mt5bot.bridge.files import DECISION_HEADER, RESOLVED_HEADER, WATCH_HEADER

    ea = (ROOT / "mql5" / "Experts" / "MT5Bot" / "MT5Bot_Observer.mq5").read_text(encoding="utf-8")
    ind = (ROOT / "mql5" / "Indicators" / "MT5Bot" / "MT5Bot_Decisions.mq5").read_text(encoding="utf-8")
    assert f'#define RESOLVED_HEADER "{RESOLVED_HEADER}"' in ea
    assert len(DECISION_HEADER.split(",")) == 14 and "StringSplit(lines[i], ',', f) < 14" in ind
    assert len(WATCH_HEADER.split(",")) == 7 and "StringSplit(lines[i], ',', f) < 7" in ea
    cols = WATCH_HEADER.split(",")
    for idx, name in (
        (2, "start_server"),
        (3, "stop_dist"),
        (4, "target_dist"),
        (5, "expiry_server"),
        (6, "slip_points"),
    ):
        assert cols[idx] == name
