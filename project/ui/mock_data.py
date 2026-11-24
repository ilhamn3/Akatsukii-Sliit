# Fake repair records (the "source repairs")
REPAIRS = {
    "REP-014": {
        "machine": "Press-03 (hydraulic press)",
        "technician": "Tech-07",
        "symptom": "Pressure drops while pressing",
        "root_cause": "Worn hydraulic seal",
        "fix": "Replaced the seal, pressure back to normal",
        "failed_attempts": "Topped up oil first, did not help",
        "safety_warning": "Lock out the machine and release pressure first",
    },
    "REP-102": {
        "machine": "Press-01 (hydraulic press)",
        "technician": "Tech-03",
        "symptom": "Oil leaking near the cylinder",
        "root_cause": "Worn hydraulic seal",
        "fix": "Replaced the seal and cleaned the area",
        "failed_attempts": "Tightened the fittings, leak stayed",
        "safety_warning": "Wear gloves, hot oil can burn",
    },
}


def mock_answer(query: str) -> dict:
    return {
        "answer": "Most likely cause: worn hydraulic seal.\n\n"
                  "Steps: 1) Lock out the machine. 2) Check the seal. 3) Replace it.",
        "confidence": 0.82,
        "safety_verdict": "NOT CHECKED(mock)",
        "citations": ["REP-014", "REP-102"],
    }


def get_repair(repair_id: str) -> dict:
    return REPAIRS.get(repair_id, {})