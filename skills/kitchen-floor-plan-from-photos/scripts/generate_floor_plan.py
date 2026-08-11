#!/usr/bin/env python3
"""
Reusable kitchen floor-plan generator.
Produces a clean dimensioned PDF + SVG from a simple geometry description.

Edit the GEOMETRY dictionary below (or pass values) then run:
    python generate_floor_plan.py

Outputs:
    artifacts/Existing_Kitchen_Floor_Plan.pdf
    artifacts/Existing_Kitchen_Floor_Plan.svg
"""

from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black, white
import math
import os

# ---------------------------------------------------------------------------
# EDIT THIS SECTION FOR EACH NEW JOB
# ---------------------------------------------------------------------------
GEOMETRY = {
    "address": "123 Example St, Anytown, CA 90000",
    "title": "EXISTING KITCHEN FLOOR PLAN",
    "room_w": 13.4,          # feet – east-west
    "room_d": 10.75,         # feet – north-south
    "scale": 28,             # drawing units per foot (PDF)

    # Island (long axis can be "NS" or "EW")
    "island": {
        "long": 4.5,
        "short": 2.5,
        "orientation": "NS",   # "NS" = long edges vertical on page
        "x": 5.0,              # left edge
        "y": 3.0,              # bottom edge
    },

    # North (window) run – contains DW, sink, range
    "north_run": {
        "start_x": 1.5,
        "length": 10.0,
        "cab_d": 2.0,
    },

    # East short return (coffee / fridge side)
    "east_run": {
        "length": 3.5,
        "cab_d": 2.0,
    },

    # Appliances (relative to room)
    "dw": {"x": 1.8, "w": 2.0},
    "sink": {"x": 4.5, "w": 2.4},
    "range": {"x": 7.7, "w": 2.8},
    "fridge": {"y": 2.2, "w": 3.0, "d": 2.5},

    "sliding_door": {"start": 1.8, "length": 6.0},

    "notes": [
        "Counter surface ≈ 52 sq ft + additional ~26' linear (per field sketch)",
        "Existing cabinets: honey oak • White ceramic tile counters • 24\" base depth",
        "Island is free-standing • L-shaped base run (north + short east)",
        "Sliding door to patio on west • Window over sink • Range on north wall",
    ],
}

# Output directory (relative to where the script is run)
OUT_DIR = "/home/workdir/artifacts"
PDF_NAME = "Existing_Kitchen_Floor_Plan.pdf"
SVG_NAME = "Existing_Kitchen_Floor_Plan.svg"

# Colors
WALL_COLOR = HexColor("#1a1a1a")
CABINET_FILL = HexColor("#e8d5b5")
CABINET_STROKE = HexColor("#8b6914")
ISLAND_FILL = HexColor("#f5e6c8")
APPLIANCE_FILL = HexColor("#d0d0d0")
FRIDGE_FILL = HexColor("#b0b8c0")
DIMENSION_COLOR = HexColor("#003366")
TEXT_COLOR = HexColor("#222222")
SLIDING_DOOR_COLOR = HexColor("#4a90a4")
TITLE_BG = HexColor("#2c3e50")
WINDOW_COLOR = HexColor("#5b9bd5")


def draw_dimension_line(c, x1, y1, x2, y2, text, offset=18, above=True):
    c.setStrokeColor(DIMENSION_COLOR)
    c.setFillColor(DIMENSION_COLOR)
    c.setLineWidth(0.7)
    c.line(x1, y1, x2, y2)
    dx, dy = x2 - x1, y2 - y1
    length = math.sqrt(dx*dx + dy*dy) or 1
    ux, uy = dx/length, dy/length
    px, py = -uy, ux
    tick = 6
    c.line(x1 - px*tick, y1 - py*tick, x1 + px*tick, y1 + py*tick)
    c.line(x2 - px*tick, y2 - py*tick, x2 + px*tick, y2 + py*tick)
    arrow = 5
    c.line(x1, y1, x1 + ux*arrow + px*3, y1 + uy*arrow + py*3)
    c.line(x1, y1, x1 + ux*arrow - px*3, y1 + uy*arrow - py*3)
    c.line(x2, y2, x2 - ux*arrow + px*3, y2 - uy*arrow + py*3)
    c.line(x2, y2, x2 - ux*arrow - px*3, y2 - uy*arrow - py*3)
    mx, my = (x1+x2)/2, (y1+y2)/2
    c.setFont("Helvetica-Bold", 8)
    if above:
        c.drawCentredString(mx + px*offset, my + py*offset, text)
    else:
        c.drawCentredString(mx - px*offset, my - py*offset, text)


def create_pdf(geom, out_path):
    page_w, page_h = landscape(letter)
    c = canvas.Canvas(out_path, pagesize=landscape(letter))
    scale = geom["scale"]
    ox, oy = 1.3 * inch, 1.35 * inch

    def ft(x):
        return x * scale

    room_w = geom["room_w"]
    room_d = geom["room_d"]

    # Title block
    c.setFillColor(TITLE_BG)
    c.rect(0, page_h - 0.85*inch, page_w, 0.85*inch, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.5*inch, page_h - 0.38*inch, geom["title"])
    c.setFont("Helvetica", 10)
    c.drawString(0.5*inch, page_h - 0.60*inch, geom["address"])
    c.setFont("Helvetica", 8)
    c.drawRightString(page_w - 0.4*inch, page_h - 0.32*inch, "Prepared for Remodel Visualization")
    c.drawRightString(page_w - 0.4*inch, page_h - 0.50*inch, "Not a construction document — Reference only")
    c.drawRightString(page_w - 0.4*inch, page_h - 0.68*inch, "Scale: ≈ 1/4\" = 1'-0\"  |  Units: Feet-Inches")

    # North arrow
    nx, ny = page_w - 1.1*inch, page_h - 1.7*inch
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(1.2)
    c.circle(nx, ny, 18, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(nx, ny + 22, "N")
    c.line(nx, ny - 12, nx, ny + 12)
    c.line(nx, ny + 12, nx - 4, ny + 5)
    c.line(nx, ny + 12, nx + 4, ny + 5)

    # Walls
    c.setStrokeColor(WALL_COLOR)
    c.setLineWidth(3.5)
    c.line(ox, oy + ft(room_d), ox + ft(room_w), oy + ft(room_d))  # North
    c.line(ox + ft(room_w), oy, ox + ft(room_w), oy + ft(room_d))  # East
    c.setDash(4, 3)
    c.setLineWidth(1.2)
    c.line(ox, oy, ox + ft(room_w), oy)  # South open
    c.setDash()
    c.setLineWidth(3.5)
    c.line(ox, oy, ox, oy + ft(room_d))  # West

    # Sliding door
    sd = geom["sliding_door"]
    c.setStrokeColor(white)
    c.setLineWidth(5)
    c.line(ox, oy + ft(sd["start"]), ox, oy + ft(sd["start"] + sd["length"]))
    c.setStrokeColor(SLIDING_DOOR_COLOR)
    c.setLineWidth(1.5)
    c.setDash(2, 2)
    c.line(ox - 5, oy + ft(sd["start"]), ox - 5, oy + ft(sd["start"] + sd["length"]))
    c.setDash()
    c.setFont("Helvetica", 7)
    c.setFillColor(SLIDING_DOOR_COLOR)
    c.saveState()
    c.translate(ox - 14, oy + ft(sd["start"] + sd["length"]/2))
    c.rotate(90)
    c.drawCentredString(0, 0, "SLIDING GLASS DOOR")
    c.restoreState()

    # Base cabinets
    nr = geom["north_run"]
    er = geom["east_run"]
    cab_d = nr["cab_d"]
    c.setStrokeColor(CABINET_STROKE)
    c.setFillColor(CABINET_FILL)
    c.setLineWidth(1.0)
    c.rect(ox + ft(nr["start_x"]), oy + ft(room_d - cab_d),
           ft(nr["length"]), ft(cab_d), fill=1, stroke=1)
    c.rect(ox + ft(room_w - er["cab_d"]), oy + ft(room_d - er["length"]),
           ft(er["cab_d"]), ft(er["length"]), fill=1, stroke=1)

    # Upper cabinets (dashed)
    c.setStrokeColor(HexColor("#a67c00"))
    c.setDash(3, 2)
    c.setLineWidth(0.9)
    c.rect(ox + ft(nr["start_x"]), oy + ft(room_d - 0.25),
           ft(nr["length"]), ft(0.25), fill=0, stroke=1)
    c.rect(ox + ft(room_w - 0.25), oy + ft(room_d - er["length"]),
           ft(0.25), ft(er["length"]), fill=0, stroke=1)
    c.setDash()

    # Appliances
    c.setLineWidth(1.0)
    # DW
    dw = geom["dw"]
    c.setFillColor(APPLIANCE_FILL)
    c.setStrokeColor(black)
    c.rect(ox + ft(dw["x"]), oy + ft(room_d - cab_d), ft(dw["w"]), ft(cab_d), fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(ox + ft(dw["x"] + dw["w"]/2), oy + ft(room_d - cab_d/2 - 0.05), "DW")

    # Sink
    sk = geom["sink"]
    c.setFillColor(HexColor("#a8c8d8"))
    c.roundRect(ox + ft(sk["x"]), oy + ft(room_d - cab_d + 0.2),
                ft(sk["w"]), ft(1.6), 3, fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(ox + ft(sk["x"] + sk["w"]/2), oy + ft(room_d - cab_d + 0.9), "SINK")

    # Window
    c.setStrokeColor(WINDOW_COLOR)
    c.setLineWidth(1.5)
    c.rect(ox + ft(sk["x"] - 0.4), oy + ft(room_d) + 3, ft(sk["w"] + 0.8), 11, fill=0, stroke=1)
    c.setFont("Helvetica", 6)
    c.setFillColor(WINDOW_COLOR)
    c.drawCentredString(ox + ft(sk["x"] + sk["w"]/2), oy + ft(room_d) + 16, "WINDOW")

    # Range
    rg = geom["range"]
    c.setFillColor(APPLIANCE_FILL)
    c.setStrokeColor(black)
    c.rect(ox + ft(rg["x"]), oy + ft(room_d - cab_d), ft(rg["w"]), ft(cab_d), fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(ox + ft(rg["x"] + rg["w"]/2), oy + ft(room_d - cab_d/2 - 0.05), "RANGE")
    c.setFont("Helvetica", 5.5)
    c.setFillColor(HexColor("#666666"))
    c.drawCentredString(ox + ft(rg["x"] + rg["w"]/2), oy + ft(room_d - cab_d + 1.7), "MW ABOVE")

    # Fridge
    fr = geom["fridge"]
    c.setFillColor(FRIDGE_FILL)
    c.setStrokeColor(black)
    c.setLineWidth(1.2)
    c.rect(ox + ft(room_w - fr["d"]), oy + ft(fr["y"]),
           ft(fr["d"]), ft(fr["w"]), fill=1, stroke=1)
    c.setStrokeColor(HexColor("#555555"))
    c.setLineWidth(0.7)
    c.setDash(2, 1)
    c.arc(ox + ft(room_w - fr["d"] - 1.8), oy + ft(fr["y"]),
          ox + ft(room_w - fr["d"] + 0.3), oy + ft(fr["y"] + fr["w"]), 0, 90)
    c.setDash()
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(ox + ft(room_w - fr["d"]/2), oy + ft(fr["y"] + fr["w"]/2), "FRIDGE")

    # Island
    isl = geom["island"]
    if isl["orientation"].upper() == "NS":
        iw, ih = isl["short"], isl["long"]
    else:
        iw, ih = isl["long"], isl["short"]
    c.setFillColor(ISLAND_FILL)
    c.setStrokeColor(CABINET_STROKE)
    c.setLineWidth(1.3)
    c.rect(ox + ft(isl["x"]), oy + ft(isl["y"]), ft(iw), ft(ih), fill=1, stroke=1)
    c.setStrokeColor(HexColor("#999999"))
    c.setDash(1, 1.5)
    c.rect(ox + ft(isl["x"] - 0.1), oy + ft(isl["y"] - 0.35), ft(iw + 0.2), ft(0.35), fill=0, stroke=1)
    c.setDash()
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(ox + ft(isl["x"] + iw/2), oy + ft(isl["y"] + ih/2 + 0.15), "ISLAND")
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(ox + ft(isl["x"] + iw/2), oy + ft(isl["y"] + ih/2 - 0.25),
                        f"{isl['long']}' × {isl['short']}'")
    c.setFont("Helvetica", 5.5)
    c.setFillColor(HexColor("#555555"))
    orient_label = "(long axis N-S)" if isl["orientation"].upper() == "NS" else "(long axis E-W)"
    c.drawCentredString(ox + ft(isl["x"] + iw/2), oy + ft(isl["y"] + ih/2 - 0.55), orient_label)

    # Dimensions
    c.setStrokeColor(DIMENSION_COLOR)
    c.setFillColor(DIMENSION_COLOR)
    draw_dimension_line(c, ox, oy + ft(room_d) + 30, ox + ft(room_w), oy + ft(room_d) + 30,
                        f"{room_w}' overall".replace(".0", ""), offset=11)
    draw_dimension_line(c, ox + ft(room_w) + 24, oy, ox + ft(room_w) + 24, oy + ft(room_d),
                        f"{room_d}'", offset=13)
    if isl["orientation"].upper() == "NS":
        draw_dimension_line(c, ox + ft(isl["x"]) - 16, oy + ft(isl["y"]),
                            ox + ft(isl["x"]) - 16, oy + ft(isl["y"] + ih), "4'-6\"", offset=10)
        draw_dimension_line(c, ox + ft(isl["x"]), oy + ft(isl["y"]) - 14,
                            ox + ft(isl["x"] + iw), oy + ft(isl["y"]) - 14, "2'-6\"", offset=8, above=False)
    draw_dimension_line(c, ox + ft(nr["start_x"]), oy + ft(room_d) + 14,
                        ox + ft(nr["start_x"] + nr["length"]), oy + ft(room_d) + 14,
                        "≈ north counter run", offset=7)
    draw_dimension_line(c, ox + ft(room_w) + 9, oy + ft(room_d - cab_d),
                        ox + ft(room_w) + 9, oy + ft(room_d), "2'-0\"", offset=7)

    # Notes
    notes_x = ox + ft(0.2)
    c.setStrokeColor(HexColor("#cccccc"))
    c.setLineWidth(0.6)
    c.setFillColor(HexColor("#f8f8f8"))
    c.roundRect(notes_x, 0.22*inch, 7.0*inch, 0.9*inch, 4, fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(notes_x + 8, 0.95*inch, "KEY NOTES (from field sketches + site photos)")
    c.setFont("Helvetica", 6.5)
    y = 0.80
    for note in geom.get("notes", []):
        c.drawString(notes_x + 8, y*inch, "• " + note)
        y -= 0.13

    # Legend
    leg_x = page_w - 2.55*inch
    leg_y = 2.0*inch
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(TEXT_COLOR)
    c.drawString(leg_x, leg_y + 58, "LEGEND")
    c.setLineWidth(0.8)
    c.setFillColor(CABINET_FILL)
    c.setStrokeColor(CABINET_STROKE)
    c.rect(leg_x, leg_y + 42, 14, 10, fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica", 6)
    c.drawString(leg_x + 18, leg_y + 44, "Base cabinets")
    c.setFillColor(ISLAND_FILL)
    c.rect(leg_x, leg_y + 28, 14, 10, fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.drawString(leg_x + 18, leg_y + 30, "Island")
    c.setFillColor(APPLIANCE_FILL)
    c.setStrokeColor(black)
    c.rect(leg_x, leg_y + 14, 14, 10, fill=1, stroke=1)
    c.setFillColor(TEXT_COLOR)
    c.drawString(leg_x + 18, leg_y + 16, "Appliances")
    c.setStrokeColor(WALL_COLOR)
    c.setLineWidth(2.5)
    c.line(leg_x, leg_y + 2, leg_x + 14, leg_y + 2)
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica", 6)
    c.drawString(leg_x + 18, leg_y - 1, "Walls")

    c.setFont("Helvetica", 6)
    c.setFillColor(HexColor("#888888"))
    c.drawString(0.5*inch, 0.10*inch, "Generated for remodel presentation  •  Import SVG or trace PDF into CAD / IFC")
    c.drawRightString(page_w - 0.4*inch, 0.10*inch, "Page 1 of 1")
    c.save()
    print(f"PDF written: {out_path}")


def create_svg(geom, out_path):
    scale = 22
    margin = 50
    room_w = geom["room_w"]
    room_d = geom["room_d"]
    svg_w = int(room_w * scale + 2 * margin + 140)
    svg_h = int(room_d * scale + 2 * margin + 90)

    def sx(x):
        return margin + x * scale
    def sy(y):
        return svg_h - margin - y * scale

    nr = geom["north_run"]
    er = geom["east_run"]
    isl = geom["island"]
    if isl["orientation"].upper() == "NS":
        iw, ih = isl["short"], isl["long"]
    else:
        iw, ih = isl["long"], isl["short"]

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">')
    lines.append(f'<title>{geom["title"]} - {geom["address"]}</title>')
    lines.append('<desc>Clean dimensioned plan for CAD/IFC import. Units = feet.</desc>')
    lines.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    lines.append(f'<text x="{margin}" y="24" font-family="Arial, Helvetica, sans-serif" font-size="15" font-weight="bold" fill="#1a1a1a">{geom["title"]} — {geom["address"]}</text>')
    lines.append(f'<text x="{margin}" y="40" font-family="Arial, Helvetica, sans-serif" font-size="9" fill="#555">For CAD / IFC import  •  Not a construction document</text>')

    # Walls
    lines.append('<g id="walls" stroke="#1a1a1a" stroke-width="3.5" fill="none">')
    lines.append(f'  <line x1="{sx(0)}" y1="{sy(room_d)}" x2="{sx(room_w)}" y2="{sy(room_d)}"/>')
    lines.append(f'  <line x1="{sx(room_w)}" y1="{sy(0)}" x2="{sx(room_w)}" y2="{sy(room_d)}"/>')
    lines.append(f'  <line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(0)}" y2="{sy(room_d)}"/>')
    lines.append(f'  <line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(room_w)}" y2="{sy(0)}" stroke-dasharray="6,4" stroke-width="1.5"/>')
    lines.append('</g>')

    # Base cabinets (inside)
    lines.append('<g id="base-cabinets" fill="#e8d5b5" stroke="#8b6914" stroke-width="1.2">')
    lines.append(f'  <rect x="{sx(nr["start_x"])}" y="{sy(room_d)}" width="{nr["length"]*scale}" height="{nr["cab_d"]*scale}"/>')
    lines.append(f'  <rect x="{sx(room_w - er["cab_d"])}" y="{sy(room_d)}" width="{er["cab_d"]*scale}" height="{er["length"]*scale}"/>')
    lines.append('</g>')

    # Island
    lines.append('<g id="island" fill="#f5e6c8" stroke="#8b6914" stroke-width="1.5">')
    lines.append(f'  <rect x="{sx(isl["x"])}" y="{sy(isl["y"] + ih)}" width="{iw*scale}" height="{ih*scale}"/>')
    lines.append(f'  <text x="{sx(isl["x"] + iw/2)}" y="{sy(isl["y"] + ih/2) + 4}" font-family="Arial" font-size="11" font-weight="bold" text-anchor="middle" fill="#333">ISLAND</text>')
    lines.append(f'  <text x="{sx(isl["x"] + iw/2)}" y="{sy(isl["y"] + ih/2 - 0.4) + 4}" font-family="Arial" font-size="9" text-anchor="middle" fill="#555">{isl["long"]}\' × {isl["short"]}\'</text>')
    lines.append('</g>')

    # Fridge
    fr = geom["fridge"]
    lines.append('<g id="fridge" fill="#b0b8c0" stroke="#333" stroke-width="1.3">')
    lines.append(f'  <rect x="{sx(room_w - fr["d"])}" y="{sy(fr["y"] + fr["w"])}" width="{fr["d"]*scale}" height="{fr["w"]*scale}"/>')
    lines.append(f'  <text x="{sx(room_w - fr["d"]/2)}" y="{sy(fr["y"] + fr["w"]/2) + 3}" font-family="Arial" font-size="10" font-weight="bold" text-anchor="middle" fill="#222">FRIDGE</text>')
    lines.append('</g>')

    # Sink, DW, Range
    sk = geom["sink"]
    lines.append('<g id="sink" fill="#a8c8d8" stroke="#333" stroke-width="1">')
    lines.append(f'  <rect x="{sx(sk["x"])}" y="{sy(room_d - 0.2)}" width="{sk["w"]*scale}" height="{1.6*scale}" rx="3"/>')
    lines.append(f'  <text x="{sx(sk["x"] + sk["w"]/2)}" y="{sy(room_d - 1.0) + 3}" font-family="Arial" font-size="9" font-weight="bold" text-anchor="middle">SINK</text>')
    lines.append('</g>')

    dw = geom["dw"]
    lines.append('<g id="dishwasher" fill="#d0d0d0" stroke="#333" stroke-width="1">')
    lines.append(f'  <rect x="{sx(dw["x"])}" y="{sy(room_d)}" width="{dw["w"]*scale}" height="{nr["cab_d"]*scale}"/>')
    lines.append(f'  <text x="{sx(dw["x"] + dw["w"]/2)}" y="{sy(room_d - 1.0) + 3}" font-family="Arial" font-size="9" font-weight="bold" text-anchor="middle">DW</text>')
    lines.append('</g>')

    rg = geom["range"]
    lines.append('<g id="range" fill="#d0d0d0" stroke="#333" stroke-width="1">')
    lines.append(f'  <rect x="{sx(rg["x"])}" y="{sy(room_d)}" width="{rg["w"]*scale}" height="{nr["cab_d"]*scale}"/>')
    lines.append(f'  <text x="{sx(rg["x"] + rg["w"]/2)}" y="{sy(room_d - 1.0) + 3}" font-family="Arial" font-size="9" font-weight="bold" text-anchor="middle">RANGE</text>')
    lines.append('</g>')

    lines.append(f'<text x="{sx(sk["x"] + sk["w"]/2)}" y="{sy(room_d) - 12}" font-family="Arial" font-size="8" fill="#5b9bd5" text-anchor="middle">WINDOW</text>')

    # Dimensions
    lines.append('<g id="dimensions" font-family="Arial" font-size="11" fill="#003366" font-weight="bold">')
    lines.append(f'  <text x="{sx(room_w/2)}" y="{sy(room_d) - 22}" text-anchor="middle">{room_w}\' overall</text>')
    lines.append(f'  <text x="{sx(room_w) + 28}" y="{sy(room_d/2)}" text-anchor="middle" transform="rotate(90 {sx(room_w)+28} {sy(room_d/2)})">{room_d}\'</text>')
    lines.append('</g>')

    lines.append(f'<text x="{margin}" y="{svg_h - 22}" font-family="Arial" font-size="8" fill="#555">Ready for CAD/IFC import</text>')
    lines.append('</svg>')

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"SVG written: {out_path}")


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUT_DIR, PDF_NAME)
    svg_path = os.path.join(OUT_DIR, SVG_NAME)
    create_pdf(GEOMETRY, pdf_path)
    create_svg(GEOMETRY, svg_path)
    print("Done.")
