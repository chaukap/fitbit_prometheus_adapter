# 1. Build the image
sudo docker build -t fitbit-exporter .

# 2. Create .env file
cat > .env << EOF
FITBIT_CLIENT_ID=${FITBIT_CLIENT_ID}
FITBIT_CLIENT_SECRET=${FITBIT_CLIENT_SECRET}
FITBIT_ACCESS_TOKEN=${FITBIT_ACCESS_TOKEN}
FITBIT_REFRESH_TOKEN=${FITBIT_REFRESH_TOKEN}
EOF

# 3. Run HTTP server
sudo docker run -d \
  --name fitbit-exporter \
  --env-file .env \
  -p 8080:8080 \
  fitbit-exporter server

# 4. Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/metrics
curl http://localhost:8080/  # Dashboard