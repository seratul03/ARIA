# Dockerfile
# ARIA Runtime Container
FROM python:3.11-slim

# Install system dependencies (git is needed for versioning)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure required directories exist
RUN mkdir -p data workspace

CMD ["python", "-m", "aria", "run"]
