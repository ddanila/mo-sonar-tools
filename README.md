# MO Sonar Tools

Three local tools for a reverse-engineered side-scan sonar disk format:

- **`convert.py`** inspects disk catalogues and extracts lossless scan images and metadata.
- **`metadata.py`** extracts raw trailers and provisional navigation fields, retaining uncertainty and repeated-payload provenance.
- **`relief.py`** turns a selected grayscale scan region into an adjustable OpenSCAD intensity relief, optionally exporting STL and a rendered preview.

Runs with Python on Windows, macOS and Linux, or in Docker with Python, Pillow and OpenSCAD included. None of the tools upload input or output data. This repository contains only code, documentation and tests that generate synthetic fixtures in temporary directories.

## Supported format

This is **not a universal MO-disk recovery tool**. It supports one observed layout: a 16-KiB catalogue with 32-byte entries, followed by 4,096-byte records containing 3,840 grayscale bytes and 256 metadata bytes. Manufacturer, calendar-date ordering and physical geometry remain unconfirmed.

Use `inspect` first. Unknown layouts, truncated images, unexpected catalogue statuses and overlapping ranges are rejected for manual analysis. Some damaged records can still be extracted: invalid per-line times are flagged and preserved. A structural match is a heuristic, not definitive identification of a recorder.

## Native Python

Use Python 3.10 or newer. From the repository directory:

```sh
python -m pip install -r requirements.txt
python convert.py inspect /path/to/disk.bin
python convert.py extract /path/to/disk.bin --output converted
```

On systems where Python is named `python3`, use that instead. A virtual environment is recommended; on Windows the Python launcher `py` also works.

Batch conversion works with explicit paths or a quoted wildcard, including on Windows:

```sh
python convert.py extract "input/*.bin" --output converted
```

Each image gets a separate `<filename>_<hash-prefix>` directory. Existing extraction directories are never overwritten; choose a fresh `--output` directory to repeat a conversion. A failed image does not stop the rest of a batch, but the command exits nonzero if any image fails. `inspect` writes nothing and does not require Pillow.

## Docker

Install Docker with Linux-container support (Docker Desktop on Windows/macOS is one option), then build from this directory:

```sh
docker build -t mo-sonar-tools .
```

The Dockerfile installs OpenSCAD as well as the converter. Its build context is restricted to explicitly allowed source files: local disk images and generated outputs are excluded. Builds require internet access to download dependencies; conversion and model generation can run offline.

Create `input` and `output` directories and place acquired disk images in `input`.

macOS/Linux:

```sh
mkdir -p input output
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force input,output
```

These single-line Docker commands work in a POSIX shell or PowerShell from the repository directory:

```sh
docker run --rm --network none --mount "type=bind,source=${PWD}/input,target=/input,readonly" mo-sonar-tools convert inspect "/input/*.bin"
docker run --rm --network none --mount "type=bind,source=${PWD}/input,target=/input,readonly" --mount "type=bind,source=${PWD}/output,target=/output" mo-sonar-tools convert extract "/input/*.bin" --output /output
```

The input mount is read-only; outputs go to the host's `output` directory. On Linux, add `--user "$(id -u):$(id -g)"` after `--rm` if you want files owned by your current user. Use absolute host paths instead of `${PWD}/...` when working elsewhere. Windows Command Prompt has different variable syntax; use PowerShell for the examples above.

## What the converter writes

| Output | Contents |
|---|---|
| `contact_sheet_*.png` | First chunk of every catalogue entry, for browsing |
| `<entry>/rows_*.png` | Original grayscale intensity bytes, lossless; one scan per row |
| `<entry>/rows_*.trailers.bin` | All 256 metadata/reserved bytes per scan, unchanged |
| `<entry>/rows_*.preview.png` | Contrast-enhanced, resized viewing copies |
| `<entry>/lines.csv` | Disk offsets, BCD times, reference counters and continuity checks |
| `catalogue_header.bin` | Exact original 16-KiB header |
| `unclaimed/*.bin` | Uncatalogued nonconstant blocks, preserved without interpretation |
| `manifest.json` | Source hash, original label bytes, catalogue fields, chunk locations, warnings and validation results |

CSV files contain **metadata, not the echo samples or a depth map**. The PNG pixels contain the samples; trailers preserve every remaining record byte. Dates are retained without guessing their order. Invalid times appear as an empty decoded field plus the original bytes.

Scans are split into 2,048-row chunks by default, bounding memory use on long entries. Use `--chunk-rows 1024` or another value from 1 to 4,096. Contact sheets show only the first chunk of each entry; browse every preview to see a complete run. Preview aspect ratios are for viewing and are not spatially calibrated.

Every extracted record is re-read from the PNG and trailer and compared byte-for-byte with the input. Uncatalogued raw exports are also verified. Constant `CF23`, `00` and `FF` block ranges are recorded in the manifest. The input hash is checked again before completion. An interrupted run leaves a manifest with `status: incomplete`; retry into a new destination.

To reconstruct a record, concatenate its 3,840 **decoded PNG pixel bytes** with the corresponding 256 bytes from its trailer file. Use the manifest's record index to place it at `16384 + index * 4096`. The header and unclaimed ranges account for the rest of the image. Do not concatenate compressed PNG file bytes directly.

The converter reads regular image files, not `/dev` devices. It does not acquire physical disks, repair broken catalogues or certify the quality of an original disk acquisition. Retain the source image and acquisition logs.

## Extract candidate navigation metadata

```sh
python metadata.py /path/to/disk.bin --output metadata-output
```

Or use the same read-only input and output mounts as above:

```sh
docker run --rm --network none --mount "type=bind,source=${PWD}/input,target=/input,readonly" --mount "type=bind,source=${PWD}/output,target=/output" mo-sonar-tools metadata /input/disk.bin --output /output
```

This standard-library-only extractor writes `metadata.csv` and `manifest.json` in a new `<filename>_<hash-prefix>_metadata` directory. It validates the supported catalogue, reads only catalogued records, refuses overwrite and checks the source hash before marking completion. Memory use grows with the number of distinct serial payloads. Use `convert extract` for complete disk preservation.

The CSV retains every trailer byte, candidate GGA/GLL/RMC numeric fields, unsigned coordinate magnitudes, raw hemisphere/status tokens, candidate direction/speed and repeated-payload provenance. Empty cells mean unavailable or unparsed, not zero. Final unterminated numeric fields are deliberately left undecoded. Clock differences are same-day arithmetic, with no midnight correction or synchronization assumption. Repeated navigation can be stale; no automatic track or georeferencing is produced.

See [format findings and the CSV column reference](METADATA.md) for offsets, confidence, limitations and documentation references. These fields do not provide measured seabed geometry for the relief generator.

## Generate an OpenSCAD relief

Choose an original grayscale PNG chunk and a crop around a target. Crop coordinates are `LEFT TOP RIGHT BOTTOM`, in stored PNG pixels; right and bottom are exclusive. Whole-image input works when `--crop` is omitted.

```sh
python relief.py /path/to/scan.png --crop 100 50 900 350 --output model
```

This creates `relief.scad`, `geometry.scad`, the source crop, a derived intensity heightmap and local provenance. Open `relief.scad` in OpenSCAD, keeping `geometry.scad` next to it. The output directory must not already exist.

To export STL and a rendered preview automatically, install OpenSCAD and run:

```sh
python relief.py /path/to/scan.png --crop 100 50 900 350 --output model-export --stl --render
```

The script searches PATH and common macOS/Windows install locations. Use `--openscad "/path/to/executable"` if necessary. On headless Linux, PNG rendering needs an OpenGL-capable display; the Docker image includes Xvfb and starts it automatically.

Docker includes everything. Replace `/output/path/to/scan.png` with an actual extracted original PNG inside your host's `output` directory:

```sh
docker run --rm --network none --mount "type=bind,source=${PWD}/output,target=/output" mo-sonar-tools relief /output/path/to/scan.png --crop 100 50 900 350 --output /output/model --stl --render
```

Adjust `--width`, `--depth`, `--base` and `--height` for model dimensions; `--grid-width` / `--grid-height` for mesh resolution; `--blur` and `--contrast-cutoff` for display processing. Dark pixels are raised by default; `--bright-high` reverses that. Dimensions and intensity polarity can also be changed in the generated OpenSCAD file.

**An intensity relief is not a recovered 3D wreck.** Echo strength depends on material, orientation, acoustic shadows and recorder settings. The model's millimetres, proportions and height are arbitrary visualization choices. The generator does not infer hidden hull structure or decode GPS. A more faithful reconstruction needs geometry calibration and additional evidence such as calibrated shadows, independent passes, bathymetric sonar or underwater photographs. See [NOAA's side-scan explanation](https://oceanexplorer.noaa.gov/technology/sonar-side-scan/).

## Testing and data handling

```sh
python -m unittest discover -s tests -v
docker run --rm --network none --entrypoint python -e TEST_OPENSCAD=1 mo-sonar-tools -m unittest discover -s tests -v
```

Tests generate arithmetic pixel patterns and artificial catalogues at runtime. They check full-image byte reconstruction, unsupported/truncated layouts, unknown-block preservation, batch behavior, safe output paths, discontinuities and closed mesh topology. The optional OpenSCAD test exports STL and PNG and checks the exported mesh's winding and volume. CI runs Python tests on Windows, macOS and Linux and OpenSCAD tests in Docker.

No real disk image, recovered image, target name, source hash or source metadata is included in the repository. `.gitignore` excludes acquired and generated file types; `.dockerignore` allows only application and test source files. Local output manifests do contain source paths and metadata—treat those outputs as part of your dataset, not as material to publish automatically.

MIT licensed. The software license grants no rights to datasets processed with it.
