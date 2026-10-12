#!/usr/bin/env python3
"""Inspect or extract the custom 16-KiB catalogue / 4096-byte sonar layout.

This is a reverse-engineered format profile, not a universal MO converter.
Inputs are opened read-only. Extraction requires Pillow; inspection does not.
"""
import argparse
import csv
import glob
import hashlib
import json
import mmap
from pathlib import Path
import re
import sys

BASE, RECORD, PIXELS = 16384, 4096, 3840
TRAILER = RECORD - PIXELS
FILL_PATTERNS = {'cf23': b'\xcf\x23' * 2048, 'zero': bytes(RECORD),
                 'ff': b'\xff' * RECORD}


class Unsupported(ValueError):
    pass


def safe_name(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_')[:64] or 'UNNAMED'


def time_bcd(b):
    if len(b) != 3 or any(x >> 4 > 9 or x & 15 > 9 for x in b):
        return None
    t = [(x >> 4) * 10 + (x & 15) for x in b]
    return ':'.join(f'{x:02d}' for x in t) if t[0] < 24 and max(t[1:]) < 60 else None


def record_at(m, index):
    return m[BASE + index * RECORD:BASE + (index + 1) * RECORD]


def inspect_image(m):
    if len(m) < BASE + RECORD or (len(m) - BASE) % RECORD:
        raise Unsupported('Size does not fit this layout (possibly truncated or a different sector format).')
    capacity = (len(m) - BASE) // RECORD
    entries = []
    good_samples = 0
    for slot in range(BASE // 32):
        h = m[slot * 32:(slot + 1) * 32]
        if h == bytes(32):
            continue
        if h[0] != 1:
            raise Unsupported(f'Unknown catalogue status 0x{h[0]:02x} at slot {slot}.')
        start, end = int.from_bytes(h[20:24], 'little'), int.from_bytes(h[24:28], 'little')
        if not 0 <= start < end <= capacity:
            raise Unsupported(f'Invalid bounds at slot {slot}; image may be incomplete.')
        label_bytes = h[1:13].split(b'\xff')[0].split(b'\0')[0]
        label = label_bytes.decode('ascii', errors='replace')
        samples = sorted({start, end - 1, (start + end - 1) // 2})
        valid = sum(time_bcd(record_at(m, i)[0xf80:0xf83]) is not None
                    and record_at(m, i) not in FILL_PATTERNS.values() for i in samples)
        good_samples += valid
        entries.append(dict(slot=slot, label=label, label_hex=h[1:13].hex(),
                            catalogue_hex=h.hex(), date_bytes_hex=h[13:16].hex(' '),
                            start=start, end_exclusive=end, lines=end-start,
                            first_time=time_bcd(record_at(m, start)[0xf80:0xf83]),
                            last_time=time_bcd(record_at(m, end-1)[0xf80:0xf83]),
                            valid_sample_times=valid, sampled_records=len(samples)))
    if not entries or not good_samples:
        raise Unsupported('No usable catalogue with plausible sonar record times found.')
    ordered = sorted(entries, key=lambda e: e['start'])
    if any(a['end_exclusive'] > b['start'] for a, b in zip(ordered, ordered[1:])):
        raise Unsupported('Catalogue ranges overlap; this variant requires manual analysis.')
    return dict(format_profile='mo-sonar-4096-v1 (heuristic, manufacturer unconfirmed)',
                source_bytes=len(m), capacity_records=capacity, entries=entries,
                note='Structural match is not a manufacturer identification. Dates and geometry remain uncalibrated.')


def dump_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + '\n')


def convert(m, source, destination, info, chunk_rows):
    from PIL import Image, ImageDraw, ImageOps
    digest = hashlib.sha256(m).hexdigest()
    out = destination / f'{safe_name(source.stem)}_{digest[:10]}'
    # Never overwrite a previous extraction, source, or another disk's files.
    out.mkdir(parents=True, exist_ok=False)
    summary = dict(info, source=str(source.resolve()), sha256=digest,
                   status='incomplete', chunk_rows=chunk_rows,
                   geometry='uncalibrated; preview proportions are for viewing only')
    dump_json(out / 'manifest.json', summary)
    (out / 'catalogue_header.bin').write_bytes(m[:BASE])
    thumbs = []
    warnings = []
    extracted = 0
    for entry in summary['entries']:
        stem = f'{entry["slot"]+1:03d}_{safe_name(entry["label"])}'
        folder = out / stem
        folder.mkdir()
        entry['directory'] = stem
        entry['chunks'] = []
        raw_hash = hashlib.sha256()
        invalid_times = matches = jumps = 0
        previous = None
        with (folder / 'lines.csv').open('w', newline='') as cf:
            writer = csv.writer(cf)
            writer.writerow(['entry_row', 'disk_record', 'byte_offset', 'time_bcd',
                             'time_raw_hex', 'reference_counter_le32',
                             'matches_record_at_counter', 'counter_discontinuity'])
            for start in range(entry['start'], entry['end_exclusive'], chunk_rows):
                stop = min(start + chunk_rows, entry['end_exclusive'])
                rows = stop - start
                blocks = [record_at(m, i) for i in range(start, stop)]
                pixels = b''.join(b[:PIXELS] for b in blocks)
                tails = b''.join(b[PIXELS:] for b in blocks)
                tag = f'rows_{start-entry["start"]:07d}_{stop-entry["start"]:07d}'
                png, trailer = folder / f'{tag}.png', folder / f'{tag}.trailers.bin'
                im = Image.frombytes('L', (PIXELS, rows), pixels)
                im.save(png)
                trailer.write_bytes(tails)
                with Image.open(png) as check:
                    saved_pixels = check.tobytes()
                saved_tails = trailer.read_bytes()
                for j, b in enumerate(blocks):
                    if saved_pixels[j*PIXELS:(j+1)*PIXELS] + saved_tails[j*TRAILER:(j+1)*TRAILER] != b:
                        raise ValueError(f'Round-trip verification failed in {png}, row {j}')
                    raw_hash.update(b)
                    counter = int.from_bytes(b[0xf9c:0xfa0], 'little')
                    same = 0 <= counter < info['capacity_records'] and record_at(m, counter) == b
                    clock = time_bcd(b[0xf80:0xf83])
                    jump = previous is not None and previous + 1 != counter
                    invalid_times += clock is None
                    matches += same
                    jumps += jump
                    writer.writerow([start+j-entry['start'], start+j, BASE+(start+j)*RECORD,
                                     clock or '', b[0xf80:0xf83].hex(), counter, same, jump])
                    previous = counter
                preview = ImageOps.autocontrast(im, cutoff=2).resize(
                    (960, min(rows, 768)), Image.Resampling.LANCZOS)
                preview.save(folder / f'{tag}.preview.png')
                if start == entry['start']:
                    ratio = min(480 / preview.width, 240 / preview.height)
                    size = (max(1, round(preview.width * ratio)), max(1, round(preview.height * ratio)))
                    thumbs.append((stem, preview.convert('RGB').resize(size, Image.Resampling.LANCZOS)))
                entry['chunks'].append(dict(start_record=start, end_record_exclusive=stop,
                                             png=png.name, trailers=trailer.name))
                extracted += rows
        entry.update(raw_records_sha256=raw_hash.hexdigest(), roundtrip_verified=True,
                     invalid_times=invalid_times, counter_discontinuities=jumps,
                     records_matching_counter=matches)
        if invalid_times:
            warnings.append(f'{stem}: {invalid_times} invalid/unknown time fields; records preserved.')
        print(f'  {stem}: {entry["lines"]} lines verified', flush=True)
    # Account for every byte outside the catalogue entries. Preserve unknown
    # data verbatim; constant fill ranges can be reconstructed from the manifest.
    unclaimed = []
    ordered = sorted(summary['entries'], key=lambda e: e['start'])
    gaps = []
    cursor = 0
    for e in ordered:
        gaps.append((cursor, e['start']))
        cursor = e['end_exclusive']
    gaps.append((cursor, info['capacity_records']))
    unknown_dir = out / 'unclaimed'
    handle = None
    try:
        for start, stop in gaps:
            current = None
            for i in range(start, stop):
                b = record_at(m, i)
                kind = next((k for k, v in FILL_PATTERNS.items() if b == v), 'raw')
                if current is None or current['kind'] != kind:
                    if handle:
                        handle.close()
                        handle = None
                    current = dict(start_record=i, end_record_exclusive=i, kind=kind)
                    unclaimed.append(current)
                    if kind == 'raw':
                        unknown_dir.mkdir(exist_ok=True)
                        path = unknown_dir / f'record_{i:09d}.bin'
                        current['file'] = str(path.relative_to(out))
                        handle = path.open('wb')
                current['end_record_exclusive'] = i+1
                if handle:
                    handle.write(b)
            if handle:
                handle.close()
                handle = None
    finally:
        if handle:
            handle.close()
    # Re-read unclaimed raw exports as well.
    for r in unclaimed:
        if r['kind'] != 'raw':
            continue
        warnings.append(f'Uncatalogued data preserved: {r["file"]}; not decoded.')
        with (out / r['file']).open('rb') as f:
            for i in range(r['start_record'], r['end_record_exclusive']):
                if f.read(RECORD) != record_at(m, i):
                    raise ValueError('Unclaimed data verification failed')
            if f.read(1):
                raise ValueError('Unexpected trailing unclaimed output')
    for page in range(0, len(thumbs), 12):
        batch = thumbs[page:page+12]
        sheet = Image.new('RGB', (960, ((len(batch)+1)//2)*280+32), 'white')
        draw = ImageDraw.Draw(sheet)
        for n, (label, thumb) in enumerate(batch):
            x, y = n%2*480, n//2*280
            draw.text((x+10, y+8), label[:65], fill='black')
            sheet.paste(thumb, (x+(480-thumb.width)//2, y+30))
        draw.text((10, sheet.height-24), 'First chunk of each entry. Contrast enhanced; not to scale. Full scans in entry folders.', fill='black')
        sheet.save(out / f'contact_sheet_{page//12+1:02d}.png')
    if hashlib.sha256(m).hexdigest() != digest:
        raise ValueError('Source changed during conversion')
    accounted = extracted + sum(r['end_record_exclusive']-r['start_record'] for r in unclaimed)
    if accounted != info['capacity_records']:
        raise ValueError('Record accounting failed')
    summary.update(status='complete', source_hash_unchanged=True, extracted_records=extracted,
                   unclaimed_ranges=unclaimed, warnings=warnings, all_bytes_accounted_for=True)
    dump_json(out / 'manifest.json', summary)
    (out / 'READ_ME.txt').write_text(
        'SONAR IMAGE EXTRACTION\n\nOpen contact_sheet_*.png first. Each entry folder contains\n'
        'lossless scan chunks (*.png), separate contrast-enhanced previews,\n'
        '256-byte-per-row binary trailers, and a metadata CSV.\n'
        'Concatenate each decoded PNG row (3840 bytes) with its corresponding\n'
        '256-byte trailer to reconstruct the source record at the manifest offset.\n'
        'The original header is catalogue_header.bin. Uncatalogued nonconstant\n'
        'data is preserved in unclaimed/. Constant ranges are in manifest.json:\n'
        'cf23 = repeated CF 23; zero = 00; ff = FF.\n\n'
        'This is echo imagery, NOT bathymetry. Physical scale, geographic position,\n'
        'calendar date order and manufacturer remain unconfirmed. Counter matches\n'
        'are byte comparisons, not a guarantee of unique survey passes.\n'
        'All dates and unknown metadata bytes are preserved without guessing.\n\n'
        + ('Warnings:\n' + '\n'.join(warnings) if warnings else 'No conversion warnings.') + '\n')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'extract'])
    parser.add_argument('images', nargs='+', type=Path, help='Raw disk image(s), any filename extension')
    parser.add_argument('--output', type=Path, default=Path('converted'), help='Output parent directory')
    parser.add_argument('--chunk-rows', type=int, default=2048, help='Records per PNG, 1..4096 (default 2048)')
    args = parser.parse_args(argv)
    if not 1 <= args.chunk_rows <= 4096:
        parser.error('--chunk-rows must be between 1 and 4096')
    failed = False
    sources = []
    for item in args.images:
        matches = sorted(glob.glob(str(item))) if not item.exists() else [str(item)]
        sources.extend(Path(p) for p in (matches or [str(item)]))
    for source in dict.fromkeys(sources):
        try:
            if not source.is_file() or source.stat().st_size == 0:
                raise Unsupported('Input is not a nonempty regular file. Supply an acquired disk image.')
            with source.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                info = inspect_image(m)
                if args.command == 'inspect':
                    print(json.dumps(dict(source=str(source), **info), indent=2))
                else:
                    print(f'Converting {source}', flush=True)
                    out = convert(m, source, args.output, info, args.chunk_rows)
                    print(f'Completed: {out}', flush=True)
        except (OSError, ValueError, ImportError) as exc:
            failed = True
            print(f'{source}: {exc}', file=sys.stderr)
            if isinstance(exc, ImportError):
                print('Install Pillow: python -m pip install -r requirements.txt', file=sys.stderr)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
