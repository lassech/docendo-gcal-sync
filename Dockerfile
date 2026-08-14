FROM python:3.12-slim

WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scripts
COPY sync.py pdf_sync.py ./

# Copy entrypoint (dumps env vars so cron can access them)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set up cron job at 06:30 (use full path since cron has minimal PATH)
RUN echo "30 6 * * * . /app/env.sh && cd /app && /usr/local/bin/python3 sync.py >> /app/sync.log 2>&1" | crontab -

# Run via entrypoint so env vars are available to cron
CMD ["/entrypoint.sh"]
