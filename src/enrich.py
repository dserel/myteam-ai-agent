"""
enrich.py
---------
Non-myTeam enrichment για το parent experience: καιρός (OpenWeather) και
παραγωγή .ics (ημερολόγιο με υπενθυμίσεις). UI-agnostic & portable στο myTeam.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

OWM_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).replace("Z", "+00:00").replace(" ", "T")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def fetch_forecast(api_key: str, city: str, lang: str = "el") -> list[tuple[int, dict]]:
    """5-day/3h forecast -> list of (unix_dt, {temp, desc, rain_prob}). Best-effort -> []."""
    if not api_key or not city:
        return []
    try:
        r = requests.get(
            OWM_FORECAST,
            params={"q": city, "units": "metric", "lang": lang, "appid": api_key},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("list", []) or []
    except Exception:
        return []
    slots: list[tuple[int, dict]] = []
    for it in items:
        slots.append((
            int(it.get("dt") or 0),
            {
                "temp": (it.get("main") or {}).get("temp"),
                "desc": ((it.get("weather") or [{}])[0] or {}).get("description"),
                "rain_prob": round(100 * float(it.get("pop") or 0)),
            },
        ))
    return slots


def weather_for(slots: list[tuple[int, dict]], when: Any, max_gap_hours: float = 2.0) -> dict | None:
    """Κοντινότερο forecast slot στο `when` (αν εντός max_gap)."""
    d = _to_dt(when)
    if not d or not slots:
        return None
    target = int(d.timestamp())
    best, best_diff = None, None
    for dt, info in slots:
        diff = abs(dt - target)
        if best_diff is None or diff < best_diff:
            best, best_diff = info, diff
    if best is None or best_diff > (max_gap_hours * 3600 + 5400):
        return None
    return best


def attach_weather(api_key: str, city: str, events: list[dict]) -> list[dict]:
    """Επιστρέφει νέα λίστα events με κλειδί 'weather' (ή None)."""
    slots = fetch_forecast(api_key, city)
    out = []
    for e in events:
        e2 = dict(e)
        e2["weather"] = weather_for(slots, e.get("start_date")) if slots else None
        out.append(e2)
    return out


def attach_weather_best(
    api_key: str, city_candidates: list[str | None], events: list[dict]
) -> tuple[list[dict], str | None]:
    """Δοκιμάζει με τη σειρά κάθε candidate· χρησιμοποιεί το πρώτο που γυρνά
    forecast. Επιστρέφει (events_with_weather, used_city). Αν κανένα δεν δουλέψει,
    όλα τα events έχουν weather=None και used_city=None."""
    for city in city_candidates:
        if not city:
            continue
        slots = fetch_forecast(api_key, city)
        if slots:
            out = []
            for e in events:
                e2 = dict(e)
                e2["weather"] = weather_for(slots, e.get("start_date"))
                out.append(e2)
            return out, city
    out = []
    for e in events:
        e2 = dict(e)
        e2["weather"] = None
        out.append(e2)
    return out, None


def _ics_dt(value: Any) -> str | None:
    d = _to_dt(value)
    if not d:
        return None
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def build_ics(events: list[dict], calendar_name: str = "myTeam",
              default_minutes: int = 90, duration_min: int = 90) -> str:
    """
    .ics με VEVENT + VALARM (υπενθύμιση). Η υπενθύμιση είναι μεγαλύτερη (120')
    όταν ο τίτλος/τοποθεσία υποδηλώνει πολυσύχναστη/κεντρική περιοχή.
    """
    busy_hints = ("κεντρ", "centre", "center", "γλυφάδα", "glyfada", "μαρίνα", "πλατεία")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//myTeam//AI Agent//EL",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(calendar_name)}"]
    for i, e in enumerate(events):
        start = _ics_dt(e.get("start_date"))
        if not start:
            continue
        d = _to_dt(e.get("start_date"))
        end = (d + timedelta(minutes=duration_min)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        loc = e.get("location_alias") or ""
        blob = f"{e.get('title','')} {loc}".lower()
        rem = 120 if any(h in blob for h in busy_hints) else default_minutes
        lines += [
            "BEGIN:VEVENT",
            f"UID:myteam-{i}-{start}@myteam",
            f"DTSTAMP:{start}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{_esc(e.get('title') or 'Event')}",
        ]
        if loc:
            lines.append(f"LOCATION:{_esc(loc)}")
        lines += [
            "BEGIN:VALARM", "ACTION:DISPLAY",
            f"DESCRIPTION:{_esc('Υπενθύμιση: ' + (e.get('title') or 'Event'))}",
            f"TRIGGER:-PT{int(rem)}M", "END:VALARM", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
