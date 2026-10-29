> [!WARNING]
> **Research software. No order execution exists.** This release collects data, simulates, and observes a **demo** account. It places, modifies, closes and cancels no orders. Nothing here is evidence of profitability, and trading involves substantial risk. See the README's risk disclosures.

**Status:** pre-alpha. The Python package is tested offline and in CI on Windows and Ubuntu with Python 3.12. The MQL5 components are statically checked but **not yet compiled in MetaEditor**, and nothing has yet been verified against a live MT5 terminal.

### Assets

| File | Contents |
|---|---|
| `mt5_sclapingbot-<version>-py3-none-any.whl` / `.tar.gz` | Python package: `pip install mt5_sclapingbot-<version>-py3-none-any.whl` (add extras with `"…whl[all]"`) |
| `mt5bot-mql5-<tag>.zip` | MQL5 files: copy `Include`, `Indicators`, `Experts` and `Scripts` into your terminal's `MQL5` data folder, then compile with F7 |
| `SHA256SUMS.txt` | Checksums of the assets |

Start with `docs/VERIFY_ON_MT5.md` for the step-by-step demo verification.

