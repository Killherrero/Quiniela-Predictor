import argparse
import json
import math
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from fuentes import instant, match_key, read_results, import_football_data

SIGNS = "1X2"
COST = Decimal("1.50")
CATEGORIES = (*map(str, range(10, 15)), "15")
BANDS = "012M"


def triplet(values, *, odds=False):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("Se requieren tres valores en orden 1/X/2")
    values = [float(value) for value in values]
    if any(not math.isfinite(value) or value <= 0 or (odds and value <= 1)
           for value in values):
        raise ValueError("Terna de mercado inválida")
    if odds:
        values = [1 / value for value in values]
    return tuple(value / sum(values) for value in values)


def poisson(rate):
    values = [math.exp(-rate)]
    for goals in range(1, 30):
        values.append(values[-1] * rate / goals)
    return [value / sum(values) for value in values]


def team_model(match, history):
    competition, home, away = match_key(match)
    local = [goals for key, goals in history if key[:2] == (competition, home)][-12:]
    visitor = [goals for key, goals in history
               if (key[0], key[2]) == (competition, away)][-12:]
    if min(len(local), len(visitor)) < 5:
        return None

    def rate(first, second, prior):
        return (sum(first) + sum(second) + 5 * prior) / (len(first) + len(second) + 5)

    home_goals = poisson(rate([h for h, _ in local], [h for h, _ in visitor], 1.4))
    away_goals = poisson(rate([a for _, a in local], [a for _, a in visitor], 1.1))
    signs = [0.0] * 3
    for h, ph in enumerate(home_goals):
        for a, pa in enumerate(away_goals):
            signs[0 if h > a else 1 if h == a else 2] += ph * pa
    bands = [(*distribution[:3], sum(distribution[3:]))
             for distribution in (home_goals, away_goals)]
    pleno = "".join(BANDS[max(range(4), key=distribution.__getitem__)]
                    for distribution in bands)
    return tuple(signs), pleno


def market(match, as_of):
    eligible = []
    for snapshot in match.get("market", []):
        if not snapshot.get("captured_at") or not snapshot.get("source"):
            continue
        captured = instant(snapshot["captured_at"])
        if captured < as_of:
            eligible.append((captured, snapshot))
    if not eligible:
        return None
    _, snapshot = max(eligible, key=lambda pair: pair[0])
    return triplet(snapshot["odds"], odds=True) if "odds" in snapshot else triplet(
        snapshot["probabilities"]
    )


def top_columns(probabilities, limit):
    candidates = [(1.0, "")]
    for row in probabilities:
        candidates = sorted(((score * probability, column + sign)
                             for score, column in candidates
                             for sign, probability in zip(SIGNS, row)),
                            key=lambda item: (-item[0], item[1]))[:limit]
    return [column for _, column in candidates]


def coverage(first, second, probabilities):
    # Joint distribution of the two hit counts, capped at 10.
    distribution = {(0, 0): 1.0}
    for a, b, row in zip(first, second, probabilities, strict=True):
        next_distribution = defaultdict(float)
        for (hits_a, hits_b), mass in distribution.items():
            for sign, probability in zip(SIGNS, row):
                key = (min(10, hits_a + (sign == a)), min(10, hits_b + (sign == b)))
                next_distribution[key] += mass * probability
        distribution = next_distribution
    return sum(mass for hits, mass in distribution.items() if max(hits) == 10)


def forecast(round_data, results, as_of=None):
    cutoff = instant(round_data["closes_at"])
    if as_of is not None:
        cutoff = min(cutoff, as_of)
    matches = round_data["matches"]
    keys = [match_key(match) for match in matches]
    if len(keys) != 15 or len(set(keys)) != 15:
        raise ValueError("Se requieren 15 partidos distintos y ordenados")
    for match in matches:
        if match.get("kickoff_at") and instant(match["kickoff_at"]) < cutoff:
            raise ValueError("Hay un partido iniciado antes del corte")
    history = [(key, goals) for available, key, goals, estimated in results
               if available < cutoff and (as_of is not None or not estimated)]
    market_rows, blended, details = [], [], []
    missing = 0
    for match in matches[:14]:
        baseline = market(match, cutoff)
        model = team_model(match, history)
        missing += baseline is None
        market_rows.append(baseline)
        if baseline is None and model is None:
            blended.append(None)
            details.append({"match": match_key(match), "method": "missing"})
            continue
        if baseline and model:
            row = tuple(.65 * m + .35 * t for m, t in zip(baseline, model[0]))
            method = "market_65_team_35"
        else:
            row = baseline or model[0]
            method = "market" if baseline else "team"
        blended.append(row)
        ranked = sorted(zip(row, SIGNS), key=lambda pair: (-pair[0], pair[1]))
        details.append({"match": match_key(match), "probabilities_1X2": row,
                        "market_1X2": baseline, "team_1X2": model[0] if model else None,
                        "method": method, "favorite": ranked[0][1],
                        "alternative": ranked[1][1],
                        "margin": ranked[0][0] - ranked[1][0],
                        "close_call": ranked[0][0] - ranked[1][0] <= .10})

    pleno_model = team_model(matches[14], history)
    pleno = pleno_model[1] if pleno_model else None
    for snapshot in sorted(matches[14].get("goal_market", []),
                           key=lambda row: instant(row["captured_at"]), reverse=True):
        if snapshot.get("source") and instant(snapshot["captured_at"]) < cutoff:
            marginals = snapshot["probabilities_012M"]
            if len(marginals) != 2 or any(len(row) != 4 or any(
                not math.isfinite(value) or value < 0 for value in row
            ) or sum(row) <= 0 for row in marginals):
                raise ValueError("Distribución del Pleno inválida")
            pleno = "".join(BANDS[max(range(4), key=row.__getitem__)] for row in marginals)
            break
    manual = matches[14].get("pleno_pick")
    if manual and manual.get("source") and manual.get("captured_at"):
        if instant(manual["captured_at"]) < cutoff:
            pleno = manual["score"]
            if len(pleno) != 2 or any(goal not in BANDS for goal in pleno):
                raise ValueError("Pleno inválido")
    return market_rows, blended, details, pleno, missing


def tickets(baseline, blended, pleno):
    candidates = top_columns(blended, 128)
    first = candidates[0]
    second = max(candidates[1:], key=lambda column: coverage(first, column, blended))
    options = {
        "blend_top": candidates[:2],
        "blend_coverage": [first, second],
    }
    if all(baseline):
        options["market_top"] = top_columns(baseline, 2)
    return {name: {"columns": columns, "pleno": pleno, "cost": str(COST),
                   "coverage_10_plus_under_blend": coverage(*columns, blended)}
            for name, columns in options.items()}


def settle(ticket, official):
    signs, pleno = official["signs"], official["pleno"]
    if len(signs) != 14 or any(sign not in SIGNS for sign in signs):
        raise ValueError("Signos oficiales inválidos")
    if len(pleno) != 2 or any(goal not in BANDS for goal in pleno):
        raise ValueError("Pleno oficial inválido")
    if not official.get("source", "").startswith("https://www.loteriasyapuestas.es/"):
        raise ValueError("Falta el enlace al escrutinio oficial SELAE")
    payouts = {category: Decimal(str(official["payouts"][category]))
               for category in CATEGORIES}
    if any(not amount.is_finite() or amount < 0 for amount in payouts.values()):
        raise ValueError("Premio inválido")
    columns = ticket["columns"]
    if len(columns) != 2 or len(set(columns)) != 2 or any(
        len(column) != 14 or any(sign not in SIGNS for sign in column)
        for column in columns
    ):
        raise ValueError("Se requieren dos columnas válidas distintas")
    if len(ticket["pleno"]) != 2 or any(goal not in BANDS for goal in ticket["pleno"]):
        raise ValueError("Pleno del boleto inválido")
    hits = [sum(a == b for a, b in zip(column, signs, strict=True)) for column in columns]
    total = sum((payouts.get(str(count), Decimal(0)) for count in hits), Decimal(0))
    if 14 in hits and ticket["pleno"] == pleno:
        total += payouts["15"]
    return {"hits": hits, "payout": str(total), "balance": str(total - COST)}


def metrics(rows):
    n = len(rows)
    if not n:
        return None
    bins = defaultdict(list)
    brier = log_loss = correct = 0
    for row, sign in rows:
        index = SIGNS.index(sign)
        favorite = max(range(3), key=row.__getitem__)
        correct += favorite == index
        brier += sum((probability - (i == index)) ** 2 for i, probability in enumerate(row))
        log_loss -= math.log(max(row[index], 1e-15))
        for i, probability in enumerate(row):
            bins[(SIGNS[i], min(9, int(probability * 10)))].append(
                (probability, i == index)
            )
    return {"matches": n, "accuracy": correct / n, "brier": brier / n,
            "log_loss": log_loss / n,
            "calibration": [{"sign": sign, "bin": bucket, "n": len(points),
                             "forecast": sum(p for p, _ in points) / len(points),
                             "observed": sum(y for _, y in points) / len(points)}
                            for (sign, bucket), points in sorted(bins.items())]}


def backtest(rounds, results):
    if not isinstance(rounds, list):
        raise ValueError("El histórico debe ser un array JSON de jornadas")
    outcomes = defaultdict(list)
    observations = defaultdict(list)
    excluded = Counter()
    audit = []
    seen = set()
    for data in sorted(rounds, key=lambda item: instant(item["closes_at"])):
        identifier = data["id"]
        if identifier in seen:
            raise ValueError(f"Jornada duplicada: {identifier}")
        seen.add(identifier)
        baseline, blended, _, pleno, missing = forecast(data, results)
        reasons = []
        if missing:
            excluded["matches_without_market"] += missing
            reasons.append("missing_market")
        if pleno is None:
            reasons.append("missing_pleno")
        if not data.get("official"):
            reasons.append("missing_official")
        if reasons:
            excluded.update(reasons)
            audit.append({"id": identifier, "excluded": reasons})
            continue
        official = data["official"]
        round_tickets = tickets(baseline, blended, pleno)
        settlements = {name: settle(ticket, official)
                       for name, ticket in round_tickets.items()}
        audit.append({"id": identifier, "tickets": round_tickets, "settlements": settlements})
        for name, outcome in settlements.items():
            outcomes[name].append(outcome)
        for name, probabilities in (("market", baseline), ("blend", blended)):
            observations[name].extend(zip(probabilities, official["signs"], strict=True))

    summaries = {}
    for name, records in outcomes.items():
        paid_rounds = losing_run = worst_run = 0
        paid = Decimal(0)
        for record in records:
            payout = Decimal(record["payout"])
            paid += payout
            paid_rounds += payout > 0
            losing_run = losing_run + 1 if payout < COST else 0
            worst_run = max(worst_run, losing_run)
        spent = COST * len(records)
        summaries[name] = {"rounds": len(records), "paid_rounds": paid_rounds,
                           "spent": str(spent), "payout": str(paid),
                           "balance": str(paid - spent), "roi": float((paid - spent) / spent),
                           "worst_losing_run": worst_run}
    return {"estimated_result_rows_excluded": sum(row[3] for row in results),
            "excluded_rounds": sum("excluded" in item for item in audit),
            "exclusion_counts": dict(excluded), "strategies": summaries,
            "probability_metrics": {name: metrics(observations[name])
                                    for name in ("market", "blend")}, "audit": audit}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description="Predictor y simulador de La Quiniela")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("predict", "backtest"):
        sub = commands.add_parser(command)
        sub.add_argument("input")
        sub.add_argument("--results", action="append", default=[])
        if command == "predict":
            sub.add_argument("--as-of", required=True)
    sub = commands.add_parser("settle")
    sub.add_argument("ticket")
    sub.add_argument("round")
    sub = commands.add_parser("import-results")
    sub.add_argument("season", help="Ejemplo: 2526")
    sub.add_argument("division", choices=["SP1", "SP2"])
    sub.add_argument("output")
    sub.add_argument("--csv", help="CSV original descargado localmente")
    args = parser.parse_args()
    try:
        if args.command == "import-results":
            output = {"imported": import_football_data(args.season, args.division,
                                                        args.output, args.csv)}
        elif args.command == "settle":
            output = settle(load(args.ticket), load(args.round)["official"])
        else:
            results = read_results(args.results)
            data = load(args.input)
            if args.command == "backtest":
                output = backtest(data, results)
            else:
                baseline, blended, details, pleno, missing = forecast(
                    data, results, instant(args.as_of)
                )
                output = {"matches": details, "missing_market": missing, "pleno": pleno,
                          "tickets": tickets(baseline, blended, pleno)
                          if all(blended) and pleno else {}}
        print(json.dumps(output, ensure_ascii=True, indent=2))
    except (ValueError, TypeError, KeyError, ArithmeticError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
