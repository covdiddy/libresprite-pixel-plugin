"""Native reader/writer for the .aseprite file format plus an in-memory sprite model.

LibreSprite's JavaScript API cannot add frames, layers or tags in batch mode, so the
plugin edits .aseprite files directly (the format is open and documented) and uses
LibreSprite itself only for exports it does better (GIF/JPG/BMP) and for validation.

All pixel data is held as RGBA (4 bytes/pixel). Indexed and grayscale files are
converted to RGBA on load (and saved as RGB).
"""
import struct
import zlib

MAGIC = 0xA5E0
FRAME_MAGIC = 0xF1FA

DIRECTIONS = {0: "forward", 1: "reverse", 2: "pingpong", 3: "pingpong_reverse"}
DIRECTION_IDS = {v: k for k, v in DIRECTIONS.items()}


class AseError(Exception):
    pass


class Layer:
    def __init__(self, name, visible=True, opacity=255, blend=0, kind=0, child_level=0, flags=None):
        self.name = name
        self.kind = kind  # 0 image layer, 1 group
        self.child_level = child_level
        self.blend = blend
        self.opacity = opacity
        self.flags = flags if flags is not None else (3 if visible else 2)  # visible|editable

    @property
    def visible(self):
        return bool(self.flags & 1)

    @visible.setter
    def visible(self, v):
        self.flags = (self.flags | 1) if v else (self.flags & ~1)


class Cel:
    """A full-canvas RGBA image (width*height*4 bytes) or a link to another frame."""

    def __init__(self, data=None, x=0, y=0, opacity=255, link=None):
        self.data = data
        self.x = x
        self.y = y
        self.opacity = opacity
        self.link = link  # frame index of the source cel (same layer) when linked


class Tag:
    def __init__(self, name, from_frame, to_frame, direction="forward"):
        self.name = name
        self.from_frame = from_frame  # 0-based inclusive
        self.to_frame = to_frame
        self.direction = direction


class Sprite:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.color_mode = "rgb"
        self.durations = [100]
        self.layers = [Layer("Layer 1")]
        self.cels = {}  # (layer_index, frame_index) -> Cel
        self.tags = []
        self.palette = default_palette()
        self.path = None
        self.source_mode = "rgb"

    # -- helpers ---------------------------------------------------------
    @property
    def frame_count(self):
        return len(self.durations)

    def blank(self):
        return bytearray(self.width * self.height * 4)

    def layer_index(self, name):
        for i, l in enumerate(self.layers):
            if l.name == name:
                return i
        raise AseError("Layer not found: %s" % name)

    def check_frame(self, frame_number):
        """Convert a 1-based frame number to a 0-based index, validating the range."""
        if frame_number < 1 or frame_number > self.frame_count:
            raise AseError("Frame not found: %d (sprite has %d frames)" % (frame_number, self.frame_count))
        return frame_number - 1

    def resolve(self, li, fi):
        """Return the cel that actually holds pixel data for (layer, frame), following links."""
        cel = self.cels.get((li, fi))
        seen = 0
        while cel is not None and cel.link is not None and seen < 64:
            cel = self.cels.get((li, cel.link))
            seen += 1
        return cel

    def get_image(self, li, fi, create=True):
        """Return the writable bytearray for a layer/frame, creating the cel if needed.

        Writing to a linked cel edits the shared source image (like the original editor)."""
        cel = self.resolve(li, fi)
        if cel is None:
            if not create:
                return None
            cel = Cel(self.blank())
            self.cels[(li, fi)] = cel
        return cel.data

    def unlink_all(self):
        for key, cel in list(self.cels.items()):
            if cel.link is not None:
                src = self.resolve(*key)
                self.cels[key] = Cel(bytearray(src.data) if src else self.blank(), 0, 0, cel.opacity)

    def flatten_frame(self, fi, only_visible=True):
        out = self.blank()
        for li, layer in enumerate(self.layers):
            if layer.kind == 1 or (only_visible and not layer.visible):
                continue
            cel = self.resolve(li, fi)
            if cel is None:
                continue
            op = (layer.opacity * self.cels[(li, fi)].opacity) // 255
            _blend_over(out, cel.data, op)
        return out


def _blend_over(dst, src, opacity=255):
    n = len(dst) // 4
    for i in range(n):
        o = i * 4
        sa = src[o + 3]
        if opacity != 255:
            sa = sa * opacity // 255
        if sa == 0:
            continue
        da = dst[o + 3]
        if sa == 255 or da == 0:
            dst[o] = src[o]
            dst[o + 1] = src[o + 1]
            dst[o + 2] = src[o + 2]
            dst[o + 3] = sa
            continue
        oa = sa + da * (255 - sa) // 255
        if oa == 0:
            continue
        for c in range(3):
            dst[o + c] = (src[o + c] * sa * 255 + dst[o + c] * da * (255 - sa)) // (oa * 255)
        dst[o + 3] = oa


def default_palette():
    """A 32-colour starter palette (the first entry is transparent-black like Aseprite's default)."""
    base = [
        (0, 0, 0, 0), (0, 0, 0, 255), (255, 255, 255, 255), (136, 0, 0, 255),
        (170, 255, 238, 255), (204, 68, 204, 255), (0, 204, 85, 255), (0, 0, 170, 255),
        (238, 238, 119, 255), (221, 136, 85, 255), (102, 68, 0, 255), (255, 119, 119, 255),
        (51, 51, 51, 255), (119, 119, 119, 255), (170, 255, 102, 255), (0, 136, 255, 255),
        (187, 187, 187, 255),
    ]
    return list(base)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _read_string(buf, p):
    (n,) = struct.unpack_from("<H", buf, p)
    return buf[p + 2:p + 2 + n].decode("utf-8", "replace"), p + 2 + n


def load(path):
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 128:
        raise AseError("Not an .aseprite file: %s" % path)
    size, magic, nframes, w, h, depth, flags, speed = struct.unpack_from("<IHHHHHIH", d, 0)
    if magic != MAGIC:
        raise AseError("Not an .aseprite file (bad magic): %s" % path)
    transparent_index = d[28]
    spr = Sprite(w, h)
    spr.path = path
    spr.layers = []
    spr.durations = []
    spr.palette = []
    mode = {32: "rgb", 16: "grayscale", 8: "indexed"}.get(depth)
    if mode is None:
        raise AseError("Unsupported colour depth: %d" % depth)
    spr.source_mode = mode
    pending_cels = []
    off = 128
    old_pal_seen = False
    for fi in range(nframes):
        fsize, fmagic, old_n, dur, _, new_n = struct.unpack_from("<IHHH2sI", d, off)
        if fmagic != FRAME_MAGIC:
            raise AseError("Corrupt frame header")
        nchunks = new_n if new_n else old_n
        spr.durations.append(dur if dur else speed or 100)
        p = off + 16
        for _c in range(nchunks):
            csize, ctype = struct.unpack_from("<IH", d, p)
            body = p + 6
            end = p + csize
            if ctype == 0x2004:
                lflags, ltype, level, _dw, _dh, blend, opacity = struct.unpack_from("<HHHHHHB", d, body)
                name, _ = _read_string(d, body + 16)
                spr.layers.append(Layer(name, opacity=opacity, blend=blend, kind=ltype, child_level=level, flags=lflags))
            elif ctype == 0x2005:
                li, cx, cy, copacity, ctype2 = struct.unpack_from("<HhhBH", d, body)
                q = body + 16
                if ctype2 == 1:
                    (src,) = struct.unpack_from("<H", d, q)
                    spr.cels[(li, fi)] = Cel(None, cx, cy, copacity, link=src)
                elif ctype2 in (0, 2):
                    cw, ch = struct.unpack_from("<HH", d, q)
                    raw = d[q + 4:end]
                    if ctype2 == 2:
                        raw = zlib.decompress(raw)
                    bpp = depth // 8
                    if len(raw) < cw * ch * bpp:
                        raise AseError("Truncated cel data")
                    pending_cels.append((li, fi, cx, cy, copacity, cw, ch, raw))
                # type 3 (tilemap) is unsupported -> ignored
            elif ctype == 0x2018:
                (ntags,) = struct.unpack_from("<H", d, body)
                q = body + 10
                for _t in range(ntags):
                    f0, f1, direction = struct.unpack_from("<HHB", d, q)
                    name, q2 = _read_string(d, q + 17)
                    spr.tags.append(Tag(name, f0, f1, DIRECTIONS.get(direction, "forward")))
                    q = q2
            elif ctype == 0x2019:
                size_, first, last = struct.unpack_from("<III", d, body)
                if len(spr.palette) < size_:
                    spr.palette.extend([(0, 0, 0, 255)] * (size_ - len(spr.palette)))
                q = body + 20
                for idx in range(first, last + 1):
                    eflags, r, g, b, a = struct.unpack_from("<HBBBB", d, q)
                    q += 6
                    if eflags & 1:
                        _, q = _read_string(d, q)
                    spr.palette[idx] = (r, g, b, a)
                old_pal_seen = True
            elif ctype in (0x0004, 0x0011) and not old_pal_seen:
                (npk,) = struct.unpack_from("<H", d, body)
                q = body + 2
                idx = 0
                for _k in range(npk):
                    skip, cnt = d[q], d[q + 1]
                    q += 2
                    idx += skip
                    cnt = cnt or 256
                    for _j in range(cnt):
                        r, g, b = d[q], d[q + 1], d[q + 2]
                        if ctype == 0x0011:
                            r, g, b = r * 255 // 63, g * 255 // 63, b * 255 // 63
                        while len(spr.palette) <= idx:
                            spr.palette.append((0, 0, 0, 255))
                        spr.palette[idx] = (r, g, b, 255)
                        q += 3
                        idx += 1
            p = end
        off += fsize
    if not spr.palette:
        spr.palette = default_palette()
    for li, fi, cx, cy, copacity, cw, ch, raw in pending_cels:
        spr.cels[(li, fi)] = Cel(_to_canvas(spr, raw, cw, ch, cx, cy, depth, transparent_index), 0, 0, copacity)
    if not spr.layers:
        spr.layers = [Layer("Layer 1")]
    return spr


def _to_canvas(spr, raw, cw, ch, cx, cy, depth, transparent_index):
    out = spr.blank()
    W, H = spr.width, spr.height
    pal = spr.palette
    bpp = depth // 8
    for y in range(ch):
        ty = y + cy
        if ty < 0 or ty >= H:
            continue
        for x in range(cw):
            tx = x + cx
            if tx < 0 or tx >= W:
                continue
            s = (y * cw + x) * bpp
            o = (ty * W + tx) * 4
            if depth == 32:
                out[o:o + 4] = raw[s:s + 4]
            elif depth == 16:
                v, a = raw[s], raw[s + 1]
                out[o] = out[o + 1] = out[o + 2] = v
                out[o + 3] = a
            else:
                idx = raw[s]
                if idx == transparent_index and False:
                    continue
                if idx < len(pal):
                    r, g, b, a = pal[idx]
                    out[o:o + 4] = bytes((r, g, b, a))
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _string(s):
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def _chunk(ctype, body):
    return struct.pack("<IH", len(body) + 6, ctype) + body


def save(spr, path=None):
    path = path or spr.path
    if not path:
        raise AseError("No output path")
    W, H = spr.width, spr.height
    pal = spr.palette[:256] if spr.palette else default_palette()
    frames = []
    for fi in range(spr.frame_count):
        chunks = []
        if fi == 0:
            n = len(pal)
            body = struct.pack("<III", n, 0, n - 1) + b"\x00" * 8
            for r, g, b, a in pal:
                body += struct.pack("<HBBBB", 0, r, g, b, a)
            chunks.append(_chunk(0x2019, body))
            if spr.tags:
                tb = struct.pack("<H", len(spr.tags)) + b"\x00" * 8
                for t in spr.tags:
                    tb += struct.pack("<HHB", t.from_frame, t.to_frame, DIRECTION_IDS.get(t.direction, 0))
                    tb += b"\x00" * 8 + b"\x00\x00\x00" + b"\x00" + _string(t.name)
                chunks.append(_chunk(0x2018, tb))
            for layer in spr.layers:
                lb = struct.pack("<HHHHHHB", layer.flags, layer.kind, layer.child_level, 0, 0, layer.blend, layer.opacity)
                lb += b"\x00" * 3 + _string(layer.name)
                chunks.append(_chunk(0x2004, lb))
        for li in range(len(spr.layers)):
            cel = spr.cels.get((li, fi))
            if cel is None or spr.layers[li].kind == 1:
                continue
            if cel.link is not None:
                cb = struct.pack("<HhhBH", li, cel.x, cel.y, cel.opacity, 1) + b"\x00" * 7 + struct.pack("<H", cel.link)
            else:
                if not any(cel.data[3::4]):
                    continue  # fully transparent: omit
                comp = zlib.compress(bytes(cel.data))
                cb = struct.pack("<HhhBH", li, 0, 0, cel.opacity, 2) + b"\x00" * 7 + struct.pack("<HH", W, H) + comp
            chunks.append(_chunk(0x2005, cb))
        body = b"".join(chunks)
        hdr = struct.pack("<IHHH2sI", 16 + len(body), FRAME_MAGIC, min(len(chunks), 0xFFFF), spr.durations[fi], b"\x00\x00", len(chunks))
        frames.append(hdr + body)
    payload = b"".join(frames)
    header = struct.pack("<IHHHHHIH", 128 + len(payload), MAGIC, spr.frame_count, W, H, 32, 1, spr.durations[0])
    header += b"\x00" * 8 + b"\x00" + b"\x00" * 3 + struct.pack("<H", len(pal)) + bytes((1, 1)) + struct.pack("<hhHH", 0, 0, 16, 16)
    header += b"\x00" * (128 - len(header))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header + payload)
    import os
    os.replace(tmp, path)
    spr.path = path
