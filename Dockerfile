FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN groupadd -r bot && useradd -r -g bot bot

# Create directory for data (Railway will mount a volume here)
RUN mkdir -p /app/data && chown -R bot:bot /app/data /app

# Copy application code
COPY . .
RUN chown -R bot:bot /app

USER bot


# Health check (simplified for Railway)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os; exit(0 if os.path.exists('bot.py') else 1)"

CMD ["python", "bot.py"]

