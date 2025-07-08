#!/usr/bin/env python3
"""
Fitbit Prometheus HTTP Server
Serves Fitbit metrics via HTTP endpoint for Prometheus scraping
"""

import os
import sys
import time
import threading
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import json

# Import our Fitbit API classes
from fitbit_prometheus import FitbitAPI, PrometheusMetricsExporter

# Set up logging
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MetricsCache:
    """Thread-safe cache for metrics data"""
    
    def __init__(self):
        self._metrics = ""
        self._last_update = 0
        self._lock = threading.Lock()
        self._error_count = 0
        self._last_error = None
    
    def update(self, metrics, error=None):
        """Update cached metrics"""
        with self._lock:
            if error:
                self._error_count += 1
                self._last_error = str(error)
                logger.error(f"Failed to update metrics: {error}")
            else:
                self._metrics = metrics
                self._last_update = time.time()
                self._error_count = 0
                self._last_error = None
                logger.info("Metrics updated successfully")
    
    def get(self):
        """Get cached metrics"""
        with self._lock:
            return self._metrics, self._last_update, self._error_count, self._last_error

class FitbitMetricsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for metrics endpoint"""
    
    def __init__(self, cache, metrics_path, *args, **kwargs):
        self.cache = cache
        self.metrics_path = metrics_path
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == self.metrics_path:
            self.serve_metrics()
        elif parsed_path.path == '/health':
            self.serve_health()
        elif parsed_path.path == '/':
            self.serve_index()
        else:
            self.send_error(404, "Not Found")
    
    def serve_metrics(self):
        """Serve Prometheus metrics"""
        metrics, last_update, error_count, last_error = self.cache.get()
        
        if not metrics and error_count > 0:
            self.send_error(503, f"Service Unavailable: {last_error}")
            return
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        
        # Add metadata about the exporter
        metadata = f"""# HELP fitbit_exporter_info Information about the Fitbit exporter
# TYPE fitbit_exporter_info gauge
fitbit_exporter_info{{version="1.0.0",last_update="{datetime.fromtimestamp(last_update).isoformat()}"}} 1

# HELP fitbit_exporter_last_update_timestamp Unix timestamp of last successful update
# TYPE fitbit_exporter_last_update_timestamp gauge
fitbit_exporter_last_update_timestamp {int(last_update * 1000)}

# HELP fitbit_exporter_errors_total Total number of export errors
# TYPE fitbit_exporter_errors_total counter
fitbit_exporter_errors_total {error_count}

"""
        
        self.wfile.write((metadata + metrics).encode('utf-8'))
    
    def serve_health(self):
        """Serve health check endpoint"""
        metrics, last_update, error_count, last_error = self.cache.get()
        
        # Consider healthy if we have recent data (within last 10 minutes)
        is_healthy = (time.time() - last_update) < 600
        
        status_code = 200 if is_healthy else 503
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        health_data = {
            'status': 'healthy' if is_healthy else 'unhealthy',
            'last_update': datetime.fromtimestamp(last_update).isoformat() if last_update else None,
            'error_count': error_count,
            'last_error': last_error
        }
        
        self.wfile.write(json.dumps(health_data, indent=2).encode('utf-8'))
    
    def serve_index(self):
        """Serve index page with links"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Fitbit Prometheus Exporter</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .status {{ padding: 10px; border-radius: 5px; margin: 10px 0; }}
        .healthy {{ background-color: #d4edda; border: 1px solid #c3e6cb; }}
        .unhealthy {{ background-color: #f8d7da; border: 1px solid #f5c6cb; }}
    </style>
</head>
<body>
    <h1>Fitbit Prometheus Exporter</h1>
    <div class="status healthy">
        <strong>Status:</strong> Running
    </div>
    
    <h2>Endpoints</h2>
    <ul>
        <li><a href="{self.metrics_path}">Metrics</a> - Prometheus metrics endpoint</li>
        <li><a href="/health">Health</a> - Health check endpoint</li>
    </ul>
    
    <h2>Environment Configuration</h2>
    <ul>
        <li><strong>Metrics Path:</strong> {self.metrics_path}</li>
        <li><strong>Export Interval:</strong> {os.getenv('EXPORT_INTERVAL', '300')} seconds</li>
        <li><strong>Client ID:</strong> {"Set" if os.getenv('FITBIT_CLIENT_ID') else "Not Set"}</li>
        <li><strong>Access Token:</strong> {"Set" if os.getenv('FITBIT_ACCESS_TOKEN') else "Not Set"}</li>
    </ul>
</body>
</html>
"""
        self.wfile.write(html.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.client_address[0]} - {format % args}")

class MetricsUpdater:
    """Background thread to update metrics periodically"""
    
    def __init__(self, cache, interval):
        self.cache = cache
        self.interval = interval
        self.fitbit = None
        self.running = False
        self.thread = None
    
    def start(self):
        """Start the background updater"""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"Started metrics updater with {self.interval}s interval")
    
    def stop(self):
        """Stop the background updater"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _run(self):
        """Main updater loop"""
        # Initialize Fitbit API
        try:
            self.fitbit = FitbitAPI()
            if not self.fitbit.access_token:
                raise ValueError("No access token available. Set FITBIT_ACCESS_TOKEN environment variable.")
        except Exception as e:
            logger.error(f"Failed to initialize Fitbit API: {e}")
            self.cache.update("", error=e)
            return
        
        # Initial update
        self._update_metrics()
        
        # Periodic updates
        while self.running:
            time.sleep(self.interval)
            if self.running:
                self._update_metrics()
    
    def _update_metrics(self):
        """Update metrics from Fitbit API"""
        try:
            logger.info("Updating metrics from Fitbit API...")
            
            # Create exporter and gather metrics
            exporter = PrometheusMetricsExporter(self.fitbit)
            
            # Export all available data
            exporter.export_user_profile()
            exporter.export_daily_activity()
            exporter.export_heart_rate()
            exporter.export_sleep_data()
            # exporter.export_weight_data() Uncomment for weight data
            
            # Export time series for common metrics
            for resource in ['steps', 'distance', 'calories']:
                try:
                    exporter.export_time_series(resource, days=7)
                except Exception as e:
                    logger.warning(f"Failed to export {resource} time series: {e}")
            
            metrics = exporter.get_metrics()
            self.cache.update(metrics)
            
        except Exception as e:
            logger.error(f"Failed to update metrics: {e}")
            self.cache.update("", error=e)

def create_handler_class(cache, metrics_path):
    """Create a handler class with injected dependencies"""
    def handler(*args, **kwargs):
        return FitbitMetricsHandler(cache, metrics_path, *args, **kwargs)
    return handler

def start_server(port=8080, metrics_path='/metrics', interval=300):
    """Start the HTTP server"""
    port = int(port)
    interval = int(interval)
    
    logger.info(f"Starting Fitbit Prometheus Exporter on port {port}")
    logger.info(f"Metrics endpoint: http://localhost:{port}{metrics_path}")
    logger.info(f"Health endpoint: http://localhost:{port}/health")
    
    # Create metrics cache
    cache = MetricsCache()
    
    # Start metrics updater
    updater = MetricsUpdater(cache, interval)
    updater.start()
    
    # Create and start HTTP server
    handler_class = create_handler_class(cache, metrics_path)
    server = HTTPServer(('0.0.0.0', port), handler_class)
    
    try:
        logger.info("Server started successfully")
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        updater.stop()
        server.shutdown()
        server.server_close()

if __name__ == "__main__":
    start_server(
        port=os.getenv('METRICS_PORT', 8080),
        metrics_path=os.getenv('METRICS_PATH', '/metrics'),
        interval=int(os.getenv('EXPORT_INTERVAL', 300))
    )