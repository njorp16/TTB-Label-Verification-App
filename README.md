# TTB Label Verification App

A stateless proof of concept that compares alcohol-label images with application data and highlights fields that need human review.

- **Live demo:** https://ttb-label-verification-app-production.up.railway.app
- **Public repository:** https://github.com/njorp16/TTB-Label-Verification-App

## What It Does

- Checks one label or a batch of up to 10 labels.
- Extracts visible label text with a vision model.
- Returns a clear `APPROVED` or `NEEDS_REVIEW` verdict with field-level reasons.
- Requires an exact, case-sensitive government-warning match.
- Uses normalized or fuzzy comparison rules for all other fields.
- Reports server latency for each single-label result.

The interface uses large controls, plain language, visible progress states, keyboard-accessible navigation, and responsive layouts for non-technical users.

## Approach

The browser sends application fields and an image directly to FastAPI. The backend validates and preprocesses the image, asks the vision model for structured extraction, compares each extracted value with the submitted application, and returns field-level results. Batch requests use bounded concurrency and isolate an invalid or failed item so its siblings can still complete.

The application has no database. Images and submitted values are processed in memory for the current request and are not intentionally persisted by the application.

## Approach / Tools

The app was built with Codex using a Plan / Review / Execute cadence: requirements were grouped into small phases, each phase was checked against `demonstratable_requirements.md`, and implementation stayed scoped to the active phase. Offline pytest coverage was used before live checks, then the deployed Railway service was verified with `scripts/live_checklist.py`.

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
| `OPENAI_API_KEY` | OpenAI credential; required for verification | No real default |
| `VISION_MODEL` | Vision-capable OpenAI model | `gpt-4.1-mini` |
| `VISION_TIMEOUT_SECONDS` | Model-call timeout | `4.5` |
| `VISION_MAX_IMAGE_SIDE` | Maximum preprocessed image dimension | `1024` |
| `VISION_JPEG_QUALITY` | Preprocessed JPEG quality | `80` |
| `VISION_IMAGE_DETAIL` | Vision image detail setting | `high` |
| `BATCH_CONCURRENCY_LIMIT` | Maximum simultaneous batch model calls | `4` |

The configured production model is `gpt-4.1-mini`. When `OPENAI_API_KEY` is present, FastAPI validates `VISION_MODEL` against OpenAI's live model list during startup so an unknown model fails loudly instead of turning every extraction into a generic verification failure.

## Run Locally

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. The same FastAPI process serves both the frontend and API.

## API

- `GET /health` returns service and vision-configuration status.
- `POST /verify` accepts one image plus application form fields and returns `verdict`, `results`, and `latency_ms`. Each field result includes `field`, `match_type`, `expected`, `found`, and `status`, plus diagnostic `reason` and optional `score`.
- `POST /verify/batch` accepts matching image and application arrays for 1-10 labels. Batch responses intentionally wrap each `VerificationResult` with `index`, `filename`, `outcome`, and per-item `error` fields so one failed item does not hide successful siblings.

Uploads must be JPEG, PNG, or WebP images no larger than 10 MB each. API validation failures return safe user-facing messages without exposing tracebacks or credentials.

Single-label example:

```bash
curl -X POST "https://ttb-label-verification-app-production.up.railway.app/verify" \
  -F "image=@sample-label.png;type=image/png" \
  -F "brand_name=Acme Reserve" \
  -F "product_class=Red Wine" \
  -F "producer=Acme Winery LLC" \
  -F "country=United States" \
  -F "abv=13.5%" \
  -F "net_contents=750 ml" \
  -F "government_warning=GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
```

Successful response shape:

```json
{
  "verdict": "APPROVED",
  "results": [
    {
      "field": "brand_name",
      "match_type": "fuzzy_text",
      "expected": "Acme Reserve",
      "found": "Acme Reserve",
      "status": "PASS",
      "reason": "Values match after normalization.",
      "score": 100.0
    }
  ],
  "latency_ms": 2450
}
```

Batch example:

```bash
curl -X POST "https://ttb-label-verification-app-production.up.railway.app/verify/batch" \
  -F 'applications=[{"brand_name":"Acme Reserve","product_class":"Red Wine","producer":"Acme Winery LLC","country":"United States","abv":"13.5%","net_contents":"750 ml","government_warning":"GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."},{"brand_name":"Acme Reserve","product_class":"Red Wine","producer":"Acme Winery LLC","country":"France","abv":"13.5%","net_contents":"750 ml","government_warning":"GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."}]' \
  -F "images=@sample-label-1.png;type=image/png" \
  -F "images=@sample-label-2.png;type=image/png"
```

Batch response shape:

```json
{
  "summary": {"passed": 1, "needs_review": 1, "total": 2},
  "items": [
    {"index": 0, "filename": "sample-label-1.png", "outcome": "APPROVED", "result": {"verdict": "APPROVED", "results": [], "latency_ms": 2450}, "error": null},
    {"index": 1, "filename": "sample-label-2.png", "outcome": "NEEDS_REVIEW", "result": {"verdict": "NEEDS_REVIEW", "results": [], "latency_ms": 2510}, "error": null}
  ],
  "latency_ms": 2600
}
```

Error response shape:

```json
{"message": "Upload must be a JPEG, PNG, or WEBP image."}
```

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

Measured on July 9, 2026 with the command above and `--latency-runs 5`:

| Metric | p50 | p95 | Target |
| --- | ---: | ---: | ---: |
| Wall-clock single-label latency, 5 attempts | 3,536 ms | 4,744 ms | < 5,000 ms |
| Server-reported `latency_ms`, 3 successful responses from 5 attempts | 2,566 ms | 3,448 ms | < 5,000 ms |

The same pre-fix live checklist run reported `passed: false` because two single-label model calls returned safe 500 responses and the batch summary did not match the expected fixture. The latency samples above are real measured values from that run, not placeholders.

## Deployment

`railway.toml` starts Uvicorn on Railway's assigned port and configures `/health` as the deployment health check.

1. Create a Railway service from the public GitHub repository.
2. Configure the variables listed above in Railway; set `VISION_MODEL=gpt-4.1-mini` and keep `OPENAI_API_KEY` in Railway's environment only.
3. Deploy the default branch and generate a public domain.
4. Confirm `/`, `/health`, single verification, batch verification, and the deployed checklist.

## Assumptions

- Each uploaded image corresponds to exactly one application record.
- Users provide a reasonably readable image containing the relevant label panel or panels.
- Batch inputs preserve the same order between images and application records.
- `OPENAI_API_KEY` and production configuration are supplied through environment variables.

## Tradeoffs

- Batch size is capped at 10 labels instead of the brief's larger 200-300 target to stay inside Railway free-tier memory and latency constraints.
- The app stays stateless with no database or audit trail because this proof of concept prioritizes privacy, speed, and simple deployment.
- The vision model is a managed external dependency, so extraction quality and availability can vary with provider behavior.
- CORS is not configured because the frontend and API are served from the same FastAPI origin.

## Limitations

- This is a proof of concept, not an official TTB determination or a replacement for human review.
- Vision extraction can vary with glare, blur, curvature, small text, occlusion, or unusual typography.
- There are no accounts, database, saved results, audit trail, or background jobs.
- Batch size is limited to 10 labels; the under-five-second requirement applies to single-label verification.
- Availability and latency depend on the external model API and Railway free-tier behavior, including possible cold starts.
- Although the application does not intentionally persist uploads, external hosting and model providers have their own operational and retention policies.

## Security and Privacy

`.env` and other local environment files are ignored by Git; only `.env.example` with placeholder values is committed. Secrets must remain in local or Railway environment variables. A tracked-file secret grep audit was run with no findings. Application logs record timing and verdict metadata without recording uploaded images, filenames, extracted label text, submitted label text, or API keys.
