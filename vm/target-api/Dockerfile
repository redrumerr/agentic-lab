FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
# Build with uv as well; no pip installation path.
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
RUN uv pip install --system --no-cache -r requirements.txt
COPY app.py .
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "app:app"]
