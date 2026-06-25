# ABOUTME: Container image for the localizer (camera/imu/mag derived topics -> edge.position).

FROM python:3.12-slim

# opencv-python-headless needs these shared libs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py roi.json ./

CMD ["python", "-u", "main.py"]
