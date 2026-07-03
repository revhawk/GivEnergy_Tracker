FROM python:3.11-slim

WORKDIR /app

# Install build dependencies and jq
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files — config.py is NOT included here (it contains secrets
# and is gitignored). run.sh resolves it at container start from /data/config.py
# or /share/givenergy_config.py.
COPY run.sh tracker.py optimiser.py config.py.example /app/
RUN chmod a+x run.sh

CMD [ "./run.sh" ]
