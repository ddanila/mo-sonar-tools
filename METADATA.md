# Metadata format findings

This is a provisional profile derived by testing alternative byte interpretations against record structure and internal consistency. It is not a manufacturer specification. This document publishes format rules and methods only: no survey names, coordinates, dates, source hashes, extracted samples or dataset-specific statistics.

## Record layout and confidence

Offsets below are hexadecimal, relative to a 4096-byte record. The record area starts after the 16384-byte catalogue. These rules apply only after the catalogue/layout checks pass.

| Offset | Interpretation | Confidence and limits |
|---|---|---|
| `000–EFF` | 3840 grayscale intensity samples | Byte-preserving image extraction; physical geometry uncalibrated |
| `F00–FFF` | 256-byte trailer | Preserve all bytes, including unknown fields |
| `F80–F82` | BCD hours, minutes, seconds | Validated as a clock; calendar date and time zone unresolved |
| `F83` | Unknown setting byte | Possible mode/range/display setting; no unit assigned |
| `F84` | Candidate bottom/water-column tracking index | Image-boundary correlation supports the hypothesis, but it varies with settings; not metres |
| `F85–F86` | Little-endian unsigned direction candidate | Agreement with candidate navigation course supports degrees; heading versus course, reference frame and filtering unresolved |
| `F8C` | Candidate speed, byte divided by 5 | Agreement with candidate RMC speed supports knots; rounding/filtering unresolved |
| `F8D` | Unknown flags/settings | No bit meanings assigned |
| `F9C–F9F` | Little-endian unsigned reference counter | Whole-record comparisons can establish references to stored records; not automatically a unique ping ID |
| `FC0–FDF` | 32-byte candidate packed navigation payload | Partial numeric decoding only; may be absent, repeated, stale or truncated |

A direction outside `[0, 360)` is left undecoded. An in-range number alone does not prove that the field is a direction. Likewise, a zero speed byte is not proof of a stationary vessel. All remaining trailer bytes are unassigned.

## Packed numeric fields

Read each byte's low nibble before its high nibble. Decimal digits appear directly; nibble `A` behaves as a field separator and `B` as a decimal point. The following recurring prefix tokens are used as provisional family signatures:

| Prefix in low-nibble-first notation | Candidate family | Numeric field order, counting prefix as field 0 |
|---|---|---|
| `5cdedd01ce` | GGA-like | Time 1, latitude 2, longitude 4, quality 6, satellites 7, HDOP 8, GPS height 9 |
| `ecdedff` | GLL-like | Latitude 1, longitude 3, time 5 |
| `3cde025d43c8` | RMC-like | Time 1, latitude 3, longitude 5, speed 7, course 8 |

Hemisphere tokens follow their coordinate fields. Status tokens occur at GLL-like field 6 and RMC-like field 2. Their character encoding remains unresolved. These signatures are not decoded ASCII sentence identifiers. The remaining alphabet, escaping, termination and padding rules are unknown; this is **not a full NMEA decompressor**.

Only separator-terminated fields are decoded. The final field is retained in the raw payload but not interpreted, even when it looks numeric. This sacrifices some recoverable values to avoid silently reading a truncated number. Unsupported symbols, negative numbers and fractional-second clocks currently remain undecoded. Numeric candidates are not proof of a valid GPS fix. Checksums are never reported as validated.

Coordinate tokens resemble degrees and minutes (`DDMM…` for latitude, `DDDMM…` for longitude). The extractor validates degree/minute ranges and outputs **unsigned magnitudes**, without inferring N/S/E/W from geography. Datum, hemisphere and position of the GPS antenna relative to the sonar remain unresolved. GPS height is a navigation field candidate, not seabed depth.

The inferred field arrangement can be compared with the documented [GGA fields](https://help.fieldsystems.trimble.com/r580/nmea0183-messages-gga.htm) and [RMC fields](https://help.fieldsystems.trimble.com/r580/nmea0183-messages-rmc.htm). These references explain the protocol; they do not identify Trimble hardware in this format.

## Testing the hypotheses

Useful independent checks include comparing displacement per navigation time with candidate speed, comparing track bearing with candidate course, and comparing separately stored direction/speed fields against navigation values. These tests support a navigation interpretation more strongly than plausible numeric ranges alone. They must exclude invalid, repeated and stale messages, and require independently established coordinate signs before geographic use.

The bottom-index hypothesis can be tested against the image's water-column boundary within each stable setting interval. A correlation that changes between intervals suggests scaling or mode dependence, rather than an immediately usable depth calibration.

Repeated serial payloads and saved copies of whole scan records are separate phenomena. Byte-identical payloads can recur at later scan times. Whole-record equality at the reference counter can identify stored copies. Neither proves a new survey pass. The extractor keeps every catalogue row and reports both comparisons; it does not interpolate a track or assign navigation to every scan.

## CSV column reference

`metadata.py` writes one row per catalogued record in increasing disk-record order. It does not inspect unclaimed records for navigation. Blank decoded fields mean unavailable, invalid or unsupported; zero is a decoded value. Boolean values are written as `True` or `False`.

| Column(s) | Meaning |
|---|---|
| `catalogue_slot`, `entry_row`, `disk_record` | Zero-based catalogue slot, row within that entry, and index within the record area |
| `byte_offset` | Absolute byte offset in the image |
| `scan_time`, `scan_time_raw_hex` | Validated BCD clock as `HH:MM:SS`, and its three original bytes |
| `reference_counter_le32` | Unsigned reference counter, without chronology assumptions |
| `matches_record_at_counter` | Entire record equals the record at that in-bounds counter; may be a self-reference |
| `trailer_hex` | Every original trailer byte, in disk order |
| `serial_hex`, `serial_low_nibble_first` | Original 32-byte payload, and its nibble-order view |
| `candidate_family` | `GGA-like`, `GLL-like`, `RMC-like`, `unknown`, or `absent` for an all-zero payload |
| `candidate_time` | Validated candidate navigation clock; date/time zone not established |
| `scan_minus_navigation_seconds` | Raw same-day scan clock minus navigation clock; nonzero flags a mismatch, not proven latency; no midnight correction |
| `latitude_token`, `longitude_token` | Complete packed numeric field tokens, before decimal conversion |
| `candidate_latitude_magnitude`, `candidate_longitude_magnitude` | Unsigned decimal-degree magnitudes; never signed geographic coordinates |
| `latitude_hemisphere_token`, `longitude_hemisphere_token` | Uninterpreted packed tokens; no guessed signs |
| `candidate_status_token` | Uninterpreted navigation status token |
| `candidate_quality`, `candidate_satellites` | Integer-valued GGA-like candidates; receiver-specific quality meanings unassigned |
| `candidate_hdop`, `candidate_gps_height` | GGA-like decimal candidates; height units/reference not independently decoded |
| `candidate_speed_knots`, `candidate_course_degrees` | RMC-like numeric candidates; course limited to `[0, 360)` |
| `checksum_validated` | Always false in this decoder |
| `direction_raw_le16`, `candidate_direction_degrees` | Raw direction word and range-checked candidate |
| `candidate_speed_byte_knots` | Independent speed-byte hypothesis, `F8C / 5` |
| `candidate_bottom_index` | Raw `F84` byte under a provisional tracking-index interpretation |
| `unknown_f83`, `unknown_f8d` | Uninterpreted bytes as decimal integers |
| `serial_repeated` | Identical nonzero payload seen in an earlier exported disk record |
| `serial_first_seen_disk_record` | Earliest prior exported index with that payload; blank for first occurrence/absent payload |

The local manifest records input identity, completion state and candidate-family counts. Its hash is checked again after extraction. The metadata export is not a complete image backup; use the image converter for byte-preserving recovery.

## Recorder and format identification

MO capacity identifies a storage medium class, not a sonar model. A PC recorder can combine sonar imagery with serial data from an external navigation receiver. The application format may therefore be shared across sonar hardware, and a navigation protocol match does not identify the acoustic instrument.

A 256-byte trailer alone is insufficient to identify Q-MIPS. Its published trailer layout must also match the field offsets; the candidate clock here lies at trailer offset 128, whereas the documented Q-MIPS clock fields start near the beginning. See the [USGS Q-MIPS format description](https://woodshole.er.usgs.gov/publications/of96-83/HTMLDOCS/QMIPS.HTM). Historical acquisition systems and additional layouts are documented in the [USGS MudScan report](https://pubs.usgs.gov/of/1993/0242/report.pdf) and [Xsonar manual](https://pubs.usgs.gov/of/1997/0686/report.pdf). They are comparison leads, not positive identifications.

A defensible model identification still needs a matching specification, recorder software, or a controlled sample with known equipment and settings. No manufacturer or recorder model is assigned by these tools.
