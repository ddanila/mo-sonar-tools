#!/usr/bin/env python3
"""Extract provisional navigation metadata from the supported MO image profile."""
import argparse
from collections import Counter
import csv
import hashlib
import mmap
from pathlib import Path
import re
import sys

import convert

PREFIXES = {'5cdedd01ce': 'GGA-like', 'ecdedff': 'GLL-like',
            '3cde025d43c8': 'RMC-like'}


def number(token):
    """Only the inferred decimal alphabet; never interpret unknown symbols."""
    return float(token.replace('b', '.')) if re.fullmatch(r'[0-9]+(?:b[0-9]+)?', token) else None


def clock(token):
    if not re.fullmatch(r'[0-9]{6}', token):
        return None
    h, m, s = int(token[:2]), int(token[2:4]), int(token[4:])
    return h * 3600 + m * 60 + s if h < 24 and m < 60 and s < 60 else None


def coordinate(token, degrees):
    if not re.fullmatch(r'[0-9]{' + str(degrees + 2) + r'}(?:b[0-9]+)?', token):
        return None
    d, minutes = int(token[:degrees]), number(token[degrees:])
    limit = 90 if degrees == 2 else 180
    if minutes >= 60 or d > limit or (d == limit and minutes != 0):
        return None
    return d + minutes / 60


def decode_record(record):
    if len(record) != convert.RECORD:
        raise ValueError('Expected a complete 4096-byte record')
    raw = record[0xfc0:0xfe0]
    nibbles = ''.join(f'{b & 15:x}{b >> 4:x}' for b in raw)
    fields = nibbles.split('a')
    family = PREFIXES.get(fields[0], 'unknown') if any(raw) else 'absent'
    # The final field has no confirmed terminator. Preserve it raw, but do not
    # decode it: it could be truncated or include padding/unknown symbols.
    complete = fields[:-1]
    def field(index):
        return complete[index] if index < len(complete) else ''
    direction = int.from_bytes(record[0xf85:0xf87], 'little')
    scan = convert.time_bcd(record[0xf80:0xf83]) or ''
    row = dict(scan_time=scan, scan_time_raw_hex=record[0xf80:0xf83].hex(),
               reference_counter_le32=int.from_bytes(record[0xf9c:0xfa0], 'little'),
               trailer_hex=record[convert.PIXELS:].hex(), serial_hex=raw.hex(),
               serial_low_nibble_first=nibbles, candidate_family=family,
               candidate_time='', scan_minus_navigation_seconds='',
               candidate_latitude_magnitude='', candidate_longitude_magnitude='',
               latitude_token='', longitude_token='', latitude_hemisphere_token='',
               longitude_hemisphere_token='', candidate_status_token='',
               candidate_quality='', candidate_satellites='', candidate_hdop='',
               candidate_gps_height='', candidate_speed_knots='', candidate_course_degrees='',
               checksum_validated=False, direction_raw_le16=direction,
               candidate_direction_degrees=direction if direction < 360 else '',
               candidate_speed_byte_knots=record[0xf8c] / 5,
               candidate_bottom_index=record[0xf84], unknown_f83=record[0xf83],
               unknown_f8d=record[0xf8d])
    positions = {'GGA-like': (1, 2, 3, 4, 5), 'GLL-like': (5, 1, 2, 3, 4),
                 'RMC-like': (1, 3, 4, 5, 6)}
    if family not in positions:
        return row
    t, lat, ns, lon, ew = positions[family]
    nav = clock(field(t))
    if nav is not None:
        token = field(t)
        row['candidate_time'] = ':'.join((token[:2], token[2:4], token[4:]))
        if scan:
            row['scan_minus_navigation_seconds'] = clock(scan.replace(':', '')) - nav
    for name, index, digits in [('latitude', lat, 2), ('longitude', lon, 3)]:
        token = field(index)
        value = coordinate(token, digits)
        row[name + '_token'] = token
        row['candidate_' + name + '_magnitude'] = value if value is not None else ''
    row['latitude_hemisphere_token'] = field(ns)
    row['longitude_hemisphere_token'] = field(ew)
    if family == 'GGA-like':
        for name, index in [('quality', 6), ('satellites', 7), ('hdop', 8), ('gps_height', 9)]:
            value = number(field(index))
            if name in ('quality', 'satellites') and not re.fullmatch(r'[0-9]+', field(index)):
                value = None
            row['candidate_' + name] = value if value is not None else ''
    if family == 'RMC-like':
        row['candidate_status_token'] = field(2)
        for name, index in [('speed_knots', 7), ('course_degrees', 8)]:
            value = number(field(index))
            if name == 'course_degrees' and value is not None and value >= 360:
                value = None
            row['candidate_' + name] = value if value is not None else ''
    if family == 'GLL-like':
        row['candidate_status_token'] = field(6)
    return row


def extract(m, source, destination):
    info = convert.inspect_image(m)
    digest = hashlib.sha256(m).hexdigest()
    out = destination / f'{convert.safe_name(source.stem)}_{digest[:10]}_metadata'
    out.mkdir(parents=True, exist_ok=False)
    summary = dict(status='incomplete', format_profile=info['format_profile'],
                   source=str(source.resolve()), source_bytes=len(m), sha256=digest,
                   interpretation='Provisional; unsigned coordinate magnitudes; no validated checksums',
                   scope='Catalogue records only; use convert extract for full byte preservation')
    convert.dump_json(out / 'manifest.json', summary)
    seen, families = {}, Counter()
    count = 0
    with (out / 'metadata.csv').open('w', newline='', encoding='utf-8') as f:
        writer = None
        for entry in sorted(info['entries'], key=lambda e: e['start']):
            for index in range(entry['start'], entry['end_exclusive']):
                record = convert.record_at(m, index)
                row = dict(catalogue_slot=entry['slot'], entry_row=index-entry['start'],
                           disk_record=index, byte_offset=convert.BASE+index*convert.RECORD)
                row.update(decode_record(record))
                counter = row['reference_counter_le32']
                row['matches_record_at_counter'] = (counter < info['capacity_records'] and
                                                     convert.record_at(m, counter) == record)
                payload = bytes(record[0xfc0:0xfe0])
                row['serial_first_seen_disk_record'] = seen.get(payload, '') if any(payload) else ''
                row['serial_repeated'] = any(payload) and payload in seen
                if any(payload):
                    seen.setdefault(payload, index)
                families[row['candidate_family']] += 1
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                count += 1
    if hashlib.sha256(m).hexdigest() != digest:
        raise ValueError('Source changed during extraction')
    summary.update(status='complete', source_hash_unchanged=True, extracted_records=count,
                   candidate_families=dict(families))
    convert.dump_json(out / 'manifest.json', summary)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, default=Path('metadata-output'))
    args = parser.parse_args(argv)
    try:
        if not args.image.is_file() or not args.image.stat().st_size:
            raise convert.Unsupported('Input must be a nonempty regular acquired image file')
        with args.image.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            out = extract(m, args.image, args.output)
        print(f'Completed: {out}')
        return 0
    except (OSError, ValueError) as exc:
        print(f'{args.image}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
