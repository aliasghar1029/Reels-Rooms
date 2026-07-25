# =====================================================================
# Reel Room — End-of-day safety net
#
# Schedule this ONCE A DAY, late (e.g. 23:45 PKT = 18:45 UTC), via
# PythonAnywhere's free Scheduled Tasks:
#   python3.10 /home/YOURUSERNAME/Reels-Rooms/backend/daily_publish.py
#
# Its job: catch any idea whose date is today but somehow never got
# published by the frequent time-based check (e.g. cron-job.org had a
# hiccup). Publishes regardless of the idea's time field — it's a
# last-chance catch-all, not the primary scheduler.
#
# The primary, time-precise scheduler is app.py's /api/scheduler/run
# endpoint, pinged every 15-30 min by a free external service.
# See FACEBOOK_SETUP_GUIDE.md and CRON_SETUP_GUIDE.md.
# =====================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import env_config  # noqa: F401 — sets all os.environ values

import scheduler_core

if __name__ == "__main__":
    for line in scheduler_core.run_check(force_all=True):
        print(line)