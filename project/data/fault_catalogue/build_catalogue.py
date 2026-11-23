"""Authoring source for the fault catalogue (synthetic, written for this project).

Run:  python build_catalogue.py
Writes faults.json and hazards.json (the committed artefacts).
"""
import json
import pathlib

HAZARDS = {
 "H01": ("Stored hydraulic pressure", "Depressurise the circuit and verify zero on the gauge before opening any line or fitting.", ["lockout", "depressurise", "verify zero energy"]),
 "H02": ("Electrical energy", "Isolate, lock out and tag out the supply and prove dead before touching any electrical part.", ["lockout/tagout", "prove dead"]),
 "H03": ("Crush / pinch point", "Keep hands out of the die, mould, belt nip or ram path; block moving parts mechanically before entering.", ["mechanical block", "lockout"]),
 "H04": ("Hot oil or hot surface", "Let oil, gearbox or housing cool and wear heat-resistant gloves before draining or touching.", ["cool down", "heat-resistant gloves"]),
 "H05": ("Stored compressed air", "Vent the receiver and lines to zero pressure before servicing any pressurised component.", ["vent to zero", "lockout"]),
 "H06": ("Rotating spindle / chuck / pulley", "Bring rotation to a full stop and lock out before reaching into the machine; no loose clothing or gloves near rotating parts.", ["full stop", "lockout", "no loose clothing"]),
 "H07": ("Hot barrel / molten plastic", "Wear face shield and heat-resistant gloves; never face the nozzle when purging; let the barrel cool for non-heating work.", ["face shield", "heat-resistant gloves"]),
 "H08": ("Moving belt / chain / rollers", "Stop and lock out the conveyor; never clean, lubricate or clear jams on a running line.", ["lockout", "guards in place"]),
 "H09": ("Slippery coolant / fluid spill", "Clean spills immediately; use non-slip footwear and absorbent; avoid skin contact with coolant.", ["spill control", "non-slip footwear"]),
 "H10": ("High-pressure fluid injection", "Never check for hydraulic leaks with hands; use cardboard, wear eye protection, and seek medical help for any injection injury.", ["depressurise", "eye protection", "cardboard test only"]),
 "H11": ("Sharp swarf / hot chips", "Use hooks or brushes, not bare hands; wear cut-resistant gloves and eye protection.", ["cut-resistant gloves", "eye protection"]),
 "H12": ("Suspended / gravity load", "Support the ram, platen or load mechanically (safety blocks) before working beneath or inside.", ["safety blocks", "lockout"]),
 "H13": ("Residual capacitor / drive voltage", "Wait the drive's discharge time and measure DC bus voltage before touching drive or motor terminals.", ["wait discharge time", "measure DC bus"]),
 "H14": ("Pressure vessel / safety valve integrity", "Never plug, gag or adjust a safety valve; never raise settings beyond rated pressure.", ["no bypass of safety devices", "rated pressure only"]),
 "H15": ("Bypassed safety device", "Never bypass, jumper or defeat a light curtain, interlock or e-stop; fix the device and have the supervisor sign off.", ["no bypass", "supervisor sign-off"]),
 "H16": ("Water near live electrics", "Isolate the supply before dealing with any water leak near electrical equipment.", ["lockout/tagout", "dry area before re-energising"]),
}

# (id, machine, name, codes, symptoms, lay_description, cause_family, root_cause,
#  fixes, tools, parts, wrong_fixes[(past tense action, why it fails)], hazards, recurrence_weight)
F = []


def f(*a):
    F.append(a)


# ---------------- CNC LATHE ----------------
f("L01", "cnc_lathe", "Spindle overheating", ["ALM-410"],
  ["spindle housing too hot to touch", "thermal alarm after about two hours running", "slight whine from the spindle"],
  "spindle is running hot and the machine keeps throwing a temperature alarm",
  "clogging", "clogged filter in the spindle chiller circuit",
  ["lock out the machine", "drain the chiller reservoir", "remove and clean the chiller filter", "refill with the correct coolant", "run for 30 minutes and confirm the temperature stays under limit"],
  ["spanner set", "drain tray", "lint-free cloth"], ["chiller filter element", "spindle coolant"],
  [("replaced the spindle bearings", "bearings were fine, the heat came from restricted coolant flow"), ("lowered the spindle speed override", "only hid the symptom, alarm came back at normal speed")],
  ["H02", "H06", "H04"], 3)
f("L02", "cnc_lathe", "Chatter and poor surface finish", [],
  ["wavy surface finish on the part", "loud chatter at the cut", "tool marks repeating at regular spacing"],
  "parts are coming off rough with a wavy finish and the cut sounds awful",
  "alignment", "turret not fully clamped because chips are packed under the clamp face",
  ["stop the spindle and lock out", "index the turret to a free station", "clean chips and old coolant from the turret clamp face", "check the turret seats flat with a feeler gauge", "re-run a test cut"],
  ["air gun with guard", "brush", "feeler gauge"], [],
  [("reduced the feed rate", "chatter stayed because the turret was still moving under load"), ("swapped to a new insert", "insert was not the issue")],
  ["H06", "H11"], 3)
f("L03", "cnc_lathe", "Chuck not clamping", ["ALM-522"],
  ["low chuck pressure alarm", "workpiece slips in the jaws", "pressure gauge drops after clamping"],
  "chuck lets go of the part and the pressure alarm keeps coming up",
  "seal_leak", "worn seal in the rotary hydraulic union leaking pressure",
  ["stop the spindle and lock out", "depressurise the chuck hydraulic line and verify zero", "remove the rotary union cover", "replace the worn union seal kit", "re-pressurise and test clamping with a scrap part"],
  ["seal pick set", "torque wrench", "drip tray"], ["rotary union seal kit", "hydraulic oil"],
  [("raised the chuck pressure setting", "leak got worse and pressure still dropped, and it risks overclamping"), ("replaced the chuck jaws", "jaws were not worn")],
  ["H01", "H06", "H10"], 3)
f("L04", "cnc_lathe", "X-axis servo overload", ["ALM-401"],
  ["X axis overload alarm on rapid moves", "axis feels stiff when moved by hand", "servo motor warm"],
  "the X axis is stalling and throwing an overload alarm",
  "lubrication", "starved ball screw because the way-lube line is clogged",
  ["lock out and wait for drive discharge", "trace the way-lube line to the X ball screw", "clear the blocked lube line and replace the clogged metering block", "run the lube pump manually until oil appears", "jog the axis and confirm the load drops"],
  ["grease gun", "hex key set", "multimeter"], ["lube metering block", "way oil"],
  [("replaced the X servo motor", "motor was healthy, the screw was dry and dragging"), ("increased the overload threshold", "masked the problem and risks damaging the screw")],
  ["H02", "H13", "H03"], 2)
f("L05", "cnc_lathe", "Coolant pump no flow", ["ALM-640"],
  ["no coolant at the nozzle", "pump runs but sounds dry", "low flow alarm"],
  "no coolant is coming out even though the pump is running",
  "clogging", "suction strainer packed with swarf",
  ["lock out the machine", "remove the tank cover", "lift out and clean the suction strainer", "refill coolant to the mark and bleed the pump", "restart and check flow at the nozzle"],
  ["hook", "bucket", "gloves"], ["coolant top-up"],
  [("replaced the pump motor", "the pump was fine, the strainer was blocked"), ("added more coolant to the tank", "level was not the problem")],
  ["H02", "H09", "H11"], 3)
f("L06", "cnc_lathe", "Turret fails to index", ["ALM-715"],
  ["turret stops halfway", "index timeout alarm", "turret sometimes locks but control says unclamped"],
  "the turret will not finish its index and times out",
  "alignment", "clamp proximity sensor out of position",
  ["lock out the machine", "locate the turret clamp proximity sensor", "reset the sensor gap to the manual value", "tighten the bracket and recheck the signal on the diagnostics screen", "cycle the turret ten times"],
  ["feeler gauge", "hex key set", "multimeter"], [],
  [("replaced the turret drive motor", "motor was never failing"), ("reloaded the PLC parameters", "parameters were correct")],
  ["H02", "H03"], 2)
f("L07", "cnc_lathe", "Parts drifting out of tolerance", [],
  ["diameters creeping out of tolerance", "first parts good then they drift", "offsets look right on the screen"],
  "parts start good then slowly go out of size",
  "sensor", "damaged tool setter probe giving bad offset readings",
  ["stop the machine safely", "test the tool setter probe against a known gauge pin", "replace the damaged probe tip", "recalibrate tool offsets", "run a five-part check"],
  ["gauge pin", "micrometer"], ["tool setter probe tip"],
  [("kept adjusting the offsets by hand", "the probe kept feeding wrong values so the drift came back"), ("replaced the inserts every shift", "inserts were not the cause")],
  [], 1)
f("L08", "cnc_lathe", "Spindle encoder fault", ["ALM-362"],
  ["encoder alarm that comes and goes", "spindle speed reading jumps", "alarm worse when the door opens"],
  "spindle speed readout jumps around and the encoder alarm comes and goes",
  "electrical", "encoder cable chafed in the drag chain",
  ["lock out and wait for drive discharge", "inspect the encoder cable along the drag chain", "replace the chafed cable section", "re-route and clip the cable clear of the chain edge", "power up and confirm a stable speed reading"],
  ["multimeter", "cable ties", "side cutters"], ["encoder cable"],
  [("replaced the spindle drive", "drive was fine, the signal was being lost in the cable"), ("cleared the alarm and carried on", "alarm kept returning")],
  ["H02", "H13"], 1)

# ---------------- HYDRAULIC PRESS ----------------
f("P01", "hydraulic_press", "Slow ram descent", [],
  ["ram descends slowly", "pump whining louder than normal", "cycle times longer"],
  "the ram is crawling down and the pump sounds strained",
  "clogging", "clogged pump suction strainer",
  ["lock out and depressurise the press", "drain the tank to the strainer level", "remove and clean the suction strainer", "refill with filtered oil", "run empty cycles and time the ram"],
  ["spanner set", "drain tray", "clean rags"], ["suction strainer", "hydraulic oil"],
  [("raised the relief valve setting", "did not fix flow starvation and over-pressurised the system"), ("replaced the pump", "pump was cavitating from the blocked strainer")],
  ["H01", "H04"], 3)
f("P02", "hydraulic_press", "Pressure will not build", ["ERR-H21"],
  ["system pressure stuck low", "pump runs normally", "gauge never reaches setpoint"],
  "press will not come up to pressure even though the pump is running",
  "clogging", "relief valve stuck partly open from contamination",
  ["lock out and depressurise the press", "remove the relief valve cartridge", "flush and clean the valve seat", "inspect the poppet for scoring and replace if marked", "reinstall and set the relief pressure to rated value with a gauge"],
  ["torque wrench", "pressure test gauge", "clean solvent"], ["relief valve cartridge", "hydraulic oil filter"],
  [("replaced the main pump", "pump was delivering fine, oil was bleeding back through the relief valve"), ("added oil to the reservoir", "level was already correct")],
  ["H01", "H10"], 3)
f("P03", "hydraulic_press", "Ram drifts down under hold", [],
  ["ram slowly sinks while holding", "pressure bleeds off during hold", "cycle needs repeated top-up"],
  "the ram creeps down by itself when it should be holding",
  "seal_leak", "worn piston seal letting oil pass inside the cylinder",
  ["support the ram with safety blocks", "lock out and depressurise the press", "remove the cylinder head", "replace the piston seals and check the bore", "reassemble and run a hold test for ten minutes"],
  ["safety blocks", "seal tools", "torque wrench"], ["piston seal kit"],
  [("tightened the external fittings", "there was no external leak, the oil was passing inside"), ("replaced the directional valve", "valve was fine")],
  ["H12", "H01", "H03"], 2)
f("P04", "hydraulic_press", "Hydraulic oil overheating", ["ERR-H33"],
  ["oil temperature alarm", "tank hot to touch", "oil looks thin and dark"],
  "hydraulic oil is running hot and the temperature alarm trips",
  "thermal", "oil cooler fan failed and cooler fins fouled",
  ["lock out the machine and let the oil cool", "check the cooler fan rotation and motor", "replace the failed fan motor", "clean the cooler fins with low-pressure air", "run and confirm oil temperature stabilises"],
  ["multimeter", "brush", "air gun"], ["cooler fan motor"],
  [("topped up the oil", "oil level was fine, cooling was the issue"), ("changed the oil", "helped nothing while the cooler was blocked")],
  ["H02", "H04"], 2)
f("P05", "hydraulic_press", "Jerky ram movement", [],
  ["ram moves in jerks", "foamy oil in the tank", "knocking noise from the pump"],
  "the ram moves in jerks and the oil looks foamy",
  "seal_leak", "air drawn into the circuit through a loose suction fitting",
  ["lock out and depressurise", "inspect all suction side fittings and hoses", "tighten or replace the leaking suction fitting", "bleed air from the circuit at the cylinder bleed points", "run cycles until motion is smooth"],
  ["spanner set", "clean rags", "drain tray"], ["suction hose clamp", "hydraulic oil"],
  [("replaced the proportional valve", "valve was fine, air in the oil caused the jerking"), ("changed the oil only", "foam came back as the leak remained")],
  ["H01", "H10"], 2)
f("P06", "hydraulic_press", "Light curtain fault stops cycle", ["ERR-S07"],
  ["press will not start a cycle", "safety light curtain fault lit", "fault clears then returns"],
  "the press refuses to cycle and the light curtain keeps faulting",
  "alignment", "light curtain misaligned and lens dirty",
  ["lock out the machine", "clean the emitter and receiver lenses", "realign the light curtain using the alignment indicators", "run the safety function test and record it", "get supervisor sign-off before returning to production"],
  ["lens cloth", "alignment bracket tool"], [],
  [("bypassed the light curtain to keep producing", "unsafe and illegal: removes operator protection"), ("replaced the safety relay", "relay was healthy")],
  ["H15", "H03", "H02"], 3)
f("P07", "hydraulic_press", "Oil leak at cylinder gland", [],
  ["oil film on the cylinder rod", "oil dripping from the gland", "rod surface feels scored"],
  "oil is weeping out around the cylinder rod",
  "seal_leak", "worn rod seal damaged by a scored rod",
  ["support the ram with safety blocks", "lock out and depressurise", "remove the gland and old rod seal", "polish minor rod scoring or replace the rod if deep", "fit a new rod seal and wiper and test for leaks"],
  ["safety blocks", "seal tools", "emery cloth"], ["rod seal", "wiper seal"],
  [("tightened the gland nut", "squeezed the seal harder and wore the rod faster"), ("wrapped tape around the gland", "tape does not hold hydraulic pressure")],
  ["H12", "H01", "H10"], 2)
f("P08", "hydraulic_press", "Pump motor trips on start", ["ERR-E15"],
  ["overload trips as the pump starts", "contactor buzzes", "burning smell near the cabinet"],
  "the pump motor trips out every time it starts",
  "electrical", "pitted contactor contacts causing a phase imbalance",
  ["lock out and prove dead", "open the starter cabinet and inspect the contactor", "replace the pitted contactor", "check the motor current on all three phases", "start and confirm the overload stays closed"],
  ["multimeter", "clamp meter", "screwdriver set"], ["motor contactor"],
  [("turned the overload setting up", "motor ran with an imbalance and risked burning out"), ("replaced the pump motor", "motor was healthy")],
  ["H02", "H13"], 1)

# ---------------- INJECTION MOULDING ----------------
f("I01", "injection_moulding", "Short shots", ["ERR-I11"],
  ["parts not filling completely", "cushion position keeps changing", "shot size varies shot to shot"],
  "parts are coming out incomplete and the shot size is all over the place",
  "wear", "worn check ring letting melt flow back",
  ["lock out and let the barrel cool to a safe temperature", "pull the screw", "inspect the check ring and seat", "replace the worn check ring set", "reinstall and confirm a steady cushion"],
  ["screw puller", "spanner set", "brass brush"], ["check ring set"],
  [("raised the injection pressure", "melt just flowed back more and risked flash"), ("increased the shot size", "cushion still varied")],
  ["H07", "H01", "H02"], 3)
f("I02", "injection_moulding", "Flash on parts", [],
  ["thin flash on the parting line", "flash gets worse over the shift", "clamp tonnage looks normal"],
  "thin flash keeps showing up around the edge of the parts",
  "clogging", "debris on the mould parting line",
  ["lock out and make sure the mould is cool", "open the mould and clean the parting faces", "inspect for damaged shut-off surfaces", "apply mould protector and close slowly", "run a test shot"],
  ["brass scraper", "mould cleaner", "lamp"], ["mould protector"],
  [("increased clamp force to maximum", "damages the mould and platens without fixing the debris"), ("lowered the barrel temperature", "flash stayed")],
  ["H03", "H07"], 3)
f("I03", "injection_moulding", "Barrel temperature runaway", ["ERR-I27"],
  ["barrel temperature climbing above setpoint", "one zone reads low but material is smoking", "heater stays on continuously"],
  "one barrel zone keeps heating and the material is smoking",
  "sensor", "failed thermocouple reading low so the heater stays on",
  ["switch off the heaters and lock out", "let the barrel cool", "test the zone thermocouple against a reference", "replace the failed thermocouple", "power up and watch the zone reach setpoint without overshoot"],
  ["multimeter", "reference thermometer", "spanner set"], ["thermocouple"],
  [("replaced the heater band", "band was working, the sensor misled the controller"), ("raised the setpoint", "made the overheating worse")],
  ["H07", "H02"], 2)
f("I04", "injection_moulding", "Screw will not rotate", ["ERR-I33"],
  ["screw torque alarm at start", "screw stuck after a cold start", "motor strains and stops"],
  "the screw will not turn after starting from cold",
  "thermal", "unmelted plastic frozen in the barrel after a cold start",
  ["lock out the screw drive", "bring the barrel up to temperature and heat soak for the full soak time", "retry screw rotation at low torque", "purge the barrel with a purging compound", "verify the melt temperature is stable"],
  ["purge compound", "face shield", "heat-resistant gloves"], ["purging compound"],
  [("forced the screw with higher torque", "can shear the screw"), ("replaced the screw motor", "motor was fine")],
  ["H07", "H02"], 2)
f("I05", "injection_moulding", "Ejector sticking", [],
  ["parts stick in the mould", "ejector plate moves unevenly", "squeak from the ejector pins"],
  "the ejector is sticking and parts hang up in the mould",
  "lubrication", "dry ejector guide pins with no lubrication",
  ["lock out the machine", "open the mould and pull the ejector plate", "clean and inspect the guide pins and bushes", "apply the correct mould grease", "cycle the ejector by hand to check smooth travel"],
  ["grease gun", "lint-free cloth"], ["mould grease"],
  [("increased the ejection force", "bent pins and left the real cause"), ("sprayed release agent on the parts", "covered the problem for a few shots")],
  ["H03"], 2)
f("I06", "injection_moulding", "Black specks in parts", [],
  ["black specks in the moulded parts", "specks come and go", "parts look streaky after a restart"],
  "there are little black specks in the parts",
  "thermal", "carbonised material trapped in a dead spot in the barrel and hot runner",
  ["lock out and make sure the area is safe from molten material", "purge the barrel with purging compound", "clean the nozzle and hot runner tip", "dry the material and check the dryer settings", "run parts and inspect for specks"],
  ["purge compound", "face shield", "brass brush"], ["purging compound"],
  [("changed the colour masterbatch", "masterbatch was not the source"), ("lowered the barrel temperature below spec", "caused cold slugs without clearing the carbon")],
  ["H07"], 2)
f("I07", "injection_moulding", "Mould will not close fully", ["ERR-I40"],
  ["mould close alarm", "platen stops short", "a part is visible in the cavity"],
  "the mould stops before closing and trips an alarm",
  "alignment", "a part stuck in the cavity from the previous cycle",
  ["stop the machine and lock out", "never reach in while the platens can move", "remove the stuck part with brass tools", "check for damage to the mould faces", "reset and cycle slowly with mould protection on"],
  ["brass tools", "lamp"], [],
  [("increased the mould close pressure", "crushes the mould with the part still in place"), ("reset the alarm and tried again", "same stuck part blocked the close")],
  ["H03", "H12", "H02"], 3)
f("I08", "injection_moulding", "Water leak at cooling manifold", [],
  ["water dripping near the mould", "wet floor under the manifold", "mould cooling flow reduced"],
  "water is leaking near the mould and the floor is wet under the manifold",
  "seal_leak", "cracked hose coupling on the cooling water manifold",
  ["isolate the water supply and lock out the machine", "dry the area before touching electrical parts", "cut out and replace the cracked coupling", "check the manifold for other cracks", "restore water and check for leaks"],
  ["hose cutter", "spanner set", "wet vacuum"], ["hose coupling", "hose clamp"],
  [("wrapped tape around the coupling", "tape does not hold under pressure"), ("ignored it and mopped the floor", "leak grew and reached the cabinet")],
  ["H16", "H09", "H02"], 2)

# ---------------- CONVEYOR LINE ----------------
f("C01", "conveyor_line", "Belt mistracking", [],
  ["belt drifts to one side", "belt edge rubbing the frame", "product pushed off the edge"],
  "the belt keeps wandering off to one side",
  "alignment", "build-up on an idler roller pushing the belt off line",
  ["stop and lock out the conveyor", "find the idler with build-up", "clean the roller surface and check it spins freely", "square the roller to the frame", "restart and watch the belt track for a few minutes"],
  ["scraper", "spirit level", "spanner set"], [],
  [("tightened the belt tension fully", "over-tensioned the belt and damaged the bearings"), ("trimmed the belt edge", "wasted material and tracking stayed wrong")],
  ["H08", "H03"], 3)
f("C02", "conveyor_line", "Belt slipping at the drive", ["ERR-C04"],
  ["belt slips on the drive pulley", "motor runs but belt speed is low", "squeal on start"],
  "the drive spins but the belt does not move properly",
  "wear", "worn lagging on the drive pulley",
  ["stop and lock out the conveyor", "inspect the drive pulley lagging", "re-lag or replace the pulley lagging", "check belt tension against the spec", "test run loaded"],
  ["tension gauge", "spanner set"], ["pulley lagging"],
  [("increased belt tension", "stressed bearings and the slip returned"), ("replaced the belt", "belt was fine")],
  ["H08", "H02"], 2)
f("C03", "conveyor_line", "Gearbox overheating and noise", [],
  ["gearbox casing very hot", "grinding noise", "oil smell near the drive"],
  "the gearbox is very hot and grinding",
  "lubrication", "low gearbox oil level",
  ["stop and lock out the conveyor", "let the gearbox cool before opening", "check the oil level and condition", "top up with the specified gear oil and look for leaks", "run and confirm casing temperature"],
  ["oil can", "gloves", "infrared thermometer"], ["gear oil"],
  [("replaced the drive motor", "motor was healthy"), ("added general purpose grease", "wrong lubricant for the gearbox")],
  ["H04", "H08", "H02"], 2)
f("C04", "conveyor_line", "Drive overcurrent trips", ["ERR-C12"],
  ["VFD overcurrent fault", "motor runs hot", "fault when the belt is loaded"],
  "the drive trips on overcurrent every time the belt loads up",
  "wear", "seized idler roller dragging on the belt",
  ["stop, lock out and wait for drive discharge", "spin the idler rollers by hand to find the seized one", "replace the seized roller and bearing", "check belt and drive current", "restart and confirm no overcurrent trip"],
  ["multimeter", "clamp meter", "spanner set"], ["idler roller"],
  [("raised the VFD current limit", "motor ran hotter and the drag remained"), ("replaced the VFD", "drive was not at fault")],
  ["H02", "H13", "H08"], 2)
f("C05", "conveyor_line", "Photo-eye false stops", ["ERR-C20"],
  ["conveyor stops with nothing in the way", "photo-eye indicator flickers", "fault more common in the afternoon"],
  "the line keeps stopping for no reason and the sensor flickers",
  "sensor", "dirty and slightly misaligned photo-eye",
  ["stop and lock out the conveyor", "clean the sensor lens and reflector", "realign the sensor and reflector", "check the signal on the diagnostics screen", "restart and watch several cycles"],
  ["lens cloth", "hex key set"], [],
  [("bypassed the sensor", "unsafe: product can pile up and block detection"), ("replaced the PLC input card", "card was fine")],
  ["H08", "H15"], 3)
f("C06", "conveyor_line", "Chain skipping", [],
  ["chain jumps on the sprocket", "clicking noise at the drive", "slack hanging on the return side"],
  "the chain keeps jumping on the sprocket and clicking",
  "wear", "stretched drive chain",
  ["stop and lock out the conveyor", "measure the chain elongation", "replace the stretched chain", "check sprocket tooth wear", "re-tension to spec and lubricate"],
  ["chain gauge", "chain breaker", "spanner set"], ["drive chain"],
  [("tightened the chain tensioner to the limit", "chain still jumped and wore the sprocket"), ("lubricated the chain only", "stretch was the cause")],
  ["H08", "H03"], 1)
f("C07", "conveyor_line", "E-stop pull cord trips randomly", ["ERR-C31"],
  ["line stops at random with an e-stop fault", "pull cord switch resets by itself", "fault after washdown"],
  "the line randomly drops out on an e-stop fault",
  "electrical", "water ingress and corroded terminals in the pull cord switch",
  ["lock out and prove dead", "open the pull cord switch box and dry it out", "replace corroded terminals and the switch seal", "test the e-stop circuit end to end", "get supervisor sign-off before returning to production"],
  ["multimeter", "screwdriver set", "dry cloth"], ["terminal block", "switch box seal"],
  [("jumpered across the e-stop circuit", "removes a life-safety function"), ("replaced the safety relay", "relay was fine")],
  ["H15", "H02", "H08"], 2)
f("C08", "conveyor_line", "Roller bearing squeal", [],
  ["high pitched squeal from one roller", "roller feels rough when spun", "heat at the roller end"],
  "one roller is squealing and runs rough",
  "lubrication", "failed roller bearing with no grease",
  ["stop and lock out the conveyor", "identify the squealing roller by spinning by hand", "replace the failed bearing", "grease all bearings to schedule", "restart and listen"],
  ["grease gun", "bearing puller"], ["roller bearing", "grease"],
  [("sprayed lubricant onto the running roller", "unsafe near a moving belt and does not repair the bearing"), ("slackened the belt", "noise stayed")],
  ["H08", "H03"], 2)

# ---------------- AIR COMPRESSOR ----------------
f("A01", "air_compressor", "Fails to reach pressure", [],
  ["pressure stalls below the cut-out", "compressor runs continuously", "air demand cannot be met"],
  "the compressor runs and runs but never reaches full pressure",
  "clogging", "blocked air intake filter",
  ["stop and lock out the compressor", "vent the receiver to zero", "remove and inspect the intake filter", "replace the filter element", "restart and check the build-up time"],
  ["spanner set", "torch"], ["intake filter element"],
  [("raised the pressure switch setpoint", "overpressure risk without fixing the intake"), ("replaced the compressor element", "element was fine")],
  ["H05", "H14", "H02"], 3)
f("A02", "air_compressor", "Excess oil carryover", [],
  ["oil in the air lines", "oil mist at the outlet", "oil consumption high"],
  "there is oil coming through in the air lines",
  "wear", "failed oil separator element",
  ["stop and lock out the compressor", "vent to zero pressure", "let the oil cool", "remove the separator cover and old element", "fit a new separator element and top up oil"],
  ["filter wrench", "drain tray", "gloves"], ["oil separator element", "compressor oil"],
  [("replaced the aftercooler", "cooler was not the source"), ("drained the air receiver more often", "oil came from the separator")],
  ["H05", "H04", "H02"], 2)
f("A03", "air_compressor", "High discharge temperature", ["ERR-A15"],
  ["discharge temperature alarm", "hot air at the outlet", "fan running but air feels weak"],
  "the compressor overheats and trips on high discharge temperature",
  "thermal", "fouled cooler fins restricting airflow",
  ["stop and lock out the compressor", "let it cool", "clean the cooler fins with low-pressure air from the clean side", "check the cooling fan direction", "restart and watch temperature"],
  ["brush", "air gun", "infrared thermometer"], [],
  [("added more oil", "level was correct and overfilling causes carryover"), ("replaced the temperature sensor", "sensor reading was accurate")],
  ["H04", "H05", "H02"], 2)
f("A04", "air_compressor", "Short cycling", [],
  ["compressor starts and stops every few seconds", "pressure drops quickly after unloading", "hissing at the unloader"],
  "the compressor keeps starting and stopping every few seconds",
  "seal_leak", "leaking minimum pressure check valve",
  ["stop and lock out the compressor", "vent to zero pressure", "remove the check valve and inspect the seat", "replace the valve or seal", "restart and watch the cycle"],
  ["spanner set", "seal pick"], ["check valve kit"],
  [("replaced the pressure switch", "switch was fine"), ("fitted a bigger receiver", "did not stop the leak")],
  ["H05", "H02"], 2)
f("A05", "air_compressor", "Water in the air lines", [],
  ["water drips from air tools", "moisture in the filters", "rust marks in pipework"],
  "there is water coming out of the air tools",
  "clogging", "blocked automatic drain valve on the receiver",
  ["stop and lock out the compressor", "vent the receiver to zero", "clean or replace the auto drain valve", "manually drain and check for rust", "restart and verify the drain cycles"],
  ["spanner set", "bucket"], ["auto drain valve"],
  [("replaced the air dryer", "dryer was working, the drain was blocked"), ("added an extra filter", "did not remove the water")],
  ["H05", "H09"], 2)
f("A06", "air_compressor", "Motor trips on overload", ["ERR-A22"],
  ["motor overload trips under load", "motor hums", "lights flicker when it starts"],
  "the motor trips out on overload and hums when it starts",
  "electrical", "loose power terminal causing single phasing",
  ["lock out and prove dead", "inspect the motor and starter terminals", "tighten or replace the burned terminal", "check current balance on all phases", "restart and monitor"],
  ["multimeter", "clamp meter", "torque screwdriver"], ["terminal block"],
  [("replaced the motor", "motor was fine"), ("increased the overload setting", "damages the motor over time")],
  ["H02", "H13"], 1)
f("A07", "air_compressor", "Safety valve popping", [],
  ["safety valve lifts and vents", "pressure goes above cut-out", "loud blast of air"],
  "the safety valve keeps blowing off",
  "sensor", "faulty pressure switch so the compressor never unloads",
  ["stop and lock out the compressor", "vent to zero pressure", "test the pressure switch against a calibrated gauge", "replace the faulty pressure switch", "restart and confirm it unloads at cut-out"],
  ["calibrated gauge", "spanner set"], ["pressure switch"],
  [("plugged the safety valve to stop the noise", "extremely dangerous: removes overpressure protection"), ("fitted a higher rated safety valve", "hides a control failure and is unsafe")],
  ["H14", "H05", "H02"], 2)
f("A08", "air_compressor", "Belt squeal and vibration", [],
  ["squeal when starting", "visible vibration at the belt guard", "belt dust on the guard"],
  "the belt squeals and the whole guard shakes",
  "wear", "worn and loose drive belt",
  ["stop and lock out the compressor", "remove the belt guard", "check belt wear and tension", "replace the belt and re-tension", "refit the guard and test"],
  ["tension gauge", "spanner set"], ["drive belt"],
  [("sprayed belt dressing on the running belt", "unsafe near rotating parts and only a temporary fix"), ("replaced the motor bearings", "bearings were fine")],
  ["H06", "H02"], 1)


def main():
    out = pathlib.Path(__file__).parent
    faults = []
    for (fid, mt, name, codes, sym, lay, fam, rc, fixes, tools, parts, wrong, hz, w) in F:
        faults.append({
            "fault_id": fid, "machine_type": mt, "name": name, "error_codes": codes,
            "symptoms": sym, "lay_description": lay, "cause_family": fam,
            "root_cause": rc, "valid_fixes": fixes, "tools": tools, "parts": parts,
            "wrong_fixes": [{"action": a, "why_it_fails": b} for a, b in wrong],
            # explicit "none" is represented by an empty list plus hazard_note
            "hazards": hz, "hazard_note": "none identified" if not hz else "",
            "recurrence_weight": w,
        })
    hazards = [{"hazard_id": k, "name": v[0], "required_control": v[1], "keywords": v[2]} for k, v in HAZARDS.items()]
    (out / "faults.json").write_text(json.dumps(faults, indent=2, ensure_ascii=False) + "\n")
    (out / "hazards.json").write_text(json.dumps(hazards, indent=2, ensure_ascii=False) + "\n")
    print(len(faults), "faults,", len(hazards), "hazards")


if __name__ == "__main__":
    main()
