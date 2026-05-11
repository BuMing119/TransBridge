import struct
import sys

sys.stdout.reconfigure(encoding='utf-8')

data = open('tests/trans_exe/xt/hlioremi_english_chinese.sst', 'rb').read()
name_len = int.from_bytes(data[8:10], 'big')
start = 12 + name_len + 2 + 8

VALID_SUFFIXES = (
    'FULL', 'NAM1', 'NAM2', 'DATA', 'DESC', 'NAME', 'GOLD', 'SNAM',
    'QNAM', 'CNAM', 'EDID', 'MODL', 'MODT', 'DNAM', 'ITXT', 'NNAM',
    'RNAM', 'SHRT',
)

record_starts = []
pos = start
while pos < len(data) - 30:
    try:
        edid = data[pos + 4 : pos + 12].decode('ascii')
    except:
        pos += 1
        continue
    if not edid.endswith(VALID_SUFFIXES) or not edid.isupper():
        pos += 1
        continue
    str_idx = struct.unpack_from('<H', data, pos + 20)[0]
    str_len = struct.unpack_from('<H', data, pos + 22)[0]
    if str_idx == 0x4000 or str_len == 0 or str_len > 100000:
        pos += 1
        continue
    str_start = pos + 26
    if str_start + str_len > len(data):
        pos += 1
        continue
    try:
        data[str_start : str_start + min(str_len, 40)].decode('utf-16-le')
    except:
        pos += 1
        continue

    form_id = struct.unpack_from('<I', data, pos)[0]

    try:
        text = data[str_start : str_start + str_len].decode('utf-16-le')
    except:
        text = '<error>'

    record_starts.append((pos, edid, form_id, str_len, text))
    pos += 26 + str_len

MARKERS = (b'\x02\x00\x00\x00\x00', b'\x00\x00\x00\x00\x00', b'\x01\x00\x00\x00\x00')

for i in [111, 195, 243, 257, 709, 778, 918, 952, 1023, 1040]:
    pos = record_starts[i][0]
    rec_end = pos + 26 + record_starts[i][3]
    next_pos = record_starts[i+1][0]
    gap = data[rec_end:next_pos]

    print(f'=== Record {i}: {record_starts[i][1]} form_id=0x{record_starts[i][2]:08X} ===')
    print(f'English: {record_starts[i][4][:55]!r}')

    # Parse Chinese text from gap head
    chn_len = struct.unpack_from('<I', gap, 0)[0]
    chn_text = gap[4:4+chn_len]
    try:
        chn_decoded = chn_text.decode('utf-16-le')
        print(f'Chinese: {chn_decoded!r}')
    except:
        print(f'Chinese: [hex] {chn_text.hex()}')

    # Extra data starts after Chinese text
    extra = gap[4+chn_len:]
    print(f'Extra length: {len(extra)} bytes')

    offset = 0
    subrecord = 0

    while offset < len(extra):
        # Check for prefix/separator/suffix marker
        if offset + 5 <= len(extra) and extra[offset:offset+5] in MARKERS:
            marker = extra[offset:offset+5]
            label = 'Prefix' if subrecord == 0 else 'Marker'
            print(f'  {label}: {marker.hex()}')
            offset += 5
            continue

        # Read subrecord header
        if offset + 22 > len(extra):
            remaining = extra[offset:]
            if remaining:
                # Try decode remaining as UTF-16LE
                if len(remaining) % 2 == 0:
                    try:
                        decoded = remaining.decode('utf-16-le')
                        print(f'  Remaining text: {decoded!r}')
                    except:
                        print(f'  Remaining: {remaining.hex()}')
                else:
                    print(f'  Remaining: {remaining.hex()}')
            break

        subrecord += 1
        ref_form_id = struct.unpack_from('<I', extra, offset)[0]
        try:
            ref_edid = extra[offset+4:offset+12].decode('ascii')
        except:
            ref_edid = extra[offset+4:offset+12].hex()
        ref_unk12 = struct.unpack_from('<I', extra, offset+12)[0]
        ref_f2 = struct.unpack_from('<I', extra, offset+16)[0]
        ref_str_idx = struct.unpack_from('<H', extra, offset+20)[0]
        offset += 22

        print(f'  Subrecord {subrecord}: form_id=0x{ref_form_id:08X} edid={ref_edid}')

        # Read text blocks
        block = 0
        while offset + 4 <= len(extra):
            # Check for marker before reading block
            if offset + 5 <= len(extra) and extra[offset:offset+5] in MARKERS:
                break

            data_len = struct.unpack_from('<I', extra, offset)[0]
            if data_len == 0 or data_len > len(extra) - offset - 4 or data_len > 1000:
                break

            block += 1
            data_bytes = extra[offset+4:offset+4+data_len]
            offset += 4 + data_len

            if data_len % 2 == 0:
                try:
                    decoded = data_bytes.decode('utf-16-le')
                    print(f'    Block {block} ({data_len}b): {decoded!r}')
                except:
                    print(f'    Block {block} ({data_len}b): [hex] {data_bytes.hex()}')
            else:
                print(f'    Block {block} ({data_len}b): [hex] {data_bytes.hex()}')

    print()
