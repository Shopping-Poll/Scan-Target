FROM python:3.11-slim

WORKDIR /app

# Install only essential system dependencies (remove sqlite since using Supabase)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 7860

# Use User ID 1000 for HuggingFace compatibility
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:${PATH}"

WORKDIR /app
COPY --chown=user . .

# Render provides the PORT environment variable automatically
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-7860} bot:app"]
