# App/worker.py
import argparse, os, socket, subprocess, sys

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queue", required=True)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--hostname", default=None)
    p.add_argument("--pool", default=("solo" if os.name == "nt" else "prefork"))
    a = p.parse_args()

    hostname = a.hostname or f"{a.queue}@{socket.gethostname()}"
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "App.celery_app.celery_app",
        "worker",
        "--loglevel=INFO",
        "--hostname", hostname,
        "-Q", a.queue,
        "--pool", a.pool,
        "--concurrency", str(a.concurrency),
    ]
    print("Starting worker:", " ".join(cmd))
    subprocess.call(cmd)

if __name__ == "__main__":
    main()
