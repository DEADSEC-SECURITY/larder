# Contributing to Larder

Thanks for taking the time to improve Larder.

Before starting a large change, open an issue and describe the problem you want to solve. Small bug fixes and documentation corrections can go straight to a pull request.

## Development setup

Python 3.12, Docker, Node.js, and pnpm are used by the project. The quickest way to bring up the full application is:

```bash
cp .env.example .env
docker compose up --build -d
```

For backend work, install the test dependencies in a virtual environment and run the unit suite:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest tests/unit
```

For frontend work, install from the repository root:

```bash
pnpm install
pnpm --filter web test
pnpm --filter web run build
```

Integration tests require Docker because they start PostgreSQL with Testcontainers.

## Pull requests

Keep each pull request focused on one change. Include tests when behavior changes, explain any migration or configuration impact, and update the documentation when an operator would need to do something differently.

Do not include credentials, private hostnames, personal data, database dumps, or environment files. Use the example domains and development values already present in the repository.

By submitting a contribution, you agree that it may be distributed under the PolyForm Noncommercial License 1.0.0.
