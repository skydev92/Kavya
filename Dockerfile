FROM python:3.11-slim

WORKDIR /app

RUN echo "Debug: Starting build process for RouteLLM server"

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN echo "Debug: Build complete. Preparing to start RouteLLM server"

# Create a simple start script
RUN echo '#!/bin/bash' > start.sh && \
    echo 'echo "Debug: Starting RouteLLM server in container"' >> start.sh && \
    echo 'exec python -m routellm.openai_server --verbose --routers mf --strong-model gpt-4o --weak-model gpt-4o-mini --config config.yaml' >> start.sh && \
    chmod +x start.sh

CMD ["./start.sh"]