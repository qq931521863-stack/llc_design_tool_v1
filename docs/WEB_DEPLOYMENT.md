# Web Deployment

## 1. Architecture

The web edition keeps engineering calculations on the Python server:

```text
Browser
  -> FastAPI
  -> llc_design / backend engineering kernels
  -> structured JSON / PDF / XLSX
  -> browser rendering
```

The browser does not reimplement LLC equations. This is intentional: desktop, CLI and web workflows should not drift into separate numerical implementations.

## 2. Local run

Install web dependencies:

```bash
python -m pip install -e ".[web]"
```

Start with the project entry point:

```bash
power-design-web
```

or directly with uvicorn:

```bash
uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

OpenAPI/Swagger:

```text
http://127.0.0.1:8000/docs
```

## 3. Current LLC API

```text
GET  /api/health
GET  /api/llc/defaults
POST /api/llc/analyze
GET  /api/llc/cores
POST /api/llc/optimize
POST /api/llc/report
POST /api/llc/report.xlsx
```

The same FastAPI application also mounts the control API router from `backend.api.control`.

The SPA fallback serves the browser shell for normal application paths while `/api/*` and `/static/*` remain API/static namespaces.

## 4. Container deployment

The repository includes a provider-neutral `Dockerfile` and `render.yaml`.

Typical deployment flow:

```text
GitHub repository
 -> container build
 -> Python/FastAPI service
 -> health check /api/health
 -> public HTTPS endpoint
```

The same image can be hosted on a suitable container platform such as Render, Railway, Fly.io, Azure Container Apps, AWS App Runner, Google Cloud Run or a private Linux server.

Provider names are examples, not project dependencies.

## 5. GitHub Codespaces

Codespaces is useful for temporary engineering evaluation:

1. Create a Codespace from the repository.
2. Install/use the web dependencies defined by the development environment.
3. Start `power-design-web` or uvicorn.
4. Open forwarded port 8000.

Codespaces suspends when idle and should not be treated as the permanent production host.

## 6. Public deployment hardening

Before exposing compute-heavy engineering endpoints to anonymous Internet traffic, add or verify:

- authentication where required;
- rate limiting;
- bounded request schemas;
- request timeouts;
- per-request logging/metrics;
- resource caps for optimization/sweep jobs;
- reverse-proxy/TLS configuration;
- upload-size limits if file import endpoints are added.

The current API already uses explicit request schemas and keeps arbitrary Python execution out of the browser contract. Heavier analysis endpoints should retain that boundary.

## 7. Deployment rule

Do not modify core engineering equations solely to make a browser page easier to implement. Add web-facing schemas/adapters around the existing kernels instead. Numerical behavior should remain testable independently of the UI.
