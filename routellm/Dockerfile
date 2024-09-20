FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "routellm.openai_server", "--routers", "mf", "--strong-model", "gpt-4o", "--weak-model", "gpt-4o-mini", "--config", "config.yaml"]