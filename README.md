# vgs-stars

STARS Currency Tracker - A tool to notify users of upcoming expirations in their STARS currencies.

## Development Setup

1. Create and activate a virtual environment:

   ```bash
   python -m pip install poetry
   python -m poetry install
   ```

1. Install pre-commit hooks:

   ```bash
   poetry run pre-commit install
   ```

1. Configure your environment variables by creating a `.env` file in the project root. Refer to `.env.example` for the required variables.

    ```bash
    cp .env.example .env
    ```
