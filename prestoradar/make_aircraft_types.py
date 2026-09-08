"""
Generate prestoradar/aircraft_types_data.py -- an ICAO-type-designator ->
"Manufacturer Model" table for the tap-to-inspect panel, from ICAO Doc 8643
(the aircraft type designator standard), as republished by the OpenSky Network.

Desktop tool, standard library only. Same build-time pattern as
make_basemap.py -> basemap_data.py: fetch once, boil down to a compact dict,
write it out beside basemap_data.py, let deploy.sh carry it.

Typical use:

    python3 prestoradar/make_aircraft_types.py            # airliner set (~500)
    python3 prestoradar/make_aircraft_types.py --all      # every designator (~2.6k)
    python3 prestoradar/make_aircraft_types.py --keep seen.txt   # + a capture
    python3 prestoradar/make_aircraft_types.py --source doc8643.csv

The feed's own `desc` always wins at read time (see plane.Plane.type_description);
this table is the fallback for when adsb.lol omits it -- military, some GA,
blocked airframes. So the default keeps only the multi-engine fixed-wing set
(airliners, regionals, most bizjets) plus _KEEP and _OVERRIDES; --all emits the
lot for the desktop / Tidbyt lookup service. Doc 8643 lists several ModelFullName
variants per designator (corporate-shuttle names, parenthetical aliases); the
picker drops those and takes the shortest plain one, so B738 -> "737-800", not
"BBJ2". _OVERRIDES pins the common airliners to the exact string adsb.lol's
`desc` uses, so a type reads the same whichever source it came from.
"""

import argparse
import csv
import os
import re
import sys
import urllib.request

SOURCE_URL = "https://s3.opensky-network.org/data-samples/metadata/doc8643AircraftTypes.csv"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aircraft_types_data.py")

# ModelFullName spellings that name a corporate/VIP shuttle or an alias, not the
# aircraft -- skipped when a plainer row for the same designator exists.
_NOISE = re.compile(r"\bBBJ\b|\bACJ\b|\(|Prestige|Business Jet|Srs\.|\bVIP\b", re.I)

# Manufacturer codes that shouldn't be title-cased.
_MFR_ACRONYM = {"ATR", "BAE", "COMAC", "MD", "MDHI", "HAL", "IAI", "PZL", "CASA"}

# The maker of the original design, preferred over foreign licensees / military
# rebadges when several rows share a designator (C172 -> Cessna, not the FMA
# licence-built "P-172"; PC12 -> Pilatus, not the USAF "U-28").
_MAJOR = {
    "AIRBUS", "BOEING", "EMBRAER", "BOMBARDIER", "CANADAIR", "DE HAVILLAND CANADA",
    "ATR", "MCDONNELL DOUGLAS", "DOUGLAS", "LOCKHEED", "FOKKER", "SAAB", "BAE",
    "BAE SYSTEMS", "BRITISH AEROSPACE", "AVRO", "CESSNA", "PIPER", "BEECH",
    "RAYTHEON", "TEXTRON AVIATION", "GULFSTREAM AEROSPACE", "DASSAULT", "LEARJET",
    "PILATUS", "DAHER", "SOCATA", "CIRRUS DESIGN", "DIAMOND", "MOONEY",
    "BELL", "AIRBUS HELICOPTERS", "EUROCOPTER", "SIKORSKY", "ROBINSON HELICOPTER",
    "LEONARDO", "AGUSTAWESTLAND", "MBB", "MIL",
}

# Non-twin-jet designators common enough overhead to keep in the default cut.
_KEEP = {
    "C208", "C210", "C172", "C182", "C206", "PC12", "PC24", "TBM7", "TBM8",
    "TBM9", "B350", "BE20", "B190", "SR20", "SR22", "DA40", "DA42", "DA62",
    "M20P", "M20T", "P28A", "P32R", "PA46", "PAY2", "PAY3", "EPIC", "DHC6",
    "R44", "R66", "R22", "EC35", "EC45", "AS50", "B06", "B407", "B429", "S76",
    "H60", "C130", "C30J", "A400", "P8",
}

# designator -> exact string, applied last. Spelled the adsb.lol `desc` way.
_OVERRIDES = {
    "A19N": "Airbus A319neo", "A20N": "Airbus A320neo", "A21N": "Airbus A321neo",
    "A319": "Airbus A319", "A320": "Airbus A320", "A321": "Airbus A321",
    "A332": "Airbus A330-200", "A333": "Airbus A330-300",
    "A338": "Airbus A330-800", "A339": "Airbus A330-900",
    "A342": "Airbus A340-200", "A343": "Airbus A340-300",
    "A345": "Airbus A340-500", "A346": "Airbus A340-600",
    "A359": "Airbus A350-900", "A35K": "Airbus A350-1000", "A388": "Airbus A380-800",
    "B712": "Boeing 717-200", "B733": "Boeing 737-300", "B734": "Boeing 737-400",
    "B735": "Boeing 737-500", "B736": "Boeing 737-600", "B737": "Boeing 737-700",
    "B738": "Boeing 737-800", "B739": "Boeing 737-900",
    "B37M": "Boeing 737 MAX 7", "B38M": "Boeing 737 MAX 8",
    "B39M": "Boeing 737 MAX 9", "B3XM": "Boeing 737 MAX 10",
    "B744": "Boeing 747-400", "B748": "Boeing 747-8",
    "B752": "Boeing 757-200", "B753": "Boeing 757-300",
    "B762": "Boeing 767-200", "B763": "Boeing 767-300", "B764": "Boeing 767-400",
    "B772": "Boeing 777-200", "B77L": "Boeing 777-200LR", "B77W": "Boeing 777-300ER",
    "B778": "Boeing 777-8", "B779": "Boeing 777-9",
    "B788": "Boeing 787-8", "B789": "Boeing 787-9", "B78X": "Boeing 787-10",
    "E170": "Embraer 170", "E75S": "Embraer 175", "E75L": "Embraer 175",
    "E190": "Embraer 190", "E195": "Embraer 195",
    "E290": "Embraer E190-E2", "E295": "Embraer E195-E2",
    "BCS1": "Airbus A220-100", "BCS3": "Airbus A220-300",
    "CRJ2": "Bombardier CRJ-200", "CRJ7": "Bombardier CRJ-700",
    "CRJ9": "Bombardier CRJ-900", "CRJX": "Bombardier CRJ-1000",
    "AT72": "ATR 72", "AT75": "ATR 72-500", "AT76": "ATR 72-600",
    "AT43": "ATR 42", "AT45": "ATR 42-500", "AT46": "ATR 42-600",
    "DH8D": "Bombardier Dash 8 Q400", "DH8C": "Bombardier Dash 8 Q300",
    "MD11": "McDonnell Douglas MD-11",
    "MD88": "McDonnell Douglas MD-88", "MD90": "McDonnell Douglas MD-90",
    "GLEX": "Bombardier Global Express", "GL5T": "Bombardier Global 5000",
    "GL7T": "Bombardier Global 7500",
    "RJ85": "Avro RJ85", "RJ1H": "Avro RJ100",
    "B461": "BAe 146-100", "B462": "BAe 146-200", "B463": "BAe 146-300",
}


def _load_rows(source):
    if source:
        with open(source, newline="", encoding="utf-8") as fh:
            return list(csv.reader(fh))
    with urllib.request.urlopen(SOURCE_URL, timeout=30) as resp:
        return list(csv.reader(resp.read().decode("utf-8").splitlines()))


def _clean_mfr(code):
    code = code.strip()
    if code in _MFR_ACRONYM:
        return code
    return " ".join(w if w in _MFR_ACRONYM else w.capitalize() for w in code.split())


def _name(mfr_code, model):
    mfr = _clean_mfr(mfr_code)
    model = " ".join(model.split())
    if mfr and model.split()[:1] and model.split()[0].lower() == mfr.split()[0].lower():
        return model
    return ("%s %s" % (mfr, model)).strip()


def build(source=None, keep_all=False, keep=()):
    """Doc 8643 CSV -> { designator: "Manufacturer Model" }."""
    keep = {c.strip().upper() for c in keep if c.strip()}
    rows = _load_rows(source)
    head = rows[0]
    ci = {name: i for i, name in enumerate(head)}
    by_desig = {}   # designator -> list of sort-keys, each ending in the name
    for r in rows[1:]:
        if len(r) < len(head):
            continue
        desig = r[ci["Designator"]].strip().upper()
        mfr_code = r[ci["ManufacturerCode"]].strip()
        model = r[ci["ModelFullName"]].strip()
        if not re.match(r"^[A-Z0-9]{2,4}$", desig) or not model:
            continue
        if not keep_all:
            twin_wing = (r[ci["EngineType"]] in ("Jet", "Turboprop/Turboshaft")
                         and r[ci["EngineCount"]] in ("2", "3", "4")
                         and r[ci["AircraftDescription"]] == "LandPlane")
            if not (twin_wing or desig in _KEEP or desig in _OVERRIDES):
                continue
        digits = re.sub(r"^[A-Z]+", "", desig)          # C172 -> 172, AT76 -> 76
        name = _name(mfr_code, model)
        key = (
            bool(digits) and digits not in model,       # model names its family
            mfr_code.upper() not in _MAJOR,             # from the original maker
            bool(_NOISE.search(model)),                  # not a VIP/alias spelling
            len(name), name,                             # then just the shortest
        )
        by_desig.setdefault(desig, []).append(key)

    table = {desig: min(keys)[-1] for desig, keys in by_desig.items()}
    table.update(_OVERRIDES)
    return dict(sorted(table.items()))


def write(table, out=OUT):
    with open(out, "w", encoding="utf-8") as fh:
        fh.write('"""Generated by prestoradar/make_aircraft_types.py -- do not edit by hand.\n\n')
        fh.write("Source : ICAO Doc 8643 via OpenSky Network, plus make_aircraft_types.py's _OVERRIDES\n")
        fh.write("Key    : ICAO aircraft type designator -- the feed's `t` field, e.g. B738\n")
        fh.write("Count  : %d designators\n" % len(table))
        fh.write('"""\n\n')
        fh.write("TYPES = {\n")
        for desig, name in table.items():
            fh.write("    %r: %r,\n" % (desig, name))
        fh.write("}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", metavar="PATH",
                    help="local Doc 8643 CSV instead of fetching from OpenSky")
    ap.add_argument("--all", action="store_true",
                    help="every designator, not just the multi-engine fixed-wing set")
    ap.add_argument("--keep", metavar="FILE",
                    help="also keep designators listed one-per-line in FILE")
    args = ap.parse_args()

    keep = ()
    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = fh.read().split()
    table = build(args.source, keep_all=args.all, keep=keep)
    write(table)
    print("wrote %s: %d designators, %.1f KB"
          % (OUT, len(table), os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
