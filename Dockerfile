FROM python:3.11-slim

WORKDIR /app

RUN echo "Debug: Starting build process for RouteLLM server"

# Install build dependencies and wget for Cloud SQL proxy
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Cloud SQL Auth proxy
RUN wget https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64 -O /cloud_sql_proxy \
    && chmod +x /cloud_sql_proxy

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN echo "Debug: Build complete. Preparing to start RouteLLM server"

# Create a start script that handles both dev and prod environments
RUN echo '#!/bin/bash' > start.sh && \
    echo 'echo "Debug: Starting RouteLLM server in container"' >> start.sh && \
    echo 'if [ "$ENVIRONMENT" = "prod" ]; then' >> start.sh && \
    echo '  echo "Starting Cloud SQL proxy in background"' >> start.sh && \
    echo '  /cloud_sql_proxy --structured-logs "$INSTANCE_CONNECTION_NAME" &' >> start.sh && \
    echo '  sleep 5  # Wait for proxy to start' >> start.sh && \
    echo 'fi' >> start.sh && \
    echo 'exec python -m routellm.openai_server --verbose --routers mf --config config.yaml' >> start.sh && \
    chmod +x start.sh

# Expose port for the application
EXPOSE ${PORT:-8080}

CMD ["./start.sh"]