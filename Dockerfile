FROM python:3.11-slim

WORKDIR /app

RUN echo "Debug: Starting build process for RouteLLM server"

# Install build dependencies and SQLite tools
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN echo "Debug: Build complete. Preparing to start RouteLLM server"

# Create a simple start script
RUN echo '#!/bin/bash' > start.sh && \
    echo 'echo "Debug: Starting RouteLLM server in container"' >> start.sh && \
    echo 'exec python -m routellm.openai_server --verbose --routers mf --strong-model "gemini/gemini-2.0-flash-001" --weak-model "gemini/gemini-2.0-flash-lite-preview-02-05" --config config.yaml' >> start.sh && \
    chmod +x start.sh

# Expose port 8080 for the application
EXPOSE 8080

CMD ["./start.sh"]