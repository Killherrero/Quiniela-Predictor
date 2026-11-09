import csv
import io
import re
import unicodedata
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.request import urlopen

ALIASES = {
    "ath bilbao": "athletic club",
    "ath madrid": "atletico de madrid",
    "atletico madrid": "atletico de madrid",
    "sociedad": "real sociedad",
    "vallecano": "rayo vallecano",
    "oviedo": "real oviedo",
    "valladolid": "real valladolid",
    "gijon": "sporting de gijon",
    "sp gijon": "sporting de gijon",
    "santander": "racing de santander",
    "la coruna": "deportivo de la coruna",
}


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError(f"Se requiere zona horaria: {value}")
    return parsed


def normalized(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        char for char in value if not unicodedata.combining(char)
    )))


def match_key(row):
    competition = row["competition"].strip().upper()
    home, away = (normalized(row[field]) for field in ("home", "away"))
    home, away = ALIASES.get(home, home), ALIASES.get(away, away)
    if not competition or not home or not away or home == away:
        raise ValueError("Competición o equipos inválidos")
    return competition, home, away


def read_results(paths):
    records = {}
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                if not all(row.get(field) for field in
                           ("date", "available_at", "source", "home_goals", "away_goals")):
                    continue
                day = datetime.strptime(row["date"], "%Y-%m-%d").date()
                available = instant(row["available_at"])
                if available.date() < day:
                    raise ValueError("Disponibilidad anterior al partido")
                key = match_key(row)
                goals = int(row["home_goals"]), int(row["away_goals"])
                if min(goals) < 0:
                    raise ValueError("Goles negativos")
                identity = day, key
                estimated = row.get("availability_kind") != "verified"
                if identity in records:
                    previous = records[identity]
                    if previous[2] != goals:
                        raise ValueError(f"Resultados contradictorios: {identity}")
                    if previous[3] != estimated:
                        if estimated:
                            continue
                    else:
                        available = max(previous[0], available)
                records[identity] = available, key, goals, estimated
    return [records[identity] for identity in sorted(records)]


def import_football_data(season, division, output, local_csv=None):
    if not re.fullmatch(r"\d{4}", season) or (int(season[:2]) + 1) % 100 != int(season[2:]):
        raise ValueError("Temporada inválida")
    if division not in {"SP1", "SP2"}:
        raise ValueError("División inválida")
    source = f"https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"
    if local_csv:
        raw = Path(local_csv).read_bytes()
    else:
        with urlopen(source, timeout=20) as response:
            raw = response.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
    reader = csv.DictReader(io.StringIO(content))
    if not {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"} <= set(
        reader.fieldnames or ()
    ):
        raise ValueError("Cabecera Football-Data no reconocida")
    rows = []
    for row in reader:
        if not any(row.values()):
            continue
        if row["Div"] != division:
            raise ValueError("División distinta de la solicitada")
        if row["FTHG"] in (None, "") or row["FTAG"] in (None, ""):
            continue
        fmt = "%d/%m/%Y" if len(row["Date"].split("/")[-1]) == 4 else "%d/%m/%y"
        day = datetime.strptime(row["Date"], fmt).date()
        year = 2000 + int(season[:2])
        if not datetime(year, 7, 1).date() <= day < datetime(year + 1, 7, 1).date():
            raise ValueError(f"Fecha fuera de la temporada: {day}")
        available = datetime.combine(day + timedelta(days=2), time()).isoformat() + "+00:00"
        goals = int(row["FTHG"]), int(row["FTAG"])
        record = {"date": day.isoformat(), "competition": division,
                  "home": row["HomeTeam"], "away": row["AwayTeam"],
                  "home_goals": goals[0], "away_goals": goals[1],
                  "available_at": available, "source": source,
                  "availability_kind": "estimated_conservative"}
        match_key(record)
        if min(goals) < 0:
            raise ValueError("Goles negativos")
        rows.append(record)
    if not rows:
        raise ValueError("El CSV no contiene resultados completos")
    with open(output, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
