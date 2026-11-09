"""Palette quantization, palette analysis, antialiasing suggestions and reference-image analysis."""
import colorsys
import math
import random

from ase import AseError
from draw import to_hex, nearest, luminance, getpx, put


# ---------------------------------------------------------------------------
# Quantization
# ---------------------------------------------------------------------------

def unique_colors(img):
    """{(r,g,b): count} for opaque-enough pixels."""
    counts = {}
    for i in range(0, len(img), 4):
        if img[i + 3] == 0:
            continue
        k = (img[i], img[i + 1], img[i + 2])
        counts[k] = counts.get(k, 0) + 1
    return counts


def median_cut(counts, n):
    boxes = [list(counts.items())]
    while len(boxes) < n:
        boxes.sort(key=lambda b: -len(b))
        box = boxes[0]
        if len(box) < 2:
            break
        boxes.pop(0)
        ranges = [max(c[0][i] for c in box) - min(c[0][i] for c in box) for i in range(3)]
        ch = ranges.index(max(ranges))
        box.sort(key=lambda c: c[0][ch])
        total = sum(c[1] for c in box)
        acc = 0
        mid = 1
        for i, c in enumerate(box):
            acc += c[1]
            if acc >= total / 2:
                mid = max(1, min(len(box) - 1, i + 1))
                break
        boxes.append(box[:mid])
        boxes.append(box[mid:])
    return [_avg(b) for b in boxes if b]


def _avg(box):
    t = sum(c[1] for c in box)
    return tuple(int(round(sum(c[0][i] * c[1] for c in box) / t)) for i in range(3))


def kmeans(counts, n, iters=12, seed=7):
    items = list(counts.items())
    if len(items) <= n:
        return [c for c, _ in items]
    rng = random.Random(seed)
    # k-means++ seeding
    centers = [rng.choice(items)[0]]
    while len(centers) < n:
        dist = [min(sum((c[i] - q[i]) ** 2 for i in range(3)) for q in centers) * w for c, w in items]
        tot = sum(dist) or 1
        r = rng.random() * tot
        acc = 0
        for (c, _), d in zip(items, dist):
            acc += d
            if acc >= r:
                centers.append(c)
                break
        else:
            centers.append(items[-1][0])
    for _ in range(iters):
        groups = [[] for _ in centers]
        for c, w in items:
            bi = min(range(len(centers)), key=lambda k: sum((c[i] - centers[k][i]) ** 2 for i in range(3)))
            groups[bi].append((c, w))
        new = []
        for g, old in zip(groups, centers):
            new.append(_avg(g) if g else old)
        if new == centers:
            break
        centers = new
    return centers


def octree(counts, n):
    """Octree-style reduction: merge the least-populated deepest buckets until n colours remain."""
    depth = 5
    while depth > 0:
        buckets = {}
        for c, w in counts.items():
            key = tuple(v >> (8 - depth) for v in c)
            buckets.setdefault(key, []).append((c, w))
        if len(buckets) <= n:
            return [_avg(b) for b in buckets.values()]
        depth -= 1
        if depth == 0:
            break
    # fall back: strongest n buckets at depth 1 split further by median cut
    return median_cut(counts, n)


def build_palette(counts, n, algorithm):
    if not counts:
        raise AseError("Sprite has no opaque pixels to quantize")
    if algorithm == "median_cut":
        pal = median_cut(counts, n)
    elif algorithm == "kmeans":
        pal = kmeans(counts, n)
    elif algorithm == "octree":
        pal = octree(counts, n)
    else:
        raise AseError("Invalid algorithm: %s (median_cut, kmeans or octree)" % algorithm)
    return [tuple(c) + (255,) for c in pal]


def apply_palette(img, W, H, pal, dither):
    """Map every opaque pixel of img to the nearest colour in pal (optionally Floyd-Steinberg dithered)."""
    cache = {}

    def near(c):
        if c not in cache:
            cache[c] = nearest((c[0], c[1], c[2], 255), pal)
        return cache[c]

    if dither:
        err = [[0.0, 0.0, 0.0] for _ in range(W * H)]
        for y in range(H):
            for x in range(W):
                k = y * W + x
                o = k * 4
                if img[o + 3] == 0:
                    continue
                v = [min(255, max(0, img[o + i] + err[k][i])) for i in range(3)]
                q = near((int(v[0]), int(v[1]), int(v[2])))
                e = [v[i] - q[i] for i in range(3)]
                for dx, dy, f in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H:
                        for i in range(3):
                            err[ny * W + nx][i] += e[i] * f
                img[o:o + 3] = bytes(q[:3])
    else:
        for i in range(0, len(img), 4):
            if img[i + 3] == 0:
                continue
            q = near((img[i], img[i + 1], img[i + 2]))
            img[i:i + 3] = bytes(q[:3])


# ---------------------------------------------------------------------------
# Palette helpers
# ---------------------------------------------------------------------------

def hls(c):
    return colorsys.rgb_to_hls(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


def sort_palette(palette, method, ascending):
    if method == "hue":
        key = lambda c: (hls(c)[0], hls(c)[1])
    elif method == "saturation":
        key = lambda c: hls(c)[2]
    elif method in ("brightness", "luminance"):
        key = lambda c: luminance(c)
    else:
        raise AseError("Invalid sort method: %s (hue, saturation, brightness, luminance)" % method)
    return sorted(palette, key=key, reverse=not ascending)


def analyze_harmonies(colors):
    items = []
    for c in colors:
        h, l, s = colorsys.rgb_to_hls(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
        items.append((c, h * 360.0, s, l))
    def hd(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)
    comp, tri, ana = [], [], []
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            if items[i][2] < 0.1 or items[j][2] < 0.1:
                continue
            d = hd(items[i][1], items[j][1])
            if abs(d - 180) <= 15:
                comp.append({"color1": to_hex(items[i][0], False), "color2": to_hex(items[j][0], False), "hue_difference": round(d, 1)})
            elif d <= 30 and d > 0:
                ana.append({"color1": to_hex(items[i][0], False), "color2": to_hex(items[j][0], False), "hue_difference": round(d, 1)})
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                if min(items[i][2], items[j][2], items[k][2]) < 0.1:
                    continue
                d1, d2, d3 = hd(items[i][1], items[j][1]), hd(items[j][1], items[k][1]), hd(items[i][1], items[k][1])
                if all(abs(d - 120) <= 15 for d in (d1, d2, d3)):
                    tri.append([to_hex(items[i][0], False), to_hex(items[j][0], False), to_hex(items[k][0], False)])
    warm = cool = neutral = 0
    for c, h, s, l in items:
        if s < 0.15:
            neutral += 1
        elif h < 70 or h >= 330:
            warm += 1
        elif 150 <= h < 270:
            cool += 1
        else:
            neutral += 1
    temp = "warm" if warm > cool and warm > neutral else ("cool" if cool > warm and cool > neutral else "neutral")
    return {"complementary": comp, "triadic": tri, "analogous": ana,
            "temperature": {"overall": temp, "warm": warm, "cool": cool, "neutral": neutral}, "color_count": n}


# ---------------------------------------------------------------------------
# Antialiasing suggestions (stair-step detection)
# ---------------------------------------------------------------------------

def antialias_suggestions(img, W, H, rx, ry, rw, rh, threshold):
    """Find inner corners of diagonal stair steps and propose blended colours for them."""
    def px(x, y):
        if 0 <= x < W and 0 <= y < H:
            return getpx(img, W, x, y)
        return (0, 0, 0, 0)

    corners = {}
    for y in range(ry, min(H, ry + rh)):
        for x in range(rx, min(W, rx + rw)):
            q = px(x, y)
            for name, (ax, ay), (bx, by), (dx, dy) in (
                ("down_right", (-1, 0), (0, -1), (-1, -1)),
                ("down_left", (1, 0), (0, -1), (1, -1)),
                ("up_right", (-1, 0), (0, 1), (-1, 1)),
                ("up_left", (1, 0), (0, 1), (1, 1)),
            ):
                a, b, d = px(x + ax, y + ay), px(x + bx, y + by), px(x + dx, y + dy)
                if a == b == d and a != q and a[3] > 0:
                    corners[(x, y)] = (name, a, q)
                    break
    # group corners into diagonal chains and keep chains of at least `threshold` corners
    keep = {}
    for (x, y), v in corners.items():
        chain = 1
        for (nx, ny), nv in corners.items():
            if (nx, ny) != (x, y) and nv[0] == v[0] and max(abs(nx - x), abs(ny - y)) <= 2:
                chain += 1
        if chain >= threshold:
            keep[(x, y)] = v
    out = []
    for (x, y), (name, a, q) in sorted(keep.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if q[3] == 0:
            blend = (a[0], a[1], a[2], max(1, a[3] // 2))
        else:
            blend = tuple((a[i] + q[i]) // 2 for i in range(4))
        out.append({"x": x, "y": y, "color": to_hex(blend), "direction": name})
    return out


# ---------------------------------------------------------------------------
# Reference analysis
# ---------------------------------------------------------------------------

def analyze_reference(img, W, H, palette_size, brightness_levels, edge_threshold):
    from draw import box_downsample
    counts = unique_colors(img)
    if not counts:
        raise AseError("Reference image has no opaque pixels")
    pal = kmeans(counts, palette_size) if len(counts) > palette_size else list(counts)
    total = sum(counts.values())
    weights = {}
    for c in pal:
        weights[c] = 0
    for c, w in counts.items():
        best = min(pal, key=lambda p: sum((c[i] - p[i]) ** 2 for i in range(3)))
        weights[best] += w
    palette = []
    for c in sorted(pal, key=lambda c: (round(hls(c)[0], 2), hls(c)[1])):
        h, l, s = hls(c)
        palette.append({"color": to_hex(c, False), "frequency": round(weights[c] / total, 4),
                        "hue": round(h * 360, 1), "lightness": round(l, 3), "saturation": round(s, 3)})
    lum = [[0.0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            o = (y * W + x) * 4
            lum[y][x] = luminance((img[o], img[o + 1], img[o + 2])) if img[o + 3] else 0.0
    levels = max(2, brightness_levels)
    grid = [[min(levels - 1, int(lum[y][x] * levels)) for x in range(W)] for y in range(H)]
    legend = {str(i): ("darkest" if i == 0 else "lightest" if i == levels - 1 else "level %d" % i) for i in range(levels)}
    # Sobel edges
    egrid = [[0] * W for _ in range(H)]
    mags = {}
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            gx = (lum[y - 1][x + 1] + 2 * lum[y][x + 1] + lum[y + 1][x + 1]) - (lum[y - 1][x - 1] + 2 * lum[y][x - 1] + lum[y + 1][x - 1])
            gy = (lum[y + 1][x - 1] + 2 * lum[y + 1][x] + lum[y + 1][x + 1]) - (lum[y - 1][x - 1] + 2 * lum[y - 1][x] + lum[y - 1][x + 1])
            m = math.hypot(gx, gy) * 255.0 / 4.0
            if m >= edge_threshold:
                egrid[y][x] = 1
                mags[(x, y)] = m
    major = []
    visited = set()
    for (x, y), m in sorted(mags.items()):
        if (x, y) in visited:
            continue
        # horizontal run
        x2 = x
        while egrid[y][x2 + 1 if x2 + 1 < W else x2] and (x2 + 1, y) in mags and (x2 + 1, y) not in visited:
            x2 += 1
        if x2 - x >= 2:
            for xx in range(x, x2 + 1):
                visited.add((xx, y))
            major.append({"from": {"x": x, "y": y}, "to": {"x": x2, "y": y}, "strength": round(min(100.0, m / 2.55), 1)})
    major = sorted(major, key=lambda e: -e["strength"])[:20]
    # Composition: focal points from edge density per grid cell
    gsz = 4
    cells = []
    cw, ch = max(1, W // gsz), max(1, H // gsz)
    for gy in range(gsz):
        for gx in range(gsz):
            cnt = sum(egrid[y][x] for y in range(gy * ch, min(H, (gy + 1) * ch)) for x in range(gx * cw, min(W, (gx + 1) * cw)))
            cells.append((cnt / float(cw * ch), gx * cw + cw // 2, gy * ch + ch // 2))
    cells.sort(reverse=True)
    top = cells[0][0] or 1.0
    focal = [{"x": x, "y": y, "weight": round(d / top, 3)} for d, x, y in cells[:3] if d > 0]
    v = [W // 3, 2 * W // 3]
    hz = [H // 3, 2 * H // 3]
    inter = []
    for ix in v:
        for iy in hz:
            near = any(abs(f["x"] - ix) <= cw and abs(f["y"] - iy) <= ch for f in focal)
            inter.append({"x": ix, "y": iy, "has_focal_point": near})
    dom = {"x": 0, "y": 0, "width": W, "height": H}
    if focal:
        f = focal[0]
        dom = {"x": max(0, f["x"] - W // 4), "y": max(0, f["y"] - H // 4), "width": W // 2, "height": H // 2}
    # Dithering zones: smooth brightness gradients
    zones = []
    for gy in range(0, H - 4, 4):
        for gx in range(0, W - 4, 4):
            vals = [lum[y][x] for y in range(gy, gy + 4) for x in range(gx, gx + 4)]
            rng = max(vals) - min(vals)
            if 0.12 < rng < 0.5 and sum(egrid[y][x] for y in range(gy, gy + 4) for x in range(gx, gx + 4)) < 4:
                zones.append({"x": gx, "y": gy, "width": 4, "height": 4, "gradient": round(rng, 3), "suggested_pattern": "bayer_4x4"})
    return {
        "palette": palette,
        "brightness_map": {"grid": grid, "legend": legend},
        "edge_map": {"grid": egrid, "major_edges": major},
        "composition": {"focal_points": focal, "rule_of_thirds": {"vertical_lines": v, "horizontal_lines": hz, "intersections": inter}, "dominant_region": dom},
        "dithering_zones": zones[:30],
        "metadata": {"width": W, "height": H, "unique_colors": len(counts), "palette_size": len(palette), "brightness_levels": levels},
    }
