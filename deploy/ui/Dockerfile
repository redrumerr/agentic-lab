FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY scripts/serve_ui.py ./serve_ui.py
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1
CMD ["python", "serve_ui.py"]
