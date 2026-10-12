#!/usr/bin/env python3
"""Create an adjustable OpenSCAD intensity relief from a grayscale sonar PNG.

Echo intensity is NOT measured elevation. Model dimensions are display choices.
OpenSCAD is only required for optional --stl and --render exports.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


def make_mesh(values, width, height):
    """Return normalized vertices and outward CCW triangles; values bottom-up."""
    if width < 2 or height < 2 or len(values) != width * height:
        raise ValueError('Invalid grid dimensions')
    points = [(x / (width - 1) - 0.5, y / (height - 1) - 0.5,
               values[y * width + x] / 255)
              for y in range(height) for x in range(width)]
    faces = []
    for y in range(height - 1):
        for x in range(width - 1):
            a = y * width + x
            faces.extend(((a, a + 1, a + width + 1), (a, a + width + 1, a + width)))
    ring = (list(range(width)) + [y * width + width - 1 for y in range(1, height)]
            + [(height - 1) * width + x for x in range(width - 2, -1, -1)]
            + [y * width for y in range(height - 2, 0, -1)])
    bottom = len(points)
    points.extend((points[i][0], points[i][1], -1) for i in ring)
    center = len(points)
    points.append((0, 0, -1))
    for j, a in enumerate(ring):
        k = (j + 1) % len(ring)
        b, c, d = ring[k], bottom + j, bottom + k
        faces.extend(((a, c, d), (a, d, b), (center, d, c)))
    return points, faces


def openscad_command(explicit=None):
    if explicit:
        found = shutil.which(explicit)
        if found:
            return found
        if Path(explicit).is_file():
            return str(Path(explicit).resolve())
        raise ValueError(f'OpenSCAD executable not found: {explicit}')
    candidates = ['openscad.com', 'openscad', '/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD']
    for env in ['ProgramFiles', 'ProgramFiles(x86)']:
        if os.environ.get(env):
            candidates.extend(str(Path(os.environ[env]) / 'OpenSCAD' / exe)
                              for exe in ['openscad.com', 'openscad.exe'])
    for item in candidates:
        found = shutil.which(item)
        if found:
            return found
    raise ValueError('OpenSCAD is needed for --stl/--render; install it, set --openscad, or use Docker.')


def build(args):
    from PIL import Image, ImageFilter, ImageOps
    # Resolve requested exporter before writing any artifacts.
    cad = openscad_command(args.openscad) if args.stl or args.render else None
    with Image.open(args.image) as original:
        if original.format != 'PNG' or original.mode != 'L':
            raise ValueError('Expected an 8-bit grayscale PNG, such as a converter scan chunk.')
        crop_box = args.crop or [0, 0, original.width, original.height]
        x0, y0, x1, y1 = crop_box
        if not (0 <= x0 < x1 <= original.width and 0 <= y0 < y1 <= original.height):
            raise ValueError('Crop must lie within the source PNG; right/bottom are exclusive.')
        crop = original.crop(crop_box)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    crop.save(out / 'crop_original.png')
    small = crop.resize((args.grid_width, args.grid_height), Image.Resampling.LANCZOS)
    if args.blur:
        small = small.filter(ImageFilter.GaussianBlur(args.blur))
    heightmap = ImageOps.autocontrast(small, cutoff=args.contrast_cutoff)
    if not args.bright_high:
        heightmap = ImageOps.invert(heightmap)
    heightmap.save(out / 'intensity_heightmap.png')
    values = list(heightmap.transpose(Image.Transpose.FLIP_TOP_BOTTOM).getdata())
    points, faces = make_mesh(values, args.grid_width, args.grid_height)
    with (out / 'geometry.scad').open('w', encoding='utf-8') as f:
        f.write('// Normalized intensity mesh; z=-1 marks base vertices. NOT bathymetry.\npoints_data=[\n')
        for p in points:
            f.write('[' + ','.join(f'{v:.9f}' for v in p) + '],\n')
        f.write('];\nfaces_data=[\n')
        for a, b, c in faces:
            # OpenSCAD input expects clockwise faces viewed from outside.
            f.write(f'[{c},{b},{a}],\n')
        f.write('];\n')
    scad = f'''/* SONAR INTENSITY RELIEF: not a measured 3D reconstruction.
All model dimensions and proportions are arbitrary display choices.
Keep geometry.scad alongside this file. F5 preview; F6 render. */
/* [Model dimensions in arbitrary millimetres] */
model_width = {args.width};
model_depth = {args.depth};
base_thickness = {args.base};
relief_height = {args.height};
/* [Display] */
reverse_intensity = false;
/* [Hidden] */
include <geometry.scad>
assert(model_width > 0 && model_depth > 0 && base_thickness > 0 && relief_height >= 0);
echo("INTENSITY RELIEF ONLY: height and XY proportions are not measured geometry.");
color([0.72, 0.61, 0.40])
polyhedron(points=[for(p=points_data)
    [p[0]*model_width, p[1]*model_depth,
     p[2]<0 ? 0 : base_thickness + relief_height*(reverse_intensity ? 1-p[2] : p[2])]],
    faces=faces_data, convexity=10);
'''
    (out / 'relief.scad').write_text(scad, encoding='utf-8')
    # Local provenance only. Never sent to a service by this script.
    with args.image.open('rb') as f:
        digest = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    metadata = dict(source=str(args.image.resolve()), source_sha256=digest.hexdigest(),
                    crop_xyxy=crop_box, grid=[args.grid_width, args.grid_height],
                    blur=args.blur, contrast_cutoff_percent=args.contrast_cutoff,
                    dark_high=not args.bright_high, vertices=len(points), triangles=len(faces),
                    model_dimensions=[args.width, args.depth, args.base, args.height],
                    measured_geometry=False, status='scad_complete', exports=[])
    provenance = out / 'provenance.json'
    provenance.write_text(json.dumps(metadata, indent=2) + '\n')
    env = dict(os.environ)
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    common = [cad, '--hardwarnings'] if cad else []
    if args.stl:
        subprocess.run(common + ['-o', str(out / 'relief.stl'), str(out / 'relief.scad')],
                       check=True, env=env)
        metadata['exports'].append('relief.stl')
    if args.render:
        render_command = common
        if sys.platform.startswith('linux') and not env.get('DISPLAY') and shutil.which('xvfb-run'):
            render_command = ['xvfb-run', '-a'] + common
            env['QT_QPA_PLATFORM'] = 'xcb'
        subprocess.run(render_command + ['-o', str(out / 'render.png'), '--imgsize=1400,1000',
                                 '--camera=0,0,0,35,0,20,450', '--autocenter', '--viewall',
                                 '--render=true', str(out / 'relief.scad')], check=True, env=env)
        metadata['exports'].append('render.png')
    metadata['status'] = 'complete'
    provenance.write_text(json.dumps(metadata, indent=2) + '\n')
    (out / 'READ_ME.txt').write_text(
        'This is an intensity bas-relief, NOT recovered bathymetry or measured hull geometry.\n'
        'Model dimensions, smoothing and vertical exaggeration are arbitrary.\n'
        'Open relief.scad with geometry.scad alongside it. Adjust the parameters\n'
        'in OpenSCAD to change model width, depth, height and polarity.\n'
        'The original crop, derived heightmap and local provenance are included.\n')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='Original grayscale scan-chunk PNG')
    parser.add_argument('--output', type=Path, required=True, help='New output directory (must not exist)')
    parser.add_argument('--crop', nargs=4, type=int, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    parser.add_argument('--grid-width', type=int, default=241)
    parser.add_argument('--grid-height', type=int, default=174)
    parser.add_argument('--width', type=float, default=170, help='Arbitrary model width in mm')
    parser.add_argument('--depth', type=float, default=120, help='Arbitrary model depth in mm')
    parser.add_argument('--base', type=float, default=2)
    parser.add_argument('--height', type=float, default=5, help='Intensity relief height, NOT real height')
    parser.add_argument('--blur', type=float, default=0.65, help='Gaussian blur radius on output grid')
    parser.add_argument('--contrast-cutoff', type=float, default=2, help='Percent clipped at each histogram end')
    parser.add_argument('--bright-high', action='store_true', help='Raise bright pixels instead of dark pixels')
    parser.add_argument('--stl', action='store_true', help='Use OpenSCAD to export STL')
    parser.add_argument('--render', action='store_true', help='Use OpenSCAD to render a PNG preview')
    parser.add_argument('--openscad', help='Path to OpenSCAD executable if not on PATH')
    args = parser.parse_args(argv)
    if not (2 <= args.grid_width <= 512 and 2 <= args.grid_height <= 512):
        parser.error('Grid dimensions must each be 2..512')
    if not all(math.isfinite(v) for v in [args.width, args.depth, args.base, args.height, args.blur, args.contrast_cutoff]):
        parser.error('Numeric parameters must be finite')
    if min(args.width, args.depth, args.base) <= 0 or args.height < 0 or not 0 <= args.blur <= 20 or not 0 <= args.contrast_cutoff < 50:
        parser.error('Positive dimensions/base, nonnegative height, blur 0..20, cutoff 0..<50 required')
    try:
        out = build(args)
        print(f'Created intensity relief (not measured geometry): {out}')
        return 0
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as exc:
        print(f'Relief failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
