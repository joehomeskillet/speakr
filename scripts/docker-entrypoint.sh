#!/bin/bash
set -e

# A clinic web-only replica serves on an existing installation. Startup in src.app
# verifies clinic mode, the shared key, storage and schema before accepting requests.
case "${CLINIC_WEB_ONLY:-0}" in
    1) exec "$@" ;;
    0) ;;
    *) echo "CLINIC_WEB_ONLY must be 0 or 1" >&2; exit 64 ;;
esac

# Create necessary directories
mkdir -p /data/uploads /data/instance
chmod 755 /data/uploads /data/instance

# Initialize the database if it doesn't exist
if [ ! -f /data/instance/transcriptions.db ]; then
    echo "Database doesn't exist. Creating new database..."
    SPEAKR_SKIP_BACKGROUND_TASKS=true python -c "from src.app import app, db; app.app_context().push(); db.create_all()"
    echo "Database created successfully."
else
    echo "Database exists. Checking for schema updates..."
    SPEAKR_SKIP_BACKGROUND_TASKS=true python -c "from src.app import app; app.app_context().push()"
fi

# Check if we need to create an admin user (regardless of whether the database exists)
if [ -n "$ADMIN_USERNAME" ] && [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
    echo "Creating admin user using environment variables..."
    cd /app && SPEAKR_SKIP_BACKGROUND_TASKS=true python scripts/docker_create_admin.py
fi

# Start the application
exec "$@"
