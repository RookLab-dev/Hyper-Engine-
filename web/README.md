# Hyper-Engine Web Dashboard

React + TypeScript + Vite frontend for the Hyper-Engine FastAPI backend, developed under RookLab.

The dashboard provides a browser-based interface for monitoring persisted trading activity and reviewing performance, strategy analysis, Monte Carlo results, and risk information.

**Read-only:** The dashboard does not provide trading controls or submit, cancel, or modify orders.

## Development

### 1. Start the backend

From the repository root:

```powershell
poetry run uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Keep the backend running in this terminal.

### 2. Start the frontend

Open a second terminal:

```powershell
cd web
npm install
npm run dev
```

Open the local address printed by Vite.

During development, Vite proxies `/api` requests to the local FastAPI backend at `http://127.0.0.1:8000`.

## Available Scripts

Run these commands from the `web/` directory.

| Command             | Purpose                                             |
| ------------------- | --------------------------------------------------- |
| `npm run dev`       | Start the Vite development server                   |
| `npm run build`     | Create the production frontend build in `web/dist/` |
| `npm run preview`   | Preview the production build locally                |
| `npm run typecheck` | Run TypeScript type-checking with `tsc --noEmit`    |
| `npm run lint`      | Run ESLint                                          |
| `npm run test`      | Run the Vitest test suite                           |

## Configuration

* **`VITE_API_BASE_URL`** — optional API base URL override. When unset, API requests use the configured default and development proxy behavior.

Never place exchange private keys, signing credentials, or other secrets in frontend code or browser-accessible environment variables.

## Data Freshness and Interpretation

* The dashboard uses polling rather than WebSockets for data refresh.
* A session with `ended_at: null` is an open database record; it does not prove that the trading process is currently running.
* Displayed performance and risk values reflect the data exposed by the backend. Derived dashboard values should not be treated as authoritative risk-enforcement counters unless explicitly documented as such.
* The frontend reads data through the FastAPI API rather than accessing the SQLite database directly.

## Safety Boundary

The web dashboard is intended for monitoring and analysis. Trading execution and risk enforcement remain responsibilities of the backend.

Do not bypass execution gates, weaken risk controls, or expose the development API publicly without an appropriate deployment and security configuration.

## Related Documentation

* [Root README](../README.md)
* [Dashboard documentation](../docs/dashboard.md)
* [Architecture documentation](../docs/Hyper-Engine_architecture.md)

---

**Project:** Hyper-Engine, developed under [RookLab](https://github.com/RookLab-dev).

The dashboard is a monitoring interface; it does not establish that the trading framework is profitable, fully production-ready, or suitable for mainnet trading.
