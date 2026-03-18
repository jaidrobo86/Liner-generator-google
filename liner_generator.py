"""
Circular / Rectangular Liner Panel Layout Generator
Outputs PDF (2 pages) and DXF.

Fabric specs (defaults):
  EL6020: 500 gsm, 0.5 mm, roll 500 m
  EL6030: 750 gsm, 0.75 mm, roll 381 m
  EL6040: 1000 gsm, 1.0 mm, roll 304 m
"""

import math
import io as _io
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import ezdxf

# ---------------------------------------------------------------------------
# Default fabric presets
# ---------------------------------------------------------------------------

FABRIC_PRESETS = {
    "EL6020": {"gsm": 500,  "thickness_mm": 0.5,  "max_roll_m": 500,
               "roll_width": 3.76, "weld_overlap": 0.12},
    "EL6030": {"gsm": 750,  "thickness_mm": 0.75, "max_roll_m": 381,
               "roll_width": 3.76, "weld_overlap": 0.12},
    "EL6040": {"gsm": 1000, "thickness_mm": 1.0,  "max_roll_m": 304,
               "roll_width": 3.76, "weld_overlap": 0.12},
}

# Colours (RGB 0-1)
COL_BLACK = (0,    0,    0   )
COL_RED   = (0.85, 0.1,  0.1 )
COL_BLUE  = (0.1,  0.25, 0.65)
COL_LGRAY = (0.88, 0.88, 0.88)
COL_DGRAY = (0.45, 0.45, 0.45)
COL_WHITE = (1,    1,    1   )

STRIP_COL_A = (0.75, 0.88, 1.0)   # light blue  - odd groups
STRIP_COL_B = (0.80, 1.0,  0.80)  # light green - even groups


# ---------------------------------------------------------------------------
# Geometry: circular
# ---------------------------------------------------------------------------

def _build_circular_strips(x_start, radius, net_width, full_coverage=True):
    strips = []
    x = x_start
    while True:
        x_l = x
        x_r = x + net_width
        if x_l >= radius:
            break
        overlaps = x_r > -radius and x_l < radius
        if overlaps:
            if full_coverage:
                x_inner = min(min(abs(x_l), abs(x_r)), radius)
            else:
                # only full strips
                if abs(x_l) > radius or abs(x_r) > radius:
                    x += net_width
                    continue
                x_inner = min(abs(x_l), abs(x_r))
            chord  = 2 * math.sqrt(max(0, radius**2 - x_inner**2))
            inside = (abs(x_l) <= radius) and (abs(x_r) <= radius)
            strips.append({
                "x_left":  x_l,
                "x_right": x_r,
                "chord_m": round(chord, 1),
                "is_site": not inside,
            })
        x += net_width
    for idx, s in enumerate(strips):
        s["index"] = idx + 1
    return strips


def compute_circular_strips(diameter_m, net_width,
                             layout="auto", full_coverage=True):
    radius = diameter_m / 2.0
    n_a = math.ceil(radius / net_width)
    sa = _build_circular_strips(
        -n_a * net_width, radius, net_width, full_coverage
    )
    n_b = math.floor(radius / net_width)
    sb = _build_circular_strips(
        -(net_width / 2) - n_b * net_width, radius, net_width, full_coverage
    )

    def covers_full_circle(strips):
        if not strips:
            return False
        return strips[0]["x_left"] <= -radius and strips[-1]["x_right"] >= radius

    fab_a = sum(s["chord_m"] for s in sa)
    fab_b = sum(s["chord_m"] for s in sb)

    if full_coverage:
        a_ok = covers_full_circle(sa)
        b_ok = covers_full_circle(sb)
        if layout == "centred":
            strips, label = (sb, "Centred") if b_ok else (sa, "Straddled")
        elif layout == "straddled":
            strips, label = sa, "Straddled"
        else:  # auto
            valid = []
            if a_ok: valid.append((fab_a, sa, "Auto - straddled"))
            if b_ok: valid.append((fab_b, sb, "Auto - centred"))
            _, strips, label = min(valid, key=lambda x: x[0]) if valid else (None, sa, "Auto - straddled")
    else:
        if layout == "centred":
            strips, label = sb, "Centred"
        elif layout == "straddled":
            strips, label = sa, "Straddled"
        else:
            strips, label = (sb, "Auto - centred") if fab_b < fab_a else (sa, "Auto - straddled")

    return strips, label


# ---------------------------------------------------------------------------
# Geometry: rectangular
# ---------------------------------------------------------------------------

def compute_rectangular_strips(width_m, length_m, net_width,
                                strip_direction="along_width",
                                full_coverage=True):
    """
    strip_direction:
      'along_width'  -> strips run across the width  (seams parallel to width axis)
                        each strip has length = length_m, count = ceil(width / net_width)
      'along_length' -> strips run across the length (seams parallel to length axis)
                        each strip has length = width_m,  count = ceil(length / net_width)
    """
    if strip_direction == "along_width":
        span        = width_m
        strip_len   = length_m
        axis_label  = "Along width"
    else:
        span        = length_m
        strip_len   = width_m
        axis_label  = "Along length"

    n_strips = math.ceil(span / net_width)
    strips   = []
    for i in range(n_strips):
        x_l    = i * net_width
        x_r    = x_l + net_width
        inside = x_r <= span
        strips.append({
            "index":   i + 1,
            "x_left":  x_l,
            "x_right": min(x_r, span),
            "chord_m": round(strip_len, 1),
            "is_site": not inside,
        })

    return strips, f"Rect {axis_label}"


# ---------------------------------------------------------------------------
# Grouping / colouring
# ---------------------------------------------------------------------------

def assign_groups(strips, strips_per_unit):
    """Assign colour groups cycling every strips_per_unit strips."""
    for s in strips:
        i         = s["index"] - 1
        grp       = i // strips_per_unit + 1
        s["group"]    = grp
        s["fill_col"] = STRIP_COL_A if grp % 2 == 1 else STRIP_COL_B
    return strips


def assign_individual(strips):
    for s in strips:
        i = s["index"] - 1
        s["group"]    = i + 1
        s["fill_col"] = STRIP_COL_A if (i % 2 == 0) else STRIP_COL_B
    return strips


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def panel_weight_kg(chord_m, roll_width, gsm):
    return round(chord_m * roll_width * gsm / 1000, 1)


def total_weld_length(strips, net_width, shape, **kwargs):
    """
    Total longitudinal weld length in metres.
    = number of internal seams * average strip length
    For circle: seams between strips = n-1 internal seams, each seam length = average chord
    For rectangle: seams = n-1, each seam length = strip_len
    """
    n = len(strips)
    if n <= 1:
        return 0.0
    avg_len = sum(s["chord_m"] for s in strips) / n
    return round((n - 1) * avg_len, 1)


def build_weld_schedule(strips, max_roll_m):
    schedule = []
    for s in strips:
        L        = s["chord_m"]
        n_full   = int(L / max_roll_m)
        leftover = round(L - n_full * max_roll_m, 2)
        rolls    = n_full + (1 if leftover > 0 else 0)
        joins    = [round((j+1)*max_roll_m, 1) for j in range(n_full)
                    if (j+1)*max_roll_m < L]
        schedule.append({
            "index":      s["index"],
            "chord_m":    L,
            "rolls":      rolls,
            "joins_at":   joins,
            "leftover_m": leftover if leftover > 0 else max_roll_m,
            "is_site":    s["is_site"],
            "group":      s.get("group", None),
        })
    return schedule


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _col_xs(col_ws):
    xs = [0.0]
    for w in col_ws[:-1]:
        xs.append(xs[-1] + w * mm)
    return xs


def _tbl_header(c, ox, oy, xs, col_ws, headers, row_h):
    c.setFillColorRGB(*COL_BLUE)
    c.rect(ox, oy, sum(col_ws)*mm, row_h, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 7)
    c.setFillColorRGB(*COL_WHITE)
    for i, h in enumerate(headers):
        c.drawCentredString(ox + xs[i] + col_ws[i]*mm/2, oy + 2*mm, h)


def _tbl_row(c, ox, oy, xs, col_ws, vals, row_h, bg):
    c.setFillColorRGB(*bg)
    c.rect(ox, oy, sum(col_ws)*mm, row_h, fill=1, stroke=0)
    c.setFont("Helvetica", 6.5)
    c.setFillColorRGB(*COL_BLACK)
    for i, v in enumerate(vals):
        c.drawCentredString(ox + xs[i] + col_ws[i]*mm/2, oy + 1.8*mm, str(v))
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.setLineWidth(0.2)
    c.line(ox, oy, ox + sum(col_ws)*mm, oy)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_liner(
    # Shape
    shape="circle",          # "circle" or "rectangle"
    diameter_m=None,         # circle
    width_m=None,            # rectangle
    length_m=None,           # rectangle
    strip_direction="along_width",  # rectangle only

    # Fabric
    fabric_ref="EL6030",
    roll_width=None,         # override
    weld_overlap=None,       # override
    gsm=None,                # override
    thickness_mm=None,       # override
    max_roll_m=None,         # override
    fabric_name=None,        # override name

    # Layout
    layout="auto",           # "auto","centred","straddled"
    mode="individual",       # "individual","prefab"
    strips_per_unit=3,
    full_coverage=True,
    perimeter_allowance_mm=0,

    # Meta
    client="",
    project="",
):
    # Resolve fabric spec
    if fabric_ref in FABRIC_PRESETS and roll_width is None:
        spec = FABRIC_PRESETS[fabric_ref].copy()
    else:
        base = FABRIC_PRESETS.get(fabric_ref, FABRIC_PRESETS["EL6030"])
        spec = {
            "roll_width":   roll_width   or base["roll_width"],
            "weld_overlap": weld_overlap or base["weld_overlap"],
            "gsm":          gsm          or base["gsm"],
            "thickness_mm": thickness_mm or base["thickness_mm"],
            "max_roll_m":   max_roll_m   or base["max_roll_m"],
        }

    rw         = spec["roll_width"]
    wo         = spec["weld_overlap"]
    net_w      = round(rw - wo, 4)
    fab_gsm    = spec["gsm"]
    fab_thick  = spec["thickness_mm"]
    max_roll   = spec["max_roll_m"]
    fab_label  = fabric_name or fabric_ref
    pa_m       = perimeter_allowance_mm / 1000.0   # convert to metres

    # Compute strips
    if shape == "circle":
        eff_radius = diameter_m / 2.0 + pa_m
        strips, layout_label = compute_circular_strips(
            eff_radius * 2, net_w, layout, full_coverage)
        nom_radius = diameter_m / 2.0
    else:
        eff_w = width_m  + 2 * pa_m
        eff_l = length_m + 2 * pa_m
        strips, layout_label = compute_rectangular_strips(
            eff_w, eff_l, net_w, strip_direction, full_coverage)
        nom_radius = None

    # Assign colours / groups
    if mode == "prefab":
        strips = assign_groups(strips, strips_per_unit)
    else:
        strips = assign_individual(strips)

    # Totals
    total_fabric = round(sum(s["chord_m"] for s in strips), 1)
    total_area   = round(total_fabric * rw, 1)
    total_weight = round(sum(panel_weight_kg(s["chord_m"], rw, fab_gsm)
                             for s in strips), 0)
    site_fabric  = round(sum(s["chord_m"] for s in strips if s["is_site"]), 1)
    weld_len     = total_weld_length(strips, net_w, shape)
    n_groups     = strips[-1]["group"] if strips else 0
    mode_label   = "Prefab assembly" if mode == "prefab" else "Individual"

    if shape == "circle":
        shape_desc = f"Circle  O {diameter_m} m"
    else:
        sq = " (Square)" if width_m == length_m else ""
        shape_desc = f"Rectangle{sq}  {width_m} x {length_m} m"

    return dict(
        strips=strips, layout_label=layout_label, mode_label=mode_label,
        shape=shape, shape_desc=shape_desc,
        diameter_m=diameter_m, width_m=width_m, length_m=length_m,
        strip_direction=strip_direction,
        nom_radius=nom_radius,
        eff_width=(width_m+2*pa_m) if shape=="rectangle" else None,
        eff_length=(length_m+2*pa_m) if shape=="rectangle" else None,
        rw=rw, wo=wo, net_w=net_w, fab_gsm=fab_gsm, fab_thick=fab_thick,
        max_roll=max_roll, fab_label=fab_label,
        pa_m=pa_m, pa_mm=perimeter_allowance_mm,
        total_fabric=total_fabric, total_area=total_area,
        total_weight=total_weight, site_fabric=site_fabric,
        weld_len=weld_len, n_groups=n_groups,
        client=client, project=project,
        schedule=build_weld_schedule(strips, max_roll),
        total_rolls=sum(r["rolls"] for r in build_weld_schedule(strips, max_roll)),
    )


# ---------------------------------------------------------------------------
# PDF output
# ---------------------------------------------------------------------------

def draw_pdf(data, output_path):
    strips     = data["strips"]
    mode_label = data["mode_label"]
    shape      = data["shape"]

    page_w, page_h = landscape(A3)
    ML = 18*mm;  MR = 18*mm;  MT = 22*mm;  MB = 18*mm

    usable_w    = page_w - ML - MR
    draw_area_w = usable_w * 0.58
    draw_area_h = page_h - MT - MB - 30*mm

    # Scale
    if shape == "circle":
        nom_size = (data["diameter_m"] + 2*data["pa_m"]) * 1.15
        scale    = min(draw_area_w, draw_area_h) / nom_size
        cx       = ML + draw_area_w / 2
        cy       = MB + 30*mm + draw_area_h / 2
    else:
        eff_w  = data["eff_width"]
        eff_l  = data["eff_length"]
        scale  = min(draw_area_w / (eff_w * 1.1),
                     draw_area_h / (eff_l * 1.1))
        cx     = ML + draw_area_w / 2
        cy     = MB + 30*mm + draw_area_h / 2

    def m2pt(v): return v * scale

    buf = _io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=(page_w, page_h))

    # ── PAGE 1 ──────────────────────────────────────────────────────────────
    c.setFillColorRGB(*COL_WHITE)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    if shape == "circle":
        _draw_circular_strips(c, strips, cx, cy, m2pt,
                              data["nom_radius"], data["pa_m"])
    else:
        _draw_rectangular_strips(c, strips, cx, cy, m2pt,
                                 data["eff_width"], data["eff_length"],
                                 data["width_m"], data["length_m"],
                                 data["pa_m"], data["strip_direction"])

    # Title
    title_y = page_h - 13*mm
    c.setFont("Helvetica-Bold", 10); c.setFillColorRGB(*COL_BLACK)
    c.drawString(ML, title_y, f"Client: {data['client']}   |   Project: {data['project']}")
    c.setFont("Helvetica", 8)
    c.drawString(ML, title_y - 11,
        f"{data['shape_desc']}   |   {mode_label}   |   Layout: {data['layout_label']}   |   "
        f"Fabric: {data['fab_label']} ({data['fab_thick']} mm / {data['fab_gsm']} gsm)   |   "
        f"Roll: {data['rw']} m   Weld: {int(data['wo']*1000)} mm   Net: {data['net_w']} m"
        + (f"   |   Perimeter allowance: {data['pa_mm']} mm" if data['pa_mm'] else ""))

    # Summary box
    sx = ML;  sy = MB + 58*mm
    summary = [
        (True,  f"{data['shape_desc']}"),
        (False, f"Layout:            {data['layout_label']}"),
        (False, f"Mode:              {mode_label}"),
        (False, f"Total strips:      {len(strips)}"),
        (False, f"Prefab groups:     {data['n_groups']}") if mode_label == "Prefab assembly"
               else (False, ""),
        (False, f"Total fabric:      {data['total_fabric']} m"),
        (False, f"  site weld:       {data['site_fabric']} m"),
        (False, f"Total weld length: {data['weld_len']} m"),
        (False, f"Total area:        {data['total_area']} m2"),
        (False, f"Total weight:      {data['total_weight']} kg"),
        (False, f"Fabric:            {data['fab_label']}  (max roll {data['max_roll']} m)"),
        (False, f"Perimeter allow.:  {data['pa_mm']} mm") if data['pa_mm'] else (False, ""),
    ]
    for li, (bold, line) in enumerate(summary):
        if not line:
            continue
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.5)
        c.setFillColorRGB(*COL_BLACK)
        c.drawString(sx, sy - li * 5.6*mm, line)

    # Strip table bottom-right
    _draw_strip_table(c, strips, data, page_w, page_h, ML, MR, MB)

    c.showPage()

    # ── PAGE 2: Weld / roll schedule ─────────────────────────────────────────
    _draw_weld_schedule(c, data, page_w, page_h, ML, MR, MB, MT)

    c.showPage()
    c.save()
    buf.seek(0)
    if hasattr(output_path, "write"):
        output_path.write(buf.read())
    else:
        with open(output_path, "wb") as f:
            f.write(buf.read())


def _draw_circular_strips(c, strips, cx, cy, m2pt, nom_radius, pa_m):
    for s in strips:
        xl = s["x_left"];  xr = s["x_right"]
        eff_r  = nom_radius + pa_m
        x_inner = min(min(abs(xl), abs(xr)), eff_r)
        half_h  = math.sqrt(max(0, eff_r**2 - x_inner**2))
        px_l = cx + m2pt(xl);  px_r = cx + m2pt(xr)
        py_b = cy - m2pt(half_h);  py_t = cy + m2pt(half_h)
        c.setFillColorRGB(*s["fill_col"])
        c.setStrokeColorRGB(*COL_BLACK);  c.setLineWidth(0.4)
        c.rect(px_l, py_b, px_r-px_l, py_t-py_b, fill=1, stroke=1)
        c.setStrokeColorRGB(*COL_DGRAY);  c.setLineWidth(0.3);  c.setDash([3,3])
        c.line(px_l, py_b, px_l, py_t);  c.setDash([])
        lx = (px_l + px_r) / 2
        c.saveState();  c.translate(lx, cy);  c.rotate(90)
        c.setFont("Helvetica-Bold", 7);  c.setFillColorRGB(*COL_BLACK)
        c.drawCentredString(0, -2.5, str(s["index"]))
        c.setFont("Helvetica", 6)
        c.drawCentredString(0, -10, f"{s['chord_m']}m")
        c.restoreState()

    # Nominal circle (red)
    c.setStrokeColorRGB(*COL_RED);  c.setLineWidth(1.4)
    c.circle(cx, cy, m2pt(nom_radius), fill=0, stroke=1)


def _draw_rectangular_strips(c, strips, cx, cy, m2pt,
                              eff_w, eff_l, nom_w, nom_l,
                              pa_m, strip_direction):
    # Origin: bottom-left of effective rectangle centred on cx, cy
    ox = cx - m2pt(eff_w / 2)
    oy = cy - m2pt(eff_l / 2)

    for s in strips:
        if strip_direction == "along_width":
            # strips run along length, seams parallel to length axis
            sx_l = ox + m2pt(s["x_left"])
            sx_r = ox + m2pt(s["x_right"])
            sy_b = oy
            sy_t = oy + m2pt(eff_l)
        else:
            sx_l = ox
            sx_r = ox + m2pt(eff_w)
            sy_b = oy + m2pt(s["x_left"])
            sy_t = oy + m2pt(s["x_right"])

        c.setFillColorRGB(*s["fill_col"])
        c.setStrokeColorRGB(*COL_BLACK);  c.setLineWidth(0.4)
        c.rect(sx_l, sy_b, sx_r-sx_l, sy_t-sy_b, fill=1, stroke=1)
        c.setStrokeColorRGB(*COL_DGRAY);  c.setLineWidth(0.3);  c.setDash([3,3])
        c.line(sx_l, sy_b, sx_l, sy_t);  c.setDash([])

        lx = (sx_l + sx_r) / 2;  ly = (sy_b + sy_t) / 2
        c.saveState();  c.translate(lx, ly);  c.rotate(90)
        c.setFont("Helvetica-Bold", 7);  c.setFillColorRGB(*COL_BLACK)
        c.drawCentredString(0, -2.5, str(s["index"]))
        c.setFont("Helvetica", 6)
        c.drawCentredString(0, -10, f"{s['chord_m']}m")
        c.restoreState()

    # Nominal rectangle (red)
    nom_ox = cx - m2pt(nom_w / 2)
    nom_oy = cy - m2pt(nom_l / 2)
    c.setStrokeColorRGB(*COL_RED);  c.setLineWidth(1.4);  c.setFillColorRGB(0,0,0,0)
    c.rect(nom_ox, nom_oy, m2pt(nom_w), m2pt(nom_l), fill=0, stroke=1)


def _draw_strip_table(c, strips, data, page_w, page_h, ML, MR, MB):
    mode_label = data["mode_label"]
    tbl_cw  = [13, 17, 17, 21, 21, 16]
    tbl_hdrs = ["Strip #", "Length (m)", "Fabric (m)", "Area (m2)", "Weight (kg)", "Notes"]
    if mode_label == "Prefab assembly":
        tbl_cw.append(12);  tbl_hdrs.append("Group")

    tbl_w   = sum(tbl_cw) * mm
    row_h   = 5.0*mm;  hdr_h = 6.0*mm
    xs      = _col_xs(tbl_cw)
    avail_h = page_h * 0.52 - MB
    rpc     = max(1, int((avail_h - hdr_h - row_h) / row_h))
    chunks  = [strips[i:i+rpc] for i in range(0, len(strips), rpc)]
    n_ch    = len(chunks)
    tbl_x0  = page_w - MR - n_ch*(tbl_w + 4*mm)

    for ci, chunk in enumerate(chunks):
        ox     = tbl_x0 + ci*(tbl_w + 4*mm)
        oy_hdr = MB + (rpc+1)*row_h
        _tbl_header(c, ox, oy_hdr, xs, tbl_cw, tbl_hdrs, hdr_h)
        for ri, s in enumerate(chunk):
            ry    = MB + (rpc-ri)*row_h
            chord = s["chord_m"]
            note  = "Site" if s["is_site"] else ""
            vals  = [s["index"], f"{chord:.1f}", f"{chord:.1f}",
                     f"{round(chord*data['rw'],1):.1f}",
                     f"{panel_weight_kg(chord, data['rw'], data['fab_gsm']):.1f}", note]
            if mode_label == "Prefab assembly":
                vals.append(f"G{s['group']}")
            bg = (1,1,1) if ri%2==0 else (0.94,0.94,0.94)
            _tbl_row(c, ox, ry, xs, tbl_cw, vals, row_h, bg)
        if ci == n_ch-1:
            ry = MB
            tot = ["TOTAL","",f"{data['total_fabric']:.1f}",
                   f"{data['total_area']:.1f}",f"{data['total_weight']:.0f}",""]
            if mode_label == "Prefab assembly": tot.append("")
            c.setFillColorRGB(*COL_LGRAY)
            c.rect(ox, ry, tbl_w, row_h, fill=1, stroke=0)
            c.setFont("Helvetica-Bold", 6.5);  c.setFillColorRGB(*COL_BLACK)
            for i, v in enumerate(tot):
                c.drawCentredString(ox+xs[i]+tbl_cw[i]*mm/2, ry+1.8*mm, v)


def _draw_weld_schedule(c, data, page_w, page_h, ML, MR, MB, MT):
    c.setFillColorRGB(*COL_WHITE)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    schedule    = data["schedule"]
    total_rolls = data["total_rolls"]
    total_joins = sum(len(r["joins_at"]) for r in schedule)

    c.setFont("Helvetica-Bold", 11);  c.setFillColorRGB(*COL_BLACK)
    c.drawString(ML, page_h-13*mm,
        f"Weld / Roll Schedule  -  {data['client']}  |  {data['project']}")
    c.setFont("Helvetica", 9);  c.setFillColorRGB(*COL_DGRAY)
    c.drawString(ML, page_h-22*mm,
        f"{data['shape_desc']}   |   {data['fab_label']}   |   "
        f"Max roll: {data['max_roll']} m   |   "
        f"Strips: {len(data['strips'])}   |   Total rolls: {total_rolls}   |   "
        f"Longitudinal joins: {total_joins}   |   "
        f"Total weld length: {data['weld_len']} m")

    ws_cw   = [13, 20, 14, 13, 54, 20]
    ws_hdrs = ["Strip #", "Length (m)", "Rolls", "Joins",
               "Join positions (m from end)", "Last piece (m)"]
    if data["mode_label"] == "Prefab assembly":
        ws_cw.insert(1, 13);  ws_hdrs.insert(1, "Group")

    ws_xs      = _col_xs(ws_cw)
    ws_w       = sum(ws_cw)*mm
    row_h      = 6.0*mm;  hdr_h = 7.0*mm
    ws_top     = page_h - 30*mm
    rpc        = max(1, int((ws_top - MB - hdr_h - row_h) / row_h))
    ws_chunks  = [schedule[i:i+rpc] for i in range(0, len(schedule), rpc)]

    for ci, chunk in enumerate(ws_chunks):
        ox     = ML + ci*(ws_w + 6*mm)
        oy_hdr = ws_top - hdr_h
        _tbl_header(c, ox, oy_hdr, ws_xs, ws_cw, ws_hdrs, hdr_h)
        for ri, r in enumerate(chunk):
            ry    = ws_top - hdr_h - (ri+1)*row_h
            joins = ", ".join(str(j) for j in r["joins_at"]) if r["joins_at"] else "-"
            vals  = [r["index"], f"{r['chord_m']:.1f}", r["rolls"],
                     len(r["joins_at"]) if r["joins_at"] else 0,
                     joins, f"{r['leftover_m']:.1f}"]
            if data["mode_label"] == "Prefab assembly":
                vals.insert(1, f"G{r['group']}" if r["group"] else "")
            bg = (1.0,0.95,0.95) if r["is_site"] else (
                 (1,1,1) if ri%2==0 else (0.94,0.94,0.94))
            _tbl_row(c, ox, ry, ws_xs, ws_cw, vals, row_h, bg)

    # Roll summary
    last_ci  = len(ws_chunks)-1
    last_ox  = ML + last_ci*(ws_w+6*mm)
    last_bot = ws_top - hdr_h - (len(ws_chunks[-1])+2)*row_h
    c.setFont("Helvetica-Bold", 9);  c.setFillColorRGB(*COL_BLUE)
    c.drawString(last_ox, last_bot, "Roll Summary")
    items = [
        f"Fabric:              {data['fab_label']}",
        f"Max roll length:     {data['max_roll']} m",
        f"Total strips:        {len(data['strips'])}",
        f"Total rolls needed:  {total_rolls}",
        f"Total joins:         {total_joins}",
        f"Total weld length:   {data['weld_len']} m",
        f"Total fabric:        {data['total_fabric']} m",
        f"Total area:          {data['total_area']} m2",
        f"Total weight:        {data['total_weight']} kg",
    ]
    c.setFont("Helvetica", 8);  c.setFillColorRGB(*COL_BLACK)
    for li, item in enumerate(items):
        c.drawString(last_ox, last_bot-(li+1)*6*mm, item)


# ---------------------------------------------------------------------------
# DXF output
# ---------------------------------------------------------------------------

def draw_dxf(data, output_path):
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 6   # metres
    msp = doc.modelspace()

    # Layers
    doc.layers.add("STRIPS",   color=4)   # cyan
    doc.layers.add("SEAMS",    color=8)   # gray
    doc.layers.add("OUTLINE",  color=1)   # red
    doc.layers.add("LABELS",   color=7)   # white/black
    doc.layers.add("TITLEBLK", color=7)

    strips = data["strips"]
    shape  = data["shape"]

    if shape == "circle":
        _dxf_circular(msp, strips, data)
    else:
        _dxf_rectangular(msp, strips, data)

    # Title block (as text at bottom)
    tb_y = -data["nom_radius"]*1.3 if shape=="circle" else -(data["eff_length"]/2)*1.2
    msp.add_text(
        f"Client: {data['client']}  |  Project: {data['project']}",
        dxfattribs={"layer":"TITLEBLK","height":0.5,
                    "insert":(-(data["nom_radius"] if shape=="circle"
                                else data["eff_width"]/2), tb_y-0.8)}
    )
    msp.add_text(
        f"{data['shape_desc']}  |  {data['mode_label']}  |  "
        f"Layout: {data['layout_label']}  |  "
        f"Fabric: {data['fab_label']}  |  "
        f"Strips: {len(strips)}  |  Fabric: {data['total_fabric']} m  |  "
        f"Weld length: {data['weld_len']} m  |  "
        f"Weight: {data['total_weight']} kg",
        dxfattribs={"layer":"TITLEBLK","height":0.4,
                    "insert":(-(data["nom_radius"] if shape=="circle"
                                else data["eff_width"]/2), tb_y-1.8)}
    )

    if hasattr(output_path, "write"):
        import io as _io2
        sbuf = _io2.StringIO()
        doc.write(sbuf)
        output_path.write(sbuf.getvalue().encode("utf-8"))
    else:
        doc.saveas(output_path)


def _dxf_circular(msp, strips, data):
    nom_r  = data["nom_radius"]
    eff_r  = nom_r + data["pa_m"]

    for s in strips:
        xl = s["x_left"];  xr = s["x_right"]
        x_inner = min(min(abs(xl), abs(xr)), eff_r)
        half_h  = math.sqrt(max(0, eff_r**2 - x_inner**2))
        # Rectangle as 4 lines
        pts = [(xl,-half_h),(xr,-half_h),(xr,half_h),(xl,half_h),(xl,-half_h)]
        msp.add_lwpolyline(pts, dxfattribs={"layer":"STRIPS", "closed":True})
        # Seam line
        msp.add_line((xl,-half_h),(xl,half_h), dxfattribs={"layer":"SEAMS","linetype":"DASHED"})
        # Label
        msp.add_text(str(s["index"]),
                     dxfattribs={"layer":"LABELS","height":nom_r*0.025,
                                 "insert":((xl+xr)/2, 0),
                                 "rotation":90, "halign":1, "valign":2})
        msp.add_text(f"{s['chord_m']}m",
                     dxfattribs={"layer":"LABELS","height":nom_r*0.02,
                                 "insert":((xl+xr)/2, -nom_r*0.08),
                                 "rotation":90, "halign":1, "valign":2})

    # Nominal circle
    msp.add_circle((0,0), nom_r, dxfattribs={"layer":"OUTLINE","color":1})


def _dxf_rectangular(msp, strips, data):
    eff_w = data["eff_width"]
    eff_l = data["eff_length"]
    nom_w = data["width_m"]
    nom_l = data["length_m"]
    sd    = data["strip_direction"]
    ox    = -eff_w/2;  oy = -eff_l/2

    for s in strips:
        if sd == "along_width":
            x1 = ox + s["x_left"];  x2 = ox + s["x_right"]
            y1 = oy;  y2 = oy + eff_l
        else:
            x1 = ox;  x2 = ox + eff_w
            y1 = oy + s["x_left"];  y2 = oy + s["x_right"]
        pts = [(x1,y1),(x2,y1),(x2,y2),(x1,y2),(x1,y1)]
        msp.add_lwpolyline(pts, dxfattribs={"layer":"STRIPS","closed":True})
        msp.add_line((x1,y1),(x1,y2), dxfattribs={"layer":"SEAMS","linetype":"DASHED"})
        lx = (x1+x2)/2;  ly = (y1+y2)/2
        msp.add_text(str(s["index"]),
                     dxfattribs={"layer":"LABELS","height":min(eff_w,eff_l)*0.025,
                                 "insert":(lx,ly),"rotation":90,
                                 "halign":1,"valign":2})

    # Nominal rectangle
    r_pts = [(-nom_w/2,-nom_l/2),(nom_w/2,-nom_l/2),
             (nom_w/2,nom_l/2),(-nom_w/2,nom_l/2),(-nom_w/2,-nom_l/2)]
    msp.add_lwpolyline(r_pts, dxfattribs={"layer":"OUTLINE","color":1,"closed":True})


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def draw_liner_pdf(output_path, **kwargs):
    data = generate_liner(**kwargs)
    draw_pdf(data, output_path)
    return data


def draw_liner_dxf(output_path, **kwargs):
    data = generate_liner(**kwargs)
    draw_dxf(data, output_path)
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Liner layout generator")
    parser.add_argument("--shape",     default="circle",
                        choices=["circle","rectangle"])
    parser.add_argument("--diameter",  "-d", type=float)
    parser.add_argument("--width",     type=float)
    parser.add_argument("--length",    type=float)
    parser.add_argument("--strip-dir", default="along_width",
                        choices=["along_width","along_length"])
    parser.add_argument("--fabric",    "-f", default="EL6030",
                        choices=list(FABRIC_PRESETS.keys())+["custom"])
    parser.add_argument("--mode",      "-m", default="individual",
                        choices=["individual","prefab"])
    parser.add_argument("--spu",       type=int, default=3,
                        help="Strips per prefab unit")
    parser.add_argument("--layout",    default="auto",
                        choices=["auto","centred","straddled"])
    parser.add_argument("--pa",        type=float, default=0,
                        help="Perimeter allowance mm")
    parser.add_argument("--full-coverage", action="store_true", default=True)
    parser.add_argument("--client",    "-c", default="")
    parser.add_argument("--project",   "-p", default="")
    parser.add_argument("--output",    "-o", default=None)
    parser.add_argument("--format",    default="pdf", choices=["pdf","dxf","both"])
    args = parser.parse_args()

    base = args.output or f"liner_{args.shape}"

    kwargs = dict(
        shape=args.shape, diameter_m=args.diameter,
        width_m=args.width, length_m=args.length,
        strip_direction=args.strip_dir,
        fabric_ref=args.fabric, mode=args.mode,
        strips_per_unit=args.spu, layout=args.layout,
        full_coverage=args.full_coverage,
        perimeter_allowance_mm=args.pa,
        client=args.client, project=args.project,
    )

    if args.format in ("pdf","both"):
        path = base+".pdf"
        draw_liner_pdf(path, **kwargs)
        print(f"PDF: {path}")
    if args.format in ("dxf","both"):
        path = base+".dxf"
        draw_liner_dxf(path, **kwargs)
        print(f"DXF: {path}")
