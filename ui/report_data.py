"""Rapor dashboard için satır birleştirme ve KPI — SQL sürücüsünden bağımsız."""

from collections import defaultdict
from datetime import datetime, timedelta

from ui.shift_store import (
    assignment_kaynak,
    assignment_overlaps_session,
    get_person,
    list_kaynak_locations,
    list_people,
    normalize_people,
    people_for_zone,
)


def oturum_id_text(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def cursor_dicts(cursor):
    columns = [col[0] for col in cursor.description]
    rows = []
    for raw in cursor.fetchall():
        item = {}
        for key, value in zip(columns, raw):
            item[key] = value
        rows.append(item)
    return rows


def _as_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=None):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_occupancy_row(row):
    return {
        "LogTarihi": parse_dt(row.get("LogTarihi")),
        "KameraAdi": row.get("KameraAdi") or "",
        "KanalId": str(row.get("KanalId") or ""),
        "BolgeAdi": row.get("BolgeAdi") or "",
        "Dolu_Saniye": _as_float(row.get("Dolu_Saniye")),
        "Bos_Saniye": _as_float(row.get("Bos_Saniye")),
        "Doluluk_Yuzdesi": _as_float(row.get("Doluluk_Yuzdesi")),
        "OturumId": oturum_id_text(row.get("OturumId")) or None,
        "BolgeId": _as_int(row.get("BolgeId")),
    }


def normalize_session_row(row):
    return {
        "OturumId": oturum_id_text(row.get("OturumId")),
        "Baslangic": parse_dt(row.get("Baslangic")),
        "Bitis": parse_dt(row.get("Bitis")),
        "KameraAdi": row.get("KameraAdi") or "",
        "KanalId": str(row.get("KanalId") or ""),
        "Fps": _as_float(row.get("Fps"), 25.0),
        "FrameSayisi": _as_int(row.get("FrameSayisi"), 0) or 0,
        "ToplamZiyaret": _as_int(row.get("ToplamZiyaret"), 0) or 0,
        "OnaylananZiyaret": _as_int(row.get("OnaylananZiyaret"), 0) or 0,
        "FiltrelenenZiyaret": _as_int(row.get("FiltrelenenZiyaret"), 0) or 0,
        "ToplamTakipId": _as_int(row.get("ToplamTakipId"), 0) or 0,
        "HayaletId": _as_int(row.get("HayaletId"), 0) or 0,
        "legacy": bool(row.get("legacy")),
    }


def normalize_visit_row(row):
    return {
        "OturumId": oturum_id_text(row.get("OturumId")),
        "KameraAdi": row.get("KameraAdi") or "",
        "KanalId": str(row.get("KanalId") or ""),
        "Isci_ID": _as_int(row.get("Isci_ID")),
        "BolgeId": _as_int(row.get("BolgeId")),
        "BolgeAdi": row.get("BolgeAdi") or "",
        "Baslangic": parse_dt(row.get("Baslangic")),
        "Bitis": parse_dt(row.get("Bitis")),
        "Sure_Saniye": _as_float(row.get("Sure_Saniye")),
    }


def normalize_worker_row(row):
    return {
        "LogTarihi": parse_dt(row.get("LogTarihi")),
        "KameraAdi": row.get("KameraAdi") or "",
        "KanalId": str(row.get("KanalId") or ""),
        "Isci_ID": _as_int(row.get("Isci_ID")),
        "BolgeAdi": row.get("BolgeAdi") or "",
        "Sure_Saniye": _as_float(row.get("Sure_Saniye")),
        "OturumId": oturum_id_text(row.get("OturumId")) or None,
        "BolgeId": _as_int(row.get("BolgeId")),
    }


def _fetch_table(cursor, sql, params=None):
    if params:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    return cursor_dicts(cursor)


def fetch_report_bundle(cursor, date_from=None, date_to=None):
    """date_from/date_to verilirse aralık, yoksa tüm kayıtlar."""
    ranged = date_from is not None and date_to is not None
    params = (date_from, date_to) if ranged else None
    occ_sql = """
        SELECT LogTarihi, KameraAdi, KanalId, BolgeAdi, Dolu_Saniye, Bos_Saniye,
               Doluluk_Yuzdesi, OturumId, BolgeId
        FROM Bolge_Doluluk_Loglari
        {where}
        ORDER BY LogTarihi
    """
    ses_sql = """
        SELECT OturumId, Baslangic, Bitis, KameraAdi, KanalId, Fps, FrameSayisi,
               ToplamZiyaret, OnaylananZiyaret, FiltrelenenZiyaret,
               ToplamTakipId, HayaletId
        FROM Oturum_Loglari
        {where}
        ORDER BY Bitis
    """
    vis_sql = """
        SELECT OturumId, KameraAdi, KanalId, Isci_ID, BolgeId, BolgeAdi,
               Baslangic, Bitis, Sure_Saniye
        FROM Ziyaret_Loglari
        {where}
        ORDER BY Baslangic
    """
    wrk_sql = """
        SELECT LogTarihi, KameraAdi, KanalId, Isci_ID, BolgeAdi, Sure_Saniye,
               OturumId, BolgeId
        FROM Isci_Zaman_Loglari
        {where}
        ORDER BY LogTarihi
    """
    occ_where = "WHERE LogTarihi >= ? AND LogTarihi < ?" if ranged else ""
    ses_where = "WHERE Bitis >= ? AND Bitis < ?" if ranged else ""
    vis_where = "WHERE Baslangic >= ? AND Baslangic < ?" if ranged else ""
    wrk_where = "WHERE LogTarihi >= ? AND LogTarihi < ?" if ranged else ""
    occupancy = [normalize_occupancy_row(row) for row in _fetch_table(cursor, occ_sql.format(where=occ_where), params)]
    sessions = [normalize_session_row(row) for row in _fetch_table(cursor, ses_sql.format(where=ses_where), params)]
    visits = [normalize_visit_row(row) for row in _fetch_table(cursor, vis_sql.format(where=vis_where), params)]
    workers = [normalize_worker_row(row) for row in _fetch_table(cursor, wrk_sql.format(where=wrk_where), params)]
    return assemble_bundle(occupancy, sessions, visits, workers)


def synthesize_legacy_sessions(occupancy_rows):
    groups = defaultdict(list)
    for row in occupancy_rows:
        if row.get("OturumId"):
            continue
        key = (row.get("LogTarihi"), row.get("KanalId") or "")
        groups[key].append(row)

    sessions = []
    for (log_tarihi, kanal_id), rows in groups.items():
        if log_tarihi is None:
            continue
        stamp = log_tarihi.isoformat()
        oturum_id = f"legacy|{stamp}|{kanal_id}"
        for row in rows:
            row["OturumId"] = oturum_id
        first = rows[0]
        sessions.append(
            normalize_session_row(
                {
                    "OturumId": oturum_id,
                    "Baslangic": log_tarihi,
                    "Bitis": log_tarihi,
                    "KameraAdi": first.get("KameraAdi"),
                    "KanalId": kanal_id,
                    "legacy": True,
                }
            )
        )
    return sessions


def assemble_bundle(occupancy, sessions, visits, workers=None):
    occupancy = [dict(row) for row in occupancy]
    workers = [dict(row) for row in (workers or [])]
    known = {oturum_id_text(s.get("OturumId")) for s in sessions if s.get("OturumId")}
    legacy = synthesize_legacy_sessions(occupancy)
    merged = list(sessions)
    for item in legacy:
        if item["OturumId"] not in known:
            merged.append(item)
    lookup = {}
    for row in occupancy:
        sid = oturum_id_text(row.get("OturumId"))
        if sid:
            lookup[(row.get("LogTarihi"), row.get("KanalId") or "")] = sid
    for row in workers:
        if not row.get("OturumId"):
            row["OturumId"] = lookup.get((row.get("LogTarihi"), row.get("KanalId") or ""))
    merged.sort(key=lambda s: s.get("Bitis") or datetime.min)
    return {
        "occupancy": occupancy,
        "sessions": merged,
        "visits": [dict(row) for row in visits],
        "workers": workers,
    }


def camera_choices(bundle):
    seen = {}
    for row in bundle.get("occupancy", []) + bundle.get("sessions", []):
        kanal = str(row.get("KanalId") or "")
        if not kanal or kanal in seen:
            continue
        seen[kanal] = row.get("KameraAdi") or f"Kanal {kanal}"
    return [("__all__", "Tüm kameralar")] + sorted(
        ((kanal, f"{name} ({kanal})") for kanal, name in seen.items()),
        key=lambda item: item[1].lower(),
    )


def zone_choices(bundle):
    seen = {}
    for row in bundle.get("occupancy", []) + bundle.get("visits", []):
        bolge_id = row.get("BolgeId")
        name = row.get("BolgeAdi") or ""
        if bolge_id is not None:
            key = f"id:{bolge_id}"
            seen.setdefault(key, name or f"Bölge {bolge_id}")
        elif name:
            key = f"name:{name}"
            seen.setdefault(key, name)
    items = sorted(seen.items(), key=lambda item: item[1].lower())
    return [("__all__", "Tüm bölgeler")] + items


def _zone_match(row, zone_key):
    if not zone_key or zone_key == "__all__":
        return True
    if zone_key.startswith("id:"):
        try:
            wanted = int(zone_key.split(":", 1)[1])
        except ValueError:
            return True
        return row.get("BolgeId") == wanted
    if zone_key.startswith("name:"):
        return (row.get("BolgeAdi") or "") == zone_key.split(":", 1)[1]
    return True


def filter_bundle(bundle, camera_id=None, zone_key=None):
    camera_id = None if not camera_id or camera_id == "__all__" else str(camera_id)
    occupancy = []
    for row in bundle.get("occupancy", []):
        if camera_id and str(row.get("KanalId") or "") != camera_id:
            continue
        if not _zone_match(row, zone_key):
            continue
        occupancy.append(row)

    session_ids = {oturum_id_text(row.get("OturumId")) for row in occupancy if row.get("OturumId")}
    sessions = []
    for row in bundle.get("sessions", []):
        if camera_id and str(row.get("KanalId") or "") != camera_id:
            continue
        sid = oturum_id_text(row.get("OturumId"))
        if occupancy and sid not in session_ids:
            continue
        sessions.append(row)

    allowed = {oturum_id_text(row.get("OturumId")) for row in sessions}
    visits = []
    for row in bundle.get("visits", []):
        if camera_id and str(row.get("KanalId") or "") != camera_id:
            continue
        if allowed and oturum_id_text(row.get("OturumId")) not in allowed:
            continue
        if not _zone_match(row, zone_key):
            continue
        visits.append(row)

    workers = []
    for row in bundle.get("workers", []):
        if camera_id and str(row.get("KanalId") or "") != camera_id:
            continue
        if allowed and oturum_id_text(row.get("OturumId")) not in allowed:
            continue
        if not _zone_match(row, zone_key):
            continue
        workers.append(row)

    return {
        "occupancy": occupancy,
        "sessions": sessions,
        "visits": visits,
        "workers": workers,
    }


def compute_kpis(bundle):
    occupancy = bundle.get("occupancy") or []
    visits = bundle.get("visits") or []
    sessions = bundle.get("sessions") or []
    dolu = sum(row["Dolu_Saniye"] for row in occupancy)
    bos = sum(row["Bos_Saniye"] for row in occupancy)
    percents = [row["Doluluk_Yuzdesi"] for row in occupancy]
    tracker_ids = {row.get("Isci_ID") for row in visits if row.get("Isci_ID") is not None}
    return {
        "oturum_sayisi": len(sessions),
        "ortalama_doluluk": round(sum(percents) / len(percents), 1) if percents else 0.0,
        "dolu_saniye": round(dolu, 1),
        "bos_saniye": round(bos, 1),
        "ziyaret_sayisi": len(visits),
        "takip_id": len(tracker_ids),
    }


def zone_key_for_row(row):
    bolge_id = row.get("BolgeId")
    name = row.get("BolgeAdi") or ""
    if bolge_id is not None:
        return f"id:{bolge_id}"
    if name:
        return f"name:{name}"
    return None


def zone_occupancy_series(bundle):
    groups = defaultdict(lambda: {"name": "", "values": []})
    for row in bundle.get("occupancy") or []:
        key = zone_key_for_row(row)
        if not key:
            continue
        bolge_id = row.get("BolgeId")
        name = row.get("BolgeAdi") or (f"Bölge {bolge_id}" if bolge_id is not None else "Bölge")
        groups[key]["name"] = name
        groups[key]["values"].append(row["Doluluk_Yuzdesi"])
    series = []
    for key, info in groups.items():
        values = info["values"]
        series.append(
            (info["name"], round(sum(values) / len(values), 1) if values else 0.0, key)
        )
    series.sort(key=lambda item: item[0].lower())
    return series


def short_camera_token(name, kanal_id=""):
    text = str(name or "").strip()
    if text:
        token = text.split()[0]
        if token:
            return token
    return str(kanal_id or "").strip()


def session_trend_series(bundle):
    by_session = defaultdict(list)
    for row in bundle.get("occupancy") or []:
        sid = oturum_id_text(row.get("OturumId"))
        if not sid:
            continue
        by_session[sid].append(row["Doluluk_Yuzdesi"])
    points = []
    for session in bundle.get("sessions") or []:
        sid = oturum_id_text(session.get("OturumId"))
        values = by_session.get(sid) or []
        when = session.get("Bitis") or session.get("Baslangic")
        stamp = when.strftime("%d.%m %H:%M") if when else sid[:8]
        kamera = session.get("KameraAdi") or ""
        kanal = session.get("KanalId") or ""
        token = short_camera_token(kamera, kanal)
        value = round(sum(values) / len(values), 1) if values else 0.0
        tip = f"{stamp}  {kamera or token}  %{value}".strip()
        points.append((f"{stamp} {token}".strip(), value, tip))
    return points


def session_visits(bundle, oturum_id):
    sid = oturum_id_text(oturum_id)
    return [row for row in bundle.get("visits") or [] if oturum_id_text(row.get("OturumId")) == sid]


def _weak_camera_name(name, kanal):
    text = str(name or "").strip()
    if not text:
        return True
    kanal = str(kanal or "").strip()
    if text == kanal:
        return True
    lowered = text.casefold()
    return lowered in {f"kamera {kanal}".casefold(), f"kanal {kanal}".casefold()}


def _channel_display_name(kanal):
    try:
        number = int(str(kanal).strip()) // 100
    except (TypeError, ValueError):
        return ""
    return f"D{number}" if number > 0 else ""


def _camera_choice_name(kanal, stored_name, cameras=None):
    if not _weak_camera_name(stored_name, kanal):
        return str(stored_name).strip()
    if isinstance(cameras, dict):
        entry = cameras.get(str(kanal))
        if isinstance(entry, dict) and not _weak_camera_name(entry.get("name"), kanal):
            return str(entry.get("name")).strip()
    return _channel_display_name(kanal) or str(stored_name or "").strip() or f"Kamera {kanal}"


def chart_camera_choices(bundle, cameras=None):
    seen = {}
    for row in list(bundle.get("occupancy") or []) + list(bundle.get("sessions") or []):
        kanal = str(row.get("KanalId") or "")
        if not kanal:
            continue
        name = row.get("KameraAdi") or ""
        current = seen.get(kanal)
        if current is None or (_weak_camera_name(current, kanal) and not _weak_camera_name(name, kanal)):
            seen[kanal] = name
    labels = {}
    for kanal, stored in seen.items():
        labels[kanal] = _camera_choice_name(kanal, stored, cameras)
    counts = list(labels.values())
    items = []
    for kanal, name in labels.items():
        label = name if counts.count(name) == 1 else f"{name} ({kanal})"
        items.append((kanal, label))
    return sorted(items, key=lambda item: item[1].lower())


def session_time_span(session, occupancy=None, visits=None):
    occupancy = occupancy or []
    visits = visits or []
    start = session.get("Baslangic") if session else None
    end = session.get("Bitis") if session else None
    visit_starts = [row["Baslangic"] for row in visits if row.get("Baslangic")]
    visit_ends = [row["Bitis"] for row in visits if row.get("Bitis")]
    if visit_starts:
        start = min(item for item in [start] + visit_starts if item)
    if visit_ends:
        end = max(item for item in [end] + visit_ends if item)
    if start and end and end > start:
        return start, end
    stamp = end or start
    if occupancy:
        stamp = occupancy[0].get("LogTarihi") or stamp
        seconds = max(
            (row.get("Dolu_Saniye") or 0) + (row.get("Bos_Saniye") or 0)
            for row in occupancy
        )
        if seconds > 1 and stamp:
            return stamp - timedelta(seconds=seconds), stamp
    if stamp:
        return stamp, stamp
    return None, None


def format_time_range(start, end):
    if start and end and end > start:
        if start.date() == end.date():
            return f"{start.strftime('%d.%m.%Y')}  {start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
        return f"{start.strftime('%d.%m.%Y %H:%M')} – {end.strftime('%d.%m.%Y %H:%M')}"
    when = end or start
    return when.strftime("%d.%m.%Y  %H:%M") if when else "Kayıt"


def format_recording_label(session, occupancy=None, visits=None):
    start, end = session_time_span(session, occupancy, visits)
    return format_time_range(start, end)


def recording_choice_rows(bundle, camera_id, cameras=None):
    if isinstance(camera_id, (list, tuple, set)):
        allowed = {str(item) for item in camera_id if item}
        if not allowed:
            return []
    elif not camera_id or camera_id == "__all__":
        allowed = None
    else:
        allowed = {str(camera_id)}
    items = []
    for session in bundle.get("sessions") or []:
        kanal = str(session.get("KanalId") or "")
        if allowed is not None and kanal not in allowed:
            continue
        sid = oturum_id_text(session.get("OturumId"))
        if not sid:
            continue
        occupancy = [
            row for row in bundle.get("occupancy") or []
            if oturum_id_text(row.get("OturumId")) == sid
        ]
        visits = [
            row for row in bundle.get("visits") or []
            if oturum_id_text(row.get("OturumId")) == sid
        ]
        start, end = session_time_span(session, occupancy, visits)
        when = end or start or datetime.min
        label = format_time_range(start, end)
        if allowed is not None and len(allowed) > 1:
            camera_name = _camera_choice_name(kanal, session.get("KameraAdi"), cameras)
            label = f"{camera_name}  ·  {label}"
        items.append((sid, label, when))
    items.sort(key=lambda item: item[2], reverse=True)
    return items


def recording_choices(bundle, camera_id, cameras=None):
    return [(sid, label) for sid, label, _when in recording_choice_rows(bundle, camera_id, cameras)]


def filter_recording_rows(rows, period="all", query="", today=None):
    today = today or datetime.now().date()
    query = str(query or "").strip().casefold()
    period = str(period or "all")
    week_start = today - timedelta(days=today.weekday())
    yesterday = today - timedelta(days=1)
    result = []
    for sid, label, when in rows or []:
        if period != "all":
            if not when:
                continue
            day = when.date() if hasattr(when, "date") else when
            if period == "today" and day != today:
                continue
            if period == "yesterday" and day != yesterday:
                continue
            if period == "week" and (day < week_start or day > today):
                continue
        if query and query not in str(label or "").casefold():
            continue
        result.append((sid, label))
    return result


MEASURE_HINT = "Ölçüm bölge doluluğu. Kişi veya makine tanınmaz."


def report_summary_line(mode, item_name, recording_label, payload):
    zones = (payload or {}).get("zones") or []
    parts = []
    name = str(item_name or "").strip()
    if mode == "kaynak" and name:
        parts.append(name if name.casefold().startswith("kaynak") else f"Kaynak {name}")
        location = next(
            (
                "  /  ".join(
                    part
                    for part in (zone.get("location_camera"), zone.get("location_zone"))
                    if part
                )
                for zone in zones
                if zone.get("location_camera") or zone.get("location_zone")
            ),
            "",
        )
        if location:
            parts.append(location)
    elif name:
        parts.append(name)
    if recording_label:
        parts.append(recording_label)
    if zones:
        parts.append(f"{len(zones)} bölge")
    combined = (payload or {}).get("combined") or {}
    pct_source = None
    if combined.get("pct") is not None:
        pct_source = combined.get("pct")
    else:
        pcts = []
        for zone in zones:
            try:
                if zone.get("pct") is not None:
                    pcts.append(float(zone["pct"]))
            except (TypeError, ValueError):
                continue
        if pcts:
            pct_source = sum(pcts) / len(pcts)
    if pct_source is not None:
        text = format_occupancy_pct(pct_source)
        if text:
            parts.append(text.replace("Alan ", "alan "))
    return "  ·  ".join(parts)


def chart_bin_seconds(span_seconds):
    span_seconds = max(float(span_seconds or 0), 0.0)
    if span_seconds <= 3 * 60:
        return 5
    if span_seconds <= 15 * 60:
        return 10
    if span_seconds <= 60 * 60:
        return 30
    if span_seconds <= 4 * 60 * 60:
        return 60
    return 300


def format_duration(seconds):
    try:
        total = max(0, int(round(float(seconds or 0))))
    except (TypeError, ValueError):
        total = 0
    if total < 60:
        return f"{total} sn"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        if sec:
            return f"{minutes} dk {sec} sn"
        return f"{minutes} dk"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"{hours} sa {minutes} dk"
    return f"{hours} sa"


def format_occupancy_pct(pct):
    if pct is None:
        return None
    try:
        value = float(pct)
    except (TypeError, ValueError):
        return None
    return f"Alan %{value:.0f} dolu"


def _zone_id_from_key(key):
    text = str(key or "")
    if text.startswith("id:"):
        try:
            return int(text[3:])
        except ValueError:
            return None
    return None


def _merge_spans(spans):
    cleaned = sorted(
        (start, end)
        for start, end in spans
        if start and end and end > start
    )
    if not cleaned:
        return []
    merged = [list(cleaned[0])]
    for start, end in cleaned[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _overlap_seconds(merged, start, end):
    total = 0.0
    for left, right in merged:
        a = max(left, start)
        b = min(right, end)
        if b > a:
            total += (b - a).total_seconds()
    return total


def session_zone_charts(bundle, oturum_id, people=None):
    sid = oturum_id_text(oturum_id)
    session = next(
        (row for row in bundle.get("sessions") or [] if oturum_id_text(row.get("OturumId")) == sid),
        None,
    )
    occupancy = [
        row for row in bundle.get("occupancy") or [] if oturum_id_text(row.get("OturumId")) == sid
    ]
    visits = [
        row for row in bundle.get("visits") or [] if oturum_id_text(row.get("OturumId")) == sid
    ]
    start, end = session_time_span(session, occupancy, visits)
    channel_id = str((session or {}).get("KanalId") or "")
    if not channel_id:
        for row in occupancy + visits:
            if row.get("KanalId"):
                channel_id = str(row.get("KanalId"))
                break

    zones = {}
    for row in occupancy + visits:
        key = zone_key_for_row(row)
        if not key:
            continue
        entry = zones.setdefault(key, {"name": "", "bolge_id": None})
        entry["name"] = row.get("BolgeAdi") or entry["name"] or "Bölge"
        if row.get("BolgeId") is not None:
            entry["bolge_id"] = row.get("BolgeId")
        if entry["bolge_id"] is None:
            entry["bolge_id"] = _zone_id_from_key(key)

    span_seconds = (end - start).total_seconds() if start and end and end > start else 0.0
    bin_seconds = chart_bin_seconds(span_seconds)
    charts = []
    for key, info in sorted(zones.items(), key=lambda item: item[1]["name"].lower()):
        name = info["name"] or "Bölge"
        zone_visits = [row for row in visits if zone_key_for_row(row) == key]
        spans = _merge_spans([(row.get("Baslangic"), row.get("Bitis")) for row in zone_visits])
        occ_row = next((row for row in occupancy if zone_key_for_row(row) == key), None)
        points = []
        from_visits = bool(spans and start and end and end > start)
        if from_visits:
            cursor = start
            step = timedelta(seconds=bin_seconds)
            while cursor < end:
                nxt = min(cursor + step, end)
                occupied = _overlap_seconds(spans, cursor, nxt) > 0
                points.append((cursor, 1 if occupied else 0))
                cursor = nxt

        occupied_s = None
        empty_s = None
        pct = None
        if start and end and end > start:
            total = (end - start).total_seconds()
            if spans:
                occupied_s = _overlap_seconds(spans, start, end)
                empty_s = max(0.0, total - occupied_s)
                pct = (occupied_s / total) * 100.0 if total else 0.0
            elif occ_row:
                occupied_s = occ_row.get("Dolu_Saniye")
                empty_s = occ_row.get("Bos_Saniye")
                pct = occ_row.get("Doluluk_Yuzdesi")
                dolu = _as_float(occupied_s)
                bos = _as_float(empty_s)
                if dolu + bos > 0 and pct is None:
                    pct = dolu / (dolu + bos) * 100.0

        planned = people_for_zone(
            people,
            channel_id,
            info.get("bolge_id"),
            start,
            end,
            name,
        )
        charts.append(
            {
                "zone_key": key,
                "name": name,
                "bolge_id": info.get("bolge_id"),
                "points": points,
                "bin_seconds": bin_seconds,
                "from_visits": from_visits,
                "occupied_s": occupied_s,
                "empty_s": empty_s,
                "pct": pct,
                "planned": planned,
            }
        )
    return {
        "session": session,
        "start": start,
        "end": end,
        "bin_seconds": bin_seconds,
        "zones": charts,
    }


def _session_channel(payload):
    session = payload.get("session") or {}
    channel_id = str(session.get("KanalId") or "")
    if channel_id:
        return channel_id
    for zone in payload.get("zones") or []:
        if zone.get("kanalId"):
            return str(zone.get("kanalId"))
    return ""


def _zone_matches_location(zone, channel_id, location):
    if str(location.get("kanalId") or "") != str(channel_id or ""):
        return False
    zone_id = zone.get("bolge_id")
    loc_id = location.get("bolgeId")
    if zone_id is not None and loc_id is not None:
        try:
            return int(zone_id) == int(loc_id)
        except (TypeError, ValueError):
            pass
    return (zone.get("name") or "").strip().casefold() == str(location.get("bolgeAdi") or "").strip().casefold()


def _shift_hours_text(assignments):
    seen = []
    for item in assignments or []:
        start = item.get("start")
        end = item.get("end")
        if not start or not end:
            continue
        text = f"{start}–{end}"
        if text not in seen:
            seen.append(text)
    return "  ·  ".join(seen)


def _iter_people(people):
    if isinstance(people, list):
        return normalize_people(people)
    return list_people(people)


def _kaynak_assignments(people, kaynak_name):
    wanted = str(kaynak_name or "").strip().casefold()
    items = []
    for person in _iter_people(people):
        for assignment in person.get("assignments") or []:
            if assignment_kaynak(person, assignment).casefold() == wanted:
                items.append(assignment)
    return items


def kaynak_report(bundle, oturum_id, people, kaynak_name):
    payload = session_zone_charts(bundle, oturum_id, people)
    all_locations = list_kaynak_locations(people, kaynak_name)
    locations = list_kaynak_locations(
        people,
        kaynak_name,
        start=payload.get("start"),
        end=payload.get("end"),
    )
    channel_id = _session_channel(payload)
    zones = []
    seen = set()
    for location in locations:
        for zone in payload.get("zones") or []:
            if not _zone_matches_location(zone, channel_id, location):
                continue
            key = zone.get("zone_key") or zone.get("name")
            if key in seen:
                continue
            seen.add(key)
            item = dict(zone)
            item["kaynak"] = kaynak_name
            item["location_camera"] = location.get("kameraAdi") or ""
            item["location_zone"] = location.get("bolgeAdi") or zone.get("name") or ""
            item["location_people"] = list(location.get("people") or [])
            item["emphasize"] = "pct"
            zones.append(item)
            break
    return {
        **payload,
        "zones": zones,
        "locations": locations,
        "all_locations": all_locations,
        "outside_shift": bool(all_locations) and not locations,
        "shift_hours": _shift_hours_text(_kaynak_assignments(people, kaynak_name)),
        "mode": "kaynak",
        "title": kaynak_name,
    }


def person_report(bundle, oturum_id, people, person_id):
    payload = session_zone_charts(bundle, oturum_id, people)
    person = get_person(people, person_id) if not isinstance(people, list) else None
    if person is None and isinstance(people, list):
        from ui.shift_store import normalize_people

        person = next((item for item in normalize_people(people) if item["id"] == str(person_id)), None)
    channel_id = _session_channel(payload)
    assignments = list((person or {}).get("assignments") or [])
    on_camera = [item for item in assignments if str(item.get("kanalId") or "") == channel_id]
    here = []
    outside = []
    for item in on_camera:
        if assignment_overlaps_session(item, payload.get("start"), payload.get("end")):
            here.append(item)
        else:
            outside.append(item)
    other = [item for item in assignments if str(item.get("kanalId") or "") != channel_id]
    zones = []
    seen = set()
    for assignment in here:
        location = {
            "kanalId": assignment.get("kanalId"),
            "bolgeId": assignment.get("bolgeId"),
            "bolgeAdi": assignment.get("bolgeAdi"),
        }
        for zone in payload.get("zones") or []:
            if not _zone_matches_location(zone, channel_id, location):
                continue
            key = zone.get("zone_key") or zone.get("name")
            kaynak = assignment_kaynak(person, assignment)
            existing = next((item for item in zones if (item.get("zone_key") or item.get("name")) == key), None)
            if existing:
                current = [part.strip() for part in str(existing.get("kaynak") or "").split(",") if part.strip()]
                if kaynak and kaynak.casefold() not in {part.casefold() for part in current}:
                    current.append(kaynak)
                    existing["kaynak"] = ", ".join(current)
                if len(current) > 1:
                    existing["same_zone_kaynak"] = True
                break
            if key in seen:
                continue
            seen.add(key)
            item = dict(zone)
            item["person_name"] = (person or {}).get("name") or ""
            item["kaynak"] = kaynak
            item["location_camera"] = assignment.get("kameraAdi") or ""
            item["location_zone"] = assignment.get("bolgeAdi") or zone.get("name") or ""
            item["emphasize"] = "duration"
            zones.append(item)
            break
    return {
        **payload,
        "zones": zones,
        "combined": combined_person_zone(zones, payload.get("start"), payload.get("end")),
        "other_assignments": other,
        "outside_shift": outside,
        "shift_hours": _shift_hours_text(on_camera),
        "mode": "person",
        "title": (person or {}).get("name") or "",
    }


def _point_value_at(points, when):
    value = 0
    for stamp, occupied in points:
        if stamp <= when:
            value = 1 if occupied else 0
        else:
            break
    return value


def combined_person_zone(zones, start, end):
    usable = [zone for zone in zones or [] if zone.get("points")]
    if len(zones or []) < 2 or len(usable) < 2 or not start or not end or end <= start:
        return None
    bin_seconds = int(usable[0].get("bin_seconds") or 5)
    step = timedelta(seconds=max(bin_seconds, 1))
    points = []
    cursor = start
    while cursor < end:
        occupied = 0
        for zone in usable:
            if _point_value_at(zone.get("points") or [], cursor):
                occupied = 1
                break
        points.append((cursor, occupied))
        cursor += step
    occupied_s = 0.0
    for index, (when, value) in enumerate(points):
        nxt = points[index + 1][0] if index + 1 < len(points) else end
        if value:
            occupied_s += (nxt - when).total_seconds()
    total = (end - start).total_seconds()
    empty_s = max(0.0, total - occupied_s)
    names = []
    kaynaklar = []
    planned = []
    seen_people = set()
    for zone in zones:
        name = str(zone.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        for part in str(zone.get("kaynak") or "").split(","):
            part = part.strip()
            if part and part.casefold() not in {item.casefold() for item in kaynaklar}:
                kaynaklar.append(part)
        for person in zone.get("planned") or []:
            key = (person.get("name"), person.get("start"), person.get("end"))
            if key in seen_people:
                continue
            seen_people.add(key)
            planned.append(person)
    return {
        "zone_key": "combined",
        "name": " + ".join(names) or "Birleşik",
        "combined": True,
        "points": points,
        "bin_seconds": bin_seconds,
        "from_visits": True,
        "occupied_s": occupied_s,
        "empty_s": empty_s,
        "pct": (occupied_s / total) * 100.0 if total else 0.0,
        "planned": planned,
        "kaynak": ", ".join(kaynaklar),
        "person_name": next((zone.get("person_name") for zone in zones if zone.get("person_name")), ""),
        "emphasize": "duration",
    }


def format_dt(value):
    if not value:
        return ""
    if not isinstance(value, datetime):
        value = parse_dt(value)
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def occupancy_table_rows(bundle):
    rows = []
    for row in bundle.get("occupancy") or []:
        rows.append(
            [
                format_dt(row.get("LogTarihi")),
                row.get("KameraAdi") or "",
                row.get("KanalId") or "",
                row.get("BolgeAdi") or "",
                row.get("BolgeId") if row.get("BolgeId") is not None else "",
                row.get("Dolu_Saniye"),
                row.get("Bos_Saniye"),
                row.get("Doluluk_Yuzdesi"),
            ]
        )
    return rows


def session_table_rows(bundle):
    rows = []
    for row in bundle.get("sessions") or []:
        rows.append(
            [
                format_dt(row.get("Baslangic")),
                format_dt(row.get("Bitis")),
                row.get("KameraAdi") or "",
                row.get("KanalId") or "",
                row.get("OnaylananZiyaret"),
                row.get("HayaletId"),
                "Eski özet" if row.get("legacy") else "Ziyaretli",
                row.get("OturumId") or "",
            ]
        )
    return rows


def visit_table_rows(bundle):
    rows = []
    for row in bundle.get("visits") or []:
        rows.append(
            [
                format_dt(row.get("Baslangic")),
                format_dt(row.get("Bitis")),
                row.get("KameraAdi") or "",
                row.get("KanalId") or "",
                row.get("Isci_ID") if row.get("Isci_ID") is not None else "",
                row.get("BolgeAdi") or "",
                row.get("Sure_Saniye"),
            ]
        )
    return rows


OCCUPANCY_HEADERS = [
    "LogTarihi",
    "Kamera",
    "Kanal",
    "Bölge",
    "BolgeId",
    "Dolu_sn",
    "Bos_sn",
    "Doluluk_%",
]
SESSION_HEADERS = [
    "Başlangıç",
    "Bitiş",
    "Kamera",
    "Kanal",
    "Onaylı ziyaret",
    "Hayalet ID",
    "Tür",
    "OturumId",
]
VISIT_HEADERS = [
    "Başlangıç",
    "Bitiş",
    "Kamera",
    "Kanal",
    "Takip ID",
    "Bölge",
    "Süre_sn",
]


def sample_bundle():
    start = datetime(2026, 8, 20, 15, 0, 0)
    end = datetime(2026, 8, 20, 15, 30, 0)
    oturum = "11111111-1111-1111-1111-111111111111"
    occupancy = [
        normalize_occupancy_row(
            {
                "LogTarihi": end,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "BolgeAdi": "1. bölge",
                "Dolu_Saniye": 348.5,
                "Bos_Saniye": 100.4,
                "Doluluk_Yuzdesi": 77.6,
                "OturumId": oturum,
                "BolgeId": 1,
            }
        ),
        normalize_occupancy_row(
            {
                "LogTarihi": end,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "BolgeAdi": "2. bölge",
                "Dolu_Saniye": 406.6,
                "Bos_Saniye": 42.4,
                "Doluluk_Yuzdesi": 90.6,
                "OturumId": oturum,
                "BolgeId": 2,
            }
        ),
        normalize_occupancy_row(
            {
                "LogTarihi": datetime(2026, 8, 20, 17, 29, 35),
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "BolgeAdi": "Zone 1",
                "Dolu_Saniye": 1626.8,
                "Bos_Saniye": 146.2,
                "Doluluk_Yuzdesi": 91.8,
                "OturumId": None,
                "BolgeId": None,
            }
        ),
    ]
    sessions = [
        normalize_session_row(
            {
                "OturumId": oturum,
                "Baslangic": start,
                "Bitis": end,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "Fps": 25,
                "FrameSayisi": 45000,
                "ToplamZiyaret": 4,
                "OnaylananZiyaret": 3,
                "FiltrelenenZiyaret": 1,
                "ToplamTakipId": 8,
                "HayaletId": 1,
            }
        )
    ]
    visits = [
        normalize_visit_row(
            {
                "OturumId": oturum,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "Isci_ID": 2,
                "BolgeId": 1,
                "BolgeAdi": "1. bölge",
                "Baslangic": datetime(2026, 8, 20, 15, 2, 0),
                "Bitis": datetime(2026, 8, 20, 15, 7, 50),
                "Sure_Saniye": 348.5,
            }
        ),
        normalize_visit_row(
            {
                "OturumId": oturum,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "Isci_ID": 6,
                "BolgeId": 2,
                "BolgeAdi": "2. bölge",
                "Baslangic": datetime(2026, 8, 20, 15, 5, 0),
                "Bitis": datetime(2026, 8, 20, 15, 11, 46),
                "Sure_Saniye": 406.6,
            }
        ),
        normalize_visit_row(
            {
                "OturumId": oturum,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "Isci_ID": 17,
                "BolgeId": 1,
                "BolgeAdi": "1. bölge",
                "Baslangic": datetime(2026, 8, 20, 15, 10, 0),
                "Bitis": datetime(2026, 8, 20, 15, 18, 0),
                "Sure_Saniye": 480.0,
            }
        ),
        normalize_visit_row(
            {
                "OturumId": oturum,
                "KameraAdi": "Kamera 1",
                "KanalId": "101",
                "Isci_ID": 8,
                "BolgeId": 1,
                "BolgeAdi": "1. bölge",
                "Baslangic": datetime(2026, 8, 20, 15, 20, 0),
                "Bitis": datetime(2026, 8, 20, 15, 22, 0),
                "Sure_Saniye": 120.0,
            }
        ),
    ]
    return assemble_bundle(occupancy, sessions, visits)
