FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "routellm.openai_server", "--verbose", "--routers", "mf", "--strong-model", "gpt-4o", "--weak-model", "gpt-4o-mini", "--config", "config.yaml"]