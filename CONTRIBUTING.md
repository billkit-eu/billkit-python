# Contributing

This repository is a **read-only mirror**. It is published from the BillKit monorepo, and
anything pushed here directly is overwritten by the next release.

Bug reports and feature requests are welcome as issues. For a code change, open an issue
first and we will apply it upstream with attribution.

## Running the tests

```bash
# whichever applies to this package
npm ci && npm test
uv sync && uv run pytest
composer install && composer test
```

The integration suite is gated on `BILLKIT_INTEGRATION_BASE_URL` and skips without it. It
drives a live BillKit API and runs upstream before each release; you are not missing a
step locally.
