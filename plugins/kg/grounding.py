"""Deterministic provenance checks for automatic extraction.

An exact quote is necessary evidence, not a proof of semantic entailment.
Ambiguous dates are rejected instead of borrowing the message timestamp.
"""
from datetime import datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

from .schema import prepare_entity
from .provenance import user_message_view


def _evidence(item, snapshot):
    evidence = item.get('evidence')
    if not isinstance(evidence, dict) or set(evidence) != {'message_id', 'quote'}:
        raise ValueError('Every automatically extracted fact needs evidence.message_id and evidence.quote')
    message_id, quote = evidence['message_id'], evidence['quote']
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        raise ValueError('Evidence message_id must be an integer')
    if not isinstance(quote, str) or len(quote.strip()) < 4 or len(quote) > 4000:
        raise ValueError('Evidence quote must contain 4 to 4000 source characters')
    raw_source = next((m for m in snapshot['messages'] if m['id'] == message_id), None)
    source = user_message_view(raw_source)
    if source is None or quote not in source.get('content', ''):
        raise ValueError('Evidence must quote the identified user message verbatim')
    return source, quote


def _supporting_evidence(item, snapshot):
    supporting = item.get('supporting_evidence', [])
    if not isinstance(supporting, list) or len(supporting) > 4:
        raise ValueError('supporting_evidence must be an array with at most four citations')
    return [_evidence({'evidence': evidence}, snapshot) for evidence in supporting]


def _source_local_date(source, config):
    timestamp = source.get('timestamp')
    try:
        finite = isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError('Relative or yearless event dates require a finite source timestamp')
    try:
        return datetime.fromtimestamp(timestamp, ZoneInfo(config.timezone)).date()
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError('Event date source timestamp is outside the supported range') from exc


def _event_grounding(value, source, quote, config, supporting=()):
    local = value.astimezone(ZoneInfo(config.timezone))
    citations = [(source, quote), *supporting]
    # Full ISO timestamps carry their own offset when one is explicitly stated.
    stamps = [stamp for _, text in citations for stamp in re.findall(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?', text)]
    if stamps:
        from .schema import parse_time
        if not any(value == parse_time(stamp, config.timezone) for stamp in stamps):
            raise ValueError('Event time disagrees with the quoted timestamp')
        return 'datetime', False
    # Keep the source of each date: a yearless date must never inherit the year
    # of another supporting message, the primary citation, or the current run.
    dates = []
    absolute_pattern = re.compile(r'(?<!\d)(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})\s*(?:日|号)?(?!\d)')
    for date_source, text in citations:
        for year, month, day in absolute_pattern.findall(text):
            try:
                dates.append((datetime(int(year), int(month), int(day)).date(), False))
            except ValueError:
                pass
        # Mask complete absolute dates so their month/day cannot also be read
        # as an inferred-year date, including Chinese text with spacing.
        yearless_text = absolute_pattern.sub(' ', text)
        month_days = []
        for match in re.finditer(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)', yearless_text):
            if yearless_text[:match.start()].rstrip().endswith('年'):
                # "去年/明年/26年 ..." is not a yearless date. Unsupported year
                # qualifiers must not silently become the source message year.
                continue
            month_days.append(match.groups())
        if month_days:
            source_year = _source_local_date(date_source, config).year
            for month, day in month_days:
                try:
                    dates.append((datetime(source_year, int(month), int(day)).date(), True))
                except ValueError:
                    pass
        relative = re.findall(r'今天|昨天|明天|\btoday\b|\byesterday\b|\btomorrow\b', text.lower())
        if relative:
            day = _source_local_date(date_source, config)
            offsets = {'今天':0,'today':0,'昨天':-1,'yesterday':-1,'明天':1,'tomorrow':1}
            dates.extend((day + timedelta(days=offsets[word]), False) for word in relative)
    matching = [inferred for date, inferred in dates if date == local.date()]
    if not matching:
        raise ValueError('Event date needs a quoted absolute date, a source-year month/day, or today/yesterday/tomorrow')
    times = [clock for _, text in citations for clock in re.findall(
        r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)', text)]
    allowed = {(int(h),int(m),int(s or 0)) for h,m,s in times} if times else {(0,0,0)}
    if (local.hour,local.minute,local.second) not in allowed or local.microsecond:
        raise ValueError('Event clock time needs quoted HH:MM evidence; date-only means midnight')
    return ('datetime' if times else 'day'), all(matching)


def validate_entity_evidence(entity, snapshot, config):
    prepared, _ = prepare_entity(entity, config)
    for key in ('fictional','hypothetical','is_fictional','is_hypothetical'):
        if prepared['properties'].get(key) is True or str(prepared['properties'].get(key,'')).casefold()=='true':
            raise ValueError('Fictional or hypothetical material cannot become automatic memory')
    source, quote = _evidence(entity, snapshot)
    supporting = _supporting_evidence(entity, snapshot)
    if prepared['uri'] == config.user_person_urn:
        raise ValueError('Automatic extraction must reference, not recreate or rename, the existing user')
    if prepared['label'] == 'Person' and prepared['properties']['foaf_name'] not in quote:
        raise ValueError('An extracted person name must appear in the quoted user text')
    if prepared['label'] == 'Event':
        if prepared['properties'].get('hermes_timePrecision') == 'unknown':
            raise ValueError('Automatic extraction still requires quoted event dates; unknown timing requires explicit reviewed writing')
        precision, year_inferred = _event_grounding(prepared['properties']['hermes_eventTime'], source, quote, config, supporting)
        if prepared['properties'].get('hermes_eventEndTime') is not None:
            end_evidence = prepared['properties']['hermes_eventEndTime']
            if precision == 'day':
                end_evidence -= timedelta(days=1)
            _event_grounding(end_evidence, source, quote, config, supporting)
        property_key = 'properties' if 'properties' in entity else 'props'
        entity.setdefault(property_key, {})['hermes_timePrecision'] = precision
        if year_inferred:
            entity[property_key]['hermes_timeYearInferred'] = True
        else:
            # Existing absolute/relative evidence payloads remain byte-stable;
            # do not introduce a new false-valued property on every replay.
            entity[property_key].pop('hermes_timeYearInferred', None)
    return source, quote


def validate_relation_evidence(relation, snapshot, config):
    result = _evidence(relation, snapshot)
    _supporting_evidence(relation, snapshot)
    return result
