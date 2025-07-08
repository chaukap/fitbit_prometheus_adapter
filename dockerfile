# Multi-stage build for smaller final image
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /app

# Install system dependencies needed for building
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

# Create non-root user for security
RUN groupadd -r fitbit && useradd -r -g fitbit fitbit

# Set working directory
WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder stage
COPY --from=builder /root/.local /home/fitbit/.local

# Copy application code
COPY fitbit_prometheus.py .
COPY fitbit_http_server.py .
COPY prometheus_pusher.py .
COPY entrypoint.sh .

# Make scripts executable
RUN chmod +x entrypoint.sh

# Create directories for data and config
RUN mkdir -p /app/data /app/config && \
    chown -R fitbit:fitbit /app

# Switch to non-root user
USER fitbit

# Add local Python packages to PATH
ENV PATH=/home/fitbit/.local/bin:$PATH

# Environment variables with defaults
ENV FITBIT_CLIENT_ID=""
ENV FITBIT_CLIENT_SECRET=""
ENV FITBIT_REDIRECT_URI="http://localhost:8080/callback"
ENV FITBIT_ACCESS_TOKEN=""
ENV FITBIT_REFRESH_TOKEN=""
ENV METRICS_PORT=8080
ENV METRICS_PATH="/metrics"
ENV EXPORT_INTERVAL=300
ENV LOG_LEVEL="INFO"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${METRICS_PORT}${METRICS_PATH} || exit 1

# Expose metrics port
EXPOSE ${METRICS_PORT}

# Default command
ENTRYPOINT ["./entrypoint.sh"]
CMD ["server"]