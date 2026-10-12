"""Fixtures are generated from arithmetic patterns; no survey data is included."""
import collections
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image
import convert
import relief


def fixture():
    header = bytearray(convert.BASE)
    records = []
    for i in range(12):
        b = bytearray(convert.RECORD)
        b[:convert.PIXELS] = bytes((x * 7 + i * 11) % 256 for x in range(convert.PIXELS))
        b[0xf80:0xf83] = bytes([0x12, 0x34, i])
        b[0xf9c:0xfa0] = i.to_bytes(4, 'little')
        records.append(bytes(b))
    records[3] = bytes(convert.RECORD)
    records[5] = records[1]  # Saved selection, not a hard-coded slot type.
    records[7] = b'\xff' * convert.RECORD
    records[8] = b'\xcf\x23' * 2048
    b = bytearray(records[2]); b[0xf80] = 0xfa; records[2] = bytes(b)
    for slot, (label, start, end) in enumerate([(b'../DEMO', 0, 3), (b'EXAMPLE', 4, 7), (b'', 10, 12)]):
        h = bytearray(32)
        h[0] = 1
        h[1:13] = label.ljust(12, b'\xff')
        h[20:24] = start.to_bytes(4, 'little')
        h[24:28] = end.to_bytes(4, 'little')
        header[slot*32:(slot+1)*32] = h
    return bytes(header) + b''.join(records)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic disk.bin'
        self.source.write_bytes(fixture())

    def run_cli(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return convert.main(args)

    def test_all_bytes_roundtrip_and_no_overwrite(self):
        destination = self.root / 'out'
        self.assertEqual(self.run_cli(['extract', str(self.source), '--output', str(destination), '--chunk-rows', '2']), 0)
        out = next(destination.iterdir())
        manifest = json.loads((out / 'manifest.json').read_text())
        restored = bytearray(manifest['source_bytes'])
        restored[:convert.BASE] = (out / 'catalogue_header.bin').read_bytes()
        for entry in manifest['entries']:
            folder = out / entry['directory']
            self.assertEqual(folder.parent, out)
            for chunk in entry['chunks']:
                with Image.open(folder / chunk['png']) as im:
                    pixels = im.tobytes()
                trailers = (folder / chunk['trailers']).read_bytes()
                for j, i in enumerate(range(chunk['start_record'], chunk['end_record_exclusive'])):
                    raw = pixels[j*3840:(j+1)*3840] + trailers[j*256:(j+1)*256]
                    restored[convert.BASE+i*4096:convert.BASE+(i+1)*4096] = raw
        for r in manifest['unclaimed_ranges']:
            raw = ((out / r['file']).read_bytes() if r['kind'] == 'raw' else
                   convert.FILL_PATTERNS[r['kind']] * (r['end_record_exclusive']-r['start_record']))
            restored[convert.BASE+r['start_record']*4096:convert.BASE+r['end_record_exclusive']*4096] = raw
        self.assertEqual(restored, fixture())
        self.assertEqual(self.source.read_bytes(), fixture())
        self.assertEqual(manifest['status'], 'complete')
        self.assertTrue(manifest['all_bytes_accounted_for'])
        self.assertEqual(manifest['entries'][0]['invalid_times'], 1)
        self.assertGreater(manifest['entries'][1]['counter_discontinuities'], 0)
        self.assertTrue(manifest['warnings'])
        before = hashlib.sha256((out / 'manifest.json').read_bytes()).digest()
        self.assertEqual(self.run_cli(['extract', str(self.source), '--output', str(destination)]), 1)
        self.assertEqual(hashlib.sha256((out / 'manifest.json').read_bytes()).digest(), before)

    def test_inspection_writes_nothing(self):
        before = set(self.root.iterdir())
        self.assertEqual(self.run_cli(['inspect', str(self.source)]), 0)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_unknown_and_truncated_rejected(self):
        for raw in [b'', bytes(16384+4096), fixture()[:-1], fixture()[:16384+4096]]:
            with self.assertRaises(convert.Unsupported):
                convert.inspect_image(raw)

    def test_overlapping_catalogue_rejected(self):
        raw = bytearray(fixture())
        raw[32+20:32+24] = (2).to_bytes(4, 'little')
        with self.assertRaises(convert.Unsupported):
            convert.inspect_image(raw)

    def test_many_slots_and_quoted_glob(self):
        raw = bytearray(fixture())
        raw[64:96] = bytes(32)
        for n in range(2, 9):
            h = bytearray(32);h[0] = 1
            h[20:24] = (n+5).to_bytes(4, 'little')
            h[24:28] = (n+6).to_bytes(4, 'little')
            raw[n*32:(n+1)*32] = h
        raw.extend(fixture()[16384:16384+2*4096])
        self.source.write_bytes(raw)
        self.assertEqual(len(convert.inspect_image(raw)['entries']), 9)
        self.assertEqual(self.run_cli(['extract', str(self.root / '*.bin'), '--output', str(self.root / 'out')]), 0)
        out = next((self.root / 'out').iterdir())
        self.assertTrue((out / 'contact_sheet_01.png').exists())
        self.assertEqual(len(json.loads((out / 'manifest.json').read_text())['entries']), 9)

    def test_batch_continues_after_failure(self):
        self.assertEqual(self.run_cli(['extract', str(self.root/'missing.bin'), str(self.source), '--output', str(self.root/'out')]), 1)
        self.assertEqual(len(list((self.root/'out').iterdir())), 1)


def check_mesh(test, points, faces):
    edges = collections.Counter()
    directed = collections.Counter()
    volume = 0
    for face in faces:
        a, b, c = [points[i] for i in face]
        for i, j in zip(face, face[1:]+face[:1]):
            edges[tuple(sorted((i, j)))] += 1
            directed[(i, j)] += 1
        volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    test.assertTrue(all(v == 2 for v in edges.values()))
    test.assertTrue(all(directed[(j, i)] == count for (i, j), count in directed.items()))
    test.assertEqual(len(points)-len(edges)+len(faces), 2)
    test.assertGreater(volume, 0)


class ReliefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.png'
        im = Image.frombytes('L', (20, 12), bytes((i*13)%256 for i in range(240)))
        im.save(self.source)

    def test_mesh_is_closed_with_positive_volume(self):
        for width, height in [(2, 2), (7, 4), (3, 9)]:
            points, faces = relief.make_mesh([i%256 for i in range(width*height)], width, height)
            physical = [(x*10, y*8, 0 if z < 0 else 2+z*5) for x, y, z in points]
            check_mesh(self, physical, faces)

    def test_generate_and_reject_invalid_crop(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(relief.main([str(self.source), '--output', str(self.root/'bad'), '--crop', '0', '0', '999', '999']), 1)
            self.assertFalse((self.root/'bad').exists())
            self.assertEqual(relief.main([str(self.source), '--output', str(self.root/'model'), '--grid-width', '8', '--grid-height', '6']), 0)
            self.assertEqual(relief.main([str(self.source), '--output', str(self.root/'model')]), 1)
        self.assertTrue((self.root/'model'/'relief.scad').exists())
        self.assertTrue((self.root/'model'/'geometry.scad').exists())
        self.assertFalse(json.loads((self.root/'model'/'provenance.json').read_text())['measured_geometry'])

    @unittest.skipUnless(os.environ.get('TEST_OPENSCAD') == '1', 'Set TEST_OPENSCAD=1 for actual OpenSCAD export tests')
    def test_openscad_exports(self):
        out = self.root/'export'
        self.assertEqual(relief.main([str(self.source), '--output', str(out), '--grid-width', '8', '--grid-height', '6', '--stl', '--render']), 0)
        self.assertGreater((out/'relief.stl').stat().st_size, 100)
        with Image.open(out/'render.png') as im:
            self.assertEqual(im.size, (1400, 1000))
        # OpenSCAD exports ASCII STL by default on the supported versions.
        raw = (out/'relief.stl').read_bytes()
        triangles = []
        if raw.lstrip().startswith(b'solid'):
            vertices = [tuple(map(float, line.split()[1:])) for line in raw.decode().splitlines()
                        if line.strip().startswith('vertex ')]
            triangles = [vertices[i:i+3] for i in range(0, len(vertices), 3)]
        else:
            count = struct.unpack_from('<I', raw, 80)[0]
            for i in range(count):
                t = struct.unpack_from('<12fH', raw, 84+50*i)
                triangles.append([tuple(t[3+j*3:6+j*3]) for j in range(3)])
        ids, points, faces = {}, [], []
        for tri in triangles:
            face = []
            for p in tri:
                if p not in ids:
                    ids[p] = len(points);points.append(p)
                face.append(ids[p])
            faces.append(tuple(face))
        check_mesh(self, points, faces)


if __name__ == '__main__':
    unittest.main()
