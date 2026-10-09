FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 lab && useradd --uid 10001 --gid lab --no-create-home lab \
    && mkdir /state && chown lab:lab /state
WORKDIR /app
COPY adapter.py lab_tool.py ./
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1
VOLUME /state
CMD ["python", "adapter.py", "--serve"]
