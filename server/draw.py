"""Pixel drawing primitives, dithering, outline, shading and sprite transforms.

Everything works on RGBA bytearrays shaped (height, width) with 4 bytes/pixel.
"""
import colorsys
import math
import re
import random

from ase import AseError, Cel

HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

def parse_color(s):
    m = HEX_RE.match(str(s).strip())
    if not m:
        raise AseError("Invalid hex color: %r (expected #RRGGBB or #RRGGBBAA)" % (s,))
    h = m.group(1)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return (r, g, b, a)


def to_hex(c, with_alpha=True):
    if with_alpha:
        return "#%02X%02X%02X%02X" % (c[0], c[1], c[2], c[3])
    return "#%02X%02X%02X" % (c[0], c[1], c[2])


def nearest(color, palette):
    """Nearest opaque palette colour (by RGB distance)."""
    best = None
    bd = 1 << 60
    for p in palette:
        if p[3] == 0:
            continue
        d = (color[0] - p[0]) ** 2 + (color[1] - p[1]) ** 2 + (color[2] - p[2]) ** 2
        if d < bd:
            bd = d
            best = p
    if best is None:
        return color
    return (best[0], best[1], best[2], color[3])


def maybe_snap(spr, color, use_palette):
    if use_palette and spr.palette:
        return nearest(color, spr.palette)
    return color


def luminance(c):
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


# ---------------------------------------------------------------------------
# Pixel helpers
# ---------------------------------------------------------------------------

def put(img, W, H, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        o = (y * W + x) * 4
        img[o] = c[0]
        img[o + 1] = c[1]
        img[o + 2] = c[2]
        img[o + 3] = c[3]


def getpx(img, W, x, y):
    o = (y * W + x) * 4
    return (img[o], img[o + 1], img[o + 2], img[o + 3])


def brush(img, W, H, x, y, c, t):
    if t <= 1:
        put(img, W, H, x, y, c)
        return
    lo = -(t // 2)
    for dy in range(lo, lo + t):
        for dx in range(lo, lo + t):
            put(img, W, H, x + dx, y + dy, c)


def line(img, W, H, x1, y1, x2, y2, c, t=1):
    dx, dy = abs(x2 - x1), -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    x, y = x1, y1
    for _ in range(10 ** 7):
        brush(img, W, H, x, y, c, t)
        if x == x2 and y == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def rect(img, W, H, x, y, w, h, c, filled):
    if filled:
        for yy in range(max(0, y), min(H, y + h)):
            for xx in range(max(0, x), min(W, x + w)):
                put(img, W, H, xx, yy, c)
    else:
        for xx in range(x, x + w):
            put(img, W, H, xx, y, c)
            put(img, W, H, xx, y + h - 1, c)
        for yy in range(y, y + h):
            put(img, W, H, x, yy, c)
            put(img, W, H, x + w - 1, yy, c)


def circle(img, W, H, cx, cy, r, c, filled):
    if r <= 0:
        put(img, W, H, cx, cy, c)
        return
    if filled:
        for dy in range(-r, r + 1):
            span = int(math.sqrt(max(0, r * r + r - dy * dy)))
            for dx in range(-span, span + 1):
                put(img, W, H, cx + dx, cy + dy, c)
        return
    x, y, d = r, 0, 1 - r
    while x >= y:
        for px, py in ((x, y), (y, x), (-x, y), (-y, x), (x, -y), (y, -x), (-x, -y), (-y, -x)):
            put(img, W, H, cx + px, cy + py, c)
        y += 1
        if d < 0:
            d += 2 * y + 1
        else:
            x -= 1
            d += 2 * (y - x) + 1


def flood_fill(img, W, H, x, y, c, tolerance=0):
    if not (0 <= x < W and 0 <= y < H):
        raise AseError("Fill start point (%d,%d) is outside the canvas" % (x, y))
    target = getpx(img, W, x, y)
    if target == c:
        return 0
    def match(p):
        return all(abs(p[i] - target[i]) <= tolerance for i in range(4))
    stack = [(x, y)]
    seen = bytearray(W * H)
    n = 0
    while stack:
        px, py = stack.pop()
        if not (0 <= px < W and 0 <= py < H):
            continue
        k = py * W + px
        if seen[k]:
            continue
        seen[k] = 1
        if not match(getpx(img, W, px, py)):
            continue
        put(img, W, H, px, py, c)
        n += 1
        stack.extend(((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)))
    return n


# ---------------------------------------------------------------------------
# Dithering
# ---------------------------------------------------------------------------

BAYER = {
    "bayer_2x2": [[0, 2], [3, 1]],
    "bayer_4x4": [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
    "bayer_8x8": [
        [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
        [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
        [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
        [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]],
}
# Binary texture patterns: 1 = colour2, 0 = colour1.
BINARY = {
    "checkerboard": [[0, 1], [1, 0]],
    "grass": [[1, 0, 1, 0, 1, 0], [0, 1, 1, 0, 0, 1], [1, 1, 0, 1, 0, 0], [0, 1, 0, 1, 1, 0], [1, 0, 0, 0, 1, 1], [0, 0, 1, 1, 0, 1]],
    "water": [[0, 0, 1, 1, 0, 0], [0, 1, 1, 1, 1, 0], [1, 1, 0, 0, 1, 1], [1, 0, 0, 0, 0, 1], [0, 1, 1, 1, 1, 0], [0, 0, 1, 1, 0, 0]],
    "stone": [[0, 0, 0, 1, 1, 0], [0, 1, 0, 0, 1, 1], [0, 0, 1, 1, 0, 0], [1, 1, 0, 0, 0, 1], [1, 0, 0, 1, 1, 0], [0, 1, 1, 0, 0, 0]],
    "cloud": [[0, 0, 0, 0, 1, 1], [0, 0, 0, 1, 1, 1], [0, 0, 1, 1, 1, 0], [0, 1, 1, 1, 0, 0], [1, 1, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0]],
    "brick": [[0, 0, 0, 0, 1, 0, 0, 0], [0, 0, 0, 0, 1, 0, 0, 0], [1] * 8, [0, 0, 1, 0, 0, 0, 0, 1], [0, 0, 1, 0, 0, 0, 0, 1], [1] * 8, [0, 0, 0, 0, 1, 0, 0, 0], [0, 0, 0, 0, 1, 0, 0, 0]],
    "dots": [[1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0]],
    "diagonal": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    "cross": [[0, 1, 0], [1, 1, 1], [0, 1, 0]],
    "noise": [[1, 0, 1, 0, 0, 1], [0, 1, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1], [0, 1, 1, 0, 1, 0], [0, 0, 1, 0, 1, 1], [1, 1, 0, 1, 0, 0]],
    "horizontal_lines": [[1, 1, 1, 1], [0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]],
    "vertical_lines": [[1, 0, 1, 0]] * 4,
}
PATTERNS = sorted(list(BAYER) + list(BINARY) + ["floyd_steinberg"])


def dither_region(img, W, H, rx, ry, rw, rh, c1, c2, pattern, density=0.5):
    if pattern not in PATTERNS:
        raise AseError("Invalid pattern: %s (must be one of: %s)" % (pattern, ", ".join(PATTERNS)))
    if pattern == "floyd_steinberg":
        return _floyd(img, W, H, rx, ry, rw, rh, c1, c2, density)
    if pattern in BAYER:
        m = BAYER[pattern]
        n = len(m)
        thr = density * n * n
        for py in range(rh):
            for px in range(rw):
                use2 = m[py % n][px % n] < thr
                put(img, W, H, rx + px, ry + py, c2 if use2 else c1)
        return
    m = BINARY[pattern]
    n, k = len(m), len(m[0])
    # Binary textures: density 0 -> all colour1, 1 -> all colour2, otherwise the pattern as-is
    # (bits switched off pseudo-randomly below 0.5 / on above 0.5 to honour the ratio).
    rng = random.Random(1234)
    for py in range(rh):
        for px in range(rw):
            bit = m[py % n][px % k]
            if density <= 0:
                bit = 0
            elif density >= 1:
                bit = 1
            elif density < 0.5 and bit and rng.random() > density * 2:
                bit = 0
            elif density > 0.5 and not bit and rng.random() < (density - 0.5) * 2:
                bit = 1
            put(img, W, H, rx + px, ry + py, c2 if bit else c1)


def _floyd(img, W, H, rx, ry, rw, rh, c1, c2, density):
    """Horizontal gradient colour1 -> colour2 rendered with error diffusion."""
    err = [[0.0] * (rw + 2) for _ in range(rh + 1)]
    for y in range(rh):
        for x in range(rw):
            t = x / (rw - 1) if rw > 1 else 0.5
            t = min(1.0, max(0.0, t + (density - 0.5)))
            v = t + err[y][x + 1]
            q = 1.0 if v >= 0.5 else 0.0
            e = v - q
            err[y][x + 2] += e * 7 / 16
            err[y + 1][x] += e * 3 / 16
            err[y + 1][x + 1] += e * 5 / 16
            err[y + 1][x + 2] += e * 1 / 16
            put(img, W, H, rx + x, ry + y, c2 if q else c1)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def outline(img, W, H, color, thickness=1):
    for _ in range(max(1, thickness)):
        add = []
        for y in range(H):
            for x in range(W):
                if img[(y * W + x) * 4 + 3] != 0:
                    continue
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < W and 0 <= ny < H and img[(ny * W + nx) * 4 + 3] != 0 and \
                            tuple(img[(ny * W + nx) * 4:(ny * W + nx) * 4 + 4]) != color:
                        add.append((x, y))
                        break
        # 4-neighbour dilation compares against the *previous* ring, so mark only transparent pixels
        for x, y in add:
            put(img, W, H, x, y, color)


LIGHT = {
    "top_left": (-1, -1), "top": (0, -1), "top_right": (1, -1), "left": (-1, 0),
    "right": (1, 0), "bottom_left": (-1, 1), "bottom": (0, 1), "bottom_right": (1, 1),
}


def light_vec(direction):
    if direction not in LIGHT:
        raise AseError("Invalid light_direction: %s (one of: %s)" % (direction, ", ".join(LIGHT)))
    lx, ly = LIGHT[direction]
    n = math.hypot(lx, ly) or 1.0
    return lx / n, ly / n


def shade_region(img, W, H, rx, ry, rw, rh, palette, direction, intensity, style):
    if style not in ("smooth", "hard", "pillow"):
        raise AseError("Invalid style: %s (must be smooth, hard or pillow)" % style)
    lx, ly = light_vec(direction)
    pal = [p for p in palette if p[3] > 0]
    if not pal:
        raise AseError("palette must contain at least one colour")
    changed = 0
    for y in range(max(0, ry), min(H, ry + rh)):
        for x in range(max(0, rx), min(W, rx + rw)):
            o = (y * W + x) * 4
            if img[o + 3] == 0:
                continue
            nx = ((x - rx) / (rw - 1) * 2 - 1) if rw > 1 else 0.0
            ny = ((y - ry) / (rh - 1) * 2 - 1) if rh > 1 else 0.0
            if style == "pillow":
                f = 0.6 - math.hypot(nx, ny)  # centre bright, edges dark
            else:
                f = -(nx * lx + ny * ly)  # side facing the light is positive
                if style == "hard":
                    f = 1.0 if f > 0.33 else (-1.0 if f < -0.33 else 0.0)
            k = 1.0 + f * intensity * 0.6
            src = (img[o], img[o + 1], img[o + 2], img[o + 3])
            target = (min(255, max(0, int(src[0] * k))), min(255, max(0, int(src[1] * k))), min(255, max(0, int(src[2] * k))), src[3])
            new = nearest(target, pal)
            if new != src:
                changed += 1
            img[o:o + 4] = bytes(new)
    return changed


def shift_hue(c, dh, dl):
    r, g, b = c[0] / 255.0, c[1] / 255.0, c[2] / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + dh) % 1.0
    l = min(1.0, max(0.0, l + dl))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), c[3])


def auto_shade(spr, img, W, H, direction, intensity, style, hue_shift):
    """Shade each flat-colour region: highlights on the lit side, shadows on the far side."""
    if style not in ("cell", "smooth", "soft"):
        raise AseError("Invalid style: %s (must be cell, smooth or soft)" % style)
    lx, ly = light_vec(direction)
    seen = bytearray(W * H)
    regions = []
    for y in range(H):
        for x in range(W):
            k = y * W + x
            if seen[k] or img[k * 4 + 3] == 0:
                continue
            base = tuple(img[k * 4:k * 4 + 4])
            stack, pts = [(x, y)], []
            while stack:
                px, py = stack.pop()
                if not (0 <= px < W and 0 <= py < H):
                    continue
                kk = py * W + px
                if seen[kk] or tuple(img[kk * 4:kk * 4 + 4]) != base:
                    continue
                seen[kk] = 1
                pts.append((px, py))
                stack.extend(((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)))
            if len(pts) >= 4:
                regions.append((base, pts))
    new_colors = []
    bayer = BAYER["bayer_4x4"]
    for base, pts in regions:
        dh = 0.04 if hue_shift else 0.0
        dl = 0.18 * intensity if style != "soft" else 0.09 * intensity
        shadow = shift_hue(base, -dh * 1.5 if hue_shift else 0, -dl * 1.2)
        light = shift_hue(base, dh if hue_shift else 0, dl)
        if hue_shift:  # cool shadows, warm highlights
            shadow = shift_hue(base, 0.04, -dl * 1.2)
            light = shift_hue(base, -0.03, dl)
        for c in (shadow, light):
            if c not in new_colors and c != base:
                new_colors.append(c)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        hw, hh = max(1.0, (max(xs) - min(xs)) / 2.0), max(1.0, (max(ys) - min(ys)) / 2.0)
        for px, py in pts:
            f = -(((px - cx) / hw) * lx + ((py - cy) / hh) * ly)
            if style == "cell":
                c = light if f > 0.35 else (shadow if f < -0.35 else base)
            elif style == "smooth":
                t = (bayer[py % 4][px % 4] + 0.5) / 16.0 * 2 - 1  # -1..1
                v = f * 1.2
                c = light if v - 0.2 > t * 0.6 and f > 0.1 else (shadow if v + 0.2 < t * 0.6 and f < -0.1 else base)
            else:
                c = light if f > 0.55 else (shadow if f < -0.55 else base)
            put(img, W, H, px, py, c)
    added = 0
    for c in new_colors:
        if c not in spr.palette and len(spr.palette) < 256:
            spr.palette.append(c)
            added += 1
    return added, len(regions)


# ---------------------------------------------------------------------------
# Transforms (operate on one RGBA image, return new image + size)
# ---------------------------------------------------------------------------

def flip_img(img, W, H, direction):
    out = bytearray(len(img))
    for y in range(H):
        for x in range(W):
            sx = W - 1 - x if direction == "horizontal" else x
            sy = H - 1 - y if direction == "vertical" else y
            s = (sy * W + sx) * 4
            o = (y * W + x) * 4
            out[o:o + 4] = img[s:s + 4]
    return out


def rotate_img(img, W, H, angle):
    """Rotate clockwise by 90/180/270. Returns (img, W2, H2)."""
    if angle == 180:
        out = bytearray(len(img))
        for y in range(H):
            for x in range(W):
                s = ((H - 1 - y) * W + (W - 1 - x)) * 4
                o = (y * W + x) * 4
                out[o:o + 4] = img[s:s + 4]
        return out, W, H
    W2, H2 = H, W
    out = bytearray(len(img))
    for y in range(H):
        for x in range(W):
            if angle == 90:
                nx, ny = H - 1 - y, x
            else:  # 270
                nx, ny = y, W - 1 - x
            s = (y * W + x) * 4
            o = (ny * W2 + nx) * 4
            out[o:o + 4] = img[s:s + 4]
    return out, W2, H2


def scale_img(img, W, H, nw, nh, algorithm="nearest"):
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            o = (y * nw + x) * 4
            if algorithm == "bilinear":
                fx = (x + 0.5) * W / nw - 0.5
                fy = (y + 0.5) * H / nh - 0.5
                x0, y0 = int(math.floor(fx)), int(math.floor(fy))
                tx, ty = fx - x0, fy - y0
                acc = [0.0] * 4
                for (ix, iy, wgt) in ((x0, y0, (1 - tx) * (1 - ty)), (x0 + 1, y0, tx * (1 - ty)),
                                      (x0, y0 + 1, (1 - tx) * ty), (x0 + 1, y0 + 1, tx * ty)):
                    ix = min(W - 1, max(0, ix))
                    iy = min(H - 1, max(0, iy))
                    s = (iy * W + ix) * 4
                    a = img[s + 3] * wgt
                    acc[0] += img[s] * a
                    acc[1] += img[s + 1] * a
                    acc[2] += img[s + 2] * a
                    acc[3] += a
                if acc[3] > 0:
                    out[o] = int(acc[0] / acc[3])
                    out[o + 1] = int(acc[1] / acc[3])
                    out[o + 2] = int(acc[2] / acc[3])
                    out[o + 3] = int(acc[3])
            else:  # nearest (also used for rotsprite fallback)
                sx = min(W - 1, int(x * W / nw))
                sy = min(H - 1, int(y * H / nh))
                s = (sy * W + sx) * 4
                out[o:o + 4] = img[s:s + 4]
    return out


def crop_img(img, W, H, x, y, w, h):
    out = bytearray(w * h * 4)
    for yy in range(h):
        for xx in range(w):
            sx, sy = x + xx, y + yy
            if 0 <= sx < W and 0 <= sy < H:
                s = (sy * W + sx) * 4
                o = (yy * w + xx) * 4
                out[o:o + 4] = img[s:s + 4]
    return out


def box_downsample(img, W, H, nw, nh):
    """Area-averaging (box filter) resize; alpha-weighted so transparent pixels don't darken edges."""
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = y * H / nh, (y + 1) * H / nh
        for x in range(nw):
            x0, x1 = x * W / nw, (x + 1) * W / nw
            r = g = b = a = wsum = 0.0
            iy = int(y0)
            while iy < y1 and iy < H:
                wy = min(y1, iy + 1) - max(y0, iy)
                ix = int(x0)
                while ix < x1 and ix < W:
                    wx = min(x1, ix + 1) - max(x0, ix)
                    wgt = wx * wy
                    s = (iy * W + ix) * 4
                    pa = img[s + 3] / 255.0
                    r += img[s] * pa * wgt
                    g += img[s + 1] * pa * wgt
                    b += img[s + 2] * pa * wgt
                    a += pa * wgt
                    wsum += wgt
                    ix += 1
                iy += 1
            o = (y * nw + x) * 4
            if a > 0:
                out[o] = int(round(r / a))
                out[o + 1] = int(round(g / a))
                out[o + 2] = int(round(b / a))
                out[o + 3] = int(round(a / wsum * 255))
    return out


def anchor_offset(anchor, nw, nh, ow, oh):
    if anchor == "top_left":
        return 0, 0
    if anchor == "top_right":
        return nw - ow, 0
    if anchor == "bottom_left":
        return 0, nh - oh
    if anchor == "bottom_right":
        return nw - ow, nh - oh
    if anchor == "center":
        return (nw - ow) // 2, (nh - oh) // 2
    raise AseError("Invalid anchor: %s (center, top_left, top_right, bottom_left, bottom_right)" % anchor)
