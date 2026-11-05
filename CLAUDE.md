# CLAUDE.md

Prefer simplicity and brevity over complexity and verbosity.

Commands (from repo root):

- Always use python venv: `source .venv/bin/activate`
- Add a new dependency: `uv add <package_name>`
- Lock dependencies to latest versions: `uv lock --upgrade`
- Install dependencies: `uv sync --all-packages`
- Format code: `ruff format .`
- Lint code and auto fix problems: `ruff check --fix .`
