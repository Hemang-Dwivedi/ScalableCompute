import os

# Set these on every machine (API and workers)
BROKER_URL = os.getenv("BROKER_URL", "redis://:devhem14@127.0.0.1:6379/0")
RESULT_BACKEND = os.getenv("RESULT_BACKEND", "redis://:devhem14@127.0.0.1:6379/1")

# Optional: namespace for Redis keys if you host multiple envs
NAMESPACE = os.getenv("APP_NAMESPACE", "distcomp")

# Basic auth for API (optional). Leave empty to disable.
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()

# Store-and-forward settings
SYNC_THRESHOLD = int(os.getenv("SYNC_THRESHOLD", "5"))
MAIN_SERVER_URL = os.getenv("MAIN_SERVER_URL", "http://localhost:8000")
LOCAL_STORE_DIR = os.getenv("LOCAL_STORE_DIR", ".")
