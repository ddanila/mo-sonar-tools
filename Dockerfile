FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openscad xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Explicit code-only copy: never copy user disk images or generated artifacts.
COPY convert.py relief.py cli.py ./
COPY tests/ ./tests/
ENV PYTHONUNBUFFERED=1 QT_QPA_PLATFORM=offscreen
ENTRYPOINT ["python", "/app/cli.py"]
CMD ["--help"]
