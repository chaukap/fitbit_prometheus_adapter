#!/usr/bin/env python3
"""
Fitbit Prometheus Push Gateway Integration
Pushes Fitbit metrics to Prometheus Push Gateway or Remote Write endpoint
"""

import os
import sys
import time
import requests
import logging
from datetime import datetime
from urllib.parse import urljoin
import base64
import gzip
import json

# Import our Fitbit API classes
from fitbit_prometheus import FitbitAPI, PrometheusMetricsExporter

# Set up logging
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PrometheusPusher:
    """Push metrics to Prometheus Push Gateway or Remote Write endpoint"""
    
    def __init__(self):
        self.push_gateway_url = os.getenv('PROMETHEUS_PUSH_GATEWAY_URL')
        self.remote_write_url = os.getenv('PROMETHEUS_REMOTE_WRITE_URL')
        self.job_name = os.getenv('PROMETHEUS_JOB_NAME', 'fitbit-exporter')
        self.instance = os.getenv('PROMETHEUS_INSTANCE', 'fitbit-exporter-aws')
        
        # Authentication
        self.username = os.getenv('PROMETHEUS_USERNAME')
        self.password = os.getenv('PROMETHEUS_PASSWORD')
        self.bearer_token = os.getenv('PROMETHEUS_BEARER_TOKEN')
        
        # AWS specific
        self.aws_region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self.aws_workspace_id = os.getenv('AWS_PROMETHEUS_WORKSPACE_ID')
        
        if not (self.push_gateway_url or self.remote_write_url or self.aws_workspace_id):
            raise ValueError("Must specify PROMETHEUS_PUSH_GATEWAY_URL, PROMETHEUS_REMOTE_WRITE_URL, or AWS_PROMETHEUS_WORKSPACE_ID")
    
    def _get_auth_headers(self):
        """Get authentication headers"""
        headers = {'Content-Type': 'text/plain; charset=utf-8'}
        
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        elif self.username and self.password:
            credentials = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
            headers['Authorization'] = f'Basic {credentials}'
        
        return headers
    
    def _get_aws_auth_headers(self):
        """Get AWS SigV4 authentication headers for Prometheus"""
        try:
            import boto3
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest
            
            session = boto3.Session()
            credentials = session.get_credentials()
            
            headers = {
                'Content-Type': 'application/x-protobuf',
                'Content-Encoding': 'snappy',
                'X-Prometheus-Remote-Write-Version': '0.1.0'
            }
            
            # Create AWS request for signing
            request = AWSRequest(
                method='POST',
                url=self.remote_write_url,
                headers=headers
            )
            
            # Sign the request
            SigV4Auth(credentials, 'aps', self.aws_region).add_auth(request)
            
            return dict(request.headers)
        except ImportError:
            logger.error("boto3 is required for AWS authentication. Install with: pip install boto3")
            raise
        except Exception as e:
            logger.error(f"Failed to get AWS authentication: {e}")
            raise
    
    def push_to_gateway(self, metrics_text):
        """Push metrics to Prometheus Push Gateway"""
        if not self.push_gateway_url:
            return False
        
        try:
            url = urljoin(self.push_gateway_url, f'/metrics/job/{self.job_name}/instance/{self.instance}')
            headers = self._get_auth_headers()
            
            logger.info(f"Pushing metrics to Push Gateway: {url}")
            response = requests.post(url, data=metrics_text, headers=headers, timeout=30)
            
            if response.status_code == 200:
                logger.info("Successfully pushed metrics to Push Gateway")
                return True
            else:
                logger.error(f"Failed to push to Push Gateway: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error pushing to Push Gateway: {e}")
            return False
    
    def push_to_remote_write(self, metrics_text):
        """Push metrics to Prometheus Remote Write endpoint"""
        if not self.remote_write_url:
            return False
        
        try:
            # Convert Prometheus text format to remote write format
            samples = self._parse_metrics_to_samples(metrics_text)
            if not samples:
                logger.warning("No valid samples to push")
                return False
            
            # Create remote write request
            write_request = self._create_remote_write_request(samples)
            
            # Get appropriate headers
            if self.aws_workspace_id:
                headers = self._get_aws_auth_headers()
            else:
                headers = self._get_auth_headers()
                headers['Content-Type'] = 'application/x-protobuf'
                headers['Content-Encoding'] = 'snappy'
            
            logger.info(f"Pushing metrics to Remote Write endpoint: {self.remote_write_url}")
            response = requests.post(
                self.remote_write_url, 
                data=write_request, 
                headers=headers, 
                timeout=30
            )
            
            if response.status_code in [200, 204]:
                logger.info("Successfully pushed metrics to Remote Write endpoint")
                return True
            else:
                logger.error(f"Failed to push to Remote Write: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error pushing to Remote Write: {e}")
            return False
    
    def _parse_metrics_to_samples(self, metrics_text):
        """Parse Prometheus text format to samples (simplified)"""
        samples = []
        timestamp = int(time.time() * 1000)  # Convert to milliseconds
        
        for line in metrics_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            try:
                # Simple parser for metric_name{labels} value timestamp
                if '{' in line:
                    # Has labels
                    metric_part, rest = line.split('{', 1)
                    labels_part, value_part = rest.rsplit('}', 1)
                    value_part = value_part.strip()
                    
                    # Parse labels
                    labels = {}
                    for label_pair in labels_part.split(','):
                        if '=' in label_pair:
                            key, val = label_pair.split('=', 1)
                            labels[key.strip()] = val.strip().strip('"')
                else:
                    # No labels
                    parts = line.split()
                    if len(parts) >= 2:
                        metric_part = parts[0]
                        value_part = parts[1]
                        labels = {}
                
                # Add instance and job labels
                labels['job'] = self.job_name
                labels['instance'] = self.instance
                
                # Parse value and timestamp
                value_parts = value_part.split()
                value = float(value_parts[0])
                sample_timestamp = int(value_parts[1]) if len(value_parts) > 1 else timestamp
                
                samples.append({
                    'metric_name': metric_part.strip(),
                    'labels': labels,
                    'value': value,
                    'timestamp': sample_timestamp
                })
                
            except Exception as e:
                logger.debug(f"Failed to parse line '{line}': {e}")
                continue
        
        return samples
    
    def _create_remote_write_request(self, samples):
        """Create remote write request (simplified JSON format for demonstration)"""
        # Note: In production, you'd want to use proper Protocol Buffers
        # This is a simplified approach that works with some endpoints
        
        time_series = {}
        
        # Group samples by metric and labels
        for sample in samples:
            # Create a key from metric name and labels
            label_pairs = sorted(sample['labels'].items())
            key = (sample['metric_name'], tuple(label_pairs))
            
            if key not in time_series:
                time_series[key] = {
                    'labels': [{'name': k, 'value': v} for k, v in label_pairs],
                    'samples': []
                }
            
            time_series[key]['samples'].append({
                'value': sample['value'],
                'timestamp': sample['timestamp']
            })
        
        # Convert to remote write format
        write_request = {
            'timeseries': [
                {
                    'labels': ts_data['labels'],
                    'samples': ts_data['samples']
                }
                for ts_data in time_series.values()
            ]
        }
        
        # For this example, we'll send as JSON (some endpoints support this)
        # In production, use proper protobuf + snappy compression
        return json.dumps(write_request).encode('utf-8')
    
    def push_metrics(self, metrics_text):
        """Push metrics to configured endpoint(s)"""
        success = False
        
        if self.push_gateway_url:
            success |= self.push_to_gateway(metrics_text)
        
        if self.remote_write_url or self.aws_workspace_id:
            success |= self.push_to_remote_write(metrics_text)
        
        return success

class FitbitMetricsPusher:
    """Main class to collect and push Fitbit metrics"""
    
    def __init__(self):
        self.fitbit = FitbitAPI()
        self.pusher = PrometheusPusher()
        
        if not self.fitbit.access_token:
            raise ValueError("No access token available. Set FITBIT_ACCESS_TOKEN environment variable.")
    
    def collect_and_push_metrics(self, date=None):
        """Collect metrics from Fitbit and push to Prometheus"""
        try:
            logger.info("Collecting metrics from Fitbit API...")
            
            # Create exporter and gather metrics
            exporter = PrometheusMetricsExporter(self.fitbit)
            
            # Export all available data
            exporter.export_user_profile()
            exporter.export_daily_activity(date)
            exporter.export_heart_rate(date)
            exporter.export_sleep_data(date)
            exporter.export_weight_data(date)
            
            # Export time series for common metrics
            for resource in ['steps', 'distance', 'calories']:
                try:
                    exporter.export_time_series(resource, days=7)
                except Exception as e:
                    logger.warning(f"Failed to export {resource} time series: {e}")
            
            # Get metrics text
            metrics_text = exporter.get_metrics()
            
            if not metrics_text.strip():
                logger.warning("No metrics collected")
                return False
            
            logger.info(f"Collected {len(metrics_text.split('\\n'))} metric lines")
            
            # Push to Prometheus
            success = self.pusher.push_metrics(metrics_text)
            
            if success:
                logger.info("Successfully pushed metrics to Prometheus")
            else:
                logger.error("Failed to push metrics to Prometheus")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to collect and push metrics: {e}")
            return False
    
    def run_continuous(self, interval=300):
        """Run continuous metric collection and pushing"""
        logger.info(f"Starting continuous metric pushing every {interval} seconds")
        
        while True:
            try:
                self.collect_and_push_metrics()
                logger.info(f"Next push in {interval} seconds")
                time.sleep(interval)
            except KeyboardInterrupt:
                logger.info("Stopping continuous execution")
                break
            except Exception as e:
                logger.error(f"Error in continuous run: {e}")
                time.sleep(min(interval, 60))  # Wait at least 1 minute on error

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Push Fitbit metrics to Prometheus')
    parser.add_argument('--date', help='Date to export (YYYY-MM-DD format), defaults to today')
    parser.add_argument('--interval', type=int, default=300, help='Push interval in seconds (default: 300)')
    parser.add_argument('--continuous', action='store_true', help='Run continuously')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    
    args = parser.parse_args()
    
    try:
        pusher = FitbitMetricsPusher()
        
        if args.continuous:
            pusher.run_continuous(args.interval)
        elif args.once:
            success = pusher.collect_and_push_metrics(args.date)
            sys.exit(0 if success else 1)
        else:
            # Default: run once
            success = pusher.collect_and_push_metrics(args.date)
            sys.exit(0 if success else 1)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        print(f"\\nMake sure you have set the required environment variables:")
        print(f"Fitbit: FITBIT_CLIENT_ID, FITBIT_CLIENT_SECRET, FITBIT_ACCESS_TOKEN")
        print(f"Prometheus: PROMETHEUS_PUSH_GATEWAY_URL or PROMETHEUS_REMOTE_WRITE_URL or AWS_PROMETHEUS_WORKSPACE_ID")
        sys.exit(1)

if __name__ == "__main__":
    main()
