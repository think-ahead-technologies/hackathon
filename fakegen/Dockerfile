# ABOUTME: Container image for the dev-only fake telemetry publisher.
# ABOUTME: Used in Phase 1 to prove the loop (and the OTel path) with no board attached.

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fake.py .

CMD ["python", "-u", "fake.py"]
