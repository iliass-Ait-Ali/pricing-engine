#!/bin/sh
# Start the public demo: API + dashboard behind nginx on port 7860.
# If any of the three processes exits, the container exits (and restarts).
set -eu

cd /app

uvicorn api.main:app --host 127.0.0.1 --port 8000 --root-path /api &
API_PID=$!

streamlit run dashboard/app.py \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false \
    --browser.gatherUsageStats false &
DASH_PID=$!

nginx -c /app/deploy/nginx.conf -g "daemon off;" &
NGINX_PID=$!

# Exit as soon as one of them stops, so the platform restarts the container.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$DASH_PID" 2>/dev/null && kill -0 "$NGINX_PID" 2>/dev/null; do
    sleep 5
done
echo "a demo process exited; stopping" >&2
kill "$API_PID" "$DASH_PID" "$NGINX_PID" 2>/dev/null || true
exit 1
