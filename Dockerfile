FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY ripple ./ripple
COPY db ./db
COPY evals ./evals
RUN pip install --no-cache-dir -e ".[dev]"

ENV REPOS_DIR=/repos
VOLUME ["/repos"]
EXPOSE 8000
CMD ["ripple", "serve"]
