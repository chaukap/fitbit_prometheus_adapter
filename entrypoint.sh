#!/bin/bash
set -e

# Default values
METRICS_PORT=${METRICS_PORT:-8080}
METRICS_PATH=${METRICS_PATH:-/metrics}
EXPORT_INTERVAL=${EXPORT_INTERVAL:-300}
LOG_LEVEL=${LOG_LEVEL:-INFO}

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Validate required environment variables
validate_env() {
    if [[ -z "$FITBIT_CLIENT_ID" || -z "$FITBIT_CLIENT_SECRET" ]]; then
        log "ERROR: FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET must be set"
        exit 1
    fi
    
    if [[ -z "$FITBIT_ACCESS_TOKEN" ]]; then
        log "WARNING: FITBIT_ACCESS_TOKEN not set. You'll need to authenticate manually."
    fi
}

# Run one-time export
run_export() {
    log "Running one-time export..."
    python fitbit_prometheus.py "$@"
}

# Run HTTP server mode
run_server() {
    log "Starting Fitbit Prometheus Exporter HTTP server on port $METRICS_PORT"
    log "Metrics will be available at http://localhost:$METRICS_PORT$METRICS_PATH"
    log "Export interval: $EXPORT_INTERVAL seconds"
    
    # Start the HTTP server
    python fitbit_http_server.py
}

# Run as push mode (for AWS/cloud deployments)
run_push() {
    log "Starting push mode to Prometheus endpoint"
    log "Push interval: $EXPORT_INTERVAL seconds"
    
    if [[ "${1:-once}" == "continuous" ]]; then
        python prometheus_pusher.py --continuous --interval $EXPORT_INTERVAL
    else
        python prometheus_pusher.py --once
    fi
}

# Show help
show_help() {
    cat << EOF
Fitbit Prometheus Exporter

Usage: $0 [COMMAND] [OPTIONS]

Commands:
    server      Start HTTP server for Prometheus scraping
    push        Push metrics to Prometheus endpoint (for AWS/cloud)
    export      Run one-time export to stdout
    auth        Run OAuth authentication flow
    help        Show this help

Environment Variables:
    FITBIT_CLIENT_ID        Fitbit app client ID (required)
    FITBIT_CLIENT_SECRET    Fitbit app client secret (required)
    FITBIT_ACCESS_TOKEN     OAuth access token (optional)
    FITBIT_REFRESH_TOKEN    OAuth refresh token (optional)
    FITBIT_REDIRECT_URI     OAuth redirect URI (default: http://localhost:8080/callback)
    METRICS_PORT            HTTP server port (default: 8080)
    METRICS_PATH            Metrics endpoint path (default: /metrics)
    EXPORT_INTERVAL         Export interval in seconds (default: 300)
    LOG_LEVEL               Log level (default: INFO)

Examples:
    # Start HTTP server
    docker run -e FITBIT_CLIENT_ID=xxx -e FITBIT_CLIENT_SECRET=yyy fitbit-exporter

    # Run one-time export
    docker run -e FITBIT_CLIENT_ID=xxx -e FITBIT_CLIENT_SECRET=yyy fitbit-exporter export

    # Run with custom date
    docker run -e FITBIT_CLIENT_ID=xxx -e FITBIT_CLIENT_SECRET=yyy fitbit-exporter export --date 2025-01-15
EOF
}

# Main execution
case "${1:-server}" in
    server)
        validate_env
        shift
        run_server "$@"
        ;;
    push)
        validate_env
        shift
        run_push "$@"
        ;;
    export)
        validate_env
        shift
        run_export "$@"
        ;;
    cron)
        validate_env
        shift
        run_push continuous
        ;;
    auth)
        validate_env
        log "Starting OAuth authentication flow..."
        python fitbit_prometheus.py
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log "ERROR: Unknown command: $1"
        show_help
        exit 1
        ;;
esac