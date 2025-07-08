#!/usr/bin/env python3
"""
Fitbit API to Prometheus Metrics Exporter
Queries the Fitbit API and outputs Prometheus-compliant metrics format.
"""

import os
import requests
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time
import argparse

class FitbitAPI:
    def __init__(self):
        # Load credentials from environment variables
        self.client_id = os.getenv('FITBIT_CLIENT_ID')
        self.client_secret = os.getenv('FITBIT_CLIENT_SECRET')
        self.redirect_uri = os.getenv('FITBIT_REDIRECT_URI', 'http://localhost:8080/callback')
        self.access_token = os.getenv('FITBIT_ACCESS_TOKEN')
        self.refresh_token = os.getenv('FITBIT_REFRESH_TOKEN')
        
        # Fitbit API endpoints
        self.auth_url = 'https://www.fitbit.com/oauth2/authorize'
        self.token_url = 'https://api.fitbit.com/oauth2/token'
        self.api_base_url = 'https://api.fitbit.com/1/user/-'
        
        # Validate required environment variables
        if not self.client_id or not self.client_secret:
            raise ValueError("FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET must be set as environment variables")
    
    def get_authorization_url(self):
        """Generate the authorization URL for OAuth flow"""
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'activity heartrate location nutrition profile settings sleep social weight',
            'expires_in': '604800'  # 1 week
        }
        return f"{self.auth_url}?{urlencode(params)}"
    
    def exchange_code_for_token(self, authorization_code):
        """Exchange authorization code for access token"""
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {self._get_basic_auth_header()}'
        }
        
        data = {
            'clientId': self.client_id,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri,
            'code': authorization_code
        }
        
        response = requests.post(self.token_url, headers=headers, data=data)
        
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            
            print("# Tokens obtained successfully!")
            print(f"# Access Token: {self.access_token}")
            print(f"# Refresh Token: {self.refresh_token}")
            print("# Add these to your environment variables:")
            print(f"# export FITBIT_ACCESS_TOKEN='{self.access_token}'")
            print(f"# export FITBIT_REFRESH_TOKEN='{self.refresh_token}'")
            
            return token_data
        else:
            raise Exception(f"Failed to get tokens: {response.status_code} - {response.text}")
    
    def refresh_access_token(self):
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            raise ValueError("No refresh token available")
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {self._get_basic_auth_header()}'
        }
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token
        }
        
        response = requests.post(self.token_url, headers=headers, data=data)
        
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            return token_data
        else:
            raise Exception(f"Failed to refresh token: {response.status_code} - {response.text}")
    
    def _get_basic_auth_header(self):
        """Get basic auth header for client credentials"""
        import base64
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return encoded_credentials
    
    def _make_api_request(self, endpoint, params=None):
        """Make authenticated API request"""
        if not self.access_token:
            raise ValueError("No access token available. Please authenticate first.")
        
        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }
        
        url = f"{self.api_base_url}{endpoint}"
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 401:
            # Token expired, try to refresh
            print("# Access token expired, attempting to refresh...")
            self.refresh_access_token()
            headers['Authorization'] = f'Bearer {self.access_token}'
            response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")
    
    def get_user_profile(self):
        """Get user profile information"""
        return self._make_api_request('/profile.json')
    
    def get_daily_activity_summary(self, date=None):
        """Get daily activity summary for a specific date"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return self._make_api_request(f'/activities/date/{date}.json')
    
    def get_heart_rate(self, date=None):
        """Get heart rate data for a specific date"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return self._make_api_request(f'/activities/heart/date/{date}/1d.json')
    
    def get_sleep_data(self, date=None):
        """Get sleep data for a specific date"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return self._make_api_request(f'/sleep/date/{date}.json')
    
    def get_weight_logs(self, date=None):
        """Get weight logs for a specific date"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return self._make_api_request(f'/body/log/weight/date/{date}.json')
    
    def get_activity_time_series(self, resource, date=None, period='1m'):
        """Get activity time series data"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return self._make_api_request(f'/activities/{resource}/date/{date}/{period}.json')


class PrometheusMetricsExporter:
    """Export Fitbit data in Prometheus metrics format"""
    
    def __init__(self, fitbit_api):
        self.fitbit = fitbit_api
        self.metrics = []
        self.timestamp = int(time.time() * 1000)  # Prometheus timestamp in milliseconds
    
    def _add_metric(self, metric_name, value, labels=None, help_text=None, metric_type='gauge'):
        """Add a metric to the collection"""
        if help_text:
            self.metrics.append(f"# HELP {metric_name} {help_text}")
        if metric_type:
            self.metrics.append(f"# TYPE {metric_name} {metric_type}")
        
        if labels:
            label_str = ','.join([f'{k}="{v}"' for k, v in labels.items()])
            self.metrics.append(f"{metric_name}{{{label_str}}} {value} {self.timestamp}")
        else:
            self.metrics.append(f"{metric_name} {value} {self.timestamp}")
    
    def export_user_profile(self):
        """Export user profile metrics"""
        try:
            profile = self.fitbit.get_user_profile()
            user_info = profile['user']
            
            # Member since as Unix timestamp
            member_since = datetime.strptime(user_info['memberSince'], '%Y-%m-%d').timestamp()
            self._add_metric(
                'fitbit_user_member_since_timestamp',
                int(member_since),
                help_text='Unix timestamp when user joined Fitbit',
                metric_type='gauge'
            )
            
            # User info as labels
            user_labels = {
                'user_id': user_info['encodedId'],
                'full_name': user_info['fullName'],
                'timezone': user_info['timezone'],
                'country': user_info.get('country', 'unknown'),
                'gender': user_info.get('gender', 'unknown')
            }
            
            self._add_metric(
                'fitbit_user_info',
                1,
                labels=user_labels,
                help_text='User information (value is always 1)',
                metric_type='gauge'
            )
            
        except Exception as e:
            self._add_metric(
                'fitbit_user_profile_error',
                1,
                labels={'error': str(e)},
                help_text='Error fetching user profile',
                metric_type='counter'
            )
    
    def export_daily_activity(self, date=None):
        """Export daily activity metrics"""
        try:
            activity = self.fitbit.get_daily_activity_summary(date)
            summary = activity['summary']
            
            date_str = date or datetime.now().strftime('%Y-%m-%d')
            base_labels = {'date': date_str}
            
            # Steps
            self._add_metric(
                'fitbit_daily_steps_total',
                summary['steps'],
                labels=base_labels,
                help_text='Total steps taken for the day',
                metric_type='gauge'
            )
            
            # Distance (convert to meters for consistency)
            if summary['distances']:
                distance_miles = float(summary['distances'][0]['distance'])
                distance_meters = distance_miles * 1609.344
                self._add_metric(
                    'fitbit_daily_distance_meters',
                    round(distance_meters, 2),
                    labels=base_labels,
                    help_text='Total distance traveled in meters',
                    metric_type='gauge'
                )
            
            # Calories
            self._add_metric(
                'fitbit_daily_calories_out',
                summary['caloriesOut'],
                labels=base_labels,
                help_text='Total calories burned',
                metric_type='gauge'
            )
            
            # Active minutes
            self._add_metric(
                'fitbit_daily_lightly_active_minutes',
                summary['lightlyActiveMinutes'],
                labels=base_labels,
                help_text='Lightly active minutes',
                metric_type='gauge'
            )
            
            self._add_metric(
                'fitbit_daily_fairly_active_minutes',
                summary['fairlyActiveMinutes'],
                labels=base_labels,
                help_text='Fairly active minutes',
                metric_type='gauge'
            )
            
            self._add_metric(
                'fitbit_daily_very_active_minutes',
                summary['veryActiveMinutes'],
                labels=base_labels,
                help_text='Very active minutes',
                metric_type='gauge'
            )
            
            # Sedentary minutes
            self._add_metric(
                'fitbit_daily_sedentary_minutes',
                summary['sedentaryMinutes'],
                labels=base_labels,
                help_text='Sedentary minutes',
                metric_type='gauge'
            )
            
            # Floors climbed
            self._add_metric(
                'fitbit_daily_floors',
                summary.get('floors', 0),
                labels=base_labels,
                help_text='Floors climbed',
                metric_type='gauge'
            )
            
        except Exception as e:
            self._add_metric(
                'fitbit_daily_activity_error',
                1,
                labels={'error': str(e), 'date': date or 'today'},
                help_text='Error fetching daily activity',
                metric_type='counter'
            )
    
    def export_heart_rate(self, date=None):
        """Export heart rate metrics"""
        try:
            heart_rate = self.fitbit.get_heart_rate(date)
            
            if heart_rate['activities-heart']:
                hr_data = heart_rate['activities-heart'][0]['value']
                date_str = date or datetime.now().strftime('%Y-%m-%d')
                base_labels = {'date': date_str}
                
                # Resting heart rate
                if 'restingHeartRate' in hr_data:
                    self._add_metric(
                        'fitbit_resting_heart_rate_bpm',
                        hr_data['restingHeartRate'],
                        labels=base_labels,
                        help_text='Resting heart rate in beats per minute',
                        metric_type='gauge'
                    )
                
                # Heart rate zones
                if 'heartRateZones' in hr_data:
                    for zone in hr_data['heartRateZones']:
                        zone_labels = {**base_labels, 'zone': zone['name'].lower().replace(' ', '_')}
                        
                        self._add_metric(
                            'fitbit_heart_rate_zone_minutes',
                            zone['minutes'],
                            labels=zone_labels,
                            help_text='Minutes spent in heart rate zone',
                            metric_type='gauge'
                        )
                        
                        self._add_metric(
                            'fitbit_heart_rate_zone_calories',
                            zone['caloriesOut'],
                            labels=zone_labels,
                            help_text='Calories burned in heart rate zone',
                            metric_type='gauge'
                        )
                        
                        self._add_metric(
                            'fitbit_heart_rate_zone_min_bpm',
                            zone['min'],
                            labels=zone_labels,
                            help_text='Minimum heart rate for zone',
                            metric_type='gauge'
                        )
                        
                        self._add_metric(
                            'fitbit_heart_rate_zone_max_bpm',
                            zone['max'],
                            labels=zone_labels,
                            help_text='Maximum heart rate for zone',
                            metric_type='gauge'
                        )
                        
        except Exception as e:
            self._add_metric(
                'fitbit_heart_rate_error',
                1,
                labels={'error': str(e), 'date': date or 'today'},
                help_text='Error fetching heart rate data',
                metric_type='counter'
            )
    
    def export_sleep_data(self, date=None):
        """Export sleep metrics"""
        try:
            sleep = self.fitbit.get_sleep_data(date)
            
            if sleep['sleep']:
                sleep_data = sleep['sleep'][0]
                date_str = date or datetime.now().strftime('%Y-%m-%d')
                base_labels = {'date': date_str}
                
                # Sleep duration in minutes
                self._add_metric(
                    'fitbit_sleep_duration_minutes',
                    sleep_data['duration'] // 60000,  # Convert from milliseconds to minutes
                    labels=base_labels,
                    help_text='Total sleep duration in minutes',
                    metric_type='gauge'
                )
                
                # Sleep efficiency percentage
                self._add_metric(
                    'fitbit_sleep_efficiency_percent',
                    sleep_data['efficiency'],
                    labels=base_labels,
                    help_text='Sleep efficiency percentage',
                    metric_type='gauge'
                )
                
                # Time in bed
                self._add_metric(
                    'fitbit_sleep_time_in_bed_minutes',
                    sleep_data['timeInBed'],
                    labels=base_labels,
                    help_text='Time in bed in minutes',
                    metric_type='gauge'
                )
                
                # Sleep levels
                if 'levels' in sleep_data and 'summary' in sleep_data['levels']:
                    levels_summary = sleep_data['levels']['summary']
                    for level_name, level_data in levels_summary.items():
                        if isinstance(level_data, dict) and 'minutes' in level_data:
                            level_labels = {**base_labels, 'level': level_name}
                            self._add_metric(
                                'fitbit_sleep_level_minutes',
                                level_data['minutes'],
                                labels=level_labels,
                                help_text='Minutes spent in sleep level',
                                metric_type='gauge'
                            )
                            
                            if 'count' in level_data:
                                self._add_metric(
                                    'fitbit_sleep_level_count',
                                    level_data['count'],
                                    labels=level_labels,
                                    help_text='Number of times in sleep level',
                                    metric_type='gauge'
                                )
                        
        except Exception as e:
            self._add_metric(
                'fitbit_sleep_data_error',
                1,
                labels={'error': str(e), 'date': date or 'today'},
                help_text='Error fetching sleep data',
                metric_type='counter'
            )
    
    def export_weight_data(self, date=None):
        """Export weight metrics"""
        try:
            weight = self.fitbit.get_weight_logs(date)
            
            if weight['weight']:
                weight_data = weight['weight'][0]
                date_str = date or datetime.now().strftime('%Y-%m-%d')
                base_labels = {'date': date_str}
                
                # Weight in pounds and kilograms
                weight_lbs = weight_data['weight']
                weight_kg = weight_lbs * 0.453592  # Convert to kg
                
                self._add_metric(
                    'fitbit_weight_pounds',
                    weight_lbs,
                    labels=base_labels,
                    help_text='Weight in pounds',
                    metric_type='gauge'
                )
                
                self._add_metric(
                    'fitbit_weight_kg',
                    round(weight_kg, 2),
                    labels=base_labels,
                    help_text='Weight in kilograms',
                    metric_type='gauge'
                )
                
                # BMI
                self._add_metric(
                    'fitbit_bmi',
                    weight_data['bmi'],
                    labels=base_labels,
                    help_text='Body Mass Index',
                    metric_type='gauge'
                )
                
        except Exception as e:
            self._add_metric(
                'fitbit_weight_data_error',
                1,
                labels={'error': str(e), 'date': date or 'today'},
                help_text='Error fetching weight data',
                metric_type='counter'
            )
    
    def export_time_series(self, resource, days=7):
        """Export time series data"""
        try:
            end_date = datetime.now()
            
            for i in range(days):
                current_date = end_date - timedelta(days=i)
                date_str = current_date.strftime('%Y-%m-%d')
                
                # Get single day data for the resource
                time_series = self.fitbit.get_activity_time_series(resource, date_str, '1d')
                
                if f'activities-{resource}' in time_series:
                    data_points = time_series[f'activities-{resource}']
                    
                    for point in data_points:
                        labels = {
                            'date': point['dateTime'],
                            'resource': resource
                        }
                        
                        self._add_metric(
                            f'fitbit_timeseries_{resource}',
                            float(point['value']),
                            labels=labels,
                            help_text=f'Time series data for {resource}',
                            metric_type='gauge'
                        )
                        
        except Exception as e:
            self._add_metric(
                'fitbit_time_series_error',
                1,
                labels={'error': str(e), 'resource': resource},
                help_text='Error fetching time series data',
                metric_type='counter'
            )
    
    def get_metrics(self):
        """Get all metrics as Prometheus format string"""
        return '\n'.join(self.metrics)


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""
    
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        if 'code' in query_params:
            self.server.authorization_code = query_params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authorization successful!</h1><p>You can close this window.</p></body></html>')
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authorization failed!</h1></body></html>')
    
    def log_message(self, format, *args):
        return


def authenticate_fitbit():
    """Handle OAuth authentication flow"""
    fitbit = FitbitAPI()
    
    # Start local server for callback
    server = HTTPServer(('localhost', 8080), CallbackHandler)
    server.authorization_code = None
    
    # Start server in background thread
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    # Open browser for authorization
    auth_url = fitbit.get_authorization_url()
    print(f"# Opening browser for authorization: {auth_url}")
    webbrowser.open(auth_url)
    
    # Wait for callback
    print("# Waiting for authorization callback...")
    while server.authorization_code is None:
        time.sleep(1)
    
    server.shutdown()
    
    # Exchange code for tokens
    fitbit.exchange_code_for_token(server.authorization_code)
    return fitbit


def main():
    """Main function to export Prometheus metrics"""
    parser = argparse.ArgumentParser(description='Export Fitbit data as Prometheus metrics')
    parser.add_argument('--date', help='Date to export (YYYY-MM-DD format), defaults to today')
    parser.add_argument('--days', type=int, default=7, help='Number of days for time series data (default: 7)')
    parser.add_argument('--skip-auth', action='store_true', help='Skip OAuth flow if tokens already exist')
    parser.add_argument('--time-series', nargs='+', default=['steps', 'distance', 'calories'], 
                       help='Resources to export as time series (default: steps distance calories)')
    
    args = parser.parse_args()
    
    try:
        # Initialize Fitbit API
        fitbit = FitbitAPI()
        
        # Check if we have access token
        if not fitbit.access_token and not args.skip_auth:
            print("# No access token found. Starting OAuth flow...")
            fitbit = authenticate_fitbit()
        elif not fitbit.access_token:
            raise ValueError("No access token found. Remove --skip-auth or set FITBIT_ACCESS_TOKEN")
        
        # Create metrics exporter
        exporter = PrometheusMetricsExporter(fitbit)
        
        # Export various metrics
        exporter.export_user_profile()
        exporter.export_daily_activity(args.date)
        exporter.export_heart_rate(args.date)
        exporter.export_sleep_data(args.date)
        exporter.export_weight_data(args.date)
        
        # Export time series data
        for resource in args.time_series:
            exporter.export_time_series(resource, args.days)
        
        # Output Prometheus metrics
        print(exporter.get_metrics())
        
    except Exception as e:
        print(f"# Error: {e}")
        print(f"# Make sure you have set the following environment variables:")
        print(f"# - FITBIT_CLIENT_ID")
        print(f"# - FITBIT_CLIENT_SECRET")
        print(f"# - FITBIT_REDIRECT_URI (optional)")
        print(f"# - FITBIT_ACCESS_TOKEN (optional)")
        print(f"# - FITBIT_REFRESH_TOKEN (optional)")
        exit(1)


if __name__ == "__main__":
    main()