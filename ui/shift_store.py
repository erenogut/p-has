import re
import uuid
from copy import deepcopy

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
NAME_MAX = 80


class ShiftError(ValueError):
    pass


def new_person_id():
    return uuid.uuid4().hex[:8]


def parse_hhmm(text):
    text = str(text or "").strip()
    match = TIME_RE.match(text)
    if not match:
        raise ShiftError("Saat HH:MM olmalı.")
    return int(match.group(1)) * 60 + int(match.group(2))


def format_hhmm(minutes):
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def validate_times(start, end):
    start_min = parse_hhmm(start)
    end_min = parse_hhmm(end)
    if start_min == end_min:
        raise ShiftError("Başlangıç ve bitiş aynı olamaz.")
    return format_hhmm(start_min), format_hhmm(end_min)


def interval_ranges(start, end):
    start_min, end_min = parse_hhmm(start), parse_hhmm(end)
    if start_min < end_min:
        return [(start_min, end_min)]
    return [(start_min, 24 * 60), (0, end_min)]


def intervals_overlap(start_a, end_a, start_b, end_b):
    for left_a, right_a in interval_ranges(start_a, end_a):
        for left_b, right_b in interval_ranges(start_b, end_b):
            if left_a < right_b and left_b < right_a:
                return True
    return False


def _session_clock_range(start, end):
    if start and (not end or end <= start):
        minute = parse_hhmm(start.strftime("%H:%M"))
        return format_hhmm(minute), format_hhmm(minute + 1)
    if not start or not end or end <= start:
        return None
    if (end - start).total_seconds() >= 24 * 3600 - 1:
        return "all"
    start_hm = start.strftime("%H:%M")
    end_hm = end.strftime("%H:%M")
    if start_hm == end_hm:
        minute = parse_hhmm(start_hm)
        end_hm = format_hhmm(minute + 1)
    return start_hm, end_hm


def assignment_overlaps_session(assignment, start, end):
    clock = _session_clock_range(start, end)
    if clock == "all":
        return True
    if clock is None:
        return False
    try:
        return intervals_overlap(clock[0], clock[1], assignment["start"], assignment["end"])
    except (ShiftError, KeyError, TypeError):
        return False


def _assignment_matches_zone(assignment, channel_id, zone_id, zone_name):
    if str(assignment.get("kanalId") or "") != str(channel_id or ""):
        return False
    if zone_id is not None:
        try:
            return int(assignment.get("bolgeId")) == int(zone_id)
        except (TypeError, ValueError):
            return False
    name = str(zone_name or "").strip()
    if not name:
        return False
    return str(assignment.get("bolgeAdi") or "").strip().casefold() == name.casefold()


def people_for_zone(source, channel_id, zone_id, start, end, zone_name=None):
    if isinstance(source, list):
        people = normalize_people(source)
    else:
        people = list_people(source)
    clock = _session_clock_range(start, end)
    matches = []
    seen = set()
    for person in people:
        for assignment in person.get("assignments") or []:
            if not _assignment_matches_zone(assignment, channel_id, zone_id, zone_name):
                continue
            if clock != "all" and clock is not None:
                if not intervals_overlap(clock[0], clock[1], assignment["start"], assignment["end"]):
                    continue
            elif clock is None:
                continue
            key = (person["id"], assignment["kanalId"], assignment["bolgeId"], assignment["start"], assignment["end"])
            if key in seen:
                continue
            seen.add(key)
            matches.append({
                "name": person["name"],
                "departman": person.get("departman") or "",
                "start": assignment["start"],
                "end": assignment["end"],
            })
    matches.sort(key=lambda item: (item["start"], item["name"].casefold()))
    return matches


def normalize_assignment(raw):
    if not isinstance(raw, dict):
        return None
    channel_id = str(raw.get("kanalId") or "").strip()
    try:
        zone_id = int(raw.get("bolgeId"))
    except (TypeError, ValueError):
        return None
    if not channel_id or zone_id <= 0:
        return None
    start, end = validate_times(raw.get("start"), raw.get("end"))
    camera_name = str(raw.get("kameraAdi") or "").strip() or f"Kamera {channel_id}"
    zone_name = str(raw.get("bolgeAdi") or "").strip() or f"Zone {zone_id}"
    return {
        "kanalId": channel_id,
        "kameraAdi": camera_name,
        "bolgeId": zone_id,
        "bolgeAdi": zone_name,
        "kaynak": normalize_kaynak(raw.get("kaynak")),
        "start": start,
        "end": end,
    }


def normalize_kaynak(name):
    return str(name or "").strip()[:NAME_MAX]


def normalize_kaynak_list(raw):
    if raw is None or raw == "":
        items = []
    elif isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = [raw]
    result = []
    seen = set()
    for item in items:
        name = normalize_kaynak(item)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def assignment_key(assignment):
    zone_id = assignment.get("bolgeId")
    try:
        zone_id = int(zone_id)
    except (TypeError, ValueError):
        zone_id = 0
    return (
        str(assignment.get("kanalId") or ""),
        zone_id,
        normalize_kaynak(assignment.get("kaynak")).casefold(),
    )


def kaynaklar_from_assignments(assignments):
    return normalize_kaynak_list([item.get("kaynak") for item in assignments or []])


def _apply_legacy_kaynak(assignment, legacy_names):
    if assignment.get("kaynak"):
        return assignment
    if len(legacy_names) == 1:
        assignment["kaynak"] = legacy_names[0]
    return assignment


def normalize_department(name):
    return str(name or "").strip()[:NAME_MAX]


def normalize_name(name):
    return str(name or "").strip()[:NAME_MAX]


def normalize_person(raw):
    if not isinstance(raw, dict):
        return None
    name = normalize_name(raw.get("name"))
    if not name:
        return None
    department = normalize_department(raw.get("departman"))
    legacy_names = normalize_kaynak_list(raw.get("kaynaklar"))
    person_id = str(raw.get("id") or "").strip() or new_person_id()
    seen = set()
    assignments = []
    for item in raw.get("assignments") or []:
        try:
            assignment = normalize_assignment(item)
        except ShiftError:
            continue
        if assignment is None:
            continue
        _apply_legacy_kaynak(assignment, legacy_names)
        key = assignment_key(assignment)
        if key in seen:
            continue
        seen.add(key)
        assignments.append(assignment)
    return {
        "id": person_id,
        "name": name,
        "departman": department,
        "kaynaklar": kaynaklar_from_assignments(assignments),
        "assignments": assignments,
    }


def normalize_people(raw):
    people = []
    seen_ids = set()
    if not isinstance(raw, list):
        return people
    for item in raw:
        person = normalize_person(item)
        if person is None:
            continue
        if person["id"] in seen_ids:
            person["id"] = new_person_id()
        seen_ids.add(person["id"])
        people.append(person)
    people.sort(key=lambda person: person["name"].casefold())
    return people


def list_people(config):
    return normalize_people((config or {}).get("SHIFT_PEOPLE"))


def get_person(config, person_id):
    person_id = str(person_id or "")
    for person in list_people(config):
        if person["id"] == person_id:
            return deepcopy(person)
    return None


def upsert_person(config, person, *, require_assignment=True):
    name = normalize_name((person or {}).get("name"))
    if not name:
        raise ShiftError("Kişi adı boş olamaz.")
    department = normalize_department((person or {}).get("departman"))
    legacy_names = normalize_kaynak_list((person or {}).get("kaynaklar"))
    assignments = []
    seen = set()
    for item in (person or {}).get("assignments") or []:
        assignment = normalize_assignment(item)
        if assignment is None:
            continue
        _apply_legacy_kaynak(assignment, legacy_names)
        if not assignment.get("kaynak"):
            raise ShiftError("Her atamada kaynak gerekli.")
        key = assignment_key(assignment)
        if key in seen:
            raise ShiftError("Aynı kamera, bölge ve kaynak için ikinci satır eklenemez.")
        seen.add(key)
        assignments.append(assignment)
    if require_assignment and not assignments:
        raise ShiftError("En az bir atama gerekli.")
    person_id = str((person or {}).get("id") or "").strip() or new_person_id()
    record = {
        "id": person_id,
        "name": name,
        "departman": department,
        "kaynaklar": kaynaklar_from_assignments(assignments),
        "assignments": assignments,
    }
    people = list_people(config)
    replaced = False
    for index, existing in enumerate(people):
        if existing["id"] == person_id:
            people[index] = record
            replaced = True
            break
    if not replaced:
        people.append(record)
    people.sort(key=lambda item: item["name"].casefold())
    config["SHIFT_PEOPLE"] = people
    return deepcopy(record)


def delete_person(config, person_id):
    person_id = str(person_id or "")
    config["SHIFT_PEOPLE"] = [person for person in list_people(config) if person["id"] != person_id]
    return True


def add_assignment(person, assignment):
    normalized = normalize_assignment(assignment)
    if normalized is None:
        raise ShiftError("Atama geçersiz.")
    key = assignment_key(normalized)
    assignments = list(person.get("assignments") or [])
    for index, existing in enumerate(assignments):
        if assignment_key(existing) == key:
            assignments[index] = normalized
            person["assignments"] = assignments
            person["kaynaklar"] = kaynaklar_from_assignments(assignments)
            return deepcopy(normalized)
    assignments.append(normalized)
    person["assignments"] = assignments
    person["kaynaklar"] = kaynaklar_from_assignments(assignments)
    return deepcopy(normalized)


def remove_assignment(person, channel_id, zone_id, kaynak=""):
    key = assignment_key({"kanalId": channel_id, "bolgeId": zone_id, "kaynak": kaynak})
    person["assignments"] = [
        item
        for item in (person.get("assignments") or [])
        if assignment_key(item) != key
    ]
    person["kaynaklar"] = kaynaklar_from_assignments(person.get("assignments"))
    return person


def overlap_warnings(config, person):
    warnings = []
    person_id = str((person or {}).get("id") or "")
    others = [item for item in list_people(config) if item["id"] != person_id]
    for assignment in (person or {}).get("assignments") or []:
        for other in others:
            for other_assignment in other.get("assignments") or []:
                if other_assignment["kanalId"] != assignment["kanalId"]:
                    continue
                if int(other_assignment["bolgeId"]) != int(assignment["bolgeId"]):
                    continue
                if intervals_overlap(
                    assignment["start"],
                    assignment["end"],
                    other_assignment["start"],
                    other_assignment["end"],
                ):
                    warnings.append(
                        f"{other['name']} aynı bölgede "
                        f"{other_assignment['start']}–{other_assignment['end']} "
                        f"({assignment.get('kameraAdi')} / {assignment.get('bolgeAdi')})"
                    )
    return warnings


def cameras_from_config(config):
    cameras = {}
    raw = (config or {}).get("CAMERAS")
    if isinstance(raw, dict):
        for channel_id, entry in raw.items():
            channel_id = str(channel_id)
            name = f"Kamera {channel_id}"
            if isinstance(entry, dict) and entry.get("name"):
                name = str(entry.get("name"))
            cameras[channel_id] = {"id": channel_id, "name": name}
    for person in list_people(config):
        for assignment in person.get("assignments") or []:
            cameras.setdefault(
                assignment["kanalId"],
                {"id": assignment["kanalId"], "name": assignment.get("kameraAdi") or f"Kamera {assignment['kanalId']}"},
            )
    return _sort_cameras(cameras.values())


def merge_cameras(explicit, config):
    cameras = {item["id"]: item for item in cameras_from_config(config)}
    for item in explicit or []:
        channel_id = str(item.get("id") or "").strip()
        if not channel_id:
            continue
        cameras[channel_id] = {
            "id": channel_id,
            "name": str(item.get("name") or "").strip() or f"Kamera {channel_id}",
        }
    return _sort_cameras(cameras.values())


def _sort_cameras(cameras):
    def key(item):
        try:
            return (0, int(item["id"]), item["name"].casefold())
        except (TypeError, ValueError):
            return (1, 0, item["name"].casefold())

    return sorted(cameras, key=key)


def assignment_kaynak(person, assignment):
    direct = normalize_kaynak((assignment or {}).get("kaynak"))
    if direct:
        return direct
    names = normalize_kaynak_list((person or {}).get("kaynaklar"))
    if len(names) == 1:
        return names[0]
    return ""


def _people_source(source):
    if isinstance(source, list):
        return normalize_people(source)
    return list_people(source)


def person_choices(source):
    return [(person["id"], person["name"]) for person in _people_source(source)]


def list_kaynak_names(source):
    names = set()
    for person in _people_source(source):
        for name in normalize_kaynak_list(person.get("kaynaklar")):
            names.add(name)
        for assignment in person.get("assignments") or []:
            name = assignment_kaynak(person, assignment)
            if name:
                names.add(name)
    return sorted(names, key=lambda item: item.casefold())


def list_kaynak_locations(source, kaynak_name, start=None, end=None):
    wanted = normalize_kaynak(kaynak_name)
    if not wanted:
        return []
    grouped = {}
    timed = start is not None or end is not None
    for person in _people_source(source):
        for assignment in person.get("assignments") or []:
            if assignment_kaynak(person, assignment).casefold() != wanted.casefold():
                continue
            if timed and not assignment_overlaps_session(assignment, start, end):
                continue
            key = (assignment["kanalId"], int(assignment["bolgeId"]))
            entry = grouped.setdefault(
                key,
                {
                    "kanalId": assignment["kanalId"],
                    "kameraAdi": assignment.get("kameraAdi") or f"Kamera {assignment['kanalId']}",
                    "bolgeId": int(assignment["bolgeId"]),
                    "bolgeAdi": assignment.get("bolgeAdi") or f"Zone {assignment['bolgeId']}",
                    "people": [],
                },
            )
            if person["name"] not in entry["people"]:
                entry["people"].append(person["name"])
    return sorted(grouped.values(), key=lambda item: (item["kameraAdi"].casefold(), item["bolgeId"]))
