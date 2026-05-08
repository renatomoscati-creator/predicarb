"""PredicArb — browser-based live dashboard (FastAPI + vanilla JS)."""
from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.storage.db import Database


# ── user prefs (local-only, never transmitted) ────────────────────────────────

_PREFS_DEFAULT: dict = {
    "name": "",
    "timezone": "Europe/Rome",
    "onboarded": False,
    "polymarket_api_key": "",
    "polymarket_api_secret": "",
    "polymarket_api_passphrase": "",
    "polymarket_address": "",
}


def _prefs_path(db_path: Path) -> Path:
    return db_path.parent / "user_prefs.json"


def _load_prefs(db_path: Path) -> dict:
    p = _prefs_path(db_path)
    if p.exists():
        try:
            return {**_PREFS_DEFAULT, **json.loads(p.read_text())}
        except Exception:
            pass
    return dict(_PREFS_DEFAULT)


def _save_prefs(db_path: Path, prefs: dict) -> None:
    p = _prefs_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, indent=2))


def _update_env_file(db_path: Path, prefs: dict) -> None:
    """Write non-empty API credentials into .env.demo (project root)."""
    fields = {
        "POLYMARKET_API_KEY":        prefs.get("polymarket_api_key", ""),
        "POLYMARKET_API_SECRET":     prefs.get("polymarket_api_secret", ""),
        "POLYMARKET_API_PASSPHRASE": prefs.get("polymarket_api_passphrase", ""),
        "POLYMARKET_ADDRESS":        prefs.get("polymarket_address", ""),
    }
    fields = {k: v for k, v in fields.items() if v.strip()}
    if not fields:
        return
    env_file = db_path.parent.parent / ".env.demo"
    lines = env_file.read_text().splitlines() if env_file.exists() else ["POLYMARKET_ENV=demo"]
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in fields:
            updated.append(f"{key}={fields[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for k, v in fields.items():
        if k not in seen:
            updated.append(f"{k}={v}")
    env_file.write_text("\n".join(updated) + "\n")


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_ts(ts_utc: Optional[str], tz: str = "Europe/Rome") -> Optional[str]:
    if not ts_utc:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz)).strftime("%H:%M:%S")
    except Exception:
        return ts_utc[11:19] if len(ts_utc) > 19 else ts_utc


def _aug(rows: list, tz: str = "Europe/Rome") -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        if "ts_utc" in d:
            d["ts_rome"] = _fmt_ts(d["ts_utc"], tz)
        if "added_at" in d:
            d["added_rome"] = _fmt_ts(d["added_at"], tz)
        out.append(d)
    return out


# ── backtest shared state ─────────────────────────────────────────────────────

_bt: dict[str, Any] = {"status": "idle", "result": None, "error": None}


# ── FastAPI app factory ───────────────────────────────────────────────────────

def create_app(db_path: Path) -> FastAPI:
    db = Database(db_path)
    db.init()
    app = FastAPI(title="PredicArb", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(content=_HTML)

    @app.get("/api/prefs")
    async def get_prefs() -> JSONResponse:
        return JSONResponse(_load_prefs(db_path))

    class PrefsBody(BaseModel):
        name: str = ""
        timezone: str = "Europe/Rome"
        onboarded: bool = False
        polymarket_api_key: str = ""
        polymarket_api_secret: str = ""
        polymarket_api_passphrase: str = ""
        polymarket_address: str = ""

    @app.post("/api/prefs")
    async def save_prefs(body: PrefsBody) -> dict:
        prefs = body.model_dump()
        _save_prefs(db_path, prefs)
        _update_env_file(db_path, prefs)
        return {"ok": True}

    @app.get("/api/data")
    async def get_data() -> JSONResponse:
        prefs = _load_prefs(db_path)
        tz = prefs.get("timezone", "Europe/Rome")
        return JSONResponse({
            "summary":     dict(db.summary()),
            "positions":   _aug(db.positions(), tz),
            "open_orders": _aug(db.open_orders(), tz),
            "signals":     _aug(db.signals(), tz),
            "fills":       _aug(db.fills(), tz),
            "ticks":       _aug(db.ticks_latest(), tz),
            "watched":     _aug(db.watched(), tz),
            "bt_runs":     _aug(db.backtest_runs(), tz),
        })

    class WatchedBody(BaseModel):
        ticker: str
        label: str = ""
        benchmark: float = 0.5

    @app.post("/api/watched")
    async def add_watched(body: WatchedBody) -> dict:
        if not body.ticker.strip():
            raise HTTPException(400, "ticker required")
        if not 0 < body.benchmark < 1:
            raise HTTPException(400, "benchmark must be 0–1")
        db.add_watched(body.ticker.strip(), body.label.strip(), body.benchmark)
        return {"ok": True}

    @app.delete("/api/watched/{ticker:path}")
    async def remove_watched(ticker: str) -> dict:
        db.remove_watched(ticker)
        return {"ok": True}

    class BacktestBody(BaseModel):
        ticker: str
        csv_path: str
        benchmark: float = 0.60
        size: int = 100
        min_edge: float = 0.02

    def _run_bt(body: BacktestBody) -> None:
        global _bt
        _bt = {"status": "running", "result": None, "error": None}
        try:
            from src.benchmark.csv_benchmark import BenchmarkProvider
            from src.strategy.backtest import BacktestEngine
            from src.strategy.edge_calculator import EdgeFilters

            bv = body.benchmark

            class _Const(BenchmarkProvider):
                def get_prob(self, ts_utc: str) -> float:
                    return bv

            engine = BacktestEngine(
                benchmark=_Const(),
                filters=EdgeFilters(max_spread=0.15),
                ticker=body.ticker,
                size=body.size,
                min_edge=body.min_edge,
                benchmark_label=f"constant:{body.benchmark}",
            )
            _, run = engine.run(Path(body.csv_path))
            db2 = Database(db._path)
            db2.init()
            db2.insert_backtest_run(run)
            _bt = {"status": "done", "result": "ok", "error": None}
        except Exception as exc:
            _bt = {"status": "error", "result": None, "error": str(exc)}

    @app.post("/api/backtest")
    async def start_bt(body: BacktestBody, bg: BackgroundTasks) -> dict:
        if _bt.get("status") == "running":
            raise HTTPException(409, "backtest already running")
        if not body.ticker.strip():
            raise HTTPException(400, "ticker required")
        if not Path(body.csv_path).exists():
            raise HTTPException(400, f"CSV not found: {body.csv_path}")
        bg.add_task(_run_bt, body)
        return {"ok": True}

    @app.get("/api/backtest/status")
    async def bt_status() -> dict:
        return _bt

    return app


# ── module-level app (required for uvicorn --reload) ─────────────────────────
# uvicorn reload re-imports this module on each change, so app is rebuilt fresh.

def _default_db_path() -> Path:
    try:
        from src.config import get_settings
        return get_settings().data_dir / "polymarket_bot.sqlite"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data" / "polymarket_bot.sqlite"


app = create_app(_default_db_path())


# ── entry point ───────────────────────────────────────────────────────────────

def run(
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> None:
    url = f"http://{host}:{port}"
    print(f"\n  PredicArb dashboard  →  {url}\n  Ctrl-C to stop.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "src.web_dashboard:app",
        host=host,
        port=port,
        log_level="warning",
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
    )


# ── embedded HTML/CSS/JS ──────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PredicArb</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg:     #000000;
  --bg2:    #1C1C1E;
  --bg3:    #2C2C2E;
  --bg4:    #3A3A3C;
  --text:   #FFFFFF;
  --text2:  #EBEBF5;
  --sub:    #8E8E93;
  --border: #38383A;
  --blue:   #0A84FF;
  --green:  #30D158;
  --red:    #FF453A;
  --orange: #FF9F0A;
  --purple: #BF5AF2;
  --yellow: #FFD60A;
  --r: 10px;
  --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', monospace;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* ── scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bg4); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--sub); }

/* ── topbar ── */
.topbar {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; justify-content: space-between;
  height: 48px; padding: 0 20px;
  background: rgba(28, 28, 30, 0.9);
  backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
}
.topbar-left  { display: flex; align-items: center; gap: 10px; }
.topbar-right { display: flex; align-items: center; gap: 12px; }
.brand { font-size: 15px; font-weight: 700; letter-spacing: -0.3px; }
.brand b { color: var(--blue); }
.clock { font-family: var(--mono); font-size: 12px; color: var(--text2); }

/* greeting */
.greeting {
  font-size: 11px; font-style: italic; color: var(--sub);
  white-space: nowrap; max-width: 240px;
  overflow: hidden; text-overflow: ellipsis;
}
.greeting b { color: var(--text2); font-style: normal; font-weight: 600; }

.tz-label { font-size: 11px; color: var(--sub); }

.dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--green);
  animation: pulse 2.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(48, 209, 88, 0.5); }
  50%       { opacity: .7; box-shadow: 0 0 0 5px rgba(48, 209, 88, 0); }
}

/* gear button */
.gear-btn {
  background: none; border: none; cursor: pointer;
  color: var(--sub); font-size: 15px; padding: 4px;
  transition: color .15s; line-height: 1;
}
.gear-btn:hover { color: var(--text2); }

/* ── KPI row ── */
.kpi-row {
  display: grid; grid-template-columns: repeat(5, 1fr);
  gap: 8px; padding: 10px 16px;
}
.kpi-card {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r); padding: 10px 14px;
  cursor: default; transition: background .15s;
}
.kpi-card:hover { background: var(--bg3); }
.kpi-label {
  font-size: 10px; font-weight: 600; color: var(--sub);
  text-transform: uppercase; letter-spacing: .6px; margin-bottom: 4px;
}
.kpi-value { font-family: var(--mono); font-size: 20px; font-weight: 600; }

/* ── tabs ── */
.tab-bar {
  display: flex; background: var(--bg2);
  border-bottom: 1px solid var(--border); padding: 0 16px;
}
.tab-btn {
  background: none; border: none;
  border-bottom: 2px solid transparent;
  color: var(--sub); cursor: pointer;
  font-family: var(--font); font-size: 13px; font-weight: 500;
  padding: 10px 16px; transition: color .15s, border-color .15s;
}
.tab-btn:hover  { color: var(--text2); }
.tab-btn.active { color: var(--blue); border-bottom-color: var(--blue); font-weight: 600; }

.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── markets grid ── */
.markets-body {
  display: grid; grid-template-columns: 1fr 2fr;
  gap: 8px; padding: 8px 16px 0;
}
.col { display: flex; flex-direction: column; gap: 8px; }
.ticks-wrap { padding: 0 16px 10px; }

/* ── panel ── */
.panel {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r); overflow: hidden;
}
.panel-hd {
  background: var(--bg3); padding: 7px 14px;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid var(--border);
}
.accent  { width: 3px; height: 14px; border-radius: 2px; flex-shrink: 0; }
.panel-title {
  font-size: 10px; font-weight: 700; color: var(--sub);
  text-transform: uppercase; letter-spacing: .7px;
}
.panel-body { overflow-y: auto; max-height: 210px; }

/* ── tables ── */
table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
th {
  background: var(--bg3); color: var(--sub); font-weight: 600;
  padding: 5px 12px; text-align: left; font-size: 10px;
  text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 1;
}
td {
  padding: 6px 12px;
  border-bottom: 1px solid rgba(56, 56, 58, 0.5);
  white-space: nowrap; overflow: hidden; max-width: 180px;
  text-overflow: ellipsis;
}
tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: rgba(10, 132, 255, 0.06); }
.empty-cell {
  color: var(--sub); font-style: italic; text-align: center;
  padding: 18px !important; max-width: none;
}

/* ── colours ── */
.green  { color: var(--green); }
.red    { color: var(--red); }
.blue   { color: var(--blue); }
.orange { color: var(--orange); }
.purple { color: var(--purple); }
.yellow { color: var(--yellow); }
.sub    { color: var(--sub); }
.mono   { font-family: var(--mono); }

/* ── page padding ── */
.page { padding: 8px 16px; }

/* ── card ── */
.card {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r); overflow: hidden; margin-bottom: 8px;
}
.card-hd {
  background: var(--bg3); padding: 7px 14px;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid var(--border);
}
.card-title {
  font-size: 10px; font-weight: 700; color: var(--sub);
  text-transform: uppercase; letter-spacing: .7px;
}
.card-hint {
  font-size: 11px; color: var(--sub);
  padding: 6px 14px; border-bottom: 1px solid var(--border);
}
.card-body { padding: 12px 14px; }

/* ── form ── */
.form-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px; margin-bottom: 12px;
}
.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-label {
  font-size: 10px; font-weight: 600; color: var(--sub);
  text-transform: uppercase; letter-spacing: .4px;
}
.form-input {
  background: var(--bg4); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text);
  font-family: var(--mono); font-size: 12px;
  padding: 7px 10px; outline: none; width: 100%;
  transition: border-color .15s;
}
.form-input:focus { border-color: var(--blue); }
.form-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-msg { font-size: 12px; font-family: var(--mono); }

/* ── buttons ── */
.btn {
  background: var(--blue); border: none; border-radius: 8px;
  color: #fff; cursor: pointer;
  font-family: var(--font); font-size: 12px; font-weight: 600;
  padding: 7px 18px; transition: opacity .15s; white-space: nowrap;
}
.btn:hover  { opacity: .85; }
.btn:active { opacity: .65; }
.btn-red    { background: var(--red); }
.btn-purple { background: var(--purple); }
.btn-ghost  { background: var(--bg4); color: var(--text2); }
.btn-sm { font-size: 10px; padding: 3px 9px; border-radius: 6px; }

/* ── backtest KPI row ── */
.bt-kpi-row {
  display: grid; grid-template-columns: repeat(5, 1fr);
  gap: 8px; margin-bottom: 8px;
}
.bt-kpi-row .kpi-value { font-size: 17px; }

/* ── watch / history panel heights ── */
.watch-panel .panel-body { max-height: 360px; }
.bt-hist-panel .panel-body { max-height: 320px; }

/* ── toast ── */
#toasts {
  position: fixed; bottom: 20px; right: 20px;
  display: flex; flex-direction: column; gap: 8px; z-index: 3000;
}
.toast {
  background: var(--bg3); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px; font-size: 12px;
  animation: tin .2s ease-out; pointer-events: none; max-width: 320px;
}
.toast.ok  { border-left: 3px solid var(--green); }
.toast.err { border-left: 3px solid var(--red); }
@keyframes tin {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: none; }
}

/* ── bottom-left credit ── */
.footer-credit {
  position: fixed; bottom: 12px; left: 16px; z-index: 50;
  font-size: 10px; color: #3A3A3C;
  letter-spacing: .4px; pointer-events: none;
  font-family: var(--mono);
}

/* ══ ONBOARDING OVERLAY ═══════════════════════════════════════════════════════ */
.onb-overlay {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0,0,0,0.88);
  backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
  display: flex; align-items: center; justify-content: center;
  animation: fadeIn .25s ease-out;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.onb-card {
  background: #1C1C1E; border: 1px solid #38383A;
  border-radius: 20px; padding: 40px 44px;
  width: 480px; max-width: 94vw;
  animation: slideUp .3s cubic-bezier(0.34,1.56,0.64,1);
}
@keyframes slideUp {
  from { transform: translateY(28px); opacity: 0; }
  to   { transform: none; opacity: 1; }
}

.onb-logo {
  font-size: 22px; font-weight: 800; letter-spacing: -0.5px;
  margin-bottom: 28px;
}
.onb-logo b { color: var(--blue); }

.onb-progress { display: flex; gap: 6px; margin-bottom: 28px; }
.onb-pip {
  height: 3px; border-radius: 2px; background: var(--bg4);
  flex: 1; transition: background .3s;
}
.onb-pip.active { background: var(--blue); }

.onb-step { animation: fadeIn .2s ease-out; }
.onb-step-title {
  font-size: 20px; font-weight: 700; letter-spacing: -0.3px; margin-bottom: 6px;
}
.onb-step-sub {
  font-size: 13px; color: var(--sub); margin-bottom: 24px; line-height: 1.6;
}
.onb-form-grid { display: flex; flex-direction: column; gap: 14px; margin-bottom: 24px; }
.onb-optional {
  font-size: 10px; font-weight: 500; color: var(--sub);
  background: var(--bg3); border-radius: 4px; padding: 2px 6px;
  text-transform: uppercase; letter-spacing: .4px;
  vertical-align: middle; margin-left: 6px;
}
.onb-privacy {
  background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.22);
  border-radius: 8px; padding: 10px 14px;
  font-size: 11.5px; color: var(--sub); line-height: 1.5;
  margin-bottom: 22px;
}
.onb-privacy code, .settings-note code {
  font-family: var(--mono); font-size: 10.5px;
  color: var(--blue); background: rgba(10,132,255,0.1);
  padding: 1px 5px; border-radius: 4px;
}
.onb-actions { display: flex; gap: 10px; }

/* ── guide tab ── */
.guide-page { padding: 12px 16px 24px; max-width: 860px; }
.guide-section { margin-bottom: 12px; }
.guide-section-title {
  font-size: 11px; font-weight: 700; color: var(--sub);
  text-transform: uppercase; letter-spacing: .7px;
  margin-bottom: 8px; padding: 0 2px;
}
.guide-pre {
  background: var(--bg4); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px;
  font-family: var(--mono); font-size: 11.5px; line-height: 1.7;
  overflow-x: auto; white-space: pre; margin: 8px 0;
  color: var(--text2);
}
.guide-pre .cmd  { color: var(--blue); }
.guide-pre .cmt  { color: var(--sub); }
.guide-pre .val  { color: var(--green); }
.guide-table { width: 100%; border-collapse: collapse; font-size: 11.5px; margin: 8px 0; }
.guide-table th {
  background: var(--bg3); color: var(--sub); font-weight: 600;
  padding: 5px 12px; text-align: left; font-size: 10px;
  text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 1px solid var(--border);
}
.guide-table td {
  padding: 6px 12px;
  border-bottom: 1px solid rgba(56,56,58,0.5);
  vertical-align: top;
}
.guide-table tr:last-child td { border-bottom: none; }
.guide-table tbody tr:hover td { background: rgba(10,132,255,0.05); }
.guide-table td:first-child { font-family: var(--mono); color: var(--blue); white-space: nowrap; }
.guide-flow {
  display: flex; flex-direction: column; gap: 4px; padding: 4px 0;
}
.guide-flow-step {
  display: flex; align-items: baseline; gap: 10px;
  font-size: 12px;
}
.guide-step-num {
  font-family: var(--mono); font-size: 10px; font-weight: 700;
  color: var(--blue); background: rgba(10,132,255,0.12);
  border-radius: 4px; padding: 1px 6px; flex-shrink: 0;
}
.guide-step-cmd { font-family: var(--mono); color: var(--blue); font-size: 11.5px; }
.guide-step-desc { color: var(--sub); font-size: 11px; }
.guide-flag { font-family: var(--mono); color: var(--orange); font-size: 11px; }
.guide-badge {
  display: inline-block; font-size: 9px; font-weight: 700;
  padding: 1px 6px; border-radius: 4px; vertical-align: middle; margin-left: 6px;
  text-transform: uppercase; letter-spacing: .4px;
}
.guide-badge-opt  { background: rgba(142,142,147,0.15); color: var(--sub); }
.guide-badge-live { background: rgba(255,69,58,0.15);   color: var(--red); }
.guide-card-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 640px) { .guide-card-row { grid-template-columns: 1fr; } }
.guide-concept {
  background: rgba(10,132,255,0.07);
  border: 1px solid rgba(10,132,255,0.2);
  border-radius: 10px; padding: 14px 18px;
  font-size: 13px; line-height: 1.7; margin-bottom: 8px;
}
.guide-concept code {
  font-family: var(--mono); font-size: 13px;
  color: var(--green); background: rgba(48,209,88,0.1);
  padding: 2px 7px; border-radius: 5px;
}

/* ── settings tab ── */
.settings-note {
  font-size: 11px; color: var(--sub); margin-top: 12px; line-height: 1.6;
}
.input-wrap { position: relative; display: flex; align-items: center; }
.input-wrap .form-input { padding-right: 30px; }
.reveal-btn {
  position: absolute; right: 6px;
  background: none; border: none; cursor: pointer;
  color: var(--sub); font-size: 13px; padding: 0 4px;
}
.reveal-btn:hover { color: var(--text2); }
</style>
</head>
<body>

<!-- ═══ Onboarding overlay ═══ -->
<div id="onboarding" class="onb-overlay" style="display:none">
  <div class="onb-card">
    <div class="onb-logo">Predic<b>Arb</b></div>

    <div class="onb-progress">
      <div class="onb-pip active" id="pip-1"></div>
      <div class="onb-pip" id="pip-2"></div>
    </div>

    <!-- Step 1: name + timezone -->
    <div id="onb-step-1" class="onb-step">
      <div class="onb-step-title">Welcome</div>
      <div class="onb-step-sub">Personalize your setup. Everything stays on this machine — nothing is ever transmitted.</div>
      <div class="onb-form-grid">
        <div class="form-group">
          <label class="form-label">Your name</label>
          <input class="form-input" id="onb-name" placeholder="e.g. Renato" autocomplete="off"/>
        </div>
        <div class="form-group">
          <label class="form-label">Timezone</label>
          <input class="form-input" id="onb-tz" list="tz-list" placeholder="Europe/Rome" autocomplete="off"/>
          <datalist id="tz-list"></datalist>
        </div>
      </div>
      <div class="onb-actions">
        <button class="btn" id="onb-next" style="flex:1">Continue →</button>
      </div>
    </div>

    <!-- Step 2: API keys -->
    <div id="onb-step-2" class="onb-step" style="display:none">
      <div class="onb-step-title">API credentials<span class="onb-optional">optional</span></div>
      <div class="onb-step-sub">For live order placement on Polymarket. Skip this if you're paper trading.</div>
      <div class="onb-form-grid">
        <div class="form-group">
          <label class="form-label">API Key</label>
          <input class="form-input" id="onb-api-key" placeholder="Leave blank for paper trading" autocomplete="off"/>
        </div>
        <div class="form-group">
          <label class="form-label">API Secret</label>
          <div class="input-wrap">
            <input class="form-input" type="password" id="onb-api-secret" autocomplete="new-password"/>
            <button class="reveal-btn" onclick="toggleReveal('onb-api-secret',this)">👁</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Passphrase</label>
          <div class="input-wrap">
            <input class="form-input" type="password" id="onb-api-pass" autocomplete="new-password"/>
            <button class="reveal-btn" onclick="toggleReveal('onb-api-pass',this)">👁</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Wallet address (0x…)</label>
          <input class="form-input" id="onb-address" placeholder="0x..." autocomplete="off"/>
        </div>
      </div>
      <div class="onb-privacy">
        🔒 Credentials are saved to <code>data/user_prefs.json</code> and <code>.env.demo</code> on this machine only. They are never sent anywhere.
      </div>
      <div class="onb-actions">
        <button class="btn btn-ghost" id="onb-back">← Back</button>
        <button class="btn btn-ghost" id="onb-skip">Skip →</button>
        <button class="btn" id="onb-finish" style="flex:1">Save &amp; Enter →</button>
      </div>
      <div id="onb-error" style="display:none;margin-top:10px;font-size:12px;color:var(--red);font-family:var(--mono)"></div>
    </div>

  </div>
</div>

<!-- topbar -->
<header class="topbar">
  <div class="topbar-left">
    <div class="dot" id="dot"></div>
    <span class="brand">Predic<b>Arb</b></span>
  </div>
  <div class="topbar-right">
    <span class="greeting" id="greeting" style="display:none"></span>
    <span class="tz-label" id="tz-label"></span>
    <span class="clock" id="clock">--:--:--</span>
    <button class="gear-btn" id="btn-settings" title="Settings">⚙</button>
  </div>
</header>

<!-- KPI strip -->
<div class="kpi-row">
  <div class="kpi-card" id="kpi-pnl">
    <div class="kpi-label">Total P&amp;L</div>
    <div class="kpi-value">—</div>
  </div>
  <div class="kpi-card" id="kpi-long">
    <div class="kpi-label">Long</div>
    <div class="kpi-value">—</div>
  </div>
  <div class="kpi-card" id="kpi-short">
    <div class="kpi-label">Short</div>
    <div class="kpi-value">—</div>
  </div>
  <div class="kpi-card" id="kpi-orders">
    <div class="kpi-label">Open Orders</div>
    <div class="kpi-value">—</div>
  </div>
  <div class="kpi-card" id="kpi-markets">
    <div class="kpi-label">Markets</div>
    <div class="kpi-value">—</div>
  </div>
</div>

<!-- tab bar -->
<nav class="tab-bar">
  <button class="tab-btn active" data-tab="markets">Markets</button>
  <button class="tab-btn" data-tab="watchlist">Watchlist</button>
  <button class="tab-btn" data-tab="backtest">Backtest</button>
  <button class="tab-btn" data-tab="settings">Settings</button>
  <button class="tab-btn" data-tab="guide">Guide</button>
</nav>

<!-- ═══ Tab: Markets ═══ -->
<div id="tab-markets" class="tab-panel active">
  <div class="markets-body">
    <div class="col">

      <div class="panel">
        <div class="panel-hd">
          <div class="accent" style="background:var(--green)"></div>
          <span class="panel-title">Positions</span>
        </div>
        <div class="panel-body">
          <table>
            <thead><tr>
              <th>Ticker</th><th>Net</th><th>Avg Cost</th><th>P&amp;L</th><th>Updated</th>
            </tr></thead>
            <tbody id="tbl-positions"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-hd">
          <div class="accent" style="background:var(--orange)"></div>
          <span class="panel-title">Open Orders</span>
        </div>
        <div class="panel-body">
          <table>
            <thead><tr>
              <th>ID</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Price</th><th>Time</th>
            </tr></thead>
            <tbody id="tbl-open-orders"></tbody>
          </table>
        </div>
      </div>

    </div>
    <div class="col">

      <div class="panel">
        <div class="panel-hd">
          <div class="accent" style="background:var(--blue)"></div>
          <span class="panel-title">Signals</span>
        </div>
        <div class="panel-body">
          <table>
            <thead><tr>
              <th>Time</th><th>Ticker</th><th>Mid</th><th>Edge</th><th>Decision</th>
            </tr></thead>
            <tbody id="tbl-signals"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-hd">
          <div class="accent" style="background:var(--purple)"></div>
          <span class="panel-title">Fills</span>
        </div>
        <div class="panel-body">
          <table>
            <thead><tr>
              <th>Time</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Price</th>
            </tr></thead>
            <tbody id="tbl-fills"></tbody>
          </table>
        </div>
      </div>

    </div>
  </div>

  <div class="ticks-wrap">
    <div class="panel">
      <div class="panel-hd">
        <div class="accent" style="background:var(--yellow)"></div>
        <span class="panel-title">Live Ticks</span>
      </div>
      <div class="panel-body" style="max-height:150px">
        <table>
          <thead><tr>
            <th>Ticker</th><th>Bid</th><th>Ask</th><th>Mid</th>
            <th>Bid Sz</th><th>Ask Sz</th><th>Updated</th>
          </tr></thead>
          <tbody id="tbl-ticks"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Tab: Watchlist ═══ -->
<div id="tab-watchlist" class="tab-panel">
  <div class="page">

    <div class="card">
      <div class="card-hd">
        <div class="accent" style="background:var(--blue)"></div>
        <span class="card-title">Add New Market</span>
      </div>
      <div class="card-hint">Paste a YES token_id from Polymarket and set your fair-value benchmark.</div>
      <div class="card-body">
        <div class="form-grid">
          <div class="form-group">
            <label class="form-label">YES token_id</label>
            <input class="form-input" id="inp-ticker" placeholder="0xabc123def456…"/>
          </div>
          <div class="form-group">
            <label class="form-label">Label (optional)</label>
            <input class="form-input" id="inp-label" placeholder="e.g. Trump wins 2026"/>
          </div>
          <div class="form-group">
            <label class="form-label">Benchmark (0–1)</label>
            <input class="form-input" id="inp-bench" value="0.50" placeholder="0.60"/>
          </div>
        </div>
        <div class="form-row">
          <button class="btn" id="btn-add">＋ Add Ticker</button>
          <span class="status-msg" id="add-status"></span>
        </div>
      </div>
    </div>

    <div class="panel watch-panel">
      <div class="panel-hd">
        <div class="accent" style="background:var(--green)"></div>
        <span class="panel-title">Watched Markets</span>
      </div>
      <div class="panel-body">
        <table>
          <thead><tr>
            <th>Ticker</th><th>Label</th><th>Benchmark</th>
            <th>Bid</th><th>Ask</th><th>Mid</th><th>Added</th><th></th>
          </tr></thead>
          <tbody id="tbl-watched"></tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<!-- ═══ Tab: Backtest ═══ -->
<div id="tab-backtest" class="tab-panel">
  <div class="page">

    <div class="card">
      <div class="card-hd">
        <div class="accent" style="background:var(--purple)"></div>
        <span class="card-title">Configure &amp; Run Backtest</span>
      </div>
      <div class="card-body">
        <div class="form-grid">
          <div class="form-group">
            <label class="form-label">Ticker</label>
            <input class="form-input" id="bt-ticker" placeholder="0xabc123…"/>
          </div>
          <div class="form-group">
            <label class="form-label">Tick CSV path</label>
            <input class="form-input" id="bt-csv" placeholder="/path/to/ticks.csv"/>
          </div>
          <div class="form-group">
            <label class="form-label">Benchmark</label>
            <input class="form-input" id="bt-bench" value="0.60"/>
          </div>
          <div class="form-group">
            <label class="form-label">Size</label>
            <input class="form-input" id="bt-size" value="100"/>
          </div>
          <div class="form-group">
            <label class="form-label">Min Edge</label>
            <input class="form-input" id="bt-edge" value="0.02"/>
          </div>
        </div>
        <div class="form-row">
          <button class="btn btn-purple" id="btn-run-bt">▶ Run Backtest</button>
          <span class="status-msg" id="bt-status"></span>
        </div>
      </div>
    </div>

    <div class="bt-kpi-row">
      <div class="kpi-card" id="bt-kpi-trades">
        <div class="kpi-label">Trades</div><div class="kpi-value">—</div>
      </div>
      <div class="kpi-card" id="bt-kpi-wr">
        <div class="kpi-label">Win Rate</div><div class="kpi-value">—</div>
      </div>
      <div class="kpi-card" id="bt-kpi-pnl">
        <div class="kpi-label">Total P&amp;L</div><div class="kpi-value">—</div>
      </div>
      <div class="kpi-card" id="bt-kpi-sharpe">
        <div class="kpi-label">Sharpe</div><div class="kpi-value">—</div>
      </div>
      <div class="kpi-card" id="bt-kpi-dd">
        <div class="kpi-label">Max Drawdown</div><div class="kpi-value">—</div>
      </div>
    </div>

    <div class="panel bt-hist-panel">
      <div class="panel-hd">
        <div class="accent" style="background:var(--orange)"></div>
        <span class="panel-title">Run History</span>
      </div>
      <div class="panel-body">
        <table>
          <thead><tr>
            <th>#</th><th>Time</th><th>Market</th><th>Trades</th>
            <th>Win%</th><th>P&amp;L</th><th>Sharpe</th><th>Drawdown</th>
          </tr></thead>
          <tbody id="tbl-bt-history"></tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<!-- ═══ Tab: Settings ═══ -->
<div id="tab-settings" class="tab-panel">
  <div class="page">

    <div class="card" style="max-width:560px">
      <div class="card-hd">
        <div class="accent" style="background:var(--blue)"></div>
        <span class="card-title">Identity &amp; Timezone</span>
      </div>
      <div class="card-body">
        <div class="form-grid" style="grid-template-columns:1fr 1fr">
          <div class="form-group">
            <label class="form-label">Your name</label>
            <input class="form-input" id="set-name" placeholder="Renato" autocomplete="off"/>
          </div>
          <div class="form-group">
            <label class="form-label">Timezone</label>
            <input class="form-input" id="set-tz" list="tz-list-settings" placeholder="Europe/Rome" autocomplete="off"/>
            <datalist id="tz-list-settings"></datalist>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="max-width:560px">
      <div class="card-hd">
        <div class="accent" style="background:var(--orange)"></div>
        <span class="card-title">Polymarket API credentials<span class="onb-optional">optional</span></span>
      </div>
      <div class="card-hint">For live order placement. Leave blank for paper trading.</div>
      <div class="card-body">
        <div class="form-grid" style="grid-template-columns:1fr">
          <div class="form-group">
            <label class="form-label">API Key</label>
            <input class="form-input" id="set-api-key" placeholder="Leave blank for paper trading" autocomplete="off"/>
          </div>
          <div class="form-group">
            <label class="form-label">API Secret</label>
            <div class="input-wrap">
              <input class="form-input" type="password" id="set-api-secret" autocomplete="new-password"/>
              <button class="reveal-btn" onclick="toggleReveal('set-api-secret',this)">👁</button>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Passphrase</label>
            <div class="input-wrap">
              <input class="form-input" type="password" id="set-api-pass" autocomplete="new-password"/>
              <button class="reveal-btn" onclick="toggleReveal('set-api-pass',this)">👁</button>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Wallet address (0x…)</label>
            <input class="form-input" id="set-address" placeholder="0x..." autocomplete="off"/>
          </div>
        </div>
        <div class="form-row">
          <button class="btn" id="btn-save-settings">Save settings</button>
          <span class="status-msg" id="settings-status"></span>
        </div>
        <p class="settings-note">
          🔒 All settings are saved to <code>data/user_prefs.json</code> and <code>.env.demo</code> on this machine only. Nothing is ever transmitted or stored remotely. Restart PredicArb after changing API keys for them to take effect.
        </p>
      </div>
    </div>

  </div>
</div>

<!-- ═══ Tab: Guide ═══ -->
<div id="tab-guide" class="tab-panel">
  <div class="guide-page">

    <!-- Core Concept -->
    <div class="guide-section">
      <div class="guide-section-title">Core Concept</div>
      <div class="guide-concept">
        <code>edge = benchmark_prob − market_mid_price</code><br/>
        You supply a "true" probability. PredicArb trades when Polymarket's price disagrees
        enough: <b style="color:var(--green)">edge &gt; min_edge</b> → BUY YES,
        <b style="color:var(--red)">edge &lt; −min_edge</b> → SELL YES.
        No signal → no trade.
      </div>
    </div>

    <!-- Typical Workflow -->
    <div class="guide-section">
      <div class="card">
        <div class="card-hd">
          <div class="accent" style="background:var(--blue)"></div>
          <span class="card-title">Typical Workflow</span>
        </div>
        <div class="card-body">
          <div class="guide-flow">
            <div class="guide-flow-step">
              <span class="guide-step-num">1</span>
              <span class="guide-step-cmd">list-markets</span>
              <span class="guide-step-desc">find token_ids for markets you want to trade</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">2</span>
              <span class="guide-step-cmd">watch</span>
              <span class="guide-step-desc">sanity-check live prices before touching anything</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">3</span>
              <span class="guide-step-cmd">signal</span>
              <span class="guide-step-desc">one-shot edge calculation — is there an edge right now?</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">4</span>
              <span class="guide-step-cmd">run --dry-run</span>
              <span class="guide-step-desc">paper trade, watch DB fill with signals and orders</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">5</span>
              <span class="guide-step-cmd">collect</span>
              <span class="guide-step-desc">stream WS ticks to CSV for backtesting</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">6</span>
              <span class="guide-step-cmd">backtest</span>
              <span class="guide-step-desc">replay collected ticks, review win rate / Sharpe / drawdown</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">7</span>
              <span class="guide-step-cmd">run --ws</span>
              <span class="guide-step-desc">go live — start small, watch positions here</span>
            </div>
            <div class="guide-flow-step">
              <span class="guide-step-num">8</span>
              <span class="guide-step-cmd">monitor</span>
              <span class="guide-step-desc">runs alongside run, records fills and updates P&amp;L</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Command Reference -->
    <div class="guide-section">
      <div class="card">
        <div class="card-hd">
          <div class="accent" style="background:var(--orange)"></div>
          <span class="card-title">Command Reference</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="guide-table">
            <thead><tr>
              <th>Command</th><th>Purpose</th><th>Auth needed?</th>
            </tr></thead>
            <tbody>
              <tr><td>health</td><td>API connectivity + account balance</td><td><span class="sub">No</span></td></tr>
              <tr><td>list-markets</td><td>Browse markets with live bid/ask</td><td><span class="sub">No</span></td></tr>
              <tr><td>watch</td><td>Stream a single market orderbook</td><td><span class="sub">No</span></td></tr>
              <tr><td>signal</td><td>One-shot edge check</td><td><span class="sub">No</span></td></tr>
              <tr><td>run</td><td>Live trading loop (REST or WS)</td><td><span class="guide-badge guide-badge-live">Live orders</span></td></tr>
              <tr><td>run-multi</td><td>Concurrent WS trading, N markets</td><td><span class="guide-badge guide-badge-live">Live orders</span></td></tr>
              <tr><td>collect</td><td>WS ticks → DB + CSV</td><td><span class="sub">No</span></td></tr>
              <tr><td>backtest</td><td>Replay tick CSV through strategy</td><td><span class="sub">No</span></td></tr>
              <tr><td>monitor</td><td>Poll order fills, update positions</td><td><span class="guide-badge guide-badge-live">Live orders</span></td></tr>
              <tr><td>positions</td><td>Print positions + P&amp;L</td><td><span class="sub">No</span></td></tr>
              <tr><td>orders</td><td>Print recent orders</td><td><span class="sub">No</span></td></tr>
              <tr><td>trade</td><td>Place single manual limit order</td><td><span class="guide-badge guide-badge-live">Live orders</span></td></tr>
              <tr><td>dashboard</td><td>Open browser or TUI dashboard</td><td><span class="sub">No</span></td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Key examples -->
    <div class="guide-section">
      <div class="guide-card-row">

        <div class="card">
          <div class="card-hd">
            <div class="accent" style="background:var(--green)"></div>
            <span class="card-title">find + check + paper trade</span>
          </div>
          <div class="card-body" style="padding:8px 12px">
<pre class="guide-pre"><span class="cmt"># find markets</span>
<span class="cmd">predicarb list-markets --keywords "fed rate cut"</span>

<span class="cmt"># watch live prices</span>
<span class="cmd">predicarb watch --ticker &lt;TOKEN_ID&gt;</span>

<span class="cmt"># check edge</span>
<span class="cmd">predicarb signal --ticker &lt;TOKEN_ID&gt; \
  --benchmark <span class="val">0.65</span></span>

<span class="cmt"># paper trade</span>
<span class="cmd">predicarb run --ticker &lt;TOKEN_ID&gt; \
  --benchmark <span class="val">0.65</span> --size <span class="val">10</span> --dry-run --ws</span></pre>
          </div>
        </div>

        <div class="card">
          <div class="card-hd">
            <div class="accent" style="background:var(--blue)"></div>
            <span class="card-title">collect + backtest</span>
          </div>
          <div class="card-body" style="padding:8px 12px">
<pre class="guide-pre"><span class="cmt"># stream ticks to CSV</span>
<span class="cmd">predicarb collect --ticker &lt;TOKEN_ID&gt; \
  --csv data/my_market.csv</span>
<span class="cmt"># Ctrl-C to stop</span>

<span class="cmt"># replay collected data</span>
<span class="cmd">predicarb backtest \
  --ticker &lt;TOKEN_ID&gt; \
  --tick-csv data/my_market.csv \
  --benchmark <span class="val">0.65</span> \
  --size <span class="val">100</span> --min-edge <span class="val">0.02</span> \
  --verbose</span></pre>
          </div>
        </div>

        <div class="card">
          <div class="card-hd">
            <div class="accent" style="background:var(--red)"></div>
            <span class="card-title">live trading + monitor</span>
          </div>
          <div class="card-body" style="padding:8px 12px">
<pre class="guide-pre"><span class="cmt"># go live (terminal 1)</span>
<span class="cmd">predicarb run --ticker &lt;TOKEN_ID&gt; \
  --benchmark <span class="val">0.60</span> --size <span class="val">50</span> \
  --min-edge <span class="val">0.03</span> \
  --max-long <span class="val">200</span> --max-loss <span class="val">50</span> \
  --ws</span>

<span class="cmt"># record fills (terminal 2)</span>
<span class="cmd">predicarb monitor</span>

<span class="cmt"># view state</span>
<span class="cmd">predicarb positions</span>
<span class="cmd">predicarb orders --status placed</span></pre>
          </div>
        </div>

        <div class="card">
          <div class="card-hd">
            <div class="accent" style="background:var(--purple)"></div>
            <span class="card-title">multi-market + ZQ benchmark</span>
          </div>
          <div class="card-body" style="padding:8px 12px">
<pre class="guide-pre"><span class="cmt"># trade 3 markets concurrently</span>
<span class="cmd">predicarb run-multi \
  --tickers &lt;ID1&gt; &lt;ID2&gt; &lt;ID3&gt; \
  --benchmark <span class="val">0.55</span> \
  --size <span class="val">25</span> --dry-run</span>

<span class="cmt"># use CME futures for benchmark</span>
<span class="cmd">predicarb run --ticker &lt;ID&gt; \
  --benchmark-source <span class="val">zq</span> \
  --zq-meeting-date <span class="val">2026-09-17</span> \
  --size <span class="val">50</span></span></pre>
          </div>
        </div>

      </div>
    </div>

    <!-- Risk flags -->
    <div class="guide-section">
      <div class="card">
        <div class="card-hd">
          <div class="accent" style="background:var(--red)"></div>
          <span class="card-title">Risk Limits (run / run-multi)</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="guide-table">
            <thead><tr><th>Flag</th><th>Default</th><th>Effect</th></tr></thead>
            <tbody>
              <tr><td>--min-edge N</td><td>0.02</td><td>Minimum |edge| to trigger a trade</td></tr>
              <tr><td>--max-spread N</td><td>0.10</td><td>Skip quote if bid/ask spread is too wide</td></tr>
              <tr><td>--max-staleness N</td><td>60s</td><td>Skip quote older than N seconds</td></tr>
              <tr><td>--min-depth N</td><td>none</td><td>Require N+ contracts on both sides</td></tr>
              <tr><td>--max-long N</td><td>∞</td><td>Halt buying if net long ≥ N contracts</td></tr>
              <tr><td>--max-short N</td><td>0</td><td>Maximum net short (0 = no shorting)</td></tr>
              <tr><td>--max-loss N</td><td>none</td><td>Stop trading if realized P&amp;L &lt; −N</td></tr>
              <tr><td>--dry-run</td><td>off</td><td>Log decisions without placing real orders</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Benchmark sources -->
    <div class="guide-section">
      <div class="card">
        <div class="card-hd">
          <div class="accent" style="background:var(--yellow)"></div>
          <span class="card-title">Benchmark Sources</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="guide-table">
            <thead><tr><th>Key</th><th>Flag example</th><th>Notes</th></tr></thead>
            <tbody>
              <tr>
                <td>constant</td>
                <td style="font-family:var(--mono);font-size:11px">--benchmark 0.42</td>
                <td>Fixed probability — good for paper trading and backtesting</td>
              </tr>
              <tr>
                <td>zq</td>
                <td style="font-family:var(--mono);font-size:11px">--benchmark-source zq --zq-meeting-date 2026-06-18</td>
                <td>CME 30-Day Fed Funds Futures implied P(cut). ~15 min delay via Yahoo Finance</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Config -->
    <div class="guide-section">
      <div class="card">
        <div class="card-hd">
          <div class="accent" style="background:var(--sub)"></div>
          <span class="card-title">Configuration (.env.demo)</span>
        </div>
        <div class="card-body" style="padding:0">
          <table class="guide-table">
            <thead><tr><th>Variable</th><th>Default</th><th>Purpose</th></tr></thead>
            <tbody>
              <tr><td>POLYMARKET_ENV</td><td>demo</td><td>demo or prod — picks .env.* file</td></tr>
              <tr><td>POLYMARKET_API_KEY</td><td>—</td><td>L2 HMAC API key (live orders only)</td></tr>
              <tr><td>POLYMARKET_API_SECRET</td><td>—</td><td>L2 HMAC secret (live orders only)</td></tr>
              <tr><td>POLYMARKET_API_PASSPHRASE</td><td>—</td><td>L2 HMAC passphrase (live orders only)</td></tr>
              <tr><td>POLYMARKET_ADDRESS</td><td>—</td><td>Wallet address 0x… (live orders only)</td></tr>
              <tr><td>POLYMARKET_LOG_LEVEL</td><td>INFO</td><td>DEBUG / INFO / WARNING / ERROR</td></tr>
            </tbody>
          </table>
          <div style="padding:10px 14px;font-size:11px;color:var(--sub)">
            Credentials can also be set in the <b style="color:var(--text2)">Settings</b> tab above — they're stored locally in <code style="font-family:var(--mono);color:var(--blue)">data/user_prefs.json</code> and written to <code style="font-family:var(--mono);color:var(--blue)">.env.demo</code> automatically.
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- toasts -->
<div id="toasts"></div>

<!-- bottom-left credit (fixed) -->
<div class="footer-credit">courtesy of T</div>

<script>
// ── utils ─────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const trunc = (s, n = 16) => s && s.length > n ? s.slice(0, n) + '…' : (s || '—');
const dash  = v => v != null ? v : '—';

const fmtPnl = v => {
  if (v == null) return '<span class="sub mono">—</span>';
  const cls  = v > 0 ? 'green' : v < 0 ? 'red' : 'sub';
  const sign = v > 0 ? '+' : '';
  return `<span class="${cls} mono">${sign}${v.toFixed(2)}</span>`;
};

const fmtEdge = e => {
  if (e == null) return '<span class="sub mono">—</span>';
  const cls  = e > 0.02 ? 'green' : e < -0.02 ? 'red' : 'sub';
  const sign = e > 0 ? '+' : '';
  return `<span class="${cls} mono">${sign}${e.toFixed(3)}</span>`;
};

const fmtSide = s => {
  const u = (s || '').toUpperCase();
  return u === 'BUY'
    ? '<span class="green mono">▲ BUY</span>'
    : '<span class="red mono">▼ SELL</span>';
};

const fmtDecision = d => {
  const u = (d || '').toUpperCase();
  if (u.includes('BUY'))  return '<span class="green">▲ BUY</span>';
  if (u.includes('SELL')) return '<span class="red">▼ SELL</span>';
  return '<span class="sub">— hold</span>';
};

const fmt4 = v =>
  v != null ? `<span class="mono">${v.toFixed(4)}</span>` : '<span class="sub">—</span>';

const emptyRow = cols =>
  `<tr><td colspan="${cols}" class="empty-cell">No data yet</td></tr>`;

function toggleReveal(inputId, btn) {
  const el = $(inputId);
  if (el.type === 'password') { el.type = 'text';     btn.textContent = '🙈'; }
  else                        { el.type = 'password'; btn.textContent = '👁'; }
}

// ── tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
  });
});

$('btn-settings').addEventListener('click', () => {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelector('[data-tab="settings"]').classList.add('active');
  $('tab-settings').classList.add('active');
});

// ── prefs ─────────────────────────────────────────────────────────────────────
let _prefs = {};

function tzShortName(tz) {
  try {
    return new Intl.DateTimeFormat('en', { timeZone: tz, timeZoneName: 'short' })
      .formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || tz;
  } catch { return tz; }
}

function populateTzDatalist(listId) {
  const dl = $(listId);
  if (!dl) return;
  const tzs = (() => {
    try { return Intl.supportedValuesOf('timeZone'); }
    catch { return [
      'America/New_York','America/Chicago','America/Denver','America/Los_Angeles',
      'America/Sao_Paulo','America/Toronto','America/Vancouver',
      'Europe/London','Europe/Paris','Europe/Rome','Europe/Berlin',
      'Europe/Madrid','Europe/Amsterdam','Europe/Zurich','Europe/Stockholm',
      'Europe/Warsaw','Europe/Kiev','Europe/Moscow',
      'Asia/Dubai','Asia/Kolkata','Asia/Singapore','Asia/Hong_Kong',
      'Asia/Tokyo','Asia/Seoul','Asia/Shanghai',
      'Australia/Sydney','Australia/Melbourne','Pacific/Auckland','UTC',
    ]; }
  })();
  tzs.forEach(tz => {
    const opt = document.createElement('option');
    opt.value = tz;
    dl.appendChild(opt);
  });
}

function applyPrefs(prefs) {
  _prefs = prefs;

  const greetEl = $('greeting');
  if (prefs.name) {
    greetEl.innerHTML = `Back to printing money, <b>${prefs.name}</b>`;
    greetEl.style.display = '';
  } else {
    greetEl.style.display = 'none';
  }

  $('tz-label').textContent = tzShortName(prefs.timezone || 'UTC');

  try {
    _clockFmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: prefs.timezone || 'Europe/Rome',
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    });
  } catch {
    _clockFmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'UTC',
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    });
  }

  if ($('set-name'))       $('set-name').value       = prefs.name || '';
  if ($('set-tz'))         $('set-tz').value         = prefs.timezone || '';
  if ($('set-api-key'))    $('set-api-key').value    = prefs.polymarket_api_key || '';
  if ($('set-api-secret')) $('set-api-secret').value = prefs.polymarket_api_secret || '';
  if ($('set-api-pass'))   $('set-api-pass').value   = prefs.polymarket_api_passphrase || '';
  if ($('set-address'))    $('set-address').value    = prefs.polymarket_address || '';
}

async function loadPrefs() {
  try {
    const res = await fetch('/api/prefs');
    const prefs = await res.json();
    applyPrefs(prefs);
    if (!prefs.onboarded) showOnboarding(prefs);
  } catch {
    showOnboarding({});
  }
}

async function savePrefsApi(data) {
  const res = await fetch('/api/prefs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('save failed');
}

// ── onboarding ────────────────────────────────────────────────────────────────
function showOnboarding(prefs) {
  $('onb-name').value = prefs.name || '';
  $('onb-tz').value   = prefs.timezone ||
    Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Rome';
  $('onb-api-key').value    = prefs.polymarket_api_key || '';
  $('onb-api-secret').value = prefs.polymarket_api_secret || '';
  $('onb-api-pass').value   = prefs.polymarket_api_passphrase || '';
  $('onb-address').value    = prefs.polymarket_address || '';
  populateTzDatalist('tz-list');
  $('onboarding').style.display = 'flex';
}

function setOnbStep(n) {
  $('onb-step-1').style.display = n === 1 ? '' : 'none';
  $('onb-step-2').style.display = n === 2 ? '' : 'none';
  $('pip-1').classList.toggle('active', n >= 1);
  $('pip-2').classList.toggle('active', n >= 2);
}

$('onb-next').addEventListener('click', () => {
  if (!$('onb-name').value.trim()) { $('onb-name').focus(); return; }
  if (!$('onb-tz').value.trim())   { $('onb-tz').focus();   return; }
  setOnbStep(2);
});

$('onb-back').addEventListener('click', () => setOnbStep(1));

$('onb-skip').addEventListener('click', async () => {
  const data = {
    ..._prefs,
    name:      $('onb-name').value.trim(),
    timezone:  $('onb-tz').value.trim() || 'Europe/Rome',
    onboarded: true,
  };
  try {
    await savePrefsApi(data);
    applyPrefs(data);
    $('onboarding').style.display = 'none';
    toast(`Welcome, ${data.name || 'trader'} 💸`, 'ok');
  } catch {
    $('onboarding').style.display = 'none';
    toast('Saved locally', 'ok');
  }
});

$('onb-finish').addEventListener('click', async () => {
  const errEl = $('onb-error');
  errEl.style.display = 'none';
  $('onb-finish').disabled = true;
  $('onb-finish').textContent = 'Saving…';
  const data = {
    name:                      $('onb-name').value.trim(),
    timezone:                  $('onb-tz').value.trim() || 'Europe/Rome',
    onboarded:                 true,
    polymarket_api_key:        $('onb-api-key').value.trim(),
    polymarket_api_secret:     $('onb-api-secret').value.trim(),
    polymarket_api_passphrase: $('onb-api-pass').value.trim(),
    polymarket_address:        $('onb-address').value.trim(),
  };
  try {
    await savePrefsApi(data);
    applyPrefs(data);
    $('onboarding').style.display = 'none';
    toast(`Welcome, ${data.name || 'trader'} 💸`, 'ok');
  } catch (e) {
    errEl.textContent = '⚠ ' + (e.message || 'Save failed — check the server console.');
    errEl.style.display = '';
    toast('Could not save settings', 'err');
  } finally {
    $('onb-finish').disabled = false;
    $('onb-finish').textContent = 'Enter PredicArb →';
  }
});

// ── settings save ─────────────────────────────────────────────────────────────
$('btn-save-settings').addEventListener('click', async () => {
  const st = $('settings-status');
  const data = {
    ..._prefs,
    name:                      $('set-name').value.trim(),
    timezone:                  $('set-tz').value.trim() || 'Europe/Rome',
    onboarded:                 true,
    polymarket_api_key:        $('set-api-key').value.trim(),
    polymarket_api_secret:     $('set-api-secret').value.trim(),
    polymarket_api_passphrase: $('set-api-pass').value.trim(),
    polymarket_address:        $('set-address').value.trim(),
  };
  try {
    await savePrefsApi(data);
    applyPrefs(data);
    st.innerHTML = '<span class="green">✓ Saved.</span>';
    setTimeout(() => st.innerHTML = '', 3000);
  } catch {
    st.innerHTML = '<span class="red">⚠ Save failed.</span>';
  }
});

// ── clock ─────────────────────────────────────────────────────────────────────
let _clockFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Rome',
  day: '2-digit', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
});
function tick() { $('clock').textContent = _clockFmt.format(new Date()); }
setInterval(tick, 1000);
tick();

// ── data ──────────────────────────────────────────────────────────────────────
async function fetchData() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    render(d);
    $('dot').style.background = 'var(--green)';
  } catch {
    $('dot').style.background = 'var(--red)';
  }
}
setInterval(fetchData, 5000);

// ── render ────────────────────────────────────────────────────────────────────
function render(d) {
  const s = d.summary || {};
  setKpi('kpi-pnl',     fmtPnl(s.total_pnl));
  setKpi('kpi-long',    `<span class="green mono">${s.open_long  ?? 0}</span>`);
  setKpi('kpi-short',   `<span class="red mono">${s.open_short  ?? 0}</span>`);
  setKpi('kpi-orders',  `<span class="orange mono">${s.open_orders ?? 0}</span>`);
  setKpi('kpi-markets', `<span class="blue mono">${s.markets     ?? 0}</span>`);

  fill('tbl-positions', d.positions, r => `<tr>
    <td class="blue mono">${trunc(r.ticker, 18)}</td>
    <td class="mono">${dash(r.yes_count)}</td>
    <td>${fmt4(r.avg_cost)}</td>
    <td>${fmtPnl(r.realized_pnl)}</td>
    <td class="sub mono">${r.ts_rome || '—'}</td>
  </tr>`, 5);

  fill('tbl-open-orders', d.open_orders, r => `<tr>
    <td class="sub mono">${r.id}</td>
    <td class="blue mono">${trunc(r.ticker, 14)}</td>
    <td>${fmtSide(r.side)}</td>
    <td class="mono">${dash(r.count)}</td>
    <td>${fmt4(r.price)}</td>
    <td class="sub mono">${r.ts_rome || '—'}</td>
  </tr>`, 6);

  fill('tbl-signals', d.signals, r => `<tr>
    <td class="sub mono">${r.ts_rome || '—'}</td>
    <td class="blue mono">${trunc(r.ticker, 14)}</td>
    <td>${fmt4(r.market_mid)}</td>
    <td>${fmtEdge(r.edge)}</td>
    <td>${fmtDecision(r.decision)}</td>
  </tr>`, 5);

  fill('tbl-fills', d.fills, r => `<tr>
    <td class="sub mono">${r.ts_rome || '—'}</td>
    <td class="blue mono">${trunc(r.ticker, 14)}</td>
    <td>${fmtSide(r.side)}</td>
    <td class="mono">${dash(r.count)}</td>
    <td>${fmt4(r.price)}</td>
  </tr>`, 5);

  fill('tbl-ticks', d.ticks, r => {
    const mid = r.yes_mid;
    const avg = (r.yes_bid != null && r.yes_ask != null)
      ? (r.yes_bid + r.yes_ask) / 2 : null;
    const midCls = mid != null && avg != null
      ? (mid > avg ? 'green' : 'red') : 'sub';
    return `<tr>
      <td class="blue mono">${trunc(r.ticker, 20)}</td>
      <td>${fmt4(r.yes_bid)}</td>
      <td>${fmt4(r.yes_ask)}</td>
      <td><span class="${midCls} mono">${mid != null ? mid.toFixed(4) : '—'}</span></td>
      <td class="mono">${dash(r.bid_size)}</td>
      <td class="mono">${dash(r.ask_size)}</td>
      <td class="sub mono">${r.ts_rome || '—'}</td>
    </tr>`;
  }, 7);

  fill('tbl-watched', d.watched, r => {
    const t = r.ticker.replace(/'/g, "\\'");
    return `<tr>
      <td class="blue mono">${trunc(r.ticker, 20)}</td>
      <td>${r.label || '<span class="sub">—</span>'}</td>
      <td class="mono">${r.benchmark != null ? r.benchmark.toFixed(2) : '—'}</td>
      <td>${fmt4(r.yes_bid)}</td>
      <td>${fmt4(r.yes_ask)}</td>
      <td>${fmt4(r.yes_mid)}</td>
      <td class="sub mono">${r.added_rome || '—'}</td>
      <td><button class="btn btn-red btn-sm" onclick="removeTicker('${t}')">✕</button></td>
    </tr>`;
  }, 8);

  fill('tbl-bt-history', d.bt_runs, r => {
    const sharpe = r.sharpe != null
      ? `<span class="mono">${r.sharpe.toFixed(3)}</span>`
      : '<span class="sub">—</span>';
    return `<tr>
      <td class="sub mono">${r.id}</td>
      <td class="sub mono">${r.ts_rome || '—'}</td>
      <td class="mono">${trunc(r.market_file, 22)}</td>
      <td class="blue mono">${r.total_trades}</td>
      <td class="green mono">${r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—'}</td>
      <td>${fmtPnl(r.total_pnl)}</td>
      <td>${sharpe}</td>
      <td class="orange mono">${r.max_drawdown != null ? r.max_drawdown.toFixed(4) : '—'}</td>
    </tr>`;
  }, 8);

  if (d.bt_runs && d.bt_runs.length > 0) {
    const r = d.bt_runs[0];
    setKpi('bt-kpi-trades', `<span class="blue mono">${r.total_trades}</span>`);
    setKpi('bt-kpi-wr',     `<span class="green mono">${r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—'}</span>`);
    setKpi('bt-kpi-pnl',    fmtPnl(r.total_pnl));
    setKpi('bt-kpi-sharpe', `<span class="mono">${r.sharpe != null ? r.sharpe.toFixed(3) : '—'}</span>`);
    setKpi('bt-kpi-dd',     `<span class="orange mono">${r.max_drawdown != null ? r.max_drawdown.toFixed(4) : '—'}</span>`);
  }
}

function fill(id, rows, rowFn, cols) {
  const tbody = $(id);
  if (!tbody) return;
  if (!rows || rows.length === 0) { tbody.innerHTML = emptyRow(cols); return; }
  tbody.innerHTML = rows.map(rowFn).join('');
}

function setKpi(id, html) {
  const el = document.querySelector(`#${id} .kpi-value`);
  if (el) el.innerHTML = html;
}

// ── add ticker ────────────────────────────────────────────────────────────────
$('btn-add').addEventListener('click', async () => {
  const ticker = $('inp-ticker').value.trim();
  const label  = $('inp-label').value.trim();
  const bench  = parseFloat($('inp-bench').value);
  const st     = $('add-status');

  if (!ticker) { st.innerHTML = '<span class="red">⚠ Token ID required.</span>'; return; }
  if (isNaN(bench) || bench <= 0 || bench >= 1) {
    st.innerHTML = '<span class="red">⚠ Benchmark must be 0–1.</span>'; return;
  }

  try {
    const res = await fetch('/api/watched', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, label, benchmark: bench }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || 'error'); }
    $('inp-ticker').value = '';
    $('inp-label').value  = '';
    st.innerHTML = '<span class="green">✓ Added.</span>';
    setTimeout(() => st.innerHTML = '', 3000);
    fetchData();
  } catch (e) {
    st.innerHTML = `<span class="red">⚠ ${e.message}</span>`;
  }
});

// ── remove ticker ─────────────────────────────────────────────────────────────
async function removeTicker(ticker) {
  try {
    const res = await fetch('/api/watched/' + encodeURIComponent(ticker), { method: 'DELETE' });
    if (!res.ok) throw new Error('failed');
    toast('Removed ' + ticker.slice(0, 24), 'ok');
    fetchData();
  } catch {
    toast('Remove failed', 'err');
  }
}

// ── backtest ──────────────────────────────────────────────────────────────────
let _btPoll = null;

$('btn-run-bt').addEventListener('click', async () => {
  const ticker  = $('bt-ticker').value.trim();
  const csv     = $('bt-csv').value.trim();
  const bench   = parseFloat($('bt-bench').value);
  const size    = parseInt($('bt-size').value);
  const minEdge = parseFloat($('bt-edge').value);
  const st      = $('bt-status');

  if (!ticker) { st.innerHTML = '<span class="red">⚠ Ticker required.</span>'; return; }
  if (!csv)    { st.innerHTML = '<span class="red">⚠ CSV path required.</span>'; return; }
  if (isNaN(bench) || isNaN(size) || isNaN(minEdge)) {
    st.innerHTML = '<span class="red">⚠ Invalid number.</span>'; return;
  }

  try {
    const res = await fetch('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, csv_path: csv, benchmark: bench, size, min_edge: minEdge }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || 'error'); }
    st.innerHTML = '<span class="orange">⏳ Running…</span>';
    if (_btPoll) clearInterval(_btPoll);
    _btPoll = setInterval(pollBt, 1500);
  } catch (e) {
    st.innerHTML = `<span class="red">⚠ ${e.message}</span>`;
  }
});

async function pollBt() {
  try {
    const res = await fetch('/api/backtest/status');
    const d   = await res.json();
    const st  = $('bt-status');
    if (d.status === 'done') {
      clearInterval(_btPoll); _btPoll = null;
      st.innerHTML = '<span class="green">✓ Complete.</span>';
      setTimeout(() => st.innerHTML = '', 4000);
      fetchData();
    } else if (d.status === 'error') {
      clearInterval(_btPoll); _btPoll = null;
      st.innerHTML = `<span class="red">⚠ ${d.error}</span>`;
    }
  } catch { clearInterval(_btPoll); _btPoll = null; }
}

// ── toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── init ──────────────────────────────────────────────────────────────────────
populateTzDatalist('tz-list-settings');
loadPrefs().then(() => fetchData());
</script>
</body>
</html>"""
