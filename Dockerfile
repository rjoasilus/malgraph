# Sprint 0: minimal reproducible environment for MalGraph.
# Not a production image — Sprint 6 will replace this with a multi-stage
# build for the FastAPI service.

FROM python:3.10-slim

# System deps: keep minimal. Add here as sprints need them.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so pip-install layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project. Note: data/raw is excluded via .dockerignore,
# since reports are multi-GB and belong on the host, not in the image.
COPY . .

# Default command: smoke-test import of the core stack.
# Override at runtime for real work (e.g. `python ingest/load.py <path>`).
CMD ["python", "-c", "import pandas, networkx, pyarrow, pytest; print('malgraph env ok')"]
