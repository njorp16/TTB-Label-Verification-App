# TTB Label Verification App

A hardened proof of concept for checking single alcohol labels or batches against application data.

## Requirements

- Python 3.12
- uv

## Local Setup

```bash
uv sync
```

Copy `.env.example` to `.env` for local-only settings. Real API keys must only live in environment variables and must never be committed.

## Run Locally

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

The page should show the `/health` response from the backend.

## Test

```bash
uv run pytest
```

The unit suite is offline and must not call the live vision service. Production verification uses
the model, timeout, image-size, JPEG-quality, detail, and batch-concurrency environment variables
listed in `.env.example`. Keep `OPENAI_API_KEY` in the environment only.

The `/verify` response includes total server `latency_ms`. Server logs also record upload-read,
preprocessing, model, and comparison timings without recording label text, filenames, or images.

## Vision Sample

Set `OPENAI_API_KEY` in your local `.env`, then run one image through the VisionService:

```bash
uv run python scripts/run_vision_sample.py path/to/sample-label.jpg
```

If no path is provided, the script generates a simple sample label image in memory and prints the extracted JSON:

```bash
uv run python scripts/run_vision_sample.py
```

## Deployment: Railway

The checked-in `railway.toml` sets the start command and `/health` healthcheck path.

1. Push this repo to GitHub:

   ```bash
   git add .
   git commit -m "Scaffold Phase 0 FastAPI app"
   git push
   ```

2. In Railway, create a new project from the GitHub repo.
3. Add environment variables in Railway using `.env.example` as the template.
4. Generate a public Railway domain for the service.
5. Deploy.

If you prefer the Railway CLI:

   ```bash
   railway login
   railway init
   railway up
   ```

Verify the live URL:

   - `/` loads the frontend.
   - `/health` returns `{"status":"healthy"}`.
   - The frontend shows `Health response: {"status":"healthy"}`.

## Secret Handling

`.env` and other local environment files are ignored by Git. Commit `.env.example` only with placeholder values.
