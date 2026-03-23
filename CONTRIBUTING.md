# Contributing to Echo Guard

Thanks for your interest in contributing! Here's how to get started.

## Development setup

```bash
git clone https://github.com/jwizenfeld04/Echo-Guard.git
cd Echo-Guard
python -m venv .venv
source .venv/bin/activate
pip install -e ".[languages,dev]"
```

## Running tests

```bash
pytest
```

## Making changes

1. Fork the repo and create a branch from `main`.
2. Make your changes.
3. Add or update tests if applicable.
4. Run `pytest` and make sure all tests pass.
5. Open a pull request.

## Code style

- Use type hints for function signatures.
- Keep functions focused and small.
- Follow existing patterns in the codebase.

## Reporting bugs

Open an issue at https://github.com/jwizenfeld04/Echo-Guard/issues with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS

## Feature requests

Open an issue describing the use case and why it would be valuable. Discussion before implementation is encouraged for larger changes.
