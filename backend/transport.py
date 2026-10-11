"""Train, bus and flight search, Tatkal rules, booking and payment against simulated inventory.

IMPORTANT: this is NOT connected to IRCTC or to any bus operator, because neither exposes a
public booking API - rail booking requires an authorised IRCTC B2B partnership. Routes, station
codes, class rules, Tatkal windows and the fare model below follow the real ones, but seats and
PNRs are generated locally. Nothing here reserves a real ticket and no money moves: the payment
step validates what the traveller types, then simulates the gateway. Card numbers and CVVs are
checked in memory and never stored - only the method and the last four digits are kept.

Availability is derived from a hash of (service, date, class) so a given search returns the same
answer on every refresh instead of flickering on each poll.
"""

import hashlib
import math
import random
import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import store

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------- places

# Boarding points a traveller is likely to start from. `rail` marks a hub with a railway station.
HUBS = {
    "MAS": {"name": "Chennai Central / Egmore", "city": "Chennai", "lat": 13.0827, "lon": 80.2707, "rail": True},
    "CGL": {"name": "Chengalpattu Junction", "city": "Chengalpattu", "lat": 12.6819, "lon": 79.9888, "rail": True},
    "CJ":  {"name": "Kanchipuram", "city": "Kanchipuram", "lat": 12.8342, "lon": 79.7036, "rail": True},
    "VM":  {"name": "Villupuram Junction", "city": "Villupuram", "lat": 11.9401, "lon": 79.4861, "rail": True},
    "PDY": {"name": "Puducherry", "city": "Puducherry", "lat": 11.9416, "lon": 79.8083, "rail": True},
    "TPJ": {"name": "Tiruchirappalli Junction", "city": "Trichy", "lat": 10.7905, "lon": 78.7047, "rail": True},
    "TJ":  {"name": "Thanjavur Junction", "city": "Thanjavur", "lat": 10.7870, "lon": 79.1378, "rail": True},
    "VLNK": {"name": "Velankanni", "city": "Velankanni", "lat": 10.6813, "lon": 79.8506, "rail": True},
    "MDU": {"name": "Madurai Junction", "city": "Madurai", "lat": 9.9252, "lon": 78.1198, "rail": True},
    "RMM": {"name": "Rameswaram", "city": "Rameswaram", "lat": 9.2876, "lon": 79.3129, "rail": True},
    "TEN": {"name": "Tirunelveli Junction", "city": "Tirunelveli", "lat": 8.7139, "lon": 77.7567, "rail": True},
    "CAPE": {"name": "Kanyakumari", "city": "Kanyakumari", "lat": 8.0883, "lon": 77.5385, "rail": True},
    "KQN": {"name": "Kodai Road", "city": "Kodai Road", "lat": 10.2333, "lon": 77.8833, "rail": True},
    "DG":  {"name": "Dindigul Junction", "city": "Dindigul", "lat": 10.3624, "lon": 77.9695, "rail": True},
    "SA":  {"name": "Salem Junction", "city": "Salem", "lat": 11.6643, "lon": 78.1460, "rail": True},
    "ED":  {"name": "Erode Junction", "city": "Erode", "lat": 11.3410, "lon": 77.7172, "rail": True},
    "CBE": {"name": "Coimbatore Junction", "city": "Coimbatore", "lat": 11.0168, "lon": 76.9558, "rail": True},
    "MTP": {"name": "Mettupalayam", "city": "Mettupalayam", "lat": 11.2996, "lon": 76.9366, "rail": True},
    "UAM": {"name": "Udagamandalam (Ooty)", "city": "Ooty", "lat": 11.4102, "lon": 76.6950, "rail": True},
    "SBC": {"name": "KSR Bengaluru", "city": "Bengaluru", "lat": 12.9716, "lon": 77.5946, "rail": True},
    "KOD": {"name": "Kodaikanal bus stand", "city": "Kodaikanal", "lat": 10.2381, "lon": 77.4892, "rail": False},
    "MAH": {"name": "Mahabalipuram bus stand", "city": "Mahabalipuram", "lat": 12.6269, "lon": 80.1927, "rail": False},
}

# Where each destination is actually reached from. Kodaikanal and Mahabalipuram have no station -
# their nearest railhead is a road transfer away, which is why the bus tab matters there.
DEST_HUB = {
    "madurai": {"hub": "MDU"},
    "rameswaram": {"hub": "RMM"},
    "thanjavur": {"hub": "TJ"},
    "kanyakumari": {"hub": "CAPE"},
    "ooty": {"hub": "UAM", "railhead": "MTP", "transfer_km": 50,
             "note": "Ooty's own railway is the slow Nilgiri Mountain Railway. Main-line trains stop at Mettupalayam (MTP), about 1.5 hours away by road."},
    "kodaikanal": {"hub": "KOD", "railhead": "KQN", "transfer_km": 80,
                   "note": "Kodaikanal has no railway station. Trains use Kodai Road (KQN), about 3 hours away by ghat road."},
    "mahabalipuram": {"hub": "MAH", "railhead": "CGL", "transfer_km": 29,
                      "note": "Mahabalipuram has no railway station. Trains use Chengalpattu (CGL), 29 km away by road."},
    "chennai": {"hub": "MAS"},
    "velankanni": {"hub": "VLNK"},
    "kanchipuram": {"hub": "CJ"},
}

# ---------------------------------------------------------------- trains

CLASSES = {
    "1A": {"label": "AC First (1A)", "rate": 2.90, "ac": True,  "tatkal": False},
    "2A": {"label": "AC 2-Tier (2A)", "rate": 1.72, "ac": True,  "tatkal": True},
    "3A": {"label": "AC 3-Tier (3A)", "rate": 1.20, "ac": True,  "tatkal": True},
    "CC": {"label": "AC Chair Car (CC)", "rate": 1.28, "ac": True,  "tatkal": True},
    "SL": {"label": "Sleeper (SL)", "rate": 0.46, "ac": False, "tatkal": True},
    "2S": {"label": "Second Sitting (2S)", "rate": 0.32, "ac": False, "tatkal": True},
}

# Tatkal premium: 30% of base fare for every class except 2S (10%), inside the real floor/ceiling.
TATKAL_CHARGE = {
    "2A": (400, 500), "3A": (300, 400), "CC": (125, 225), "SL": (100, 200), "2S": (10, 15),
}

# route entries are (hub code, departure HH:MM, day offset from origin)
TRAINS = [
    {"no": "12635", "name": "Vaigai SF Express", "days": "Daily", "classes": ["CC", "2S"],
     "route": [("MAS", "13:40", 0), ("CGL", "14:28", 0), ("VM", "15:33", 0), ("TPJ", "18:03", 0), ("DG", "19:38", 0), ("KQN", "20:14", 0), ("MDU", "21:10", 0)]},
    {"no": "12637", "name": "Pandian SF Express", "days": "Daily", "classes": ["1A", "2A", "3A", "SL"],
     "route": [("MAS", "21:30", 0), ("CGL", "22:18", 0), ("VM", "23:28", 0), ("TPJ", "02:05", 1), ("DG", "03:48", 1), ("KQN", "04:26", 1), ("MDU", "05:40", 1)]},
    {"no": "16101", "name": "Sethu Express", "days": "Daily", "classes": ["2A", "3A", "SL", "2S"],
     "route": [("MAS", "20:15", 0), ("CGL", "21:05", 0), ("VM", "22:20", 0), ("TPJ", "01:10", 1), ("MDU", "04:30", 1), ("RMM", "08:45", 1)]},
    {"no": "12633", "name": "Kanyakumari SF Express", "days": "Daily", "classes": ["2A", "3A", "SL"],
     "route": [("MAS", "17:25", 0), ("CGL", "18:14", 0), ("VM", "19:28", 0), ("TPJ", "22:15", 0), ("MDU", "01:35", 1), ("TEN", "04:20", 1), ("CAPE", "06:30", 1)]},
    {"no": "16177", "name": "Rockfort Express", "days": "Daily", "classes": ["2A", "3A", "SL", "2S"],
     "route": [("MAS", "22:30", 0), ("CGL", "23:20", 0), ("VM", "00:38", 1), ("TJ", "03:40", 1), ("TPJ", "05:15", 1)]},
    {"no": "16853", "name": "Cholan Express", "days": "Daily", "classes": ["3A", "SL", "2S"],
     "route": [("MAS", "08:00", 0), ("CGL", "08:52", 0), ("VM", "10:05", 0), ("TJ", "13:35", 0), ("TPJ", "15:10", 0)]},
    {"no": "16183", "name": "Uzhavan Express", "days": "Daily", "classes": ["3A", "SL", "2S"],
     "route": [("MAS", "22:45", 0), ("CGL", "23:35", 0), ("VM", "00:55", 1), ("TJ", "04:05", 1), ("VLNK", "06:20", 1)]},
    {"no": "12631", "name": "Nellai SF Express", "days": "Daily", "classes": ["2A", "3A", "SL"],
     "route": [("MAS", "19:00", 0), ("CGL", "19:48", 0), ("VM", "21:00", 0), ("TPJ", "23:45", 0), ("MDU", "03:05", 1), ("TEN", "05:45", 1)]},
    {"no": "12675", "name": "Kovai SF Express", "days": "Daily", "classes": ["CC", "2S"],
     "route": [("MAS", "06:15", 0), ("CGL", "07:02", 0), ("SA", "10:28", 0), ("ED", "11:20", 0), ("CBE", "13:05", 0)]},
    {"no": "12671", "name": "Nilgiri SF Express", "days": "Daily", "classes": ["1A", "2A", "3A", "SL"],
     "route": [("MAS", "21:15", 0), ("CGL", "22:05", 0), ("SA", "01:35", 1), ("ED", "02:30", 1), ("CBE", "04:20", 1), ("MTP", "05:45", 1)]},
    {"no": "56136", "name": "Nilgiri Mountain Railway", "days": "Daily", "classes": ["1A", "2S"],
     "route": [("MTP", "07:10", 0), ("UAM", "12:00", 0)]},
    {"no": "16723", "name": "Anantapuri Express", "days": "Daily", "classes": ["2A", "3A", "SL"],
     "route": [("MAS", "19:45", 0), ("CGL", "20:35", 0), ("VM", "21:50", 0), ("TPJ", "00:40", 1), ("MDU", "04:00", 1), ("TEN", "06:35", 1)]},
    {"no": "16232", "name": "Mayiladuthurai Express", "days": "Daily", "classes": ["SL", "2S"],
     "route": [("MDU", "06:00", 0), ("DG", "07:20", 0), ("TPJ", "09:05", 0), ("TJ", "10:30", 0)]},
    {"no": "16849", "name": "Trichy - Rameswaram Express", "days": "Daily", "classes": ["3A", "SL", "2S"],
     "route": [("TPJ", "17:30", 0), ("DG", "19:00", 0), ("MDU", "20:45", 0), ("RMM", "00:35", 1)]},
    {"no": "20681", "name": "Bengaluru - Madurai SF", "days": "Daily", "classes": ["2A", "3A", "SL"],
     "route": [("SBC", "20:50", 0), ("SA", "01:15", 1), ("DG", "04:40", 1), ("MDU", "06:15", 1)]},
    {"no": "22671", "name": "Coimbatore - Kodai Road Express", "days": "Daily", "classes": ["3A", "SL", "2S"],
     "route": [("CBE", "05:40", 0), ("ED", "07:05", 0), ("DG", "09:30", 0), ("KQN", "10:35", 0)]},
    {"no": "16860", "name": "Chennai - Kanchipuram Passenger", "days": "Daily", "classes": ["2S"],
     "route": [("MAS", "07:20", 0), ("CGL", "08:30", 0), ("CJ", "09:25", 0)]},
    {"no": "12693", "name": "Pearl City SF Express", "days": "Daily", "classes": ["2A", "3A", "SL"],
     "route": [("MAS", "19:30", 0), ("CGL", "20:20", 0), ("VM", "21:35", 0), ("TPJ", "00:25", 1), ("MDU", "03:45", 1)]},
]

def _return_services(outbound):
    """A return service for each train: same stops in reverse, number + 1 (the real convention).

    The return leaves the far terminus 40 minutes after the outbound train arrives there, and
    each stop keeps the same elapsed time from that terminus. Modelled, like the rest of the data.
    """
    def minutes(stop):
        h, m = stop[1].split(":")
        return stop[2] * 1440 + int(h) * 60 + int(m)

    out = []
    for t in outbound:
        last = minutes(t["route"][-1])
        start = (last % 1440 + 40) % 1440
        route = []
        for stop in reversed(t["route"]):
            at = start + (last - minutes(stop))
            route.append((stop[0], f"{at % 1440 // 60:02d}:{at % 1440 % 60:02d}", at // 1440))
        out.append({"no": str(int(t["no"]) + 1), "name": t["name"], "days": t["days"],
                    "classes": t["classes"], "route": route})
    return out


TRAINS += _return_services(TRAINS)
assert len({t["no"] for t in TRAINS}) == len(TRAINS), "duplicate train numbers"

# ---------------------------------------------------------------- buses

BUS_OPERATORS = [
    {"name": "TNSTC", "kind": "Government", "types": ["Non-AC Seater", "Super Deluxe"]},
    {"name": "SETC Tamil Nadu", "kind": "Government", "types": ["AC Seater", "Super Deluxe"]},
    {"name": "KPN Travels", "kind": "Private", "types": ["AC Sleeper", "Volvo Multi-Axle AC"]},
    {"name": "SRM Transports", "kind": "Private", "types": ["AC Sleeper", "Non-AC Sleeper"]},
    {"name": "Rathimeena Travels", "kind": "Private", "types": ["AC Seater", "Non-AC Seater"]},
    {"name": "Parveen Travels", "kind": "Private", "types": ["Volvo Multi-Axle AC", "AC Sleeper"]},
    {"name": "YBM Travels", "kind": "Private", "types": ["AC Sleeper", "Super Deluxe"]},
]

BUS_TYPE_RATE = {
    "Non-AC Seater": 1.05, "Super Deluxe": 1.35, "AC Seater": 1.75,
    "Non-AC Sleeper": 1.60, "AC Sleeper": 2.30, "Volvo Multi-Axle AC": 2.70,
}



# ---------------------------------------------------------------- flights (Tamil Nadu)

AIRPORTS = {
    "MAA": {"name": "Chennai International", "city": "Chennai", "lat": 12.9941, "lon": 80.1709},
    "CJB": {"name": "Coimbatore International", "city": "Coimbatore", "lat": 11.0300, "lon": 77.0434},
    "IXM": {"name": "Madurai Airport", "city": "Madurai", "lat": 9.8345, "lon": 78.0934},
    "TRZ": {"name": "Tiruchirappalli International", "city": "Tiruchirappalli", "lat": 10.7654, "lon": 78.7097},
    "TCR": {"name": "Thoothukudi Airport", "city": "Thoothukudi", "lat": 8.7242, "lon": 78.0258},
    "SXV": {"name": "Salem Airport", "city": "Salem", "lat": 11.7833, "lon": 78.0656},
}

# Which airport serves each place on the site, with the road transfer when it is not in town.
DEST_AIRPORT = {
    "chennai": {"airport": "MAA"},
    "mahabalipuram": {"airport": "MAA", "transfer_km": 55,
                      "note": "Mahabalipuram has no airport. Fly into Chennai (MAA), about 55 km away by road."},
    "kanchipuram": {"airport": "MAA", "transfer_km": 70,
                    "note": "Kanchipuram has no airport. Fly into Chennai (MAA), about 70 km away by road."},
    "madurai": {"airport": "IXM"},
    "rameswaram": {"airport": "IXM", "transfer_km": 170,
                   "note": "Rameswaram has no airport. Fly into Madurai (IXM), about 3 hours away by road."},
    "kodaikanal": {"airport": "IXM", "transfer_km": 120,
                   "note": "Kodaikanal has no airport. Fly into Madurai (IXM), about 3 hours away by ghat road."},
    "thanjavur": {"airport": "TRZ", "transfer_km": 60,
                  "note": "Thanjavur has no scheduled flights. Fly into Tiruchirappalli (TRZ), about 1.5 hours away by road."},
    "velankanni": {"airport": "TRZ", "transfer_km": 150,
                   "note": "Velankanni has no airport. Fly into Tiruchirappalli (TRZ), about 3.5 hours away by road."},
    "ooty": {"airport": "CJB", "transfer_km": 90,
             "note": "Ooty has no airport. Fly into Coimbatore (CJB), about 3 hours away by the Nilgiri ghat road."},
    "kanyakumari": {"airport": "TCR", "transfer_km": 90,
                    "note": "Kanyakumari has no airport in Tamil Nadu. Fly into Thoothukudi (TCR), about 2.5 hours away by road."},
}

# The same boarding points the train and bus search use, mapped to their nearest Tamil Nadu airport.
HUB_AIRPORT = {
    "MAS": "MAA", "CGL": "MAA", "CJ": "MAA", "MAH": "MAA", "PDY": "MAA", "VM": "MAA",
    "CBE": "CJB", "MTP": "CJB", "UAM": "CJB", "ED": "CJB",
    "MDU": "IXM", "KOD": "IXM", "KQN": "IXM", "DG": "IXM", "RMM": "IXM",
    "TPJ": "TRZ", "TJ": "TRZ", "VLNK": "TRZ",
    "TEN": "TCR", "CAPE": "TCR",
    "SA": "SXV",
}

AIRLINES = {
    "6E": {"name": "IndiGo", "kind": "Low-cost"},
    "IX": {"name": "Air India Express", "kind": "Low-cost"},
    "9I": {"name": "Alliance Air", "kind": "Regional"},
    "S5": {"name": "Star Air", "kind": "Regional"},
}

# Scheduled corridors inside Tamil Nadu: (from, to, [(airline, number, departure, aircraft)]).
# The return flight is generated from each entry. Schedules are modelled on the real network.
FLIGHT_ROUTES = [
    ("MAA", "CJB", [("6E", "6101", "06:05", "A320"), ("IX", "1371", "09:40", "B737"), ("6E", "6143", "13:15", "A321"),
                    ("6E", "6207", "17:50", "A320"), ("IX", "1389", "21:10", "B737")]),
    ("MAA", "IXM", [("6E", "6311", "06:40", "A320"), ("IX", "1141", "11:25", "B737"), ("6E", "6329", "15:30", "ATR72"),
                    ("6E", "6377", "20:05", "A320")]),
    ("MAA", "TRZ", [("6E", "6431", "07:30", "ATR72"), ("IX", "1621", "12:45", "B737"), ("6E", "6459", "18:20", "ATR72")]),
    ("MAA", "TCR", [("6E", "7231", "08:15", "ATR72"), ("6E", "7255", "16:40", "ATR72")]),
    ("MAA", "SXV", [("9I", "9301", "10:20", "ATR72")]),
    ("CJB", "IXM", [("S5", "5201", "11:00", "ATR72")]),
]

CABINS = {
    "ECO": {"label": "Economy", "mult": 1.0, "gst": 0.05},
    "BIZ": {"label": "Business", "mult": 2.7, "gst": 0.12},
}


def _build_flights():
    """Flatten FLIGHT_ROUTES into services, adding the return of each (number + 1)."""
    out = []
    for frm, to, rows in FLIGHT_ROUTES:
        km = round(haversine(AIRPORTS[frm]["lat"], AIRPORTS[frm]["lon"], AIRPORTS[to]["lat"], AIRPORTS[to]["lon"]) * 1.04)
        mins = 28 + round(km / 9.2)  # block time: taxi + climb + cruise at a regional-jet pace
        for code, num, dep, craft in rows:
            cabins = ["ECO", "BIZ"] if craft in ("A320", "A321") and code == "6E" and km > 400 else ["ECO"]
            out.append({"no": f"{code}-{num}", "code": code, "from": frm, "to": to, "depart": dep,
                        "aircraft": craft, "km": km, "mins": mins, "cabins": cabins})
            h, m = map(int, dep.split(":"))
            back = (h * 60 + m + 210) % 1440  # return leaves ~3.5 h after the outbound
            out.append({"no": f"{code}-{int(num) + 1}", "code": code, "from": to, "to": frm,
                        "depart": f"{back // 60:02d}:{back % 60:02d}", "aircraft": craft, "km": km,
                        "mins": mins, "cabins": cabins})
    return out



# ---------------------------------------------------------------- helpers

def haversine(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _rng(*parts):
    """A generator seeded by the arguments, so the same query always yields the same result."""
    seed = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(seed[:12], 16))


FLIGHTS = _build_flights()
assert len({f["no"] for f in FLIGHTS}) == len(FLIGHTS), "duplicate flight numbers"


def rail_km(a, b):
    return round(haversine(HUBS[a]["lat"], HUBS[a]["lon"], HUBS[b]["lat"], HUBS[b]["lon"]) * 1.22)


def road_km(a, b):
    return round(haversine(HUBS[a]["lat"], HUBS[a]["lon"], HUBS[b]["lat"], HUBS[b]["lon"]) * 1.32)


def now_ist():
    return datetime.now(IST)


def parse_date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- fares

def train_fare(cls, km, tatkal):
    c = CLASSES[cls]
    base = 40 + km * c["rate"]
    base = round(base / 5) * 5
    extra = 0
    if tatkal and c["tatkal"]:  # 1A has no Tatkal quota, so it never carries the premium
        lo, hi = TATKAL_CHARGE[cls]
        pct = 0.10 if cls == "2S" else 0.30
        extra = min(max(round(base * pct), lo), hi)
    reservation = {"1A": 60, "2A": 50, "3A": 40, "CC": 40, "SL": 20, "2S": 15}[cls]
    gst = round((base + extra) * 0.05) if c["ac"] else 0
    return {"base": base, "tatkal": extra, "reservation": reservation, "gst": gst,
            "total": base + extra + reservation + gst}


def bus_fare(bus_type, km):
    return round((60 + km * BUS_TYPE_RATE[bus_type]) / 5) * 5


def flight_fare(cabin, km, journey, flight_no):
    """Fare per passenger. Airline fares move with demand, so the base is scaled by how close the
    date is and by a per-flight factor - both deterministic, so a refresh never changes the price."""
    c = CABINS[cabin]
    days_out = max((journey - now_ist().date()).days, 0)
    advance = 1.0 + max(14 - days_out, 0) * 0.045            # up to ~63% dearer on the day itself
    flight_factor = _rng("fare", flight_no, journey.isoformat()).uniform(0.88, 1.32)
    base = round((1450 + km * 3.4) * c["mult"] * advance * flight_factor / 50) * 50
    fees = 410 if km > 250 else 330                          # user development fee + passenger service fee
    gst = round(base * c["gst"])
    return {"base": base, "fees": fees, "gst": gst, "total": base + fees + gst}


# ---------------------------------------------------------------- tatkal window

def tatkal_window(journey):
    """Real IRCTC rule: Tatkal opens one day before the journey - 10:00 for AC, 11:00 for non-AC."""
    opens_on = journey - timedelta(days=1)
    now = now_ist()
    out = {"opens_on": opens_on.isoformat(), "ac_time": "10:00", "non_ac_time": "11:00"}
    if journey <= now.date():
        out.update(ac_open=False, non_ac_open=False,
                   status="closed", message="Tatkal is not available on the day of travel.")
        return out
    if now.date() < opens_on:
        out.update(ac_open=False, non_ac_open=False, status="upcoming",
                   message=f"Tatkal opens on {opens_on.strftime('%d %b')} - 10:00 AM for AC classes, 11:00 AM for Sleeper and Second Sitting.")
        return out
    hhmm = now.strftime("%H:%M")
    ac_open, non_ac_open = hhmm >= "10:00", hhmm >= "11:00"
    if ac_open and non_ac_open:
        msg = "Tatkal booking is open now."
    elif ac_open:
        msg = "Tatkal is open for AC classes. Sleeper and Second Sitting open at 11:00 AM."
    else:
        msg = "Tatkal opens at 10:00 AM for AC classes and 11:00 AM for Sleeper and Second Sitting."
    out.update(ac_open=ac_open, non_ac_open=non_ac_open,
               status="open" if (ac_open or non_ac_open) else "waiting", message=msg)
    return out


# ---------------------------------------------------------------- availability

def _held(key):
    return store.read()["held"].get(key, 0)


def availability(train_no, journey, cls, quota):
    """Returns a status dict. Deterministic per (train, date, class, quota), minus anything booked."""
    r = _rng(train_no, journey.isoformat(), cls, quota)
    if quota == "tatkal":
        if not CLASSES[cls]["tatkal"]:
            return {"code": "NA", "label": "Tatkal not offered in this class", "seats": 0, "bookable": False}
        pool = r.choice([0, 0, 2, 4, 6, 9, 12, 18])
    else:
        roll = r.random()
        if roll < 0.34:
            pool = 0
        elif roll < 0.5:
            pool = -r.randint(1, 26)  # negative pool means RAC / waitlist
        else:
            pool = r.randint(3, 120)
    key = f"{train_no}|{journey.isoformat()}|{cls}|{quota}"
    if pool > 0:
        left = max(pool - _held(key), 0)
        if left:
            return {"code": "AVL", "label": f"AVAILABLE {left}", "seats": left, "bookable": True}
        return {"code": "WL", "label": "WL 1", "seats": 0, "bookable": False}
    if pool == 0:
        if quota == "tatkal":
            return {"code": "REGRET", "label": "REGRET / NO ROOM", "seats": 0, "bookable": False}
        n = r.randint(4, 88)
        return {"code": "WL", "label": f"WL {n}", "seats": 0, "bookable": False}
    n = -pool
    if n <= 12 and cls in ("SL", "3A"):
        return {"code": "RAC", "label": f"RAC {n}", "seats": 0, "bookable": True,
                "note": "RAC gives you a shared side-lower berth; it usually clears before departure."}
    return {"code": "WL", "label": f"WL {n}", "seats": 0, "bookable": False}


# ---------------------------------------------------------------- search

def _leg(train, frm, to):
    codes = [s[0] for s in train["route"]]
    if frm not in codes or to not in codes or codes.index(frm) >= codes.index(to):
        return None
    a, b = train["route"][codes.index(frm)], train["route"][codes.index(to)]
    return a, b


def _arrive(dep_hm, dep_day, arr_hm, arr_day):
    d0 = datetime.strptime(dep_hm, "%H:%M") + timedelta(days=dep_day)
    d1 = datetime.strptime(arr_hm, "%H:%M") + timedelta(days=arr_day)
    mins = int((d1 - d0).total_seconds() // 60)
    return mins


def search_trains(frm, to, journey, quota="general"):
    out = []
    for t in TRAINS:
        leg = _leg(t, frm, to)
        if not leg:
            continue
        (_, dep, dep_day), (_, arr, arr_day) = leg
        mins = _arrive(dep, dep_day, arr, arr_day)
        km = rail_km(frm, to)
        classes = []
        for cls in t["classes"]:
            av = availability(t["no"], journey, cls, quota)
            classes.append({"cls": cls, "label": CLASSES[cls]["label"], "ac": CLASSES[cls]["ac"],
                            "fare": train_fare(cls, km, quota == "tatkal"),
                            "availability": av})
        out.append({
            "no": t["no"], "name": t["name"], "days": t["days"],
            "from": frm, "from_name": HUBS[frm]["name"], "to": to, "to_name": HUBS[to]["name"],
            "depart": dep, "arrive": arr, "arrive_next_day": arr_day > dep_day,
            "duration": f"{mins // 60}h {mins % 60:02d}m", "km": km, "classes": classes,
            "any_bookable": any(c["availability"]["bookable"] for c in classes),
        })
    out.sort(key=lambda t: t["depart"])
    return out


def search_buses(frm, to, journey):
    km = road_km(frm, to)
    if km < 8:
        return []
    r = _rng("bus", frm, to, journey.isoformat())
    count = r.randint(5, 9)
    out = []
    for i in range(count):
        op = r.choice(BUS_OPERATORS)
        bus_type = r.choice(op["types"])
        hour = r.choice([6, 7, 8, 9, 11, 14, 16, 19, 20, 21, 21, 22, 22, 23])
        minute = r.choice([0, 15, 20, 30, 40, 45])
        mins = int(km / r.uniform(38, 52) * 60) + r.randint(10, 45)
        dep = datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M")
        arr = dep + timedelta(minutes=mins)
        seats_total = 36 if "Sleeper" in bus_type else 44
        key = f"BUS|{op['name']}|{bus_type}|{frm}|{to}|{journey.isoformat()}|{i}"
        taken = sorted(r.sample(range(1, seats_total + 1), r.randint(2, seats_total - 6)))
        left = seats_total - len(taken) - _held(key)
        taken = sorted(set(taken) | _booked_seats(key))  # show seats other travellers already booked
        out.append({
            "id": f"{frm}-{to}-{i}", "key": key, "operator": op["name"], "kind": op["kind"],
            "type": bus_type, "ac": "AC" in bus_type or "Volvo" in bus_type,
            "sleeper": "Sleeper" in bus_type,
            "from": frm, "from_name": HUBS[frm]["name"], "to": to, "to_name": HUBS[to]["name"],
            "depart": dep.strftime("%H:%M"), "arrive": arr.strftime("%H:%M"),
            "arrive_next_day": arr.day > dep.day,
            "duration": f"{mins // 60}h {mins % 60:02d}m", "km": km,
            "fare": bus_fare(bus_type, km), "rating": round(r.uniform(3.2, 4.8), 1),
            "seats_total": seats_total, "taken": taken, "seats_left": max(left, 0),
        })
    out.sort(key=lambda b: b["depart"])
    return out


def flight_seats(flight_no, journey, cabin):
    """Seats left on a flight. Deterministic per (flight, date, cabin), minus anything held or booked."""
    r = _rng("seats", flight_no, journey.isoformat(), cabin)
    total = 180 if cabin == "ECO" else 12
    pool = 0 if r.random() < 0.08 else r.randint(2, total - 20 if cabin == "ECO" else 10)
    key = f"FLT|{flight_no}|{journey.isoformat()}|{cabin}"
    return key, max(pool - _held(key), 0)


def search_flights(frm, to, journey):
    """Flights between two Tamil Nadu airports for one date. Past departures today are dropped."""
    out = []
    now = now_ist()
    for f in FLIGHTS:
        if f["from"] != frm or f["to"] != to:
            continue
        h, m = map(int, f["depart"].split(":"))
        dep_dt = datetime(journey.year, journey.month, journey.day, h, m, tzinfo=IST)
        if dep_dt < now + timedelta(hours=2):  # check-in closes well before departure
            continue
        arr_dt = dep_dt + timedelta(minutes=f["mins"])
        cabins = []
        for cab in f["cabins"]:
            key, left = flight_seats(f["no"], journey, cab)
            cabins.append({"cabin": cab, "label": CABINS[cab]["label"], "key": key, "seats_left": left,
                           "bookable": left > 0, "fare": flight_fare(cab, f["km"], journey, f["no"])})
        out.append({
            "no": f["no"], "airline": AIRLINES[f["code"]]["name"], "kind": AIRLINES[f["code"]]["kind"],
            "aircraft": f["aircraft"], "from": frm, "from_name": AIRPORTS[frm]["name"], "from_city": AIRPORTS[frm]["city"],
            "to": to, "to_name": AIRPORTS[to]["name"], "to_city": AIRPORTS[to]["city"],
            "depart": dep_dt.strftime("%H:%M"), "arrive": arr_dt.strftime("%H:%M"),
            "arrive_next_day": arr_dt.date() > dep_dt.date(),
            "duration": f"{f['mins'] // 60}h {f['mins'] % 60:02d}m", "km": f["km"],
            "stops": "Non-stop", "cabins": cabins,
            "any_bookable": any(c["bookable"] for c in cabins),
        })
    out.sort(key=lambda x: x["depart"])
    return out


def nearest_hubs(lat, lon, limit=6, rail_only=False):
    rows = []
    for code, h in HUBS.items():
        if rail_only and not h["rail"]:
            continue
        rows.append({"code": code, "name": h["name"], "city": h["city"], "rail": h["rail"],
                     "km": round(haversine(lat, lon, h["lat"], h["lon"]), 1)})
    rows.sort(key=lambda x: x["km"])
    return rows[:limit]




# ---------------------------------------------------------------- booking + payment
#
# A booking is made in two steps, like every real ticketing site:
#   1. book()  - validates the trip, HOLDS the seats and returns a PENDING_PAYMENT booking
#   2. pay()   - takes the payment details, simulates the gateway and issues the PNR / ticket number
# An unpaid hold is released after HOLD_MINUTES so seats are never locked forever.

HOLD_MINUTES = 10
LIVE = ("PENDING_PAYMENT", "CONFIRMED", "RAC")
BANKS = ["State Bank of India", "HDFC Bank", "ICICI Bank", "Axis Bank", "Indian Bank",
         "Indian Overseas Bank", "Canara Bank", "Kotak Mahindra Bank", "Tamilnad Mercantile Bank"]
TEST_DECLINE_CARD = "4000000000000002"
TEST_DECLINE_UPI = "fail@upi"


def _pnr():
    return str(secrets.randbelow(9_000_000_000) + 1_000_000_000)


def _ticket_no():
    return "TN" + str(secrets.randbelow(900_000) + 100_000)


def _flight_pnr():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


def _release(db, rec):
    """Give a booking's held seats back to the pool. Safe to call twice."""
    key, n = rec.get("hold_key"), rec.get("hold_n", 0)
    if key and n:
        left = db["held"].get(key, 0) - n
        if left > 0:
            db["held"][key] = left
        else:
            db["held"].pop(key, None)
    rec["hold_n"] = 0


def sweep():
    """Expire unpaid holds that ran out of time. Called on every read/write of bookings."""
    db = store.read()
    now, changed = now_ist(), False
    for b in db["bookings"]:
        if b["status"] == "PENDING_PAYMENT" and datetime.fromisoformat(b["expires_at"]) <= now:
            b["status"], b["payment_status"] = "EXPIRED", "NOT_PAID"
            _release(db, b)
            changed = True
    if changed:
        store.commit()


def _booked_seats(key):
    """Bus seats already claimed by a live booking on this bus."""
    seats = set()
    for b in store.read()["bookings"]:
        if b.get("kind") == "bus" and b.get("hold_key") == key and b["status"] in LIVE:
            seats.update(b["seats"])
    return seats


def _clean_passengers(passengers, limit):
    if not 1 <= len(passengers) <= limit:
        return None, f"Add between 1 and {limit} passengers."
    clean = []
    for p in passengers:
        name = (p.get("name") or "").strip()
        age = p.get("age")
        if not name:
            return None, "Every passenger needs a name."
        if not isinstance(age, int) or isinstance(age, bool) or not 1 <= age <= 120:
            return None, "Enter a valid age for every passenger."
        gender = p.get("gender") if p.get("gender") in ("M", "F", "O") else "O"
        clean.append({"name": name[:80], "age": age, "gender": gender})
    return clean, None


def book(user, payload):
    sweep()
    kind = payload.get("kind")
    if kind not in ("train", "bus", "flight"):
        return None, "Unknown booking type."
    passengers, err = _clean_passengers(payload.get("passengers") or [], 6)
    if err:
        return None, err
    journey = parse_date(payload.get("date"))
    if not journey or journey < now_ist().date():
        return None, "Choose a journey date that has not passed."

    db = store.read()
    frm, to = payload.get("from"), payload.get("to")
    n = len(passengers)

    if kind == "train":
        quota = payload.get("quota", "general")
        cls = payload.get("cls")
        train = next((t for t in TRAINS if t["no"] == payload.get("train_no")), None)
        leg = _leg(train, frm, to) if train else None
        if not leg:
            return None, "That train does not run between those stations."
        if cls not in train["classes"]:
            return None, "Choose a travel class."
        if quota not in ("general", "tatkal"):
            return None, "Unknown quota."
        if quota == "tatkal":
            win = tatkal_window(journey)
            open_now = win["ac_time" if CLASSES[cls]["ac"] else "non_ac_time"]
            if not (win["ac_open"] if CLASSES[cls]["ac"] else win["non_ac_open"]):
                return None, f"Tatkal for {CLASSES[cls]['label']} is not open yet - it opens at {open_now} on {win['opens_on']}."
            if n > 4:
                return None, "Tatkal allows a maximum of 4 passengers per booking."
        av = availability(train["no"], journey, cls, quota)
        if not av["bookable"]:
            return None, f"That class is {av['label']} - no seats to confirm."
        if av["code"] == "AVL" and av["seats"] < n:
            return None, f"Only {av['seats']} seat(s) left in that class."
        hold_key = f"{train['no']}|{journey.isoformat()}|{cls}|{quota}"
        fare = train_fare(cls, rail_km(frm, to), quota == "tatkal")
        (_, dep, _), (_, arr, _) = leg
        rec = {
            "kind": "train", "train_no": train["no"], "train_name": train["name"],
            "from": frm, "from_name": HUBS[frm]["name"], "to": to, "to_name": HUBS[to]["name"],
            "depart": dep, "arrive": arr, "cls": cls, "cls_label": CLASSES[cls]["label"], "quota": quota,
            "final_status": "RAC" if av["code"] == "RAC" else "CONFIRMED",
            "fare_each": fare, "total": fare["total"] * n,
        }

    elif kind == "bus":
        buses = search_buses(frm, to, journey) if frm in HUBS and to in HUBS else []
        bus = next((b for b in buses if b["key"] == payload.get("key")), None)
        if not bus:
            return None, "That bus is no longer available. Search again."
        seats = payload.get("seats") or []
        if len(seats) != n or len(set(seats)) != n:
            return None, "Pick one seat per passenger."
        if any(not isinstance(x, int) or isinstance(x, bool) or not 1 <= x <= bus["seats_total"] for x in seats):
            return None, "That seat does not exist on this bus."
        if set(seats) & set(bus["taken"]):
            return None, "One of those seats was just taken. Pick another."
        hold_key = bus["key"]
        rec = {
            "kind": "bus", "operator": bus["operator"], "type": bus["type"],
            "from": frm, "from_name": HUBS[frm]["name"], "to": to, "to_name": HUBS[to]["name"],
            "depart": bus["depart"], "arrive": bus["arrive"], "seats": sorted(seats),
            "final_status": "CONFIRMED", "fare_each": {"total": bus["fare"]}, "total": bus["fare"] * n,
        }

    else:  # flight
        flight = next((f for f in FLIGHTS if f["no"] == payload.get("flight_no")), None)
        cabin = payload.get("cabin")
        if not flight or flight["from"] != frm or flight["to"] != to:
            return None, "That flight does not fly between those airports."
        if cabin not in flight["cabins"]:
            return None, "Choose a cabin."
        h, m = map(int, flight["depart"].split(":"))
        dep_dt = datetime(journey.year, journey.month, journey.day, h, m, tzinfo=IST)
        if dep_dt < now_ist() + timedelta(hours=2):
            return None, "Booking closes 2 hours before departure. Pick a later flight or date."
        hold_key, left = flight_seats(flight["no"], journey, cabin)
        if left < n:
            return None, "Sold out in that cabin." if not left else f"Only {left} seat(s) left in that cabin."
        fare = flight_fare(cabin, flight["km"], journey, flight["no"])
        arr = dep_dt + timedelta(minutes=flight["mins"])
        rec = {
            "kind": "flight", "flight_no": flight["no"], "airline": AIRLINES[flight["code"]]["name"],
            "aircraft": flight["aircraft"], "from": frm, "from_name": AIRPORTS[frm]["name"],
            "to": to, "to_name": AIRPORTS[to]["name"], "depart": flight["depart"], "arrive": arr.strftime("%H:%M"),
            "cabin": cabin, "cabin_label": CABINS[cabin]["label"],
            "final_status": "CONFIRMED", "fare_each": fare, "total": fare["total"] * n,
        }

    now = now_ist()
    rec.update({
        "id": secrets.token_hex(8), "user": user["email"], "date": journey.isoformat(), "passengers": passengers,
        "status": "PENDING_PAYMENT", "payment_status": "PENDING",
        "hold_key": hold_key, "hold_n": n,
        "booked_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(minutes=HOLD_MINUTES)).isoformat(timespec="seconds"),
    })
    db["held"][hold_key] = db["held"].get(hold_key, 0) + n
    db["bookings"].append(rec)
    store.commit()
    return rec, None


def get_booking(email, booking_id):
    sweep()
    for b in store.read()["bookings"]:
        if b["id"] == booking_id and b["user"] == email:
            return b
    return None


def bookings_for(email):
    sweep()
    rows = [b for b in store.read()["bookings"] if b["user"] == email]
    rows.sort(key=lambda b: b["booked_at"], reverse=True)
    return rows


# ---- payment

def _luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card_brand(n):
    if n.startswith("4"):
        return "Visa"
    if n[:2] in ("51", "52", "53", "54", "55") or 2221 <= int(n[:4] or 0) <= 2720:
        return "Mastercard"
    if n[:2] in ("34", "37"):
        return "Amex"
    if n[:2] in ("60", "65", "81", "82"):
        return "RuPay"
    return "Card"


def _check_payment(payload):
    """Validate what the traveller typed. Returns (detail string, None) or (None, error).

    Raw card data lives only in this function's locals - it is never written to the store or logged.
    """
    method = payload.get("method")
    if method == "card":
        c = payload.get("card") or {}
        number = "".join(ch for ch in str(c.get("number", "")) if ch.isdigit())
        if not 13 <= len(number) <= 19 or not _luhn(number):
            return None, "That card number is not valid. Check the digits and try again."
        if not (c.get("name") or "").strip():
            return None, "Enter the name on the card."
        try:
            mm, yy = str(c.get("expiry", "")).replace(" ", "").split("/")
            mm, yy = int(mm), int(yy)
            yy += 2000 if yy < 100 else 0
            if not 1 <= mm <= 12:
                raise ValueError
        except ValueError:
            return None, "Enter the expiry as MM/YY."
        today = now_ist().date()
        if (yy, mm) < (today.year, today.month):
            return None, "That card has expired."
        cvv = str(c.get("cvv", ""))
        if not cvv.isdigit() or len(cvv) != (4 if number[:2] in ("34", "37") else 3):
            return None, "Enter the card's security code (CVV)."
        if number == TEST_DECLINE_CARD:
            return None, "The bank declined this card (test card). Try another payment method."
        return f"{_card_brand(number)} card ending {number[-4:]}", None
    if method == "upi":
        vpa = str(payload.get("vpa", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}", vpa):
            return None, "Enter a valid UPI ID, like name@bank."
        if vpa.lower() == TEST_DECLINE_UPI:
            return None, "The UPI request was declined (test ID). Try another payment method."
        return f"UPI {vpa}", None
    if method == "netbanking":
        bank = payload.get("bank")
        if bank not in BANKS:
            return None, "Choose your bank."
        return f"Net banking - {bank}", None
    return None, "Choose a payment method."


def pay(email, booking_id, payload):
    sweep()
    db = store.read()
    rec = next((b for b in db["bookings"] if b["id"] == booking_id and b["user"] == email), None)
    if not rec:
        return None, "Booking not found."
    if rec["status"] == "EXPIRED":
        return None, "This booking timed out before payment. Search again to hold new seats."
    if rec["status"] == "CANCELLED":
        return None, "This booking was cancelled."
    if rec["status"] != "PENDING_PAYMENT":
        return None, "This booking is already paid."
    detail, err = _check_payment(payload)
    if err:
        return None, err
    paid_at = now_ist().isoformat(timespec="seconds")
    rec["payment"] = {"method": payload["method"], "detail": detail, "amount": rec["total"],
                      "txn": "TXN" + secrets.token_hex(5).upper(), "paid_at": paid_at}
    rec["payment_status"] = "PAID"
    rec["status"] = rec["final_status"]
    rec["hold_n"] = 0  # the seats are now sold, not just held; the count stays in `held`
    ref = {"train": _pnr, "bus": _ticket_no, "flight": _flight_pnr}[rec["kind"]]()
    rec["pnr" if rec["kind"] != "bus" else "ticket"] = ref
    rec.pop("expires_at", None)
    store.commit()
    return rec, None


def cancel(email, booking_id):
    sweep()
    db = store.read()
    for b in db["bookings"]:
        if b["id"] == booking_id and b["user"] == email:
            if b["status"] == "CANCELLED":
                return None, "That booking is already cancelled."
            if b["status"] == "EXPIRED":
                return None, "That booking already expired."
            was_paid = b.get("payment_status", "PAID") == "PAID"  # tickets from before payments existed count as paid
            if b.get("hold_key"):  # a paid booking's seats stay counted in `held`, so free them too
                key = b["hold_key"]
                left = db["held"].get(key, 0) - (len(b["passengers"]) if was_paid else b.get("hold_n", 0))
                db["held"][key] = max(left, 0)
                b["hold_n"] = 0
            b["status"] = "CANCELLED"
            if was_paid:
                b["payment_status"] = "REFUNDED"
                b["refund"] = {"amount": b["total"], "ref": "RFN" + secrets.token_hex(5).upper(),
                               "at": now_ist().isoformat(timespec="seconds"),
                               "note": "Refund goes back to the original payment method (simulated)."}
            else:
                b["payment_status"] = "NOT_PAID"
            store.commit()
            return b, None
    return None, "Booking not found."
