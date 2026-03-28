FROM python:3.12-slim

WORKDIR /app

# System deps for pydub, playwright, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/state /app/static/icons

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
