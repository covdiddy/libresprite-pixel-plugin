"""Minimal PNG reader/writer (standard library only). Pixels are RGBA bytes."""
import struct
import zlib


def write_png(path, width, height, rgba):
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def read_png(path):
    """Return (width, height, rgba bytearray). Supports all colour types, bit depths 1-16, no interlace."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")
    p = 8
    idat = b""
    plte = None
    trns = None
    w = h = depth = ctype = interlace = 0
    while p < len(d):
        (n,) = struct.unpack_from(">I", d, p)
        t = d[p + 4:p + 8]
        body = d[p + 8:p + 8 + n]
        p += 12 + n
        if t == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif t == b"PLTE":
            plte = body
        elif t == b"tRNS":
            trns = body
        elif t == b"IDAT":
            idat += body
        elif t == b"IEND":
            break
    if interlace:
        raise ValueError("Interlaced PNG is not supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = depth * channels
    bpp = max(1, bits // 8)
    rowbytes = (w * bits + 7) // 8
    raw = zlib.decompress(idat)
    rows = []
    prev = bytearray(rowbytes)
    pos = 0
    for _y in range(h):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + rowbytes])
        pos += 1 + rowbytes
        if ft == 1:
            for i in range(bpp, rowbytes):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(rowbytes):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(rowbytes):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(rowbytes):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(line)
        prev = line
    out = bytearray(w * h * 4)
    maxv = (1 << depth) - 1

    def sample(line, idx):
        if depth == 8:
            return line[idx]
        if depth == 16:
            return line[idx * 2]  # high byte
        per = 8 // depth
        byte = line[idx // per]
        shift = 8 - depth * (idx % per + 1)
        return (byte >> shift) & maxv

    for y, line in enumerate(rows):
        for x in range(w):
            o = (y * w + x) * 4
            if ctype == 6:
                for c in range(4):
                    out[o + c] = sample(line, x * 4 + c)
            elif ctype == 2:
                for c in range(3):
                    out[o + c] = sample(line, x * 3 + c)
                out[o + 3] = 255
            elif ctype == 0:
                v = sample(line, x)
                if depth < 8:
                    v = v * 255 // maxv
                out[o] = out[o + 1] = out[o + 2] = v
                out[o + 3] = 255
            elif ctype == 4:
                v = sample(line, x * 2)
                out[o] = out[o + 1] = out[o + 2] = v
                out[o + 3] = sample(line, x * 2 + 1)
            else:  # palette
                i = sample(line, x)
                if plte is None or i * 3 + 2 >= len(plte):
                    out[o:o + 4] = b"\x00\x00\x00\xff"
                else:
                    out[o] = plte[i * 3]
                    out[o + 1] = plte[i * 3 + 1]
                    out[o + 2] = plte[i * 3 + 2]
                    out[o + 3] = trns[i] if trns and i < len(trns) else 255
    return w, h, out
