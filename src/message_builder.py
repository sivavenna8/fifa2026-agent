from __future__ import annotations

from collections import defaultdict
from typing import Any


def _label(item: dict[str, Any]) -> str:
    home, away = item.get("home_team") or "TBD", item.get("away_team") or "TBD"
    if item["status"] == "completed":
        score = f"{item.get('home_score')}-{item.get('away_score')}"
        if item.get("home_penalties") is not None:
            score += f" ({item['home_penalties']}-{item['away_penalties']} pens)"
        return f"{home} {score} {away}"
    return f"{home} vs {away} -> {item.get('winner') or 'TBD'}"


def compare_snapshots(current: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> dict[str, Any]:
    previous = previous or []
    old_by_id = {item["match_id"]: item for item in previous}
    new_results = [item for item in current if item["status"] == "completed" and old_by_id.get(item["match_id"], {}).get("status") != "completed"]
    changed = []
    for item in current:
        old = old_by_id.get(item["match_id"])
        if old and item["status"] != "completed" and (old.get("home_team"), old.get("away_team"), old.get("winner")) != (item.get("home_team"), item.get("away_team"), item.get("winner")):
            changed.append({"before": old, "after": item})
    old_final = next((x.get("winner") for x in previous if x["stage"] == "Final"), None)
    new_final = next((x.get("winner") for x in current if x["stage"] == "Final"), None)
    return {"new_results": new_results, "changed": changed, "old_champion": old_final, "new_champion": new_final}


def change_summary(changes: dict[str, Any]) -> str:
    if not changes["new_results"] and not changes["changed"]:
        return "No bracket-path changes since the previous snapshot."
    parts = []
    for result in changes["new_results"]:
        loser = result["away_team"] if result["winner"] == result["home_team"] else result["home_team"]
        parts.append(f"{loser} were knocked out by {result['winner']}")
    if changes["old_champion"] != changes["new_champion"]:
        parts.append(f"winner prediction changed from {changes['old_champion']} to {changes['new_champion']}")
    return "; ".join(parts) + "."


def build_message(current: list[dict[str, Any]], previous: list[dict[str, Any]] | None = None) -> str:
    changes = compare_snapshots(current, previous)
    completed = [item for item in current if item["status"] == "completed"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in current:
        if item["status"] != "completed":
            grouped[item["stage"]].append(item)

    lines = ["FIFA 2026 Agent Update", "", "1. Latest Results"]
    latest = changes["new_results"] or completed[-3:]
    lines.extend([f"- {_label(item)}" for item in latest] or ["- No new completed results."])
    lines += ["", "2. Confirmed Knockout Changes"]
    if changes["new_results"]:
        for item in changes["new_results"]:
            loser = item["away_team"] if item["winner"] == item["home_team"] else item["home_team"]
            lines.append(f"- {loser} are eliminated. {item['winner']} advance.")
    else:
        lines.append("- No newly confirmed eliminations.")
    lines += ["", "3. Updated Bracket"]
    for stage in ("Round of 32", "Round of 16", "Quarter-final", "Semi-final", "Final"):
        items = grouped.get(stage, [])
        if items:
            lines.append(f"{stage}:")
            lines.extend(f"- {_label(item)} [{item['fixture_type']}]" for item in items)
    lines += ["", "4. New Prediction Path"]
    late_rounds = [item for item in current if item["stage"] in {"Semi-final", "Final"}]
    lines.extend(f"- {item['stage']} {item['match_number']}: {_label(item)}" for item in late_rounds)
    champion = next((item.get("winner") for item in current if item["stage"] == "Final"), "TBD")
    lines += ["", "5. Final Prediction", f"Winner Prediction: {champion}", "", "6. Why It Changed", change_summary(changes)]
    return "\n".join(lines)


def render_table(bracket: list[dict[str, Any]]) -> str:
    headers = ("Stage", "Match", "Type", "Fixture / result", "Winner")
    rows = []
    for item in bracket:
        fixture = _label(item)
        rows.append((item["stage"], str(item["match_number"]), item["fixture_type"], fixture, item.get("winner") or "TBD"))
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    fmt = " | ".join(f"{{:<{width}}}" for width in widths)
    return "\n".join([fmt.format(*headers), "-+-".join("-" * w for w in widths), *(fmt.format(*row) for row in rows)])
