def mock_answer(query: str) -> dict:
    return {
        "answer": "Most likely cause: worn hydraulic seal. "
                  "Steps: 1) Lock out the machine. 2) Check the seal. 3) Replace it.",
        "confidence": 0.82,
        "safety_verdict": "approved",
        "citations": [
            {"id": "REP-014", "summary": "Press-03 seal replaced, pressure OK"},
            {"id": "REP-102", "summary": "Press-01 same fault, seal replaced"},
        ],
    }