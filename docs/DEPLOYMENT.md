# Running on a VPS later (optional)

The project runs from a Python virtual environment on a Windows PC and needs no Docker. A Windows VPS works the same way, because the MetaTrader5 package needs a Windows host with the terminal installed. This page is guidance only. Nothing here has been set up or tested.

- **Paths.** Set `storage.db_path`, `storage.model_dir`, `storage.report_dir` and `storage.log_dir` in `config/local.toml` to absolute paths on the VPS.
- **Secrets.** Put them in `.env` on the VPS only. Do not copy `.env` into the repository or into images.
- **Running the monitor.** Use Windows Task Scheduler to start `mt5bot monitor start` at log-on. Use "Run whether user is logged on or not" only if the MT5 terminal can run in that session.
- **Shutdown.** The monitor stops cleanly on Ctrl+C or SIGTERM, and reports why it stopped.
- **Logs.** They are JSON lines on stderr. Redirect them to a file under `storage.log_dir`, for example `mt5bot monitor start 2>> logs\monitor.jsonl`.
- **Network.** Do not open inbound ports. The project needs only outbound connections: the broker via the terminal, the news feeds you enable, and the optional LLM API.
- **Access.** Use a dedicated Windows user with strong credentials, keep the OS patched, and restrict RDP access.
