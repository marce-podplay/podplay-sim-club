"""ASCII projection for status and watch commands."""

from typing import Any, Dict, Iterable


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value.ljust(width)
    return value[: max(0, width - 1)] + "…"


def _box(title: str, lines: Iterable[str], width: int = 32) -> list:
    inner = width - 2
    output = [f"┌ {_clip(title, inner - 2)} ┐"]
    for line in lines:
        output.append(f"│{_clip(' ' + line, inner)}│")
    output.append("└" + "─" * inner + "┘")
    return output


def _side_by_side(left: list, right: list, gap: str = "  ") -> list:
    height = max(len(left), len(right))
    left_width = max(len(line) for line in left)
    return [
        (left[index] if index < len(left) else " " * left_width)
        + gap
        + (right[index] if index < len(right) else "")
        for index in range(height)
    ]


def render(view: Dict[str, Any]) -> str:
    people = view["people"]
    pods = view["pods"]
    clock = view["observedAt"]
    preview_state = "ONLINE" if view["preview"]["reachable"] else "OFFLINE"
    header = [
        f"PREVIEW CLUB // WORLD SIGNAL   {preview_state} {clock}",
        f"{view['season']}   beat {view['beat']['count']}   "
        f"last turns {view['beat']['lastTurnCount']}",
        "",
    ]

    club_lines = []
    for actor_id in ("sofia", "alex", "riley"):
        actor = people[actor_id]
        marker = "●" if actor["location"] == "club" else "·"
        club_lines.append(f"[{actor['glyph']}] {actor['name']} {marker} {actor['state']}")

    pod_lines = []
    for pod_id in sorted(pods):
        pod = pods[pod_id]
        actor_order = ("red-captain", "blue-captain", "sofia", "alex", "riley")
        occupants = [
            f"[{people[actor_id]['glyph']}]"
            for actor_id in actor_order
            if people[actor_id]["location"] == pod_id
        ]
        occupant_text = " ⇄ ".join(occupants) if occupants else "·"
        pod_lines.append(f"{pod['name']} {occupant_text} {pod['state']}")

    body = _side_by_side(
        _box("CLUB / NETWORK", club_lines), _box("COURT ZONE", pod_lines)
    )

    signals = ["", "LATEST SIGNALS"]
    for signal in reversed(view["signals"][-5:]):
        time_part = signal["observedAt"][11:16]
        signals.append(f"{time_part} {signal['kind']:<16} {signal['text']}")

    bookings = ["", "BOOKING TRACE"]
    if not view["bookings"]:
        bookings.append("· no bookings yet")
    else:
        for booking in view["bookings"][-4:]:
            glyph = "●" if booking["state"] == "current" else "✓"
            bookings.append(
                f"{glyph} {booking['occurrenceKey']}  "
                f"{len(booking['checkedIn'])}/{len(booking['participants'])} checked in"
            )

    attention = []
    if view["attention"]:
        attention = ["", "ATTENTION"] + [f"! {item}" for item in view["attention"]]
    return "\n".join(header + body + signals + bookings + attention)


def render_needs(view: Dict[str, Any]) -> str:
    needs = view.get("needs", {})
    intents = view.get("bookingIntents", [])
    lines = [
        f"PREVIEW CLUB // NEEDS SIGNAL   {view['observedAt']}",
        f"{view['season']}   beat {view['beat']['count']}   "
        f"last turns {view['beat']['lastTurnCount']}",
        "",
        "NEEDS",
    ]
    for actor_id in ("red-captain", "blue-captain"):
        need = needs.get(actor_id, {})
        pressure = int(need.get("desireToPlay", 0))
        threshold = int(need.get("threshold", 0))
        state = "READY" if pressure >= threshold else "quiet"
        lines.append(f"- {actor_id}: desire-to-play {pressure}/{threshold}  {state}")

    lines.extend(["", "LATEST NEED SIGNALS"])
    need_signals = [
        signal
        for signal in view.get("signals", [])
        if signal.get("kind")
        in {
            "RED_NEED_RISES",
            "RED_PROPOSES_FROM_NEED",
            "BLUE_AGREES_FROM_AVAILABILITY",
            "LEAD_RECORDS_BOOKING_INTENT",
            "SOFIA_ANNOUNCES_PRIORITY_SESSION",
            "RED_ACCEPTS_PRIORITY_SESSION",
            "BLUE_ACCEPTS_PRIORITY_SESSION",
            "LEAD_RECORDS_PROMOTION_INTENT",
        }
    ]
    for signal in need_signals[-5:]:
        lines.append(
            f"{signal['observedAt'][11:16]} {signal['kind']:<34} {signal['text']}"
        )
    if not need_signals:
        lines.append("· no need-driven turns yet")

    lines.extend(["", "BOOKING INTENTS"])
    if not intents:
        lines.append("· no agreements ready for preview planning")
    for intent in intents:
        lines.append(
            f"● {intent['id']}  {intent['status']}  "
            f"{','.join(intent['participants'])}"
        )
    campaigns = view.get("campaigns", [])
    lines.extend(["", "OWNER CAMPAIGNS"])
    if not campaigns:
        lines.append("· no owner announcement yet")
    for campaign in campaigns:
        lines.append(
            f"● {campaign['id']}  {campaign['status']}  {campaign['owner']}"
        )
    return "\n".join(lines)
