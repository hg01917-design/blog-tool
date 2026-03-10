#!/bin/bash
# Generate IndexNow key and set up
KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
echo "Generated IndexNow key: $KEY"

# Update .env - replace empty INDEXNOW_KEY line
sed -i "s/^INDEXNOW_KEY=$/INDEXNOW_KEY=$KEY/" ~/blog-tool/.env

# Create key file in WordPress root
echo "$KEY" > ~/applications/qkdpuhswfk/public_html/${KEY}.txt
echo "Created key file: ~/applications/qkdpuhswfk/public_html/${KEY}.txt"

# Verify
echo "--- .env INDEXNOW_KEY ---"
grep INDEXNOW_KEY ~/blog-tool/.env
echo "--- Key file content ---"
cat ~/applications/qkdpuhswfk/public_html/${KEY}.txt

# Restart Gunicorn
cd ~/blog-tool
kill $(cat gunicorn.pid 2>/dev/null) 2>/dev/null
sleep 1
~/.local/bin/gunicorn --bind 127.0.0.1:5000 --workers 2 --daemon --pid gunicorn.pid --access-logfile access.log --error-logfile error.log --timeout 300 app:app
echo "Gunicorn restarted"

# Purge Varnish cache
curl -s -X PURGE http://127.0.0.1:8080/ -H "Host: app.baremi542.com" -o /dev/null -w "Varnish purge: %{http_code}\n"

# Health check
sleep 1
curl -s -o /dev/null -w "Flask health: %{http_code}\n" http://127.0.0.1:5000/
echo "SETUP_COMPLETE"
