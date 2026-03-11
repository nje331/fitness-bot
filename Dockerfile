FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install OS deps for matplotlib/Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Directories for persistent data and logs
RUN mkdir -p /data /logs

# Default: production mode
CMD ["python", "bot.py"]
