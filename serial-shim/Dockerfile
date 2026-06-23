# ABOUTME: Container image for the serial→NATS shim (device→fabric transport).
# ABOUTME: Linux edge nodes can pass the board's serial device through; on macOS run it on the host.

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shim.py .

CMD ["python", "-u", "shim.py"]
