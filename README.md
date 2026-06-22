# TTB Label Verification App

A stateless proof of concept that compares alcohol-label images with application data and highlights fields that need human review.

- **Live demo:** https://ttb-label-verification-app-production.up.railway.app
- **Public repository:** https://github.com/njorp16/TTB-Label-Verification-App

## What It Does

- Checks one label or a batch of up to 10 labels.
- Extracts visible label text with a vision model.
- Returns a clear `PASS` or `NEEDS_REVIEW` verdict with field-level reasons.
- Requires an exact, case-sensitive government-warning match.
- Uses normalized or fuzzy comparison rules for all other fields.
- Reports server latency for each single-label result.

The interface uses large controls, plain language, visible progress states, keyboard-accessible navigation, and responsive layouts for non-technical users.

## Approach

The browser sends application fields and an image directly to FastAPI. The backend validates and preprocesses the image, asks the vision model for structured extraction, compares each extracted value with the submitted application, and returns field-level results. Batch requests use bounded concurrency and isolate an invalid or failed item so its siblings can still complete.

The application has no database. Images and submitted values are processed in memory for the current request and are not intentionally persisted by the application.

## Comparison Rules

The government health warning must match the extracted text exactly, including capitalization, punctuation, and spacing. Brand name, product class, producer, and country use normalized fuzzy matching. Alcohol by volume and net contents use normalized numeric and unit-aware comparisons.

Missing or unreadable extracted fields fail safely as `NEEDS_REVIEW`; the service does not guess missing label text.

## Tools

- Python 3.12, FastAPI, Uvicorn
- OpenAI vision model and structured extraction
- Pillow image preprocessing
- RapidFuzz normalized text comparison
- Plain HTML, CSS, and JavaScript frontend
- pytest and HTTPX test tooling
- Railway deployment
- `uv` dependency and environment management

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key for live vision verification

## Local Setup

```bash
uv sync --locked
```

Copy `.env.example` to `.env`, then replace local placeholders as needed. Never commit `.env` or a real API key.

| Variable | Purpose | Default/example |
| --- | --- | --- |
| `APP_ENV` | Runtime environment name | `local` |
| `ALLOWED_ORIGINS` | Comma-separated permitted browser origins | `http://localhost:8000` |
| `OPENAI_API_KEY` | OpenAI credential; required for verification | No real default |
| `VISION_MODEL` | Vision-capable OpenAI model | `gpt-4.1-mini` |
| `VISION_TIMEOUT_SECONDS` | Model-call timeout | `4.5` |
| `VISION_MAX_IMAGE_SIDE` | Maximum preprocessed image dimension | `1024` |
| `VISION_JPEG_QUALITY` | Preprocessed JPEG quality | `80` |
| `VISION_IMAGE_DETAIL` | Vision image detail setting | `high` |
| `BATCH_CONCURRENCY_LIMIT` | Maximum simultaneous batch model calls | `4` |

## Run Locally

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. The same FastAPI process serves both the frontend and API.

## API

- `GET /health` returns service and vision-configuration status.
- `POST /verify` accepts one image plus application form fields.
- `POST /verify/batch` accepts matching image and application arrays for 1–10 labels.

Uploads must be JPEG, PNG, or WebP images no larger than 10 MB each. API validation failures return safe user-facing messages without exposing tracebacks or credentials.

## Test and Verify

Run the offline suite, which uses fake vision clients and does not spend API credits:

```bash
uv run pytest -q
```

Run one real vision extraction after setting `OPENAI_API_KEY`:

```bash
uv run python scripts/run_vision_sample.py path/to/sample-label.jpg
```

Run the deployed end-to-end checklist, including single, batch, comparison, error, imperfect-image, and latency cases:

```bash
uv run python scripts/live_checklist.py https://ttb-label-verification-app-production.up.railway.app --latency-runs 5
```

The hard performance target is under 5,000 ms for every single-label result, measured both wall-clock and through the response's `latency_ms` value.

## Deployment

`railway.toml` starts Uvicorn on Railway's assigned port and configures `/health` as the deployment health check.

1. Create a Railway service from the public GitHub repository.
2. Configure the variables listed above in Railway; keep `OPENAI_API_KEY` in Railway's environment only.
3. Deploy the default branch and generate a public domain.
4. Confirm `/`, `/health`, single verification, batch verification, and the deployed checklist.

## Assumptions

- Each uploaded image corresponds to exactly one application record.
- Users provide a reasonably readable image containing the relevant label panel or panels.
- Batch inputs preserve the same order between images and application records.
- `OPENAI_API_KEY` and production configuration are supplied through environment variables.

## Limitations

- This is a proof of concept, not an official TTB determination or a replacement for human review.
- Vision extraction can vary with glare, blur, curvature, small text, occlusion, or unusual typography.
- There are no accounts, database, saved results, audit trail, or background jobs.
- Batch size is limited to 10 labels; the under-five-second requirement applies to single-label verification.
- Availability and latency depend on the external model API and Railway free-tier behavior, including possible cold starts.
- Although the application does not intentionally persist uploads, external hosting and model providers have their own operational and retention policies.

## Security and Privacy

`.env` and other local environment files are ignored by Git; only `.env.example` with placeholder values is committed. Secrets must remain in local or Railway environment variables. Application logs record timing and verdict metadata without recording uploaded images, filenames, extracted label text, submitted label text, or API keys.
