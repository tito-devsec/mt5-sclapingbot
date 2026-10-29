"""mt5bot command-line interface; research, data and observe-only commands, never order placement."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mt5bot.config.settings import Settings, load_settings
from mt5bot.core.errors import ConfigError, Mt5BotError
from mt5bot.core.logging import configure_logging, register_settings_secrets
from mt5bot.core.types import Timeframe, utc_now

EXIT_OK, EXIT_ERROR, EXIT_VALIDATION = 0, 1, 2


def _ts(text: str | None) -> datetime | None:
    if text is None:
        return None
    t = pd.Timestamp(text)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def _db(settings: Settings) -> Any:
    from mt5bot.storage.db import open_database

    return open_database(settings.storage.db_path)


def _clock(settings: Settings) -> Any:
    from mt5bot.gateway.clock import ServerClock

    if settings.mt5.server_timezone or settings.mt5.server_utc_offset_hours is not None:
        return ServerClock(settings.mt5.server_timezone, settings.mt5.server_utc_offset_hours)
    return None


def _gateway(settings: Settings) -> Any:
    from mt5bot.gateway.mt5_gateway import MT5Gateway

    return MT5Gateway(settings.mt5, _clock(settings))


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------- commands
def cmd_check(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.gateway.account import check_account
    from mt5bot.gateway.clock import observed_offset_hours
    from mt5bot.gateway.symbols import resolve_symbol

    gw = _gateway(s)
    gw.connect()
    ok = True
    try:
        term = gw.terminal_status()
        print(f"terminal connected: {term.connected}  build: {term.build}")
        acct = gw.account()
        for c in check_account(acct, s.mt5.expected_currency):
            ok &= c.ok
            print(f"[{'OK ' if c.ok else 'FAIL'}] {c.code}: {c.detail}")
        print(
            f"broker: {acct.company}  leverage: 1:{acct.leverage}  (balance shown by the terminal; the configured "
            f"reference balance {s.risk.reference_balance_usd:g} USD is a simulation setting)"
        )
        available = gw.symbols()
        for sym in s.instruments.symbols:
            try:
                meta = resolve_symbol(sym, available, s.mt5.symbol_map)
                epoch = gw.server_epoch_now(meta.name)
                off = observed_offset_hours(epoch, utc_now()) if epoch else None
                print(
                    f"[OK ] {sym} -> {meta.name} point={meta.point} contract={meta.contract_size} "
                    f"server offset now={'n/a (no fresh tick)' if off is None else f'UTC{off:+g}h'}"
                )
            except Mt5BotError as exc:
                ok = False
                print(f"[FAIL] {sym}: {exc.code}: {exc}")
        if not (s.mt5.server_timezone or s.mt5.server_utc_offset_hours is not None):
            ok = False
            print("[FAIL] SERVER_TIME_UNKNOWN: set mt5.server_timezone (e.g. 'NY+7') before collecting history")
    finally:
        gw.disconnect()
    return EXIT_OK if ok else EXIT_VALIDATION


def cmd_collect(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.gateway.account import require_valid_account
    from mt5bot.ingest.collector import Collector

    gw = _gateway(s)
    gw.connect()
    try:
        require_valid_account(gw.account(), s.mt5.expected_currency)
        conn = _db(s)
        symbols = a.symbols.split(",") if a.symbols else list(s.instruments.symbols)
        tfs = [Timeframe(t) for t in a.timeframes.split(",")] if a.timeframes else list(s.instruments.timeframes)
        reports = Collector(gw, conn, s).collect(symbols, tfs, start=_ts(a.start), end=_ts(a.end))
    finally:
        gw.disconnect()
    worst = EXIT_OK
    for r in reports:
        print(
            f"{r.symbol:8} {r.timeframe.value:4} {r.status:8} received={r.received} inserted={r.inserted} "
            f"rejected={r.rejected} forming_dropped={r.forming_dropped} issues={r.issues}"
            + (f" error={r.error}" if r.error else "")
        )
        if r.status == "FAILED":
            worst = EXIT_ERROR
    return worst


def cmd_inspect(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.storage.repositories import BarRepository, IngestionRepository

    conn = _db(s)
    for r in BarRepository(conn).count(a.symbol):
        print(f"{r['symbol']:8} {r['timeframe']:4} n={r['n']:>8} first={r['first']} last={r['last']}")
    print("-- data-quality issues --")
    for r in IngestionRepository(conn).issue_summary():
        print(f"{r['symbol']:8} {r['timeframe'] or '':4} {r['severity']:7} {r['issue']:26} {r['n']}")
    if a.tail:
        tf = Timeframe(a.timeframe)
        print(BarRepository(conn).load(a.symbol, tf).tail(a.tail).to_string(index=False))
    return EXIT_OK


def cmd_news_refresh(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.news.providers.calendar_csv import CSVCalendarProvider
    from mt5bot.news.service import build_registry, refresh

    reg = build_registry(s.news)
    cal = CSVCalendarProvider(s.news.calendar_csv) if s.news.calendar_csv else None
    if not reg.ids and cal is None:
        print("no news provider enabled (see [[news.providers]] in your config); news state stays UNKNOWN")
        return EXIT_VALIDATION
    for r in refresh(_db(s), reg, utc_now(), cal):
        print(
            f"{r.provider_id:14} ok={r.ok} fetched={r.fetched} new={r.inserted}"
            + (f" error={r.error}" if r.error else "")
        )
    return EXIT_OK


def cmd_features_docs(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.features.pipeline import features_markdown
    from mt5bot.pipeline.context import feature_config

    md = features_markdown(feature_config(s))
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(md)
    return EXIT_OK


def cmd_config_docs(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.config.docs import config_markdown

    md = config_markdown()
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(md)
    return EXIT_OK


def cmd_features_build(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.pipeline.inference import ResearchPipeline

    p = ResearchPipeline(_db(s), s)
    f = p.feature_frame(a.symbol, Timeframe(a.timeframe), _ts(a.start), _ts(a.end) or utc_now())
    if a.start:
        f = f[f["time_utc"] >= pd.Timestamp(_ts(a.start))]
    out = Path(a.out or s.storage.report_dir / f"features_{a.symbol}_{a.timeframe}.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(out, index=False)
    print(f"{len(f)} rows, {f.shape[1]} columns -> {out} (feature set {p.fs_hash})")
    return EXIT_OK


def cmd_train(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.pipeline.jobs import train_job

    symbols = a.symbols.split(",") if a.symbols else list(s.instruments.symbols)
    out = train_job(
        _db(s),
        s,
        symbols,
        Timeframe(a.timeframe),
        _ts(a.start),
        _ts(a.end) or utc_now(),
        model_type=a.model,
        tune_trials=a.tune,
    )
    print(f"result: {out.action} {out.model_id or ''}\nreason: {out.reason}")
    if out.summary:
        t = out.summary["test"]
        print(
            f"test (untouched): trades={t['selected']['trades']} expectancy_r={t['selected']['expectancy_r']:.3f} "
            f"brier={t['classification']['brier']:.3f} auc={t['classification']['auc']:.3f}"
        )
        b = out.summary["baselines"]["test"]["rules_all_eligible"]
        print(f"rule baseline on test: trades={b['trades']} expectancy_r={b['expectancy_r']:.3f}")
        for name, g in out.summary.get("gates", {}).items():
            print(f"  gate {name:32} {'PASS' if g['passed'] else 'FAIL'} value={g['value']} threshold={g['threshold']}")
        print("NOTE: results are from historical simulation and are not evidence of future performance.")
    return EXIT_OK if out.action in ("PROMOTE", "REJECT", "SKIPPED") else EXIT_ERROR


def cmd_backtest(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.pipeline.jobs import backtest_job

    out = backtest_job(
        _db(s),
        s,
        a.symbol,
        Timeframe(a.timeframe),
        _ts(a.start),
        _ts(a.end) or utc_now(),
        a.strategies.split(",") if a.strategies else None,
        tick_resolve=a.tick_resolve,
    )
    if "error" in out:
        print(out["error"])
        return EXIT_VALIDATION
    if out["unverified_spec"]:
        print("WARNING: contract spec not captured from the broker yet; using typical defaults (run collect).")
    print(f"{out['symbol']} {out['timeframe']} - SIMULATED results by cost multiplier:")
    for k, r in out["runs"].items():
        if r.get("trades", 0) == 0:
            print(f"  {k:5} no trades")
            continue
        print(
            f"  {k:5} trades={r['trades']:5} net_r={r['net_r']:8.2f} expectancy_r={r['expectancy_r']:7.3f} "
            f"win_rate={r['win_rate']:.2f} pf={r['profit_factor']:.2f} max_dd_r={r['max_drawdown_r']:.2f} "
            f"ambiguous={r['ambiguous_exits']} tick_resolved={r['tick_resolved_exits']} "
            f"(to_target={r['tick_resolved_to_target']})"
        )
    if not out["tick_resolved"]:
        print("bars touching both stop and target were counted as stop-first; use --tick-resolve with stored ticks")
    print("NOTE: historical simulation only; not evidence of future profitability.")
    if a.json:
        _print(out)
    return EXIT_OK


def cmd_replay(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.pipeline.inference import ResearchPipeline

    start = _ts(a.start)
    if start is None:
        print("replay needs --from")
        return EXIT_VALIDATION
    counts = ResearchPipeline(_db(s), s).replay(a.symbol, Timeframe(a.timeframe), start, _ts(a.end) or utc_now())
    print(f"historical decisions recorded: {counts}")
    return EXIT_OK


def cmd_recommend(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.pipeline.inference import ResearchPipeline

    conn = _db(s)
    account, mode = None, "SIMULATED"
    if a.observe:
        from mt5bot.gateway.account import require_valid_account
        from mt5bot.ingest.collector import Collector

        gw = _gateway(s)
        gw.connect()
        try:
            account = require_valid_account(gw.account(), s.mt5.expected_currency)
            Collector(gw, conn, s).collect([a.symbol], [Timeframe(a.timeframe)])
            mode = "OBSERVED_DEMO"
        finally:
            gw.disconnect()
    at = (_ts(a.at) if not a.observe else None) or utc_now()
    rec = ResearchPipeline(conn, s).recommend(a.symbol, Timeframe(a.timeframe), mode=mode, now=at, account=account)
    if rec is None:
        print("no closed bars available; collect data first")
        return EXIT_VALIDATION
    print(f"[{rec.status.value}] {rec.symbol} {rec.timeframe} bar {rec.bar_time} regime={rec.regime}")
    if rec.strategy:
        print(f"  candidate: {rec.strategy} {rec.direction}")
    print(f"  rationale: {rec.rationale}")
    print("  This is a research record only. No order was or will be sent.")
    return EXIT_OK


def cmd_models(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.ml.registry import ModelRegistry

    reg = ModelRegistry(_db(s), s.storage.model_dir)
    if a.action == "list":
        for r in reg.repo.list_all():
            m = json.loads(r["metrics_json"]).get("test", {}).get("selected", {})
            print(
                f"{r['status']:10} {r['id']}  test_trades={m.get('trades')} test_expectancy_r={m.get('expectancy_r')}"
            )
    elif a.action == "history":
        for p in reg.repo.promotions("meta_label"):
            print(f"{p['created_at']} {p['action']:8} {p['model_id']} prev={p['previous_champion']} :: {p['reason']}")
    elif a.action == "rollback":
        print(f"champion restored: {reg.rollback('meta_label')}")
    elif a.action == "show":
        row = reg.repo.get(a.id)
        if row is None:
            print("unknown model id")
            return EXIT_VALIDATION
        _print({k: (json.loads(row[k]) if k.endswith("_json") else row[k]) for k in row})
    return EXIT_OK


def cmd_outcomes(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.memory.outcomes import evaluate_pending
    from mt5bot.pipeline.context import SpecProvider, cost_model, feature_config, sizing

    conn = _db(s)
    rep = evaluate_pending(
        conn,
        spec_for=SpecProvider(conn),
        costs_for=lambda sym: cost_model(s, sym),
        sizing=sizing(s),
        selection=s.selection,
        now=pd.Timestamp(utc_now()),
        feature_cfg=feature_config(s),
    )
    print(f"evaluated={rep.evaluated} still_pending={rep.still_pending} not_applicable={rep.not_applicable}")
    return EXIT_OK


def cmd_drift(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.drift.monitor import check_inputs, check_regimes, persist
    from mt5bot.pipeline.inference import ResearchPipeline
    from mt5bot.regime.rules import classify_rules

    conn = _db(s)
    p = ResearchPipeline(conn, s)
    tf = Timeframe(a.timeframe)
    now = pd.Timestamp(utc_now())
    feats = p.feature_frame(a.symbol, tf, None, now.to_pydatetime())
    if len(feats) < 2000:
        print("not enough history for a drift check (need >= 2000 bars)")
        return EXIT_VALIDATION
    ref, cur = feats.iloc[: -a.recent], feats.iloc[-a.recent :]
    scope = f"{a.symbol}:{tf.value}"
    alerts = check_inputs(ref, cur, scope, now) + check_regimes(
        classify_rules(ref)["regime"], classify_rules(cur)["regime"], scope, now
    )
    n = persist(conn, alerts)
    for al in alerts:
        print(f"{al.severity:8} {al.kind:16} {al.scope} {al.metric}={al.value:.3f} -> {al.response}")
    print(f"{len(alerts)} alert(s), {n} new")
    return EXIT_OK


def cmd_strategies(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.storage.repositories import StrategyStatusRepository

    conn = _db(s)
    repo = StrategyStatusRepository(conn)
    if a.action == "list":
        for r in conn.execute("SELECT * FROM strategy_status ORDER BY symbol, timeframe, strategy").fetchall():
            print(
                f"{r['status']:9} {r['strategy']:15} {r['symbol']} {r['timeframe']} :: {r['reason']} "
                f"({r['changed_at']})"
            )
        return EXIT_OK
    # restore: only after a fresh evaluation on recent data shows positive expectancy after costs
    from mt5bot.pipeline.jobs import backtest_job

    end = utc_now()
    start = (pd.Timestamp(end) - pd.Timedelta(days=a.days)).to_pydatetime()
    out = backtest_job(conn, s, a.symbol, Timeframe(a.timeframe), start, end, [a.strategy])
    x1 = out.get("runs", {}).get("x1", {})
    if x1.get("trades", 0) < s.selection.min_samples or x1.get("expectancy_r", 0) <= 0:
        print(
            f"not restored: re-evaluation over {a.days} days gave trades={x1.get('trades', 0)} "
            f"expectancy_r={x1.get('expectancy_r', 0):.3f} (needs >= {s.selection.min_samples} trades and > 0)"
        )
        return EXIT_VALIDATION
    repo.set_status(a.strategy, a.symbol, a.timeframe, "ACTIVE", f"restored after re-evaluation run {x1['run_id']}")
    print(f"restored {a.strategy} on {a.symbol} {a.timeframe}")
    return EXIT_OK


def cmd_status(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.reporting.report import gather, text_status

    print(text_status(gather(_db(s))))
    return EXIT_OK


def cmd_report(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.reporting.report import gather, write_csvs, write_html

    data = gather(_db(s))
    out = Path(a.out or s.storage.report_dir)
    html_path = write_html(data, out / "report.html")
    csvs = write_csvs(data, out / "csv")
    print(f"wrote {html_path} and {len(csvs)} CSV files in {out / 'csv'}")
    return EXIT_OK


def cmd_monitor(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.monitor.service import DemoMonitor

    gw = _gateway(s)
    gw.connect()
    try:
        clock = _clock(s)
        if clock is None:
            print("note: server time zone not set, so the MT5 chart bridge is off (set mt5.server_timezone)")
        mon = DemoMonitor(gw, _db(s), s, clock=clock)
        mon.install_signal_handlers()
        print("observe-only monitor started (Ctrl+C to stop). No orders are ever sent.")
        stats = mon.run(max_cycles=a.cycles)
    finally:
        gw.disconnect()
    print(
        f"stopped: {stats.stopped_reason}; cycles={stats.cycles} decisions={stats.decisions} errors={stats.errors} "
        f"bridge_files={stats.bridge_files} tick_resolutions={stats.tick_resolutions}"
    )
    return EXIT_OK if "account validation failed" not in stats.stopped_reason else EXIT_VALIDATION


def cmd_bridge(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.bridge.files import bridge_dir
    from mt5bot.bridge.service import export_state, import_results

    d = bridge_dir(s.bridge)
    if a.action == "path":
        print(d if d else "unknown: set bridge.directory (APPDATA is not available on this system)")
        return EXIT_OK if d else EXIT_VALIDATION
    clock = _clock(s)
    if clock is None:
        print("set mt5.server_timezone first: chart times are broker server times")
        return EXIT_VALIDATION
    conn = _db(s)
    if a.action == "export":
        rep = export_state(conn, s, clock, pd.Timestamp(utc_now()), mode=a.mode)
        print(f"{rep.note or 'exported'}: {len(rep.files_written)} file(s) in {rep.directory}")
    else:
        rep = import_results(conn, s, clock)
        print(f"{rep.note or 'imported'}: tick resolutions={rep.resolutions_imported} ticks={rep.ticks_imported}")
        for sym, hb in rep.heartbeats.items():
            print(f"  EA heartbeat {sym}: {hb}")
    return EXIT_OK


def cmd_ticks(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.gateway.account import require_valid_account
    from mt5bot.pipeline.ticks_job import collect_ambiguous_ticks, collect_ticks

    gw = _gateway(s)
    gw.connect()
    try:
        require_valid_account(gw.account(), s.mt5.expected_currency)
        conn = _db(s)
        end = _ts(a.end) or utc_now()
        if a.action == "collect":
            start = _ts(a.start)
            if start is None:
                print("ticks collect needs --from")
                return EXIT_VALIDATION
            rep = collect_ticks(gw, conn, s, a.symbol, start, end)
        else:
            rep = collect_ambiguous_ticks(gw, conn, s, a.symbol, Timeframe(a.timeframe), _ts(a.start), end)
    finally:
        gw.disconnect()
    print(f"windows={rep.windows} ticks_inserted={rep.ticks_inserted} ambiguous_trades={rep.ambiguous_trades}")
    return EXIT_OK


def cmd_llm_explain(s: Settings, a: argparse.Namespace) -> int:
    from mt5bot.llm.adapter import ResearchAdapter
    from mt5bot.storage.repositories import DecisionRepository

    conn = _db(s)
    row = DecisionRepository(conn).get(a.decision)
    if row is None:
        print("unknown decision id")
        return EXIT_VALIDATION
    payload = {
        k: row[k]
        for k in (
            "symbol",
            "timeframe",
            "bar_time_utc",
            "status",
            "regime",
            "selected_strategy",
            "direction",
            "rationale",
        )
    }
    payload["candidates"] = [dict(c) for c in DecisionRepository(conn).candidates(a.decision)]
    payload["risk_checks"] = [dict(c) for c in DecisionRepository(conn).risk_checks(a.decision)]
    res = ResearchAdapter(s.llm, conn).explain_decision(payload)
    print(f"status: {res.status} {res.detail}")
    if res.data is not None:
        _print(res.data.model_dump())
    return EXIT_OK


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mt5bot", description="Research, data and observe-only monitoring for MT5. This tool never places orders."
    )
    sub = p.add_subparsers(dest="command", required=True)

    def period(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--from", dest="start", help="UTC start, e.g. 2024-01-01")
        sp.add_argument("--to", dest="end", help="UTC end (default: now)")

    sub.add_parser("check", help="check terminal, demo account, currency, symbols, server time").set_defaults(
        fn=cmd_check
    )
    c = sub.add_parser("collect", help="collect closed historical bars (incremental)")
    c.add_argument("--symbols")
    c.add_argument("--timeframes")
    period(c)
    c.set_defaults(fn=cmd_collect)
    i = sub.add_parser("inspect", help="inspect collected records and data-quality issues")
    i.add_argument("--symbol")
    i.add_argument("--timeframe", default="M1")
    i.add_argument("--tail", type=int, default=0)
    i.set_defaults(fn=cmd_inspect)
    n = sub.add_parser("news", help="news providers").add_subparsers(dest="action", required=True)
    n.add_parser("refresh", help="fetch enabled providers").set_defaults(fn=cmd_news_refresh)
    f = sub.add_parser("features", help="feature pipeline").add_subparsers(dest="action", required=True)
    fd = f.add_parser("docs", help="print or write the feature reference")
    fd.add_argument("--out")
    fd.set_defaults(fn=cmd_features_docs)
    fb = f.add_parser("build", help="build features to parquet")
    fb.add_argument("--symbol", required=True)
    fb.add_argument("--timeframe", required=True)
    fb.add_argument("--out")
    period(fb)
    fb.set_defaults(fn=cmd_features_build)
    cf = sub.add_parser("config", help="configuration").add_subparsers(dest="action", required=True)
    cd = cf.add_parser("docs", help="print or write the configuration reference")
    cd.add_argument("--out")
    cd.set_defaults(fn=cmd_config_docs)
    t = sub.add_parser("train", help="train a challenger and apply promotion gates")
    t.add_argument("--timeframe", required=True)
    t.add_argument("--symbols")
    t.add_argument("--model", choices=["logreg", "random_forest", "lightgbm", "xgboost", "catboost"])
    t.add_argument("--tune", type=int, default=0, help="Optuna trials on validation folds (0 = off)")
    period(t)
    t.set_defaults(fn=cmd_train)
    b = sub.add_parser("backtest", help="cost-aware backtest with cost-sensitivity runs")
    b.add_argument("--symbol", required=True)
    b.add_argument("--timeframe", required=True)
    b.add_argument("--strategies")
    b.add_argument("--json", action="store_true")
    b.add_argument("--tick-resolve", action="store_true", help="settle bars that hit both stop and target from ticks")
    period(b)
    b.set_defaults(fn=cmd_backtest)
    br = sub.add_parser("bridge", help="file exchange with the MQL5 indicator and observer EA")
    br.add_argument("action", choices=["path", "export", "import"])
    br.add_argument("--mode", default="SIMULATED", choices=["SIMULATED", "HISTORICAL", "OBSERVED_DEMO"])
    br.set_defaults(fn=cmd_bridge)
    tk = sub.add_parser("ticks", help="collect ticks through the Python MT5 API")
    tk.add_argument("action", choices=["collect", "ambiguous"])
    tk.add_argument("--symbol", required=True)
    tk.add_argument("--timeframe", default="M1")
    period(tk)
    tk.set_defaults(fn=cmd_ticks)
    r = sub.add_parser("replay", help="replay the decision pipeline over history (HISTORICAL records)")
    r.add_argument("--symbol", required=True)
    r.add_argument("--timeframe", required=True)
    period(r)
    r.set_defaults(fn=cmd_replay)
    rc = sub.add_parser("recommend", help="produce one research recommendation or NO_TRADE")
    rc.add_argument("--symbol", required=True)
    rc.add_argument("--timeframe", required=True)
    rc.add_argument("--observe", action="store_true", help="refresh from the demo terminal first (OBSERVED_DEMO)")
    rc.add_argument("--at", help="decide as of this UTC time using stored data (SIMULATED research mode)")
    rc.set_defaults(fn=cmd_recommend)
    m = sub.add_parser("models", help="model registry")
    m.add_argument("action", choices=["list", "history", "rollback", "show"])
    m.add_argument("--id")
    m.set_defaults(fn=cmd_models)
    sub.add_parser("outcomes", help="evaluate matured decision outcomes").set_defaults(fn=cmd_outcomes)
    d = sub.add_parser("drift", help="input/regime drift check on recent bars")
    d.add_argument("--symbol", required=True)
    d.add_argument("--timeframe", required=True)
    d.add_argument("--recent", type=int, default=500)
    d.set_defaults(fn=cmd_drift)
    st = sub.add_parser("strategies", help="list or restore (after re-evaluation) strategies")
    st.add_argument("action", choices=["list", "restore"])
    st.add_argument("--strategy")
    st.add_argument("--symbol")
    st.add_argument("--timeframe")
    st.add_argument("--days", type=int, default=60)
    st.set_defaults(fn=cmd_strategies)
    sub.add_parser("status", help="data freshness, providers, models, alerts").set_defaults(fn=cmd_status)
    rp = sub.add_parser("report", help="write HTML and CSV reports")
    rp.add_argument("--out")
    rp.set_defaults(fn=cmd_report)
    mo = sub.add_parser("monitor", help="observe-only demo monitoring").add_subparsers(dest="action", required=True)
    ms = mo.add_parser("start")
    ms.add_argument("--cycles", type=int)
    ms.set_defaults(fn=cmd_monitor)
    ll = sub.add_parser("llm", help="optional research adapter (disabled by default)").add_subparsers(
        dest="action", required=True
    )
    le = ll.add_parser("explain")
    le.add_argument("--decision", type=int, required=True)
    le.set_defaults(fn=cmd_llm_explain)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    register_settings_secrets(settings)
    configure_logging(settings.logging.level, settings.logging.json_format)
    if (
        args.command == "strategies"
        and args.action == "restore"
        and not (args.strategy and args.symbol and args.timeframe)
    ):
        print("restore needs --strategy, --symbol and --timeframe", file=sys.stderr)
        return EXIT_VALIDATION
    try:
        return int(args.fn(settings, args))
    except Mt5BotError as exc:
        print(f"error {exc.code}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION if exc.code.startswith(("ACCOUNT", "CONFIG", "SYMBOL", "SERVER_TIME")) else EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
