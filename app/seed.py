ALL = [1, 1, 1, 1, 1]


def ver(identity="Pending", background="Pending", references="Pending") -> dict:
    return {k: {"status": v, "date": "2026-11-01" if v != "Pending" else None} for k, v in (("identity", identity), ("background", background), ("references", references))}


def _bday(dob: str, age: int) -> str:
    """The birthday `age` years after the date of birth."""
    year, month, day = (int(p) for p in dob.split("-"))
    return f"{year + age:04d}-{month:02d}-{day:02d}"


def caregiver(cid, name, city, stage, modules, dob, verification, institution, extended=False, languages=None, days=None, hours="09:00-17:00", capacity=3, location_consent=False, documents=None, housing=None, checkins=None, credentials=None) -> dict:
    """A seed caregiver. The aftercare window ends on the 21st birthday unless
    extended (to the 23rd birthday); the default is the 21st birthday."""
    until = _bday(dob, 23 if extended else 21)
    return {
        "id": cid, "name": name, "city": city, "stage": stage, "modules": modules, "dateOfBirth": dob,
        "verification": verification,
        "institution": institution, "aftercareUntil": until, "extended": extended,
        "contacts": [{"name": "Ms. Rao", "role": "Aftercare officer", "phone": "+91 90000 00001"}],
        "locationConsent": location_consent,
        "documents": documents or [],
        "housing": housing,
        "checkins": checkins or [],
        "credentials": credentials or [],
        "languages": languages or [],
        "availability": {"days": days or [], "hours": hours},
        "capacityVisitsPerWeek": capacity,
    }


def seed() -> dict:
    return {
        "seq": 100,
        "caregivers": [
            caregiver("c1", "Anita", "Hyderabad", "Applied", [0, 0, 0, 0, 0], "2006-03-14", ver(), "Hyderabad Children's Home",
                      languages=["Telugu", "Hindi"], days=["Monday", "Wednesday", "Friday"],
                      housing={"status": "Temporary", "details": "Hostel stay while training."}),
            caregiver("c2", "Ravi", "Hyderabad", "Training", [1, 1, 0, 0, 0], "2005-07-21", ver("Verified"), "Hyderabad Children's Home",
                      languages=["Hindi", "English"], days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], capacity=4),
            caregiver("c3", "Meena", "Bengaluru", "Training", [1, 1, 1, 1, 0], "2006-01-30", ver("Verified"), "Bengaluru Bal Mandir",
                      languages=["Kannada", "English"], days=["Tuesday", "Thursday", "Saturday"]),
            caregiver("c4", "Suresh", "Chennai", "Certified", list(ALL), "2005-11-02", ver("Verified", "Verified"), "Chennai Children's Home",
                      languages=["Tamil", "English"], days=["Monday", "Wednesday", "Friday"], capacity=2),
            caregiver("c5", "Lakshmi", "Hyderabad", "Certified", list(ALL), "2004-09-09", ver("Verified", "Verified", "Verified"), "Hyderabad Children's Home",
                      extended=True, languages=["Hindi", "English", "Telugu"],
                      days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"], hours="08:00-18:00", capacity=5,
                      documents=[{"type": "Aadhaar", "reference": "XXXX-XXXX-1234", "status": "Verified"}],
                      housing={"status": "Stable", "details": "Rented room near Hyderabad."},
                      credentials=[
                          {"id": "k1", "type": "Background check", "issuedDate": "2026-06-01", "expiryDate": "2026-12-01", "status": "Valid"},
                          {"id": "k2", "type": "First aid", "issuedDate": "2026-01-15", "expiryDate": "2027-01-15", "status": "Valid"},
                      ]),
            caregiver("c6", "Imran", "Pune", "Matched", list(ALL), "2005-02-18", ver("Verified", "Verified", "Verified"), "Pune Children's Home",
                      languages=["Hindi", "English", "Urdu"], days=["Monday", "Wednesday", "Friday", "Sunday"], hours="09:00-17:00", capacity=3,
                      location_consent=True,
                      housing={"status": "Stable", "details": "Rented room in Pune."},
                      checkins=[{"date": "2026-11-10", "note": "Settled into the Pune placement; weekly call with the aftercare officer."}],
                      credentials=[
                          {"id": "k3", "type": "Background check", "issuedDate": "2025-11-01", "expiryDate": "2026-11-01", "status": "Expired"},
                          {"id": "k4", "type": "Training certificate", "issuedDate": "2026-05-01", "expiryDate": "2028-05-01", "status": "Valid"},
                      ]),
        ],
        "requests": [
            {"id": "r1", "family": "Menon family, Dallas", "parent": "Mr. Raghavan", "city": "Pune", "visitsPerWeek": 2, "need": "Companionship", "language": "Hindi", "status": "Matched", "caregiverId": "c6", "matchedAt": "2026-11-10",
             "carePlan": {"goals": "Daily companionship, a short walk, and help with lunch.", "tasks": [
                 {"id": "t1", "title": "Morning walk in the garden", "done": True},
                 {"id": "t2", "title": "Read the newspaper together", "done": False},
                 {"id": "t3", "title": "Help with lunch", "done": False},
             ]}},
            {"id": "r2", "family": "Rao family, Austin", "parent": "Mrs. Rao", "city": "Hyderabad", "visitsPerWeek": 3, "need": "Medical visit escort", "language": "Hindi", "status": "Open", "caregiverId": None, "matchedAt": None},
            {"id": "r3", "family": "Iyer family, Edison NJ", "parent": "Mr. Iyer", "city": "Chennai", "visitsPerWeek": 2, "need": "Daily check-in", "language": "Tamil", "status": "Open", "caregiverId": None, "matchedAt": None},
        ],
        "visits": [
            {"id": "v1", "requestId": "r1", "caregiverId": "c6", "date": "2026-11-12", "mood": "Good", "note": "Walked in the garden and read the newspaper together. Ate lunch well.",
             "plannedAt": "2026-11-12T10:00:00+00:00", "checkinAt": "2026-11-12T10:05:00+00:00", "checkoutAt": "2026-11-12T11:30:00+00:00"}
        ],
        "notifications": [],
        "incidents": [],
        "breaches": [],
        # Demo consent only: these records are ASSUMED for the seed people so
        # the consent flag shows as granted in the sample data. They are not
        # real consent — real consent must be recorded (POST /api/consents)
        # before real people's data is used. A parent is identified by their
        # family request id.
        "consents": [
            {"id": f"con{i}", "personType": "caregiver", "personId": f"c{i}", "purposes": ["care matching", "visit updates to family"], "status": "Granted", "grantedAt": "2026-11-01", "withdrawnAt": None, "noticeVersion": "1"}
            for i in range(1, 7)
        ] + [
            {"id": f"con{6 + i}", "personType": "parent", "personId": f"r{i}", "purposes": ["visit updates to family"], "status": "Granted", "grantedAt": "2026-11-01", "withdrawnAt": None, "noticeVersion": "1"}
            for i in range(1, 4)
        ],
        "audit": [],
    }
