# ABOUTME: Container image for the bridge service.
# ABOUTME: Subscribes NATS, persists to Timescale, serves /label + /metrics.

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY test_bridge.py .

EXPOSE 8000
CMD ["python", "-u", "main.py"]
