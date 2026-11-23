"""Validate the fault catalogue.

PROVISIONAL schema: Ilham's Pydantic schemas were due 22 Nov. This checks the structural
rules in the work plan (T1 'done when'). Swap in the real Pydantic model when it lands.
"""
import json, pathlib, sys

HERE = pathlib.Path(__file__).parent
MACHINES = {"cnc_lathe", "hydraulic_press", "injection_moulding", "conveyor_line", "air_compressor"}
REQUIRED = ["fault_id", "machine_type", "name", "error_codes", "symptoms", "lay_description", "cause_family",
            "root_cause", "valid_fixes", "tools", "parts", "wrong_fixes", "hazards", "recurrence_weight"]

def validate():
    faults = json.loads((HERE / "faults.json").read_text())
    hazards = {h["hazard_id"] for h in json.loads((HERE / "hazards.json").read_text())}
    errors, ids = [], set()
    for f in faults:
        for k in REQUIRED:
            if k not in f: errors.append(f"{f.get('fault_id')}: missing {k}")
        if f["fault_id"] in ids: errors.append(f"duplicate id {f['fault_id']}")
        ids.add(f["fault_id"])
        if f["machine_type"] not in MACHINES: errors.append(f"{f['fault_id']}: bad machine")
        if len(f["symptoms"]) < 3: errors.append(f"{f['fault_id']}: needs >=3 symptoms")
        if len(f["valid_fixes"]) < 3: errors.append(f"{f['fault_id']}: needs >=3 fix steps")
        if len(f["wrong_fixes"]) < 1: errors.append(f"{f['fault_id']}: needs a wrong fix")
        if not f["hazards"] and f.get("hazard_note") != "none identified":
            errors.append(f"{f['fault_id']}: needs a hazard or explicit 'none'")
        for h in f["hazards"]:
            if h not in hazards: errors.append(f"{f['fault_id']}: unknown hazard {h}")
    per = {m: sum(1 for f in faults if f["machine_type"] == m) for m in MACHINES}
    if set(per.values()) != {8}: errors.append(f"expected 8 faults per machine, got {per}")
    return faults, errors, per

if __name__ == "__main__":
    faults, errors, per = validate()
    print(len(faults), "faults", per)
    if errors:
        print("\n".join(errors)); sys.exit(1)
    print("catalogue OK")
