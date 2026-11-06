"""Check the connection by listing your 5 latest activities."""
import sys

from garmin_api import GarminError, get

try:
    acts = get("/activitylist-service/activities/search/activities?start=0&limit=5") or []
except GarminError as e:
    print("Connection failed:", e)
    sys.exit(1)

print(f"Connected. Your {len(acts)} latest activities:\n")
for a in acts:
    date = (a.get("startTimeLocal") or "")[:16]
    kind = (a.get("activityType") or {}).get("typeKey", "?").replace("_", " ")
    km = (a.get("distance") or 0) / 1000
    print(f"  {date}  {kind:<22} {km:6.2f} km  ({km * 0.621371:5.2f} mi)  {a.get('activityName') or ''}")
