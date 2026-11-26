"""Separator – UCS categories and names.

The category tables (UCS CatIDs, AudioSet mapping, German terms, fine sub-labels) and everything
that turns detected categories into a UCS file name, FX name and description.
"""

import os
import re

import config as cfg
from wavio import tc


# Categories: (UCS CatID, Category, SubCategory, description for CLAP in English).
# Several rows with the same CatID are allowed (= several descriptions).
LABELS = [
    ("SPEECH",    "",               "",          "a person speaking"),
    ("SPEECH",    "",               "",          "people talking in a conversation"),
    ("SPEECH",    "",               "",          "a man talking"),
    ("SPEECH",    "",               "",          "a woman talking"),
    ("SPEECH",    "",               "",          "someone shouting words"),
    ("AMBRoom",   "AMBIENCE",       "ROOM TONE", "quiet indoor room tone"),
    ("AMBUrbn",   "AMBIENCE",       "URBAN",     "outdoor ambience with distant city noise"),
    ("AMBTraf",   "AMBIENCE",       "TRAFFIC",   "city traffic noise"),
    ("VEHBy",     "VEHICLES",       "BY",        "a car driving by"),
    ("VEHCar",    "VEHICLES",       "CAR",       "a car engine idling or starting"),
    ("VEHDoor",   "VEHICLES",       "DOOR",      "a car door opening or slamming"),
    ("DOORMisc",  "DOORS",          "MISC",      "a door opening or closing"),
    ("DOORKnck",  "DOORS",          "KNOCK",     "knocking on a door"),
    ("FEETHmn",   "FOOTSTEPS",      "HUMAN",     "footsteps walking"),
    ("CLOTHMvmt", "CLOTH",          "MOVEMENT",  "clothes rustling and body movement"),
    ("TOOLHand",  "TOOLS",          "HAND",      "hammering and hand tools"),
    ("FGHTImpt",  "FIGHT",          "IMPACT",    "punches and fighting, bodies hitting"),
    ("WATRSplsh", "WATER",          "SPLASH",    "water splashing, someone swimming"),
    ("WATRFlow",  "WATER",          "FLOW",      "water running from a tap"),
    ("HMNBrth",   "HUMAN",          "BREATH",    "heavy breathing"),
    ("HMNSnor",   "HUMAN",          "SNORE",     "someone sleeping and snoring"),
    ("FOODTware", "FOOD & DRINK",   "TABLEWARE", "dishes and cups clattering"),
    ("OBJMisc",   "OBJECTS",        "MISC",      "objects being handled and put down"),
    ("PAPRHndl",  "PAPER",          "HANDLE",    "paper rustling"),
    ("GLASBrk",   "GLASS",          "BREAK",     "glass breaking"),
    ("COMTelph",  "COMMUNICATIONS", "TELEPHONE", "a telephone ringing"),
    ("BIRDMisc",  "BIRDS",          "MISC",      "birds chirping"),
    ("ANMLDog",   "ANIMALS",        "DOG",       "a dog barking"),
    ("WIND",      "WIND",           "GENERAL",   "wind blowing"),
    ("RAIN",      "RAIN",           "GENERAL",   "rain falling"),
    ("MUSCMisc",  "MUSICAL",        "MISC",      "music playing"),
    ("VOXScrm",   "VOICES",         "SCREAM",    "a person screaming or yelling"),
    ("VOXCry",    "VOICES",         "CRY",       "a person crying or sobbing"),
    ("VOXLaff",   "VOICES",         "LAUGH",     "people laughing"),
    ("VOXEfrt",   "VOICES",         "EFFORT",    "grunts, groans and gasps of effort"),
    ("HMNCough",  "HUMAN",          "COUGH",     "someone coughing or clearing the throat"),
    ("HMNSneez",  "HUMAN",          "SNEEZE",    "someone sneezing"),
    ("HMNSniff",  "HUMAN",          "SNIFF",     "someone sniffing"),
]


VOCAL_CATS = {"VOXScrm", "VOXCry", "VOXLaff", "VOXEfrt"}


# AudioSet class (exact name, lower case) -> CatID. Classes not listed here are ignored.
AUDIOSET_MAP = {
    "SPEECH": ["speech", "male speech, man speaking", "female speech, woman speaking", "child speech, kid speaking",
               "conversation", "narration, monologue", "babbling", "whispering", "speech synthesizer"],
    "VOXScrm": ["screaming", "shout", "yell", "bellow", "children shouting", "whoop", "battle cry"],
    "VOXCry": ["crying, sobbing", "baby cry, infant cry", "whimper", "wail, moan"],
    "VOXLaff": ["laughter", "baby laughter", "giggle", "snicker", "belly laugh", "chuckle, chortle"],
    "VOXEfrt": ["grunt", "groan", "gasp", "pant", "sigh"],
    "HMNCough": ["cough", "throat clearing"],
    "HMNSneez": ["sneeze"],
    "HMNSniff": ["sniff"],
    "HMNBrth": ["breathing", "wheeze"],
    "HMNSnor": ["snoring"],
    "FGHTImpt": ["slap, smack", "whack, thwack"],
    "DOORMisc": ["door", "slam", "sliding door", "cupboard open or close"],
    "DOORKnck": ["knock"],
    "FEETHmn": ["walk, footsteps", "run", "shuffle"],
    "CLOTHMvmt": ["rustle", "zipper (clothing)"],
    "VEHBy": ["car passing by", "vehicle"],
    "VEHCar": ["car", "engine", "idling", "engine starting", "accelerating, revving, vroom", "vehicle horn, car horn, honking",
               "motor vehicle (road)", "truck", "bus", "motorcycle"],
    "AMBTraf": ["traffic noise, roadway noise"],
    "AMBUrbn": ["outside, urban or manmade"],
    "AMBRoom": ["inside, small room", "inside, large room or hall", "inside, public space", "hum", "air conditioning",
                "mechanical fan"],
    "AMBMisc": ["outside, rural or natural"],
    "TOOLHand": ["hammer", "sawing", "drill", "power tool", "tools", "jackhammer", "ratchet, pawl", "filing (rasp)",
                 "sanding"],
    "WATRSplsh": ["splash, splatter", "waves, surf", "slosh"],
    "WATRFlow": ["water", "water tap, faucet", "sink (filling or washing)", "bathtub (filling or washing)",
                 "toilet flush", "stream", "pour", "drip", "fill (with liquid)", "trickle, dribble", "gurgling"],
    "FOODTware": ["dishes, pots, and pans", "cutlery, silverware", "chopping (food)", "frying (food)"],
    "OBJMisc": ["thump, thud", "clatter", "keys jangling", "drawer open or close", "coin (dropping)", "scissors",
                "typing", "computer keyboard"],
    "PAPRHndl": ["crumpling, crinkling", "tearing"],
    "GLASBrk": ["shatter", "breaking", "glass", "chink, clink"],
    "COMTelph": ["telephone", "telephone bell ringing", "ringtone", "telephone dialing, dtmf", "dial tone"],
    "BIRDMisc": ["bird", "bird vocalization, bird call, bird song", "chirp, tweet", "pigeon, dove", "crow", "caw"],
    "ANMLDog": ["dog", "bark", "yip", "howl", "bow-wow", "growling", "whimper (dog)"],
    "WIND": ["wind", "rustling leaves", "wind noise (microphone)"],
    "RAIN": ["rain", "raindrop", "rain on surface", "thunderstorm", "thunder"],
    "MUSCMisc": ["music", "musical instrument"],
}


UCS = {c: (cat, sub) for c, cat, sub, _ in LABELS if cat}


UCS[cfg.FALLBACK_CAT[0]] = cfg.FALLBACK_CAT[1:]


# German terms: used in markers, descriptions and keywords, never in file names
DE_NAMES = {
    "AMBRoom": "Raumton", "AMBUrbn": "Stadt-Atmo", "AMBTraf": "Verkehr", "AMBMisc": "Atmo",
    "VEHBy": "Auto-Vorbeifahrt", "VEHCar": "Automotor", "VEHDoor": "Autotür",
    "DOORMisc": "Tür", "DOORKnck": "Klopfen", "FEETHmn": "Schritte",
    "CLOTHMvmt": "Kleidung/Bewegung", "TOOLHand": "Hämmern/Werkzeug", "FGHTImpt": "Kampf/Schläge",
    "WATRSplsh": "Wasser/Schwimmen", "WATRFlow": "Wasser fließend", "HMNBrth": "Atmen",
    "HMNSnor": "Schnarchen/Schlafen", "FOODTware": "Geschirr", "OBJMisc": "Gegenstände",
    "PAPRHndl": "Papier", "GLASBrk": "Glasbruch", "COMTelph": "Telefon", "BIRDMisc": "Vögel",
    "ANMLDog": "Hund", "WIND": "Wind", "RAIN": "Regen", "MUSCMisc": "Musik", "MISC": "unklar",
    "VOXScrm": "Schreie", "VOXCry": "Weinen", "VOXLaff": "Lachen", "VOXEfrt": "Stöhnen/Keuchen",
    "HMNCough": "Husten/Räuspern", "HMNSneez": "Niesen", "HMNSniff": "Schniefen",
}


def _clean(s, maxlen=40):
    s = _ascii(str(s)).decode("ascii")
    s = re.sub(r"[^A-Za-z0-9 .()&+-]", "", s.replace("_", "-"))
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if len(s) > maxlen:
        cut = s[:maxlen + 1].rsplit(" ", 1)[0] if " " in s[:maxlen + 1] else s[:maxlen]
        s = cut
    return s[:maxlen].strip(" .-")


def _readable(catid):
    cat, sub = UCS.get(catid, ("", ""))
    if cat == "AMBIENCE":
        return sub.title()
    nice = {"VOXScrm": "Screams", "VOXCry": "Crying", "VOXLaff": "Laughter", "VOXEfrt": "Vocal Efforts",
            "HMNCough": "Coughs", "HMNSneez": "Sneezes", "HMNSniff": "Sniffs", "HMNBrth": "Breaths",
            "HMNSnor": "Snores"}
    if catid in nice:
        return nice[catid]
    return cat.title() if sub in ("MISC", "GENERAL", "HUMAN", "HAND", "IMPACT", "") else \
        f"{cat.title()} {sub.title()}"


def ranking(runs):
    """CatIDs ordered by weight; single sounds count 3x as much as ambiences"""
    w = {}
    for r in runs:
        for s, e, l, _ in r["events"]:
            if l in UCS:
                w[l] = w.get(l, 0) + (e - s) * (1 if l.startswith("AMB") else 3)
    return [k for k, _ in sorted(w.items(), key=lambda kv: -kv[1])]


def ucs_name(rank, meta, creator, part=""):
    catid = rank[0] if rank else cfg.FALLBACK_CAT[0]
    names = []
    for c in rank:
        n = _readable(c)
        if n and n not in names:
            names.append(n)
        if len(names) == 3:
            break
    fx = ("Wild Track " if meta["wild"] else "") + (" ".join(names) or "Set Ambience")
    source = _clean(meta.get("project") or cfg.SOURCE_ID, 24).replace(" ", "")
    if meta.get("scene") or meta.get("take"):
        notes = "-".join(p for p in (f"Sc{meta.get('scene', '')}" if meta.get("scene") else "",
                                     f"Tk{meta.get('take', '')}" if meta.get("take") else "") if p)
    else:
        notes = os.path.splitext(meta["file"])[0]
    notes = _clean(notes, 30).replace(" ", "") + part
    return catid, f"{catid}_{_clean(fx)}_{_clean(creator, 12) or 'NN'}_{source}_{notes}", _clean(fx)


def description(rank, meta, kept_names, info):
    parts = ["Set recording, dialogue removed."]
    if rank:
        parts.append("Contains: " + ", ".join(_readable(c) for c in rank) + ".")
        parts.append("Inhalt: " + ", ".join(DE_NAMES.get(c, c) for c in rank) + ".")
    info_bits = []
    for k, lab in (("project", "Project"), ("scene", "Scene"), ("take", "Take"), ("tape", "Roll")):
        if meta.get(k):
            info_bits.append(f"{lab} {meta[k]}")
    if meta.get("circled", "").upper() == "TRUE":
        info_bits.append("Circled")
    if meta["wild"]:
        info_bits.append("Wild track")
    if meta.get("note"):
        info_bits.append(f"Note: {meta['note']}")
    for f in meta["free"]:
        if f and f not in info_bits:
            info_bits.append(f)
    if kept_names:
        info_bits.append("Tracks: " + ", ".join(kept_names))
    if meta.get("date"):
        info_bits.append(f"Rec {meta['date']} {meta.get('time', '')}".strip())
    info_bits.append(f"Orig TC {tc(info.time_ref / info.sr)}")
    if meta.get("originator"):
        info_bits.append(f"Recorder {meta['originator']}")
    info_bits.append(f"Original file {meta['file']}")
    return " ".join(parts) + " " + " | ".join(info_bits)


def _ascii(s):
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"),
                 ("Ü", "Ue"), ("ß", "ss")):
        s = s.replace(a, b)
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore")


# Fine sub-categories per family for project sausages: (CatID, Category, SubCategory, FX name, description for CLAP)
SUBLABELS = {
    "DOOR": [
        ("DOORWood", "DOORS", "WOOD", "Wooden Doors", "a wooden door opening or closing"),
        ("DOORMetl", "DOORS", "METAL", "Metal Doors", "a heavy metal door slamming"),
        ("DOORGlas", "DOORS", "GLASS", "Glass Doors", "a glass door opening or closing"),
        ("DOORCreak", "DOORS", "CREAK", "Creaking Doors", "a creaking squeaky door"),
        ("DOORSlid", "DOORS", "SLIDING", "Sliding Doors", "a sliding door"),
        ("DOORHdwr", "DOORS", "HARDWARE", "Door Handles and Locks", "a door handle and a key turning in a lock"),
        ("DOORKnck", "DOORS", "KNOCK", "Door Knocks", "knocking on a door"),
    ],
    "FEET": [
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Wood", "footsteps on a wooden floor"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Concrete", "footsteps on concrete"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Gravel", "footsteps on gravel"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Carpet", "soft footsteps on carpet"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Heels", "high heels walking"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Stairs", "footsteps on stairs"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Running", "someone running"),
    ],
    "AMB": [
        ("AMBRoom", "AMBIENCE", "ROOM TONE", "Room Tone", "quiet indoor room tone"),
        ("AMBOffc", "AMBIENCE", "OFFICE", "Office", "office ambience"),
        ("AMBRest", "AMBIENCE", "RESTAURANT", "Restaurant", "restaurant ambience with dishes"),
        ("AMBUrbn", "AMBIENCE", "URBAN", "City", "city ambience"),
        ("AMBTraf", "AMBIENCE", "TRAFFIC", "Traffic", "street traffic"),
        ("AMBSubn", "AMBIENCE", "SUBURBAN", "Suburban", "quiet suburban street"),
        ("AMBPark", "AMBIENCE", "PARK", "Park", "park with birds and distant voices"),
        ("AMBForst", "AMBIENCE", "FOREST", "Forest", "forest ambience"),
        ("AMBRurl", "AMBIENCE", "RURAL", "Countryside", "countryside ambience"),
        ("AMBInd", "AMBIENCE", "INDUSTRIAL", "Industrial", "industrial machinery hum"),
    ],
    "VEH": [
        ("VEHCar", "VEHICLES", "CAR", "Car Engine", "a car engine idling or starting"),
        ("VEHBy", "VEHICLES", "BY", "Car Pass By", "a car driving by"),
        ("VEHDoor", "VEHICLES", "DOOR", "Car Doors", "a car door opening or slamming"),
        ("VEHHorn", "VEHICLES", "HORN", "Car Horns", "a car horn"),
        ("VEHTruck", "VEHICLES", "TRUCK", "Trucks", "a truck"),
        ("VEHMoto", "VEHICLES", "MOTORCYCLE", "Motorcycles", "a motorcycle"),
        ("VEHBus", "VEHICLES", "BUS", "Buses", "a bus"),
    ],
    "CLOTH": [
        ("CLOTHMvmt", "CLOTH", "MOVEMENT", "Cloth Movement", "clothes rustling and body movement"),
        ("CLOTHHndl", "CLOTH", "HANDLE", "Cloth Handling", "handling fabric"),
        ("CLOTHRip", "CLOTH", "RIP", "Cloth Rips", "fabric ripping"),
        ("CLOTHFlp", "CLOTH", "FLAP", "Cloth Flaps", "fabric flapping"),
    ],
    "FGHT": [
        ("FGHTImpt", "FIGHT", "IMPACT", "Punches", "punches and hits"),
        ("FGHTBf", "FIGHT", "BODYFALL", "Bodyfalls", "a body falling to the ground"),
        ("FGHTGrab", "FIGHT", "GRAB", "Grabs", "grabbing and wrestling"),
        ("FGHTClth", "FIGHT", "CLOTH", "Fight Cloth", "clothes during a fight"),
    ],
    "WATR": [
        ("WATRSplsh", "WATER", "SPLASH", "Water Splashes", "water splashing, someone swimming"),
        ("WATRFlow", "WATER", "FLOW", "Water Flow", "water running"),
        ("WATRPour", "WATER", "POUR", "Water Pour", "water being poured"),
        ("WATRDrip", "WATER", "DRIP", "Drips", "water dripping"),
        ("WATRPlmb", "WATER", "PLUMBING", "Plumbing", "a toilet flush and pipes"),
    ],
    "HMN": [
        ("HMNBrth", "HUMAN", "BREATH", "Breaths", "heavy breathing"),
        ("HMNSnor", "HUMAN", "SNORE", "Snores", "someone sleeping and snoring"),
        ("HMNCough", "HUMAN", "COUGH", "Coughs", "someone coughing"),
        ("HMNSniff", "HUMAN", "SNIFF", "Sniffs", "someone sniffing"),
    ],
    "FOOD": [
        ("FOODTware", "FOOD & DRINK", "TABLEWARE", "Tableware", "plates and cutlery on a table"),
        ("FOODKware", "FOOD & DRINK", "KITCHENWARE", "Kitchenware", "pots and pans in a kitchen"),
        ("FOODCook", "FOOD & DRINK", "COOKING", "Cooking", "food sizzling in a pan"),
        ("FOODEat", "FOOD & DRINK", "EATING", "Eating", "someone eating"),
        ("FOODDrnk", "FOOD & DRINK", "DRINKING", "Drinking", "someone drinking"),
        ("FOODPour", "FOOD & DRINK", "POUR", "Pouring Drinks", "pouring a drink into a glass"),
    ],
    "OBJ": [
        ("OBJKey", "OBJECTS", "KEYS", "Keys", "a bunch of keys jingling"),
        ("OBJBag", "OBJECTS", "BAG", "Bags", "a bag being handled"),
        ("OBJFurn", "OBJECTS", "FURNITURE", "Furniture", "a chair or table being moved"),
        ("OBJHsehld", "OBJECTS", "HOUSEHOLD", "Household Objects", "household objects being handled"),
        ("OBJMisc", "OBJECTS", "MISC", "Objects", "objects being handled and put down"),
    ],
}


DE_SUB = {"Wooden Doors": "Holztüren", "Metal Doors": "Metalltüren", "Glass Doors": "Glastüren",
          "Creaking Doors": "knarzende Türen", "Sliding Doors": "Schiebetüren",
          "Door Handles and Locks": "Türklinken und Schlösser", "Door Knocks": "Klopfen",
          "Footsteps Wood": "Schritte Holz", "Footsteps Concrete": "Schritte Beton",
          "Footsteps Gravel": "Schritte Kies", "Footsteps Carpet": "Schritte Teppich",
          "Footsteps Heels": "Schritte Absätze", "Footsteps Stairs": "Schritte Stiegen",
          "Footsteps Running": "Laufen", "Room Tone": "Raumton", "Office": "Büro-Atmo",
          "Restaurant": "Restaurant-Atmo", "City": "Stadt-Atmo", "Traffic": "Verkehr",
          "Suburban": "Vorstadt-Atmo", "Park": "Park-Atmo", "Forest": "Wald-Atmo",
          "Countryside": "Land-Atmo", "Industrial": "Industrie-Atmo"}


def family(catid):
    for k in SUBLABELS:
        if catid.startswith(k):
            return k
    return catid
