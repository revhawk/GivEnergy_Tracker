FROM python:3.11-slim

WORKDIR /app

# Install build dependencies and jq
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (to be populated from workspace)
COPY run.sh config.py tracker.py optimiser.py /app/
RUN chmod a+x run.sh

CMD [ "./run.sh" ]
