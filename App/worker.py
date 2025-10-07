import argparse
import socket
import subprocess
import sys

"""
Helper: start a Celery worker bound to a specific queue (worker tag).
Run this on ANY machine you want to act as a worker.

Examples:
  python -m app.worker --queue cpu-a --concurrency 4
  python -m app.worker --queue host-ny-1 --concurrency 8
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True, help="Queue name this worker consumes")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--hostname", default=None, help="Optional Celery worker hostname")
    args = parser.parse_args()

    hostname = args.hostname or f"{args.queue}@{socket.gethostname()}"
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "App.celery_app.celery_app",
        "worker",
        "--loglevel=INFO",
        "--concurrency", str(args.concurrency),
        "--hostname", hostname,
        "-Q", args.queue
    ]
    print("Starting worker:", " ".join(cmd))
    subprocess.call(cmd)

if __name__ == "__main__":
    main()
