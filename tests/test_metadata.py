"""Synthetic packed fields only; no recovered payloads or survey fixtures."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

import convert
import metadata
from test_tools import fixture


def record(fields):
    nibbles = 'a'.join(fields)
    if len(nibbles) > 64:
        raise ValueError('Synthetic payload too long')
    nibbles = nibbles.ljust(64, 'f')
    b = bytearray(convert.RECORD)
    b[0xfc0:0xfe0] = bytes(int(nibbles[i], 16) | int(nibbles[i+1], 16) << 4
                          for i in range(0, 64, 2))
    b[0xf80:0xf83] = bytes.fromhex('123457')
    b[0xf85:0xf87] = (123).to_bytes(2, 'little')
    b[0xf8c] = 17
    return b


class MetadataTests(unittest.TestCase):
    def test_rmc_candidates_and_unknown_hemispheres(self):
        row = metadata.decode_record(record(['3cde025d43c8', '123456', 'c',
            '1234b5', 'c', '04506b7', 'd', '3b4', '123b4', '']))
        self.assertEqual(row['candidate_family'], 'RMC-like')
        self.assertAlmostEqual(row['candidate_latitude_magnitude'], 12+34.5/60)
        self.assertAlmostEqual(row['candidate_longitude_magnitude'], 45+6.7/60)
        self.assertEqual(row['latitude_hemisphere_token'], 'c')
        self.assertEqual(row['candidate_speed_knots'], 3.4)
        self.assertEqual(row['candidate_course_degrees'], 123.4)
        self.assertEqual(row['candidate_speed_byte_knots'], 3.4)
        self.assertEqual(row['scan_minus_navigation_seconds'], 1)
        self.assertFalse(row['checksum_validated'])

    def test_gga_and_gll(self):
        row = metadata.decode_record(record(['5cdedd01ce', '123456', '1234', 'c',
            '04506', 'd', '1', '8', '1b2', '9b3', '']))
        self.assertEqual(row['candidate_satellites'], 8)
        self.assertEqual(row['candidate_hdop'], 1.2)
        self.assertEqual(row['candidate_gps_height'], 9.3)
        row = metadata.decode_record(record(['ecdedff', '1234', 'c', '04506', 'd', '123456', 'c', '']))
        self.assertEqual(row['candidate_family'], 'GLL-like')
        self.assertEqual(row['candidate_time'], '12:34:56')
        self.assertEqual(row['candidate_status_token'], 'c')

    def test_invalid_and_unterminated_fields(self):
        for token in ['246000', '126060', '12b456', '12345']:
            self.assertIsNone(metadata.clock(token))
        for token in ['1260', '9100', '9000b1', '12c4', '123']:
            self.assertIsNone(metadata.coordinate(token, 2))
        self.assertIsNone(metadata.number('1e3'))
        b = record(['3cde025d43c8', '123456', 'c', '1234', 'c', '04506', 'd', '3b4', '123'])
        b[0xf85:0xf87] = (360).to_bytes(2, 'little')
        row = metadata.decode_record(b)
        self.assertEqual(row['candidate_course_degrees'], '')
        self.assertEqual(row['candidate_direction_degrees'], '')
        self.assertEqual(metadata.decode_record(bytes(4096))['candidate_family'], 'absent')
        self.assertEqual(metadata.decode_record(record(['cccc', '12', '']))['candidate_family'], 'unknown')
        with self.assertRaises(ValueError):
            metadata.decode_record(bytes(4095))

    def test_export_raw_bytes_repetition_hash_and_no_overwrite(self):
        raw = bytearray(fixture())
        payload = record(['ecdedff', '1234', 'c', '04506', 'd', '123456', 'c', ''])[0xfc0:0xfe0]
        for index in [0, 1]:
            offset = convert.BASE+index*convert.RECORD+0xfc0
            raw[offset:offset+32] = payload
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'synthetic.bin'
            source.write_bytes(raw)
            out = metadata.extract(raw, source, root/'out')
            with (out/'metadata.csv').open(newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows[0]['serial_repeated'], 'False')
            self.assertEqual(rows[1]['serial_repeated'], 'True')
            self.assertEqual(rows[1]['serial_first_seen_disk_record'], '0')
            self.assertNotEqual(rows[1]['scan_minus_navigation_seconds'], '0')
            for row in rows:
                offset = int(row['byte_offset'])
                self.assertEqual(bytes.fromhex(row['trailer_hex']), raw[offset+3840:offset+4096])
            manifest = json.loads((out/'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'complete')
            self.assertTrue(manifest['source_hash_unchanged'])
            with self.assertRaises(FileExistsError):
                metadata.extract(raw, source, root/'out')
            self.assertEqual(source.read_bytes(), raw)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(metadata.main([str(root/'missing.bin')]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(metadata.main([str(source), '--output', str(root/'cli')]), 0)
