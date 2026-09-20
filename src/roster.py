"""Validated private roster additions without editing public employee data."""


def with_additions(base_roster, base_competitors, additions):
    roster, competitors = dict(base_roster), set(base_competitors)
    if not isinstance(additions, list):
        raise ValueError("Private roster additions must be a list.")
    for entry in additions:
        if not isinstance(entry, dict):
            raise ValueError("Each private roster addition must be an object.")
        display = entry.get("display")
        aliases = entry.get("aliases")
        competing = entry.get("competitor")
        if not isinstance(display, str) or not display.strip():
            raise ValueError("Each roster addition needs a display name.")
        display = display.strip()
        if not isinstance(aliases, list) or not aliases:
            raise ValueError("Each roster addition needs exact till aliases.")
        if any(not isinstance(a, str) or not a.strip() for a in aliases):
            raise ValueError("Till aliases must be nonblank strings.")
        if type(competing) is not bool:
            raise ValueError("The competitor flag must be true or false.")
        if display in roster.values() and (display in competitors) != competing:
            raise ValueError("Roster additions cannot change an existing person's prize eligibility.")
        for alias in aliases:
            alias = alias.strip()
            if alias in roster and roster[alias] != display:
                raise ValueError("A till alias is already assigned to another person.")
            roster[alias] = display
        if competing:
            competitors.add(display)
    return roster, competitors
