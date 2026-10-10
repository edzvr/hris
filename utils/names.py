def format_person_name(value):
    words = str(value or "").split()
    return " ".join(
        word.title() for word in words
        if word.casefold() not in {"n/a", "n.a.", "n.a"}
    )


def format_suffix_name(value):
    name = format_person_name(value)
    suffix = name.upper().replace(".", "")
    return {
        "JR": "Jr.", "SR": "Sr.", "II": "II", "III": "III", "IV": "IV",
    }.get(suffix, name)
