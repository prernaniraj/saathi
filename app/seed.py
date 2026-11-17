ALL = [1, 1, 1, 1, 1]


def ver(identity="Pending", background="Pending", references="Pending") -> dict:
    return {k: {"status": v, "date": "2026-11-01" if v != "Pending" else None} for k, v in (("identity", identity), ("background", background), ("references", references))}


def seed() -> dict:
    return {
        "seq": 100,
        "caregivers": [
            {"id": "c1", "name": "Anita", "city": "Hyderabad", "stage": "Applied", "modules": [0, 0, 0, 0, 0], "dateOfBirth": "2006-03-14", "verification": ver()},
            {"id": "c2", "name": "Ravi", "city": "Hyderabad", "stage": "Training", "modules": [1, 1, 0, 0, 0], "dateOfBirth": "2005-07-21", "verification": ver("Verified")},
            {"id": "c3", "name": "Meena", "city": "Bengaluru", "stage": "Training", "modules": [1, 1, 1, 1, 0], "dateOfBirth": "2006-01-30", "verification": ver("Verified")},
            {"id": "c4", "name": "Suresh", "city": "Chennai", "stage": "Certified", "modules": list(ALL), "dateOfBirth": "2005-11-02", "verification": ver("Verified", "Verified")},
            {"id": "c5", "name": "Lakshmi", "city": "Hyderabad", "stage": "Certified", "modules": list(ALL), "dateOfBirth": "2004-09-09", "verification": ver("Verified", "Verified", "Verified")},
            {"id": "c6", "name": "Imran", "city": "Pune", "stage": "Matched", "modules": list(ALL), "dateOfBirth": "2005-02-18", "verification": ver("Verified", "Verified", "Verified")},
        ],
        "requests": [
            {"id": "r1", "family": "Menon family, Dallas", "parent": "Mr. Raghavan", "city": "Pune", "visitsPerWeek": 2, "need": "Companionship", "status": "Matched", "caregiverId": "c6"},
            {"id": "r2", "family": "Rao family, Austin", "parent": "Mrs. Rao", "city": "Hyderabad", "visitsPerWeek": 3, "need": "Medical visit escort", "status": "Open", "caregiverId": None},
            {"id": "r3", "family": "Iyer family, Edison NJ", "parent": "Mr. Iyer", "city": "Chennai", "visitsPerWeek": 2, "need": "Daily check-in", "status": "Open", "caregiverId": None},
        ],
        "visits": [
            {"id": "v1", "requestId": "r1", "caregiverId": "c6", "date": "2026-11-12", "mood": "Good", "note": "Walked in the garden and read the newspaper together. Ate lunch well."}
        ],
        "audit": [],
    }
