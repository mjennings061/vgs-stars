# vgs-stars

STARS Authorisation Expiry Notification Service - A FastAPI backend to notify users of upcoming expirations in their STARS engineering authorisations.

## Overview

This service provides a REST API that checks for expiring STARS authorisations and sends email notifications. It's designed to scale to zero when idle and be triggered by external schedulers (e.g., Google Cloud Scheduler, cron).

## Development Setup

1. Install Poetry and project dependencies:

   ```bash
   python -m pip install poetry
   python -m poetry install
   ```

2. Install pre-commit hooks:

   ```bash
   poetry run pre-commit install
   ```

3. Configure your environment variables by creating a `.env` file in the project root. Refer to `.env.example` for the required variables.

    ```bash
    cp .env.example .env
    ```

   Edit `.env` and add your credentials:
   - STARS API credentials (URI and API key)
   - MongoDB connection URI
   - SendGrid API key and sender email
   - Organisation unit ID and resource ID defaults

## Running the Application

### Development Server

Start the FastAPI development server with hot reload:

```bash
poetry run python run.py
```

The API will be available at `http://localhost:8000`

### API Documentation

Once running, visit `http://localhost:8000/docs` for interactive Swagger documentation.

## API Endpoints

### Health

- `GET /health` - Basic liveness check (returns status and timestamp)
- `GET /health/ready` - Readiness check (verifies MongoDB and STARS API connectivity)

### Authorisations

- `POST /auths/notify-auth-expiry` - Check for expiring auths and send email notifications
  - Optional body: `{"unit_id": "string", "warning_days": int}`
  - Returns counts of sent/failed notifications and summary
- `POST /auths/notify-auth-expiry/user` - Send expiry notification for a single user
  - Body: `{"resource_id": "R:XXXXX", "unit_id": "string", "warning_days": int}`
  - Returns counts of sent/failed notifications and summary
  - Does not deduplicate or persist notifications; intended for ad-hoc sends
- `GET /auths/expiring` - List expiring auths without sending notifications (for debugging)
  - Query params: `unit_id` (optional), `warning_days` (optional)
  - Returns list of expiring authorisations
- `POST /auths/test-email` - Send a test notification email (for debugging)
  - Body: `{"email": "test@example.com", "resource_id": "string"}`

## Testing

Run the functional tests:

```bash
poetry run pytest tests/test_functional.py -v
```

## Usage Example

Trigger a notification check:

```bash
curl -X POST http://localhost:8000/auths/notify-auth-expiry \
  -H "Content-Type: application/json" \
  -d '{"unit_id": "206749", "warning_days": 30}'
```

Trigger a single-user notification check:

```bash
curl -X POST http://localhost:8000/auths/notify-auth-expiry/user \
  -H "Content-Type: application/json" \
  -d '{"resource_id": "R:XXXXX", "warning_days": 30}'
```

## External Scheduling

The API is designed to be triggered externally. Example schedulers:

- **Google Cloud Scheduler** - HTTP POST to `/auths/notify-auth-expiry` daily
- **Azure Logic Apps** - Recurrence trigger calling the API
- **AWS EventBridge + Lambda** - Scheduled Lambda invokes the API
- **Cron job** - `curl` command in crontab

## Managing Users

All endpoints except `/health` require an API key stored in MongoDB. Keys live in the `users` collection with fields `name` and `api_key` (hashed). To create one:

1) Ensure your `.env` has Mongo settings loaded, then install deps: `poetry install`.
2) Run the helper and follow the prompt:

   ```bash
   poetry run python scripts/users.py --name "661VGS"
   ```

3) Copy the printed API key once; the database stores only the SHA-256 hash in `api_key`.
4) Call the API with the header shown (defaults to `X-API-Key`):

   ```bash
   curl -H "X-API-Key: <your key>" http://localhost:8000/auths/expiring
   ```

To revoke, remove the user document from `users` or replace the stored hash.
