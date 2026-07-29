"""
Generate ALL NextXR Digital Twin technical documents with embedded diagram images.

Produces:
  - 4 PNG diagrams (Pillow-rendered)
  - 5 DOCX documents with diagrams embedded at appropriate sections

Usage: python gen_all_docs.py
"""

from __future__ import annotations
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

# ============================================================================
# PATHS
# ============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TECHDOCS_DIR = os.path.join(SCRIPT_DIR, "TechDocs")
os.makedirs(TECHDOCS_DIR, exist_ok=True)

# Output PNG paths
PNG_INFRA = os.path.join(TECHDOCS_DIR, "01_infrastructure.png")
PNG_COMPONENT = os.path.join(TECHDOCS_DIR, "02_component.png")
PNG_DATAFLOW = os.path.join(TECHDOCS_DIR, "03_dataflow.png")
PNG_ERD = os.path.join(TECHDOCS_DIR, "04_erd.png")

# ============================================================================
# FONT SETUP
# ============================================================================
_FONT_PATHS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font():
    for p in _FONT_PATHS:
        if os.path.exists(p):
            return p
    return None


_FONT_FILE = _find_font()


def get_font(size):
    if _FONT_FILE:
        try:
            return ImageFont.truetype(_FONT_FILE, size)
        except Exception:
            pass
    return ImageFont.load_default()


def get_bold_font(size):
    # Most system fonts don't have separate bold files accessible easily
    # Use same font at slightly larger size for emphasis
    return get_font(size)


# ============================================================================
# DIAGRAM COLORS
# ============================================================================
# Layer colors matching the HTML diagrams
COL_CLIENT = "#E8F5E9"       # light green
COL_CLIENT_BORDER = "#4CAF50"
COL_PUBLIC = "#FFEBEE"       # light red
COL_PUBLIC_BORDER = "#F44336"
COL_PRIVATE = "#FFF8E1"      # light yellow
COL_PRIVATE_BORDER = "#FF9800"
COL_ISOLATED = "#E8EAF6"     # light indigo
COL_ISOLATED_BORDER = "#3F51B5"
COL_SERVERLESS = "#F3E5F5"   # light purple
COL_SERVERLESS_BORDER = "#9C27B0"
COL_EXTERNAL = "#ECEFF1"     # light grey
COL_EXTERNAL_BORDER = "#607D8B"

# Component layer colors
COL_L1 = "#E3F2FD"
COL_L1_B = "#1976D2"
COL_L2 = "#E8F5E9"
COL_L2_B = "#388E3C"
COL_L3 = "#FFF3E0"
COL_L3_B = "#F57C00"
COL_L4 = "#F3E5F5"
COL_L4_B = "#7B1FA2"
COL_L5 = "#ECEFF1"
COL_L5_B = "#455A64"
COL_L6 = "#FBE9E7"
COL_L6_B = "#D84315"

# Service box colors
COL_SVC_BG = "#FFFFFF"
COL_SVC_BORDER = "#90A4AE"

# Arrow and text
COL_ARROW = "#37474F"
COL_TEXT = "#212121"
COL_TITLE_BG = "#1B2A4A"
COL_TITLE_TEXT = "#FFFFFF"
COL_BADGE_BG = "#2E5BFF"
COL_BADGE_TEXT = "#FFFFFF"

# ERD colors
COL_ERD_REGISTRY = "#E3F2FD"
COL_ERD_GOVERNANCE = "#E8F5E9"
COL_ERD_HISTORIAN = "#FFF3E0"
COL_ERD_EDGE = "#F3E5F5"
COL_ERD_AGENTS = "#FFEBEE"
COL_ERD_THREED = "#ECEFF1"
COL_ERD_GRAPH = "#FFF8E1"
COL_ERD_TABLE_HEADER = "#1B2A4A"
COL_ERD_TABLE_BG = "#FFFFFF"
COL_ERD_FK_LINE = "#F44336"
COL_ERD_PK = "#2E5BFF"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    if fill:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)
    if outline:
        draw.arc([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=outline, width=width)
        draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=width)


def draw_arrow(draw, x1, y1, x2, y2, color, width=2, head_size=8):
    """Draw a line with an arrowhead."""
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    ax1 = x2 - head_size * math.cos(angle - math.pi / 6)
    ay1 = y2 - head_size * math.sin(angle - math.pi / 6)
    ax2 = x2 - head_size * math.cos(angle + math.pi / 6)
    ay2 = y2 - head_size * math.sin(angle + math.pi / 6)
    draw.polygon([(x2, y2), (int(ax1), int(ay1)), (int(ax2), int(ay2))], fill=color)


def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def text_height(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def draw_svc_box(draw, x, y, w, h, label, fill_color, border_color, font):
    """Draw a service box with label centered inside."""
    draw_rounded_rect(draw, (x, y, x + w, y + h), 6,
                      fill=hex_to_rgb(fill_color),
                      outline=hex_to_rgb(border_color), width=2)
    lines = label.split("\n")
    line_h = 14
    total = len(lines) * line_h
    ty = y + (h - total) // 2
    for i, line in enumerate(lines):
        tw = text_width(draw, line, font)
        draw.text((x + (w - tw) // 2, ty + i * line_h), line,
                  fill=hex_to_rgb(COL_TEXT), font=font)


def draw_layer_box(draw, x, y, w, h, label, fill_color, border_color, font_label):
    """Draw a layer box with label at top-left."""
    draw_rounded_rect(draw, (x, y, x + w, y + h), 8,
                      fill=hex_to_rgb(fill_color),
                      outline=hex_to_rgb(border_color), width=2)
    draw.text((x + 12, y + 6), label, fill=hex_to_rgb(border_color), font=font_label)


# ============================================================================
# 1. INFRASTRUCTURE DIAGRAM (01_infrastructure.png)
# ============================================================================

def generate_infrastructure_png():
    W, H = 1200, 780
    img = Image.new("RGB", (W, H), hex_to_rgb("#FAFBFC"))
    draw = ImageDraw.Draw(img)

    f_title = get_font(20)
    f_layer = get_font(14)
    f_svc = get_font(11)
    f_badge = get_font(10)
    f_small = get_font(9)

    # Title bar
    draw.rectangle([(0, 0), (W, 50)], fill=hex_to_rgb(COL_TITLE_BG))
    title = "NextXR Digital Twin  --  AWS Infrastructure Architecture"
    tw = text_width(draw, title, f_title)
    draw.text(((W - tw) // 2, 14), title, fill=hex_to_rgb(COL_TITLE_TEXT), font=f_title)

    # Cost badge
    badge_text = "Dev ~$118/mo  |  Prod ~$470/mo  |  ECS Fargate + RDS + Redis + S3"
    bw = text_width(draw, badge_text, f_badge) + 20
    bx = 560
    draw_rounded_rect(draw, (bx, 66, bx + bw, 91), 4, fill=hex_to_rgb(COL_BADGE_BG))
    draw.text((bx + 10, 71), badge_text, fill=hex_to_rgb(COL_BADGE_TEXT), font=f_badge)

    # Layer 1: Client (top)
    ly = 65
    draw_layer_box(draw, 30, ly, 500, 65, "Client Layer", COL_CLIENT, COL_CLIENT_BORDER, f_layer)
    draw_svc_box(draw, 60, ly + 28, 160, 30, "React SPA + three.js", COL_SVC_BG, COL_CLIENT_BORDER, f_svc)
    draw_svc_box(draw, 240, ly + 28, 160, 30, "GoalCert Hub", COL_SVC_BG, COL_CLIENT_BORDER, f_svc)

    draw_arrow(draw, 260, ly + 65, 260, ly + 85, hex_to_rgb(COL_ARROW), width=2, head_size=10)

    # Layer 2: Public Subnet
    ly2 = ly + 90
    draw_layer_box(draw, 30, ly2, 1140, 90, "Public Subnet", COL_PUBLIC, COL_PUBLIC_BORDER, f_layer)
    draw_svc_box(draw, 60, ly2 + 28, 200, 50, "ALB\n(HTTPS/443)", COL_SVC_BG, COL_PUBLIC_BORDER, f_svc)
    draw_svc_box(draw, 290, ly2 + 28, 200, 50, "CloudFront (optional)\n+ Route 53", COL_SVC_BG, COL_PUBLIC_BORDER, f_svc)
    draw_arrow(draw, 260, ly2 + 53, 290, ly2 + 53, hex_to_rgb(COL_ARROW), width=2, head_size=8)

    draw_arrow(draw, 390, ly2 + 90, 390, ly2 + 115, hex_to_rgb(COL_ARROW), width=2, head_size=10)

    # Layer 3: Private Subnet
    ly3 = ly2 + 120
    draw_layer_box(draw, 30, ly3, 1140, 115, "Private Subnet", COL_PRIVATE, COL_PRIVATE_BORDER, f_layer)
    draw_svc_box(draw, 60, ly3 + 30, 240, 70, "ECS Fargate (Task 1)\nnextxr-twin :8080\n136 endpoints, SSE", COL_SVC_BG, COL_PRIVATE_BORDER, f_svc)
    draw_svc_box(draw, 330, ly3 + 30, 240, 70, "ECS Fargate (Task 2)\nnextxr-twin :8080\nStateless, twin leases", COL_SVC_BG, COL_PRIVATE_BORDER, f_svc)
    draw.text((600, ly3 + 55), "Desired count 2+  (twin ownership via Redis lease)",
              fill=hex_to_rgb("#FF9800"), font=f_small)

    draw_arrow(draw, 300, ly3 + 115, 300, ly3 + 140, hex_to_rgb(COL_ARROW), width=2, head_size=10)

    # Layer 4: Isolated / Data Subnet
    ly4 = ly3 + 145
    draw_layer_box(draw, 30, ly4, 1140, 115, "Data Tier (Isolated)", COL_ISOLATED, COL_ISOLATED_BORDER, f_layer)
    draw_svc_box(draw, 60, ly4 + 30, 215, 70, "RDS PostgreSQL 16\n+ TimescaleDB\n8 tables + historian", COL_SVC_BG, COL_ISOLATED_BORDER, f_svc)
    draw_svc_box(draw, 300, ly4 + 30, 200, 70, "ElastiCache Redis 7\nEvent bus streams\nTwin ownership lease", COL_SVC_BG, COL_ISOLATED_BORDER, f_svc)
    draw_svc_box(draw, 525, ly4 + 30, 185, 70, "S3 Bucket\nGLBs + 3-D artifacts\n30-90 day lifecycle", COL_SVC_BG, COL_ISOLATED_BORDER, f_svc)
    draw_svc_box(draw, 735, ly4 + 30, 175, 70, "RDS Proxy\nConnection pooling\n(recommended)", COL_SVC_BG, COL_ISOLATED_BORDER, f_svc)

    # Layer 5: Serverless / Management
    ly5 = ly4 + 135
    draw_layer_box(draw, 30, ly5, 550, 90, "Serverless / Management", COL_SERVERLESS, COL_SERVERLESS_BORDER, f_layer)
    draw_svc_box(draw, 60, ly5 + 30, 150, 50, "CloudWatch\nLogs + Alarms", COL_SVC_BG, COL_SERVERLESS_BORDER, f_svc)
    draw_svc_box(draw, 230, ly5 + 30, 150, 50, "Secrets Manager\n7 secrets", COL_SVC_BG, COL_SERVERLESS_BORDER, f_svc)
    draw_svc_box(draw, 400, ly5 + 30, 150, 50, "ECR\nContainer Registry", COL_SVC_BG, COL_SERVERLESS_BORDER, f_svc)

    # Layer 6: External Services
    ly6 = ly5 + 105
    draw_layer_box(draw, 30, ly6, 1140, 90, "External Services", COL_EXTERNAL, COL_EXTERNAL_BORDER, f_layer)
    draw_svc_box(draw, 60, ly6 + 28, 180, 50, "Neo4j Aura\nOntology graph", COL_SVC_BG, COL_EXTERNAL_BORDER, f_svc)
    draw_svc_box(draw, 270, ly6 + 28, 180, 50, "Anthropic Claude\nCopilot agents", COL_SVC_BG, COL_EXTERNAL_BORDER, f_svc)
    draw_svc_box(draw, 480, ly6 + 28, 180, 50, "RunPod GPU\nTRELLIS photo-to-3D", COL_SVC_BG, COL_EXTERNAL_BORDER, f_svc)
    draw_svc_box(draw, 690, ly6 + 28, 200, 50, "Field devices\nModbus / OPC-UA / MQTT", COL_SVC_BG, COL_EXTERNAL_BORDER, f_svc)

    # Arrows from ECS to data tier
    draw_arrow(draw, 180, ly3 + 100, 170, ly4 + 30, hex_to_rgb("#3F51B5"), width=1, head_size=6)
    draw_arrow(draw, 400, ly3 + 100, 410, ly4 + 30, hex_to_rgb("#3F51B5"), width=1, head_size=6)

    # Vertical connector from private to external
    draw_arrow(draw, 950, ly3 + 70, 950, ly6 + 28, hex_to_rgb(COL_ARROW), width=1, head_size=6)
    draw.text((958, ly3 + 130), "TLS / HTTPS", fill=hex_to_rgb("#607D8B"), font=f_small)

    # Legend
    lx, legy = 850, 65
    draw.rectangle([(lx, legy), (lx + 310, legy + 195)], fill=hex_to_rgb("#FFFFFF"), outline=hex_to_rgb("#BDBDBD"))
    draw.text((lx + 10, legy + 5), "AWS Components", fill=hex_to_rgb(COL_TEXT), font=f_layer)
    items = [
        (COL_CLIENT, "Client Layer"),
        (COL_PUBLIC, "Public Subnet (ALB)"),
        (COL_PRIVATE, "Private Subnet (ECS)"),
        (COL_ISOLATED, "Data Tier (RDS/Redis/S3)"),
        (COL_SERVERLESS, "Serverless / Mgmt"),
        (COL_EXTERNAL, "External Services"),
    ]
    for i, (color, label) in enumerate(items):
        iy = legy + 28 + i * 26
        draw.rectangle([(lx + 15, iy), (lx + 35, iy + 18)], fill=hex_to_rgb(color), outline=hex_to_rgb("#999999"))
        draw.text((lx + 42, iy + 1), label, fill=hex_to_rgb(COL_TEXT), font=f_svc)

    img.save(PNG_INFRA, "PNG")
    print(f"  Generated: {PNG_INFRA}")


# ============================================================================
# 2. COMPONENT DIAGRAM (02_component.png)
# ============================================================================

def generate_component_png():
    W, H = 1200, 745
    img = Image.new("RGB", (W, H), hex_to_rgb("#FAFBFC"))
    draw = ImageDraw.Draw(img)

    f_title = get_font(20)
    f_layer = get_font(13)
    f_svc = get_font(10)

    # Title bar
    draw.rectangle([(0, 0), (W, 45)], fill=hex_to_rgb(COL_TITLE_BG))
    title = "NextXR Digital Twin  --  Component Architecture"
    tw = text_width(draw, title, f_title)
    draw.text(((W - tw) // 2, 12), title, fill=hex_to_rgb(COL_TITLE_TEXT), font=f_title)

    layers = [
        ("Layer 1: Client", COL_L1, COL_L1_B, [
            "React SPA\n(Vite + three.js)", "GoalCert Hub\n(Module Federation)", "SSE / Event\nStream Client"
        ]),
        ("Layer 2: API (136 endpoints, 12 routers)", COL_L2, COL_L2_B, [
            "FastAPI\nApplication", "AuthMiddleware\n(API keys)", "enforce_tenant_scope\n(global dependency)",
            "CORS\nMiddleware", "3-D Platform\n(mounted app)"
        ]),
        ("Layer 3: Core Twin Services", COL_L3, COL_L3_B, [
            "MachineEngine\n(1 Hz physics tick)", "TwinCoordinator\n(Redis ownership lease)",
            "BehaviorRegistry\n(3-tier, 48 rules)", "DynamicsEngine\n(coupled topology)"
        ]),
        ("Layer 4: Platform Services", COL_L4, COL_L4_B, [
            "GraphWriter\n(validate-commit)", "ChangeLog\n(hash chain)", "Historian\n(TimescaleDB)",
            "ConnectorManager\n(Modbus/OPC-UA/MQTT)", "Copilot Agents\n(14 Claude agents)", "Storage\n(S3 / local)"
        ]),
        ("Layer 5: Data", COL_L5, COL_L5_B, [
            "Neo4j\nOntology graph", "PostgreSQL 16\n8 tables + historian", "Redis 7\nBus + leases", "S3\nBlob store"
        ]),
        ("Layer 6: External", COL_L6, COL_L6_B, [
            "Anthropic Claude\nSonnet 5", "RunPod GPU\nTRELLIS", "Field Devices\nPLC / inverter / BMS", "IFC / Floor plans\n2d-to-3d"
        ]),
    ]

    start_y = 55
    layer_h = 105
    gap = 8
    margin = 30

    for li, (label, fill, border, services) in enumerate(layers):
        y = start_y + li * (layer_h + gap)
        lw = W - 2 * margin

        draw_layer_box(draw, margin, y, lw, layer_h, label, fill, border, f_layer)

        n = len(services)
        box_area_x = margin + 15
        box_area_w = lw - 30
        box_gap = 12
        box_w = (box_area_w - (n - 1) * box_gap) // n
        box_h = layer_h - 38
        box_y = y + 26

        for si, svc in enumerate(services):
            bx = box_area_x + si * (box_w + box_gap)
            draw_svc_box(draw, bx, box_y, box_w, box_h, svc, COL_SVC_BG, border, f_svc)

        if li < len(layers) - 1:
            ay = y + layer_h
            draw_arrow(draw, W // 2, ay + 1, W // 2, ay + gap - 1, hex_to_rgb(COL_ARROW), width=2, head_size=6)

    img.save(PNG_COMPONENT, "PNG")
    print(f"  Generated: {PNG_COMPONENT}")


# ============================================================================
# 3. DATAFLOW DIAGRAM (03_dataflow.png)
# ============================================================================

def generate_dataflow_png():
    W, H = 1200, 560
    img = Image.new("RGB", (W, H), hex_to_rgb("#FAFBFC"))
    draw = ImageDraw.Draw(img)

    f_title = get_font(20)
    f_step = get_font(11)
    f_detail = get_font(9)
    f_num = get_font(14)

    draw.rectangle([(0, 0), (W, 45)], fill=hex_to_rgb(COL_TITLE_BG))
    title = "NextXR Digital Twin  --  Data Flow (Telemetry to Finding)"
    tw = text_width(draw, title, f_title)
    draw.text(((W - tw) // 2, 12), title, fill=hex_to_rgb(COL_TITLE_TEXT), font=f_title)

    steps = [
        ("Signal\nSource", "Device token,\nconnector poll\nor simulated feed", "#E3F2FD", "#1976D2"),
        ("Ingest\nPipeline", "Validate, map\npoint to asset,\ntenant scope", "#E8F5E9", "#388E3C"),
        ("Historian\nWrite", "TimescaleDB\nhypertable,\nidempotent PK", "#FFF3E0", "#F57C00"),
        ("Behaviour\nRegistry", "3 tiers:\nphysics, stats,\nrules (48)", "#F3E5F5", "#7B1FA2"),
        ("Graph\nWriter", "SHACL validate\nthen commit\nto Neo4j", "#E3F2FD", "#1565C0"),
        ("Change\nLog", "Per-tenant\nhash chain,\ntamper-evident", "#ECEFF1", "#455A64"),
        ("Event Bus\n+ SSE", "Redis stream\nper tenant to\nlive clients", "#FFEBEE", "#C62828"),
    ]

    box_w = 130
    box_h = 100
    start_x = 40
    y_main = 80
    gap = 20

    for i, (label, detail, fill, border) in enumerate(steps):
        x = start_x + i * (box_w + gap)

        cx = x + box_w // 2
        cy = y_main - 5
        draw.ellipse([(cx - 12, cy - 12), (cx + 12, cy + 12)], fill=hex_to_rgb(border))
        num = str(i + 1)
        nw = text_width(draw, num, f_num)
        draw.text((cx - nw // 2, cy - 8), num, fill=hex_to_rgb("#FFFFFF"), font=f_num)

        draw_rounded_rect(draw, (x, y_main + 10, x + box_w, y_main + 10 + box_h), 8,
                          fill=hex_to_rgb(fill), outline=hex_to_rgb(border), width=2)

        lines = label.split("\n")
        for li, line in enumerate(lines):
            lw = text_width(draw, line, f_step)
            draw.text((x + (box_w - lw) // 2, y_main + 18 + li * 16), line,
                      fill=hex_to_rgb(border), font=f_step)

        detail_lines = detail.split("\n")
        for di, dline in enumerate(detail_lines):
            dlw = text_width(draw, dline, f_detail)
            draw.text((x + (box_w - dlw) // 2, y_main + 55 + di * 14), dline,
                      fill=hex_to_rgb(COL_TEXT), font=f_detail)

        if i < len(steps) - 1:
            ax1 = x + box_w
            ax2 = x + box_w + gap
            ay = y_main + 10 + box_h // 2
            draw_arrow(draw, ax1 + 2, ay, ax2 - 2, ay, hex_to_rgb(COL_ARROW), width=2, head_size=8)

    # Second row: machine-twin runtime tick detail
    y2 = 230
    draw.rectangle([(30, y2), (W - 30, y2 + 30)], fill=hex_to_rgb("#E0E0E0"))
    draw.text((40, y2 + 6), "Machine-Twin Runtime Detail (owner task, 1 Hz per tenant):",
              fill=hex_to_rgb(COL_TEXT), font=f_step)

    sub_steps = [
        ("Acquire / renew\nRedis lease", "#E3F2FD"),
        ("Drain command\nqueue", "#E8F5E9"),
        ("Integrate physics\n(forward model)", "#FFF3E0"),
        ("Compute residuals\nvs baseline", "#F3E5F5"),
        ("Evaluate behaviour\nregistry", "#FCE4EC"),
        ("Persist findings\n(once)", "#E3F2FD"),
        ("Publish authoritative\nstate", "#E8F5E9"),
        ("Followers serve\npublished state", "#ECEFF1"),
    ]

    sub_w = 120
    sub_h = 55
    sub_y = y2 + 40
    sub_gap = 16

    for i, (label, fill) in enumerate(sub_steps):
        sx = 40 + i * (sub_w + sub_gap)
        draw_rounded_rect(draw, (sx, sub_y, sx + sub_w, sub_y + sub_h), 6,
                          fill=hex_to_rgb(fill), outline=hex_to_rgb("#90A4AE"), width=1)
        lines = label.split("\n")
        for li, line in enumerate(lines):
            lw2 = text_width(draw, line, f_detail)
            draw.text((sx + (sub_w - lw2) // 2, sub_y + 14 + li * 14), line,
                      fill=hex_to_rgb(COL_TEXT), font=f_detail)
        if i < len(sub_steps) - 1:
            draw_arrow(draw, sx + sub_w + 1, sub_y + sub_h // 2,
                       sx + sub_w + sub_gap - 1, sub_y + sub_h // 2,
                       hex_to_rgb("#90A4AE"), width=1, head_size=5)

    # Third row: bus event types
    y3 = sub_y + sub_h + 30
    draw.rectangle([(30, y3), (W - 30, y3 + 30)], fill=hex_to_rgb("#E0E0E0"))
    draw.text((40, y3 + 6), "Event Bus / SSE Stream:", fill=hex_to_rgb(COL_TEXT), font=f_step)

    events = [
        ("create", "#1976D2"),
        ("update", "#388E3C"),
        ("delete", "#F57C00"),
        ("finding", "#7B1FA2"),
        ("state", "#C62828"),
        ("connector", "#00695C"),
        ("ingest", "#455A64"),
        ("heartbeat", "#1B2A4A"),
    ]

    ev_y = y3 + 38
    ev_w = 115
    ev_h = 35
    ev_gap = 20

    for i, (evt, color) in enumerate(events):
        ex = 40 + i * (ev_w + ev_gap)
        if ex + ev_w > W - 30:
            break
        draw_rounded_rect(draw, (ex, ev_y, ex + ev_w, ev_y + ev_h), 4,
                          fill=hex_to_rgb(color), outline=hex_to_rgb(color), width=1)
        ew = text_width(draw, evt, f_detail)
        draw.text((ex + (ev_w - ew) // 2, ev_y + 12), evt,
                  fill=hex_to_rgb("#FFFFFF"), font=f_detail)
        if i < len(events) - 1 and ex + ev_w + ev_gap + ev_w < W - 30:
            draw_arrow(draw, ex + ev_w + 2, ev_y + ev_h // 2,
                       ex + ev_w + ev_gap - 2, ev_y + ev_h // 2,
                       hex_to_rgb("#BDBDBD"), width=1, head_size=4)

    # Bottom row: post-write pipeline
    y4 = ev_y + ev_h + 25
    draw.rectangle([(30, y4), (W - 30, y4 + 80)], fill=hex_to_rgb("#FAFAFA"), outline=hex_to_rgb("#E0E0E0"))
    draw.text((40, y4 + 8), "Post-Write Pipeline:", fill=hex_to_rgb(COL_TEXT), font=f_step)

    post_items = [
        "Finding node written to Neo4j",
        "Change-log event appended (advisory lock)",
        "wm_hash chained to prev_event_hash",
        "BusEvent published to nxr:events:<tenant>",
        "Copilot agents read live diagnostics",
    ]
    for i, item in enumerate(post_items):
        ix = 45 + i * 228
        if ix + 210 > W - 30:
            break
        draw.text((ix, y4 + 35), f"  {item}", fill=hex_to_rgb(COL_TEXT), font=f_detail)

    img.save(PNG_DATAFLOW, "PNG")
    print(f"  Generated: {PNG_DATAFLOW}")


# ============================================================================
# 4. ERD DIAGRAM (04_erd.png)
# ============================================================================

def generate_erd_png():
    W, H = 1400, 875
    img = Image.new("RGB", (W, H), hex_to_rgb("#FAFBFC"))
    draw = ImageDraw.Draw(img)

    f_title = get_font(18)
    f_domain = get_font(12)
    f_table = get_font(11)
    f_col = get_font(8)

    draw.rectangle([(0, 0), (W, 40)], fill=hex_to_rgb(COL_TITLE_BG))
    title = "NextXR Digital Twin  --  Data Model (9 Relational Tables + 3 Rollups + Graph)"
    tw = text_width(draw, title, f_title)
    draw.text(((W - tw) // 2, 10), title, fill=hex_to_rgb(COL_TITLE_TEXT), font=f_title)

    def draw_erd_table(x, y, name, columns, w=180):
        header_h = 24
        row_h = 16
        total_h = header_h + len(columns) * row_h + 4

        draw.rectangle([(x, y), (x + w, y + total_h)], fill=hex_to_rgb(COL_ERD_TABLE_BG),
                       outline=hex_to_rgb("#90A4AE"), width=1)
        draw.rectangle([(x, y), (x + w, y + header_h)], fill=hex_to_rgb(COL_ERD_TABLE_HEADER))
        nw = text_width(draw, name, f_table)
        draw.text((x + (w - nw) // 2, y + 4), name, fill=hex_to_rgb("#FFFFFF"), font=f_table)

        for i, col in enumerate(columns):
            cy = y + header_h + i * row_h + 2
            prefix = ""
            col_color = COL_TEXT
            if col.startswith("PK "):
                prefix = "PK "
                col = col[3:]
                col_color = "#2E5BFF"
            elif col.startswith("FK "):
                prefix = "FK "
                col = col[3:]
                col_color = "#F44336"
            if prefix:
                pw = text_width(draw, prefix, f_col)
                draw.text((x + 6, cy), prefix, fill=hex_to_rgb(col_color), font=f_col)
                draw.text((x + 6 + pw, cy), col, fill=hex_to_rgb(COL_TEXT), font=f_col)
            else:
                draw.text((x + 6, cy), col, fill=hex_to_rgb(COL_TEXT), font=f_col)

        return (x, y, w, total_h)

    domains_layout = [
        ("Twin Registry", 20, 50, 330, 170, COL_ERD_REGISTRY),
        ("Governance (tamper-evident)", 370, 50, 340, 240, COL_ERD_GOVERNANCE),
        ("Agents & Capability", 730, 50, 340, 170, COL_ERD_AGENTS),
        ("3-D Reconstruction", 1090, 50, 290, 230, COL_ERD_THREED),
        ("Edge / Field", 20, 330, 690, 280, COL_ERD_EDGE),
        ("Historian (TimescaleDB)", 730, 330, 650, 280, COL_ERD_HISTORIAN),
        ("Neo4j Graph Model (10 closed taxonomy categories)", 20, 630, 1360, 225, COL_ERD_GRAPH),
    ]

    for label, dx, dy, dw, dh, color in domains_layout:
        draw_rounded_rect(draw, (dx, dy, dx + dw, dy + dh), 8,
                          fill=hex_to_rgb(color), outline=hex_to_rgb("#BDBDBD"), width=1)
        draw.text((dx + 8, dy + 4), label, fill=hex_to_rgb("#666666"), font=f_domain)

    # Twin Registry
    draw_erd_table(35, 75, "twins", [
        "PK tenant_id TEXT", "name TEXT", "domain TEXT",
        "description TEXT", "created_at TEXT", "seed_asset_id TEXT",
    ], w=160)
    draw_erd_table(205, 75, "scene_cache", [
        "PK tenant_id TEXT", "scene JSONB", "updated_at TIMESTAMPTZ",
    ], w=130)

    # Governance
    draw_erd_table(385, 75, "events", [
        "PK seq BIGSERIAL", "event_id TEXT UNIQUE (ULID)",
        "tenant_id TEXT", "entity_id TEXT", "entity_type TEXT",
        "actor TEXT", "action TEXT", "field_changes JSONB",
        "ts TEXT", "prev_event_hash TEXT", "wm_hash TEXT",
    ], w=195)
    draw.text((590, 200), "hash chain:", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((590, 214), "prev_event_hash", fill=hex_to_rgb("#F44336"), font=f_col)
    draw.text((590, 228), "  -> wm_hash", fill=hex_to_rgb("#F44336"), font=f_col)
    draw.text((590, 246), "per-tenant,", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((590, 260), "advisory-locked", fill=hex_to_rgb("#666666"), font=f_col)

    # Agents & Capability
    draw_erd_table(745, 75, "published_bundles", [
        "PK bundle_id TEXT", "name TEXT", "domains JSONB",
        "payload JSONB", "tenant_id TEXT", "created_at TIMESTAMPTZ",
    ], w=155)
    draw_erd_table(915, 75, "checkpoints", [
        "PK thread_id TEXT", "graph_name TEXT", "state JSONB",
        "resume_at TEXT", "updated_at TIMESTAMPTZ",
    ], w=140)

    # 3-D
    draw_erd_table(1105, 75, "threed_jobs", [
        "PK job_id TEXT", "status TEXT", "stage TEXT",
        "filename TEXT", "fields JSONB", "stages JSONB",
        "state JSONB", "error TEXT",
        "created DOUBLE", "updated DOUBLE",
    ], w=160)

    # Edge / Field
    draw_erd_table(35, 355, "ingest_devices", [
        "PK device_id TEXT", "tenant_id TEXT", "name TEXT",
        "token_hash TEXT UNIQUE", "asset_prefix TEXT",
        "enabled INTEGER", "created_at / created_by",
        "expires_at TEXT", "last_seen_at / last_seen_ip",
        "samples_total DOUBLE", "rejected_total DOUBLE",
    ], w=210)

    draw_erd_table(265, 355, "connectors", [
        "PK connector_id TEXT", "tenant_id TEXT",
        "protocol TEXT", "name TEXT",
        "enabled INTEGER", "config JSONB (point map",
        "  + redacted credentials)",
        "created_at TEXT", "updated_at TEXT",
    ], w=200)

    draw.text((485, 380), "protocols:", fill=hex_to_rgb("#666666"), font=f_col)
    for i, p in enumerate(["modbus_tcp", "modbus_rtu", "opcua", "mqtt (Sparkplug B)"]):
        draw.text((485, 396 + i * 14), "  - " + p, fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((485, 460), "7 point-map profiles", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((485, 474), "(sunspec_inverter_3ph,", fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((485, 488), " string_combiner, ...)", fill=hex_to_rgb(COL_TEXT), font=f_col)

    # Historian
    draw_erd_table(745, 355, "measurements", [
        "PK tenant_id TEXT", "PK asset_id TEXT",
        "PK signal TEXT", "PK ts TIMESTAMPTZ",
        "value DOUBLE PRECISION", "unit TEXT",
        "quality SMALLINT (OPC)", "source TEXT",
        "received_at TIMESTAMPTZ",
    ], w=205)
    draw.text((965, 380), "hypertable, 1-day chunks", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((965, 394), "compress after 7 days", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((965, 408), "raw retention 30 days", fill=hex_to_rgb("#666666"), font=f_col)

    draw_erd_table(1120, 355, "measurements_1m / _1h / _1d", [
        "tenant_id, asset_id, signal", "bucket TIMESTAMPTZ",
        "count / sum_value", "min_value / max_value",
        "first_value / last_value", "bad_count", "unit",
    ], w=240)
    draw.text((1120, 500), "continuous aggregates", fill=hex_to_rgb("#666666"), font=f_col)
    draw.text((1120, 514), "retention 400 / 1095 / forever", fill=hex_to_rgb("#666666"), font=f_col)

    # Neo4j graph model
    cats = [
        ("PhysicalAsset", "equipment, sensors"),
        ("MobileAsset", "trains, vehicles, drones"),
        ("Actor", "people, teams, units"),
        ("Location", "sites, spaces"),
        ("Process", "activities over time"),
        ("Observation", "timestamped measurements"),
        ("Finding", "atomic anomaly signals"),
        ("Incident", "correlated findings"),
        ("Document", "diagnoses, recommendations"),
        ("Capability", "bundles, adapters, models"),
    ]
    gx0, gy0 = 40, 663
    bw, bh, bgap = 260, 46, 12
    for i, (cat, desc) in enumerate(cats):
        col = i % 5
        row = i // 5
        bx = gx0 + col * (bw + bgap)
        by = gy0 + row * (bh + bgap)
        draw_rounded_rect(draw, (bx, by, bx + bw, by + bh), 6,
                          fill=hex_to_rgb("#FFFFFF"), outline=hex_to_rgb("#3F51B5"), width=1)
        draw.text((bx + 8, by + 6), cat, fill=hex_to_rgb("#1B2A4A"), font=f_table)
        draw.text((bx + 8, by + 25), desc, fill=hex_to_rgb("#666666"), font=f_col)

    gy1 = gy0 + 2 * (bh + bgap) + 6
    draw.text((40, gy1), "Every node: (tenantId, id) UNIQUE constraint  +  index on updatedAt.  "
                         "Relationships are ontology object properties (CURIE prefixed).",
              fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((40, gy1 + 16), "Structural classes (StateMachine, State, Transition, QuantitativeResult) are "
                              "nxr:isStructural and exempt from the closed-category rule.",
              fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((40, gy1 + 32), "The eleventh label is :ChangeLog - unique on (tenantId, id), indexed on "
                              "(tenantId, seq) and (tenantId, entityId).",
              fill=hex_to_rgb(COL_TEXT), font=f_col)

    # Legend
    lx, ly = 20, 232
    draw.rectangle([(lx, ly), (lx + 330, ly + 58)], fill=hex_to_rgb("#FFFFFF"), outline=hex_to_rgb("#BDBDBD"))
    draw.text((lx + 10, ly + 4), "Legend:", fill=hex_to_rgb(COL_TEXT), font=f_table)
    draw.text((lx + 10, ly + 22), "PK", fill=hex_to_rgb("#2E5BFF"), font=f_col)
    draw.text((lx + 30, ly + 22), "= Primary Key", fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((lx + 130, ly + 22), "FK", fill=hex_to_rgb("#F44336"), font=f_col)
    draw.text((lx + 150, ly + 22), "= Foreign Key", fill=hex_to_rgb(COL_TEXT), font=f_col)
    draw.text((lx + 10, ly + 38), "tenant_id is the isolation key on every table (no cross-tenant FK).",
              fill=hex_to_rgb(COL_TEXT), font=f_col)

    img.save(PNG_ERD, "PNG")
    print(f"  Generated: {PNG_ERD}")


# ============================================================================
# DOCX HELPER FUNCTIONS (shared across all docs)
# ============================================================================

NAVY_D       = RGBColor(0x1B, 0x2A, 0x4A)
ACCENT_D     = RGBColor(0x2E, 0x5B, 0xFF)
SUBTITLE_D   = RGBColor(0x66, 0x66, 0x66)
WHITE_D      = RGBColor(0xFF, 0xFF, 0xFF)
DARK_D       = RGBColor(0x33, 0x33, 0x33)
GREY_D       = RGBColor(0x66, 0x66, 0x66)
FONT_NAME_D  = "Calibri"
HEADER_BG_D  = "1B2A4A"
ALT_ROW_D    = "F2F6FA"


def d_set_cell_shading(cell, colour_hex: str):
    shading = cell._element.get_or_add_tcPr()
    shd = shading.makeelement(qn("w:shd"), {
        qn("w:fill"): colour_hex, qn("w:val"): "clear",
    })
    shading.append(shd)


def d_style_heading(doc, level):
    s = doc.styles[f"Heading {level}"]
    s.font.name = FONT_NAME_D
    s.font.bold = True
    if level == 1:
        s.font.size = Pt(16); s.font.color.rgb = NAVY_D
        s.paragraph_format.space_before = Pt(24); s.paragraph_format.space_after = Pt(0)
    elif level == 2:
        s.font.size = Pt(14); s.font.color.rgb = ACCENT_D
        s.paragraph_format.space_before = Pt(10); s.paragraph_format.space_after = Pt(0)
    elif level == 3:
        s.font.size = Pt(12); s.font.color.rgb = ACCENT_D
        s.paragraph_format.space_before = Pt(10); s.paragraph_format.space_after = Pt(0)


def d_title_run(paragraph, text, size_pt, bold=False, color=None):
    if color is None:
        color = NAVY_D
    run = paragraph.add_run(text)
    run.font.name = FONT_NAME_D; run.font.size = Pt(size_pt)
    run.font.bold = bold; run.font.color.rgb = color


def d_set_font(run, name="Calibri", size=11, bold=False, color=None):
    if color is None:
        color = DARK_D
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def d_add_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]; cell.text = h
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.name = FONT_NAME_D; r.font.size = Pt(9.5)
                r.font.bold = True; r.font.color.rgb = WHITE_D
        d_set_cell_shading(cell, HEADER_BG_D)
    for ri, rd in enumerate(rows):
        for ci, val in enumerate(rd):
            cell = table.rows[ri + 1].cells[ci]; cell.text = str(val)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.name = FONT_NAME_D; r.font.size = Pt(9.5)
    if col_widths:
        for row in table.rows:
            for ci, w in enumerate(col_widths):
                row.cells[ci].width = Cm(w)
    return table


def d_add_table_alt(doc, headers, rows, col_widths=None):
    """Table with alternating row shading (DRD/AWS style)."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hrow = table.rows[0]
    for i, h in enumerate(headers):
        cell = hrow.cells[i]
        d_set_cell_shading(cell, HEADER_BG_D)
        p = cell.paragraphs[0]
        run = p.add_run(h)
        d_set_font(run, size=10, bold=True, color=WHITE_D)
    for ri, rd in enumerate(rows):
        row = table.add_row()
        for ci, val in enumerate(rd):
            cell = row.cells[ci]
            if ri % 2 == 0:
                d_set_cell_shading(cell, ALT_ROW_D)
            p = cell.paragraphs[0]
            run = p.add_run(str(val))
            d_set_font(run, size=9, color=DARK_D)
    if col_widths:
        for row in table.rows:
            for ci, w in enumerate(col_widths):
                row.cells[ci].width = Cm(w)
    return table


def d_bullet(doc, text):
    doc.add_paragraph(text, style="List Bullet")


def d_para(doc, text):
    doc.add_paragraph(text)


def d_code(doc, text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)


def d_add_req_table(doc, reqs):
    d_add_table(doc,
                ["Req ID", "Requirement", "Acceptance Criteria", "Priority"],
                reqs, col_widths=[2, 5.5, 7, 1.5])


def d_add_heading_styled(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.name = "Calibri"
        run.font.color.rgb = NAVY_D
    return h


def d_add_para_styled(doc, text, size=11, bold=False, color=None, space_after=6):
    if color is None:
        color = DARK_D
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    d_set_font(run, size=size, bold=bold, color=color)
    return p


def d_add_bullet_styled(doc, text, size=10):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text)
    d_set_font(run, size=size, color=DARK_D)
    return p


def init_doc(margins_cm=2.5):
    """Create a new document with standard styling."""
    doc = Document()
    for section in doc.sections:
        section.left_margin = Cm(margins_cm)
        section.right_margin = Cm(margins_cm)
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
    doc.styles["Normal"].font.name = FONT_NAME_D
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.styles["List Bullet"].font.name = FONT_NAME_D
    doc.styles["List Bullet"].font.size = Pt(10.5)
    d_style_heading(doc, 1)
    d_style_heading(doc, 2)
    d_style_heading(doc, 3)
    return doc


def add_title_page(doc, subtitle, tagline, sub_tagline):
    for _ in range(6):
        doc.add_paragraph("")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, "GoalCert", 30, bold=True, color=NAVY_D)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, "NextXR Digital Twin Platform", 20, bold=True, color=ACCENT_D)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, subtitle, 16, color=SUBTITLE_D)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, tagline, 12, color=SUBTITLE_D)
    for _ in range(4):
        doc.add_paragraph("")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, sub_tagline, 10, color=ACCENT_D)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d_title_run(p, "Commercial in Confidence   \u00b7   \u00a9 2026 GoalCert. All rights reserved.",
                9, color=SUBTITLE_D)
    doc.add_page_break()


def add_doc_control(doc, title, doc_type, version_desc):
    doc.add_heading("Document Control", level=1)
    d_add_table(doc, ["Field", "Value"], [
        ["Document Title", f"NextXR Digital Twin Platform \u2014 {title}"],
        ["Document Type", doc_type],
        ["Project", "NextXR \u2014 Ontology-Driven Industrial Digital Twin Platform"],
        ["Version", "1.0"],
        ["Status", "Draft for Review"],
        ["Prepared By", "GoalCert \u2014 Product Engineering"],
        ["Date", datetime.now().strftime("%d %B %Y")],
        ["Classification", "Commercial in Confidence"],
    ], col_widths=[5, 12])

    doc.add_heading("Version History", level=2)
    d_add_table(doc, ["Version", "Date Created", "Description", "Prepared By", "Approved By", "Date Approved"], [
        ["1.0", datetime.now().strftime("%d %B %Y"), version_desc, "GoalCert Eng", "\u2014", "\u2014"],
    ], col_widths=[1.5, 2.5, 6, 2.5, 2, 2.5])

    doc.add_heading("Reference Documents", level=2)
    d_add_table(doc, ["Ref", "Document"], [
        ["R1", "NextXR Digital Twin Platform \u2014 Functional Requirements Document (FRD)"],
        ["R2", "NextXR Digital Twin Platform \u2014 High-Level Design (HLD)"],
        ["R3", "NextXR Digital Twin Platform \u2014 Low-Level Design (LLD)"],
        ["R4", "NextXR Digital Twin Platform \u2014 Data Requirements Document (DRD)"],
        ["R5", "NextXR Digital Twin Platform \u2014 AWS Architecture Document"],
        ["R6", "AWS_DEPLOYMENT.md \u2014 the authoritative deployment runbook (section numbers are stable)"],
        ["R7", "GoalCert Hub \u2014 Module Federation Architecture"],
        ["R8", "Enterprise Solar Digital Twin Specification (\u00a72.1\u2013\u00a75.3)"],
    ], col_widths=[2, 15])


def add_toc_placeholder(doc):
    doc.add_heading("Table of Contents", level=1)
    d_para(doc, "The contents below are a live Word field. On open, Word refreshes the page numbers "
                "automatically; you can also right-click \u2192 \u201cUpdate Field\u201d.")
    d_para(doc, "Table of contents \u2014 update in Microsoft Word (F9) to populate page numbers.")
    doc.add_page_break()


def add_diagram(doc, png_path, caption, width_inches=6.5):
    """Add a diagram image to the document with a caption."""
    if os.path.exists(png_path):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        run.add_picture(png_path, width=Inches(width_inches))
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cap.add_run(caption)
        r.font.name = FONT_NAME_D
        r.font.size = Pt(9)
        r.font.italic = True
        r.font.color.rgb = SUBTITLE_D
        doc.add_paragraph("")


# ============================================================================
# SHARED CONTENT TABLES (single source of truth across documents)
# ============================================================================

MACHINE_TWIN_DOMAINS = [
    ["turbine-engine", "Gas Turbine Engine", "turbine", "throttle",
     "Compressor, Combustor, Turbine, Rotor & Bearings, Lubrication"],
    ["edm-machine", "Wire EDM Machine", "edm", "intensity",
     "Wire feed, Dielectric, Servo axes, Generator"],
    ["railway-metro", "Metro Rail Network", "railway", "service_level",
     "Track circuits, Third rail, Signalling, Stations, Depot"],
    ["railway-trainset", "Rolling Stock (Train Set)", "railway", "throttle",
     "Traction, Bogies, Doors, HVAC, Brakes"],
    ["tram-network", "Tram / Light-Rail Network", "fleet", "service_level",
     "Vehicles, Depot, Line sections, Substations"],
    ["hospital-campus", "Hospital Campus", "hospital", "patient_load",
     "Beds, Operating theatres, Medical gas, Cold chain, Infection control"],
    ["ev-charging-network", "EV Charging Network", "ev", "demand_level",
     "Chargers, Grid connection, Load balancing, V2G"],
    ["ev-battery-pack", "EV Battery Pack", "ev", "c_rate",
     "Cells, Modules, Thermal management, BMS"],
    ["defence-base", "Military Base (C4ISR)", "defence", "readiness",
     "Perimeter, Power, Ammunition, Aircraft, Comms"],
    ["defence-warship", "Warship", "defence", "speed_demand",
     "Propulsion, Damage control, Sensors, Weapons, Power"],
    ["solar-pv-array", "Enterprise Solar PV Array", "solar", "curtailment",
     "PV Modules (soiling), Strings & Combiners, Cell degradation, Inverter, Sensors"],
]

TAXONOMY_CATEGORIES = [
    ["PhysicalAsset", "Fixed, durable material things: equipment and sensors.",
     "AirHandler, PVArray, Inverter, Chiller, Transformer"],
    ["MobileAsset", "Material things whose normal operation involves moving.",
     "RollingStock, Tram, Vehicle, Aircraft, Vessel"],
    ["Actor", "Humans, teams, or organisational units that take action.",
     "Technician, ClinicalTeam, OperationsUnit"],
    ["Location", "Bounded places: sites and spaces.",
     "Site, Space, Zone, Ward, Platform"],
    ["Process", "Activities that unfold over time.",
     "MaintenanceTask, Surgery, ChargingSession, Mission"],
    ["Observation", "Timestamped measurements of observable properties.",
     "TemperatureObservation, IrradianceObservation"],
    ["Finding", "Atomic anomaly signals from behaviour models.",
     "Finding (severity, behaviourId, evidence)"],
    ["Incident", "Correlated groups of findings \u2014 the unit the operator acts on.",
     "Incident (rolls up related findings)"],
    ["Document", "Copyable information artefacts, including diagnoses and recommendations.",
     "Diagnosis, WorkOrder, Procedure, IncidentReport"],
    ["Capability", "The platform's self-model: bundles, adapters, models, ports, failure modes.",
     "CapabilityBundle, BehaviourModel, FailureMode, Adapter"],
]

COPILOT_AGENTS = [
    ["build-twin/message", "Build-a-Twin conversation", "Turns a free-text conversation into a twin specification", "Sonnet 5, thinking off"],
    ["build-twin/spec", "Vision to twin spec", "Photo or floor plan \u2192 structured TwinSpec (assets, relationships)", "Sonnet 5, vision"],
    ["narrate", "Sensor narration", "One-paragraph plain-English reading of the live sensor frame", "Sonnet 5, thinking off"],
    ["asset", "Asset status", "Explains one asset's condition from its live properties", "Sonnet 5, thinking off"],
    ["predict-alert", "Predictive alert", "Turns an RUL/health forecast into an actionable warning", "Sonnet 5, thinking off"],
    ["diagnosis", "Diagnosis agent", "Root-cause reasoning over live diagnostics and findings", "Sonnet 5, adaptive thinking"],
    ["analysis", "Analysis agent", "Combines diagnostics and prediction into an engineering assessment", "Sonnet 5, adaptive thinking"],
    ["cascade", "Cascade analysis", "Projects how a fault propagates through coupled subsystems", "Sonnet 5, adaptive thinking"],
    ["work-order", "Work order generation", "Produces a structured maintenance work order from a diagnosis", "Sonnet 5, adaptive thinking"],
    ["procurement", "Parts procurement", "Derives the parts list and lead times for a work order", "Sonnet 5, adaptive thinking"],
    ["incident-report", "Incident report", "Formal incident narrative from findings and diagnostics", "Sonnet 5, adaptive thinking"],
    ["procedure", "Repair procedure", "Step-by-step repair procedure for a named fault", "Sonnet 5, adaptive thinking"],
    ["troubleshoot", "Troubleshoot chat", "Multi-turn mechanic chat grounded in live diagnostics", "Sonnet 5, thinking off"],
    ["dashboard-chat", "Dashboard chat", "Multi-turn operator chat grounded in a twin snapshot", "Sonnet 5, thinking off"],
]

TECH_STACK = [
    ["Application Framework", "FastAPI (Python 3.12) with Pydantic models; Uvicorn ASGI server on :8080"],
    ["Ontology / Semantics", "OWL 2 + SHACL in Turtle (rdflib, pySHACL); 4-layer ontology with a closed 10-category taxonomy"],
    ["Graph Database", "Neo4j 5 (official Python driver); one uniqueness constraint and updatedAt index per taxonomy category"],
    ["Relational Store", "PostgreSQL 16 (psycopg2 pool) with a SQLite fallback for offline development; 8 platform tables"],
    ["Telemetry Historian", "TimescaleDB extension on the same PostgreSQL instance \u2014 hypertable, 1m/1h/1d continuous aggregates, compression, retention"],
    ["Event Bus", "Redis 7 Streams, one stream per tenant (nxr:events:<tenant_id>), with an in-memory fallback"],
    ["Blob Store", "AWS S3 (boto3) or any S3-compatible endpoint (MinIO); local filesystem fallback"],
    ["Field Protocols", "Modbus TCP/RTU (pymodbus), OPC-UA (asyncua), MQTT + Sparkplug B (paho-mqtt)"],
    ["AI / LLM", "Anthropic Claude (Sonnet 5 default, Opus configurable) via the embedded copilot layer, with deterministic stubs when keyless"],
    ["Agent Runtime", "In-house LangGraph-compatible StateGraph executor with a PostgreSQL-backed CheckpointSaver"],
    ["3-D Reconstruction", "TRELLIS on RunPod serverless GPU (CUDA 12.1, 24 GB VRAM); 2d-to-3d floor-plan parser; IFC parser"],
    ["Frontend", "React 18 + Vite, three.js / react-three-fiber, Module Federation remote for the GoalCert Hub"],
    ["Containerisation", "Multi-stage Docker (node:20-slim build \u2192 python:3.12-slim runtime), non-root uid/gid 10001"],
    ["Testing", "pytest \u2014 171 tests across De Soto physics, ingest/historian, point maps, solar specification and tenant isolation"],
]


# ============================================================================
# BUILD HLD
# ============================================================================

def build_hld():
    doc = init_doc()
    add_title_page(doc, "High-Level Design (HLD)",
                   "Architecture \u00b7 Ontology \u00b7 Domain packs \u00b7 Tech stack \u00b7 NFRs",
                   "FastAPI \u00b7 Neo4j \u00b7 PostgreSQL/TimescaleDB \u00b7 Redis \u00b7 S3 \u00b7 Anthropic Claude \u00b7 Docker")
    add_doc_control(doc, "High-Level Design (HLD)", "High-Level Design (HLD)",
                    "Initial issue covering full platform architecture, the four-layer ontology, "
                    "11 machine-twin domains, module map, tech stack, integrations and NFRs.")
    add_toc_placeholder(doc)

    # 1. Introduction
    doc.add_heading("1. Introduction", level=1)
    d_para(doc,
           "This High-Level Design describes the architecture of NextXR \u2014 an ontology-driven industrial "
           "digital-twin platform built by GoalCert. NextXR lets an organisation stand up a live, "
           "semantically-governed twin of a physical facility or machine, drive it from real field telemetry, "
           "reason about its condition with physics and statistics, visualise it in 3-D, and act on it through "
           "an embedded AI copilot.")
    doc.add_heading("1.1 Purpose", level=2)
    d_para(doc,
           "This document provides the architectural blueprint for the NextXR platform. It covers the system "
           "architecture, the four-layer ontology and its closed taxonomy, module decomposition, the domain-pack "
           "extension model, the technology stack, integration points and non-functional requirements. "
           "It is the contract between the FRD (R1) and the LLD (R3).")
    doc.add_heading("1.2 Scope", level=2)
    d_para(doc,
           "The scope covers the NextXR backend platform \u2014 the FastAPI application, its ontology and graph "
           "layer, the machine-twin physics runtime, the telemetry historian, the field-protocol connectors, "
           "the embedded copilot, the 3-D generation platform \u2014 together with the React front end that ships "
           "inside the same container and the Docker/ECS deployment architecture. The GoalCert Hub that "
           "federates the UI is referenced but documented separately (R7).")
    doc.add_heading("1.3 Audience", level=2)
    d_para(doc,
           "Engineering leads, solution architects, DevOps engineers, ontology engineers and stakeholders "
           "reviewing the system design. Familiarity with FastAPI, async Python, property graphs, OWL/SHACL "
           "and industrial control protocols is assumed.")

    # 2. System Overview
    doc.add_heading("2. System Overview", level=1)
    d_para(doc,
           "NextXR is a multi-tenant digital-twin platform. A \u201ctwin\u201d is one isolated instance, keyed by "
           "tenant_id, whose entities live in a Neo4j property graph shaped by the NextXR ontology. Around "
           "that graph the platform runs four things that turn it from a data model into a twin:")
    d_bullet(doc, "A live physics runtime \u2014 each machine-twin domain ships a forward model that is integrated "
                  "at 1 Hz. Wear accumulates, heat soaks, and an injected fault persists, so the twin is a "
                  "stateful simulation rather than a cache of the last reading.")
    d_bullet(doc, "A three-tier behaviour registry \u2014 48 behaviours across Tier A (physics residuals), "
                  "Tier B (statistical baselines) and Tier C (deterministic rules). Each consumes telemetry "
                  "samples and emits Findings; the registry itself is domain-agnostic and never touches Neo4j.")
    d_bullet(doc, "A telemetry historian \u2014 TimescaleDB on the same PostgreSQL instance, holding raw "
                  "measurements plus incrementally-maintained 1-minute, 1-hour and 1-day rollups, so a "
                  "90-day trend reads pre-computed rows instead of scanning millions.")
    d_bullet(doc, "An embedded AI copilot \u2014 14 Claude agents that run in-process against the same live state "
                  "the 3-D scene renders, producing diagnoses, work orders, procurement lists, incident "
                  "reports, repair procedures and operator chat.")
    d_para(doc,
           "Eleven machine-twin domains ship in the box (turbine, wire EDM, metro rail, rolling stock, tram "
           "network, hospital campus, EV charging network, EV battery pack, military base, warship and "
           "enterprise solar PV). Four further packs (BIM, CFP, HVAC, datacenter) are ontology-and-behaviour "
           "packs consumed by the plan\u2192 3-D and simulated-feed paths.")
    d_para(doc,
           "Telemetry arrives three ways: HTTP ingest authenticated by a per-device token, field-protocol "
           "connectors polling Modbus / OPC-UA / MQTT equipment, or a deterministic simulated feed used for "
           "demonstrations and tests. All three converge on the same pipeline, so a rule written for the "
           "simulator fires unchanged against a real inverter.")

    # 3. Architecture Overview
    doc.add_heading("3. Architecture Overview", level=1)
    doc.add_heading("3.1 Architecture Principles", level=2)
    d_bullet(doc, "Ontology first \u2014 the data model is OWL + SHACL, not an ORM. Four layers: imported upper "
                  "ontologies (BFO, SOSA), the NextXR core, the closed governance taxonomy, and domain packs. "
                  "A pack extends within the taxonomy and can never introduce an eleventh category; that rule "
                  "is machine-checked by nxr:TaxonomyClosureShape against the ontology itself.")
    d_bullet(doc, "Validate then commit \u2014 every graph mutation goes through the Graph Writer, which SHACL-"
                  "validates the payload, commits to Neo4j, appends a change-log event and publishes a bus "
                  "event. There is no back door: twin seeding, agent authoring and the write API all use it, "
                  "so a seeded twin honours every platform guarantee the moment it is born.")
    d_bullet(doc, "Multi-tenancy enforced centrally \u2014 tenant scope is a FastAPI application-level dependency "
                  "(enforce_tenant_scope), not a per-route check, so it covers the tenant wherever it appears: "
                  "query string, path parameter or request body. It cannot be forgotten by a route added later.")
    d_bullet(doc, "One owner per twin \u2014 live physics is a stateful integrator, so exactly one task holds a "
                  "short Redis lease per tenant. The owner ticks, evaluates behaviours and persists findings "
                  "once; every other task serves the published state and forwards control actions through a "
                  "per-tenant command queue. Ownership is per tenant, so load still spreads across the fleet.")
    d_bullet(doc, "Stateless tasks, shared stores \u2014 records in RDS, blobs in S3, live events in ElastiCache, "
                  "graph in Neo4j. Nothing durable is left on the container, which is what makes desired "
                  "count 2+ and rolling deploys safe.")
    d_bullet(doc, "Loud failure over silent degradation \u2014 each shared store has a task-local fallback that is "
                  "correct at one task and quietly wrong at two. Four required-flags (NXR_REQUIRE_DB / _S3 / "
                  "_REDIS / _TIMESCALE) turn that misconfiguration into a failed boot rather than a fleet that "
                  "serves half the twins.")
    d_bullet(doc, "Degrade, never crash, on optional dependencies \u2014 with no Anthropic key every copilot agent "
                  "falls back to a deterministic stub and reports ai.backend = \"stub\", so the product keeps "
                  "working keyless and a stub answer is never presented as real reasoning.")

    doc.add_heading("3.2 High-Level Architecture Diagram", level=2)
    d_para(doc, "The diagram below illustrates the infrastructure architecture, showing all AWS components "
                "and their relationships:")
    add_diagram(doc, PNG_INFRA, "Figure 1: NextXR AWS Infrastructure Architecture")

    d_para(doc, "The system consists of four layers:")
    d_bullet(doc, "API Layer \u2014 a single FastAPI application on port 8080 with CORS and API-key "
                  "authentication middleware and a global tenant-scope dependency. 136 endpoints are grouped "
                  "into 12 routers plus the mounted 3-D sub-application.")
    d_bullet(doc, "Service Layer \u2014 MachineEngine (1 Hz physics), TwinCoordinator (Redis ownership leases), "
                  "BehaviorRegistry (48 behaviours in 3 tiers), DynamicsEngine (coupled topology simulation), "
                  "GraphWriter, ChangeLog, Historian, ConnectorManager, IngestPipeline, EventBus, Storage, "
                  "CapabilityBundle registry and the copilot agent layer.")
    d_bullet(doc, "Data Layer \u2014 Neo4j (the ontology graph), PostgreSQL 16 with TimescaleDB (8 platform "
                  "tables plus the measurements hypertable and its three rollups), Redis 7 (event bus and "
                  "ownership leases) and S3 (generated GLBs and 3-D job artifacts).")
    d_bullet(doc, "External Services \u2014 Anthropic Claude (Sonnet 5), RunPod serverless GPU running TRELLIS "
                  "for photo-to-3-D reconstruction, and customer field equipment reached over Modbus, "
                  "OPC-UA and MQTT.")

    doc.add_heading("3.3 Component Architecture", level=2)
    d_para(doc, "The component architecture shows the layered structure of the NextXR application:")
    add_diagram(doc, PNG_COMPONENT, "Figure 2: NextXR Component Architecture")

    doc.add_heading("3.4 Service Decomposition", level=2)
    d_para(doc,
           "NextXR is a single-process modular monolith. server/main.py assembles CORS and authentication "
           "middleware, the global tenant-scope dependency, a Neo4j-down exception handler and twelve routers "
           "into one FastAPI instance, then mounts the 3-D generation platform as a sub-application at "
           "/api/v1/threed and serves the built React SPA from / with a catch-all fallback for client routing.")
    d_para(doc,
           "The deployment unit is a single Docker container. A multi-stage build compiles the front end with "
           "node:20-slim and runs it from python:3.12-slim as non-root uid/gid 10001, exposing :8080 with a "
           "container health check on GET /api/v1/health. The same image serves the API and the UI, which is "
           "what makes the deployment a genuine one-URL deploy with no separate static hosting.")

    # 4. Module Map
    doc.add_heading("4. Module Map", level=1)
    d_para(doc, "The contract between the FRD (R1) and this design. Each functional module maps to route and "
                "service packages in the codebase.")

    doc.add_heading("4.1 Ontology Module", level=2)
    d_para(doc, "The ontology is four layers of Turtle under platform/ and packs/, loaded by rdflib and "
                "enforced with pySHACL:")
    d_add_table(doc, ["Layer", "Files", "Content"], [
        ["Layer 1 \u2014 Imported upper ontologies", "imports/bfo.ttl, imports/sosa.ttl",
         "Basic Formal Ontology continuant/occurrent distinctions and the SOSA sensor/observation vocabulary"],
        ["Layer 2 \u2014 NextXR core", "platform/nxr-classes.ttl, nxr-properties.ttl, nxr-units.ttl",
         "The core entity classes, observable and object properties, and the unit vocabulary"],
        ["Layer 3 \u2014 Governance", "platform/nxr-taxonomy.ttl, nxr-governance.ttl, nxr-base-shape.ttl, nxr-shapes.ttl",
         "The closed ten-category taxonomy, the closure-enforcement shape, and the base/entity SHACL shapes"],
        ["Layer 4 \u2014 Domain packs", "packs/<domain>/<domain>-classes.ttl + -shapes.ttl",
         "Per-domain OWL classes, observable properties, failure modes and SHACL node/property shapes"],
    ], col_widths=[4.5, 5.5, 7])
    d_para(doc, "The ten categories are fixed at platform version v3. Every entity class must declare exactly "
                "one via nxr:taxonomyCategory; structural classes (StateMachine, State, Transition, "
                "QuantitativeResult) are marked nxr:isStructural and are exempt.")
    d_add_table(doc, ["Category", "Definition", "Representative classes"], TAXONOMY_CATEGORIES,
                col_widths=[3, 7.5, 6.5])

    doc.add_heading("4.2 Domain Packs Module", level=2)
    d_para(doc, "Every domain vertical lives in one self-contained folder under packs/<domain>/, "
                "co-locating its ontology and its runtime code. A machine-twin pack ships "
                "<domain>-classes.ttl, <domain>-shapes.ttl, physics.py (component engines and the forward "
                "model), behaviors.py (the tiered registry builder), predict.py (subsystem health and RUL) "
                "and an __init__.py exposing a self-describing SPEC.")
    d_add_table(doc, ["Twin key", "Label", "Pack", "Control input", "Subsystems"],
                MACHINE_TWIN_DOMAINS, col_widths=[3.2, 3.5, 1.8, 2.3, 6.2])
    d_para(doc, "Discovery is declarative: twins/runtime.py load_specs() imports packs.<domain> and reads its "
                "SPEC or SPECS list. Adding a pack name to that tuple makes it a live machine-twin domain "
                "\u2014 state, diagnostics, predict, network and the Build-a-Twin UI \u2014 with no other wiring. "
                "Ontology files are registered in tools/ontology_graph.py and loaded by the SHACL gate; "
                "seeding templates live in twins/service.py; relationship CURIE prefixes are registered in "
                "graph/writer.py.")
    d_para(doc, "Four further packs are ontology-and-behaviour packs rather than machine twins: bim "
                "(building information models for the plan\u2192 3-D path), cfp (a 1,034-line common facility "
                "platform vocabulary of 82 classes), hvac and datacenter. They are consumed by the "
                "plan\u2192 3-D and simulated-feed paths.")

    doc.add_heading("4.3 Behaviour Registry Module", level=2)
    d_para(doc, "A behaviour declares three things \u2014 watches (the signal keys it reacts to), reads (the graph "
                "inputs it consumes) and emits (the Finding it produces) \u2014 and implements one method, "
                "evaluate(sample, query) \u2192 list[Finding]. The registry routes each incoming TelemetrySample "
                "to every behaviour whose watches set contains the sample's signal and returns the findings "
                "to the caller. The registry itself never touches Neo4j and never writes.")
    d_add_table(doc, ["Tier", "Kind", "Examples"], [
        ["Tier A", "Physics \u2014 a residual between measurement and a forward model",
         "ThermalPhysicsBehavior, TurbineEGTDivergence, SymmetricOutputDrop, TractionEnergyResidual, NetworkFlowResidual"],
        ["Tier B", "Statistical / learned \u2014 a baseline, z-score or trend",
         "TemperatureZScoreBaseline, ChillerCOPBaseline, PumpVibrationBaseline, ShuntResistanceDecay, TurbineVibTrend, DelayTrend, LoadTrend"],
        ["Tier C", "Deterministic rules \u2014 thresholds and state transitions",
         "UPSOnBatteryRule, GeneratorFuelLowRule, SmokeAlarmRule, LeakDetectedRule, DoorForcedRule, MedicalGasLowPressure, RailOvervoltage"],
    ], col_widths=[1.8, 5.5, 9.7])
    d_para(doc, "48 behaviours ship across the platform and its packs. Findings are edge-latched \u2014 a "
                "behaviour emits on the transition into a condition, not on every sample \u2014 so a sustained "
                "fault produces one Finding rather than one per second.")

    doc.add_heading("4.4 Machine-Twin Runtime Module", level=2)
    d_para(doc, "twins/runtime.py holds the domain-agnostic LiveTwin and MachineEngine:")
    d_bullet(doc, "load_specs() discovers the domains from their SPECs; a missing or broken pack is skipped "
                  "so one bad module can never take the runtime down.")
    d_bullet(doc, "Each tick advances the pack's forward model, computes residuals against the clean-machine "
                  "baseline, derives per-subsystem component_health, and evaluates the pack's behaviour registry.")
    d_bullet(doc, "A _FrameQuery gives Tier-A cross-signal rules a read-only view of the co-located signals of "
                  "the latest frame, so they can read siblings without a Neo4j round trip.")
    d_bullet(doc, "Control surfaces: throttle/setpoint (the pack's declared control), fault injection, "
                  "start/stop, what-if projection and network state for the network-shaped domains.")

    doc.add_heading("4.5 Twin Coordinator Module", level=2)
    d_para(doc, "twins/coordinator.py gives each tenant's simulation exactly one owner:")
    d_bullet(doc, "The owning task holds a short Redis lease, ticks the physics, evaluates behaviours and "
                  "persists findings once, then publishes the authoritative state every second.")
    d_bullet(doc, "Follower tasks never tick and never persist \u2014 they serve the published state, so every "
                  "task reports identical values, and they forward control actions (throttle, fault injection, "
                  "start/stop) to the owner through a per-tenant command queue.")
    d_bullet(doc, "If an owner dies the lease expires after roughly eight seconds and another task adopts the "
                  "published state, continuing the simulation rather than restarting it.")
    d_bullet(doc, "GET /api/v1/health reports this task's twin_runtime.owned list; across the fleet each "
                  "tenant should appear exactly once.")

    doc.add_heading("4.6 Graph, Change Log and Event Bus Module", level=2)
    d_bullet(doc, "GraphWriter (graph/writer.py) is the only path that mutates Neo4j: validate \u2192 commit "
                  "\u2192 changelog \u2192 bus. Relationship predicates are CURIEs resolved from a registered "
                  "prefix table.")
    d_bullet(doc, "ChangeLog (changelog/service.py) appends a per-tenant hash chain: each event carries a ULID "
                  "event_id, the field-level diff, prev_event_hash and wm_hash. Changing any stored field of an "
                  "old event breaks every wm_hash after it, which is what makes the log tamper-evident. "
                  "Appends take a PostgreSQL advisory lock per tenant so a chain cannot fork under "
                  "concurrent writers.")
    d_bullet(doc, "EventBus (bus/event_bus.py) publishes one Redis Stream per tenant, nxr:events:<tenant_id>, "
                  "so a consumer reading one tenant's stream cannot see another's. A BusEvent mirrors the "
                  "change-log event, and consumer groups are created per stream for agent consumers.")

    doc.add_heading("4.7 Telemetry Module (Ingest, Connectors, Historian)", level=2)
    d_bullet(doc, "Ingest (ingest/pipeline.py, server/ingest_routes.py) accepts single and bulk telemetry "
                  "authenticated by X-Device-Token. A device credential grants exactly one verb in exactly one "
                  "tenant and can be revoked on its own \u2014 deliberately not an API key, which would let a "
                  "gateway in a plant room read every asset and bill the LLM endpoints. Only the token hash "
                  "is stored; the plaintext is returned once at creation.")
    d_bullet(doc, "Connectors (connectors/) supervise durable Modbus TCP/RTU, OPC-UA and MQTT (Sparkplug B) "
                  "workers with seven built-in point-map profiles. Ownership uses the same lease pattern as "
                  "the twin coordinator, because polling one inverter from three tasks triples device load "
                  "and puts three masters on an RS-485 segment.")
    d_bullet(doc, "Historian (historian/) writes to a TimescaleDB hypertable with a (tenant_id, asset_id, "
                  "signal, ts) primary key that makes replay idempotent, plus 1m/1h/1d continuous aggregates "
                  "that store count and sum rather than avg so a mean is correct at any resolution.")

    doc.add_heading("4.8 Copilot Module", level=2)
    d_para(doc, "copilot/ hosts fourteen Claude agents that run in-process against the machine-twin runtime "
                "\u2014 a native twin feature, not a proxy to an external agent service. Every agent takes plain "
                "dicts, never raises, and records on agent_trace() whether the call actually reached Claude.")
    d_add_table(doc, ["Endpoint", "Agent", "Responsibility", "Model policy"], COPILOT_AGENTS,
                col_widths=[3.5, 3.5, 7, 3])
    d_para(doc, "Spend is bounded: copilot/spend.py tracks running cost, tokens, per-agent ranking, "
                "prompt-cache hit rate and the share of requests answered without an API call, and enforces "
                "an optional budget cap past which agents fall back to their stubs. All of it is reported on "
                "GET /api/v1/copilot/health.")

    doc.add_heading("4.9 Agentic Build Module", level=2)
    d_para(doc, "agents/ implements a zero-dependency, LangGraph-compatible StateGraph executor with a "
                "PostgreSQL-backed CheckpointSaver, so a run interrupted for human approval on one task "
                "resumes on another. Five graphs ship:")
    d_add_table(doc, ["Graph", "Module", "Purpose"], [
        ["Twin builder", "agents/twin_graph.py", "Conversational twin authoring \u2014 free text, a photo or a floor plan becomes a seeded twin"],
        ["Bundle author", "agents/bundle_graph.py", "Authors a capability bundle (classes, entity templates, Tier-C rules) and publishes it to the registry"],
        ["Operational", "agents/operational_graph.py", "Diagnosis, analysis and cascade reasoning over a running twin"],
        ["Plugin", "agents/plugin_graph.py", "Extends a live twin with a new pack or adapter"],
        ["Accelerator", "agents/accelerator_graph.py", "Guided vertical accelerators that assemble a domain twin from templates"],
    ], col_widths=[3, 4.5, 9.5])
    d_para(doc, "The Capability Bundle registry (agents/registry.py) unifies built-in bundles (the shipped "
                "HVAC pack) with bundles published by the Bundle Author, persisted to the shared relational "
                "store \u2014 so on PostgreSQL a bundle published by one task is immediately visible to the rest.")

    doc.add_heading("4.10 3-D and Visualisation Module", level=2)
    d_bullet(doc, "server/threed_platform is the object-photo \u2192 GLB pipeline, mounted in-process at "
                  "/api/v1/threed. Reconstruction is offloaded to a RunPod serverless GPU endpoint running "
                  "TRELLIS; if RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID are unset the stage stubs.")
    d_bullet(doc, "The 2d-to-3d parser turns a floor plan into a BIM scene graph; agents/ifc_parser.py "
                  "ingests IFC models. Resulting scenes are cached in the scene_cache table, not on task "
                  "disk, which is what keeps the 3-D viewer correct at desired count above one.")
    d_bullet(doc, "The React front end renders the scene with three.js and overlays live signal values, "
                  "health status and click-to-inspect equipment panels; domain-specific views ship for "
                  "hospital, railway, EV, defence and solar.")

    doc.add_heading("4.11 Auth and Tenancy Module", level=2)
    d_para(doc, "Authentication is API-key based (server/auth.py), with three roles \u2014 admin (all tenants), "
                "write (read + write within their tenant) and read (read-only within their tenant). Keys are "
                "configured as a JSON array in NXR_API_KEYS; the built-in demo keys load only when that "
                "variable is unset.")
    d_para(doc, "Tenant authorisation is separate and central (server/tenancy.py). It is an application-level "
                "FastAPI dependency rather than middleware, because middleware runs before routing where "
                "request.path_params is still empty \u2014 the exact blind spot that let a key scoped to one "
                "tenant reach another tenant's twin through a path parameter or a request body. Four scoping "
                "forms are supported, most specific first:")
    d_add_table(doc, ["Form", "Meaning", "Notes"], [
        ["{\"tenant\": \"acme\"}", "Exactly one tenant", "The narrowest and preferred form"],
        ["{\"tenant_prefix\": \"acme-\"}", "Every tenant id under a prefix",
         "The only form that can create twins \u2014 the new id must land inside the creator's own scope"],
        ["{\"tenants\": [\"acme\", \"acme-2\"]}", "An explicit set",
         "Expresses \u201cthis customer owns 50 twins\u201d without granting *"],
        ["{\"tenant\": \"*\"}", "Admin \u2014 all tenants",
         "Handing one to a customer hands them every other customer's data"],
    ], col_widths=[4.5, 4.5, 8])

    # 5. Technology Stack
    doc.add_heading("5. Technology Stack", level=1)
    d_add_table(doc, ["Layer", "Technology"], TECH_STACK, col_widths=[3.5, 13.5])

    # 6. Integration Points
    doc.add_heading("6. Integration Points", level=1)
    doc.add_heading("6.1 Neo4j (the ontology graph)", level=2)
    d_bullet(doc, "Connection is a shared singleton driver (graph/connection.py) reading NEO4J_URI, "
                  "NEO4J_USER and NEO4J_PASSWORD; Neo4j Aura over neo4j+s:// is the recommended production form.")
    d_bullet(doc, "graph/schema.py parses the ontology, discovers the taxonomy categories and applies one "
                  "uniqueness constraint on (tenantId, id) and one updatedAt index per category, plus the "
                  "ChangeLog label's constraints \u2014 so the graph schema always matches the ontology.")
    d_bullet(doc, "Neo4j being unreachable is handled once, globally: ServiceUnavailable, SessionExpired and "
                  "AuthError are converted to a 503 with operator guidance, so every DB-backed route degrades "
                  "the same friendly way instead of returning a raw 500.")
    doc.add_heading("6.2 Anthropic Claude API", level=2)
    d_bullet(doc, "Claude Sonnet 5 is the default model (NXR_CLAUDE_MODEL overrides it; Opus is a supported "
                  "alternative), with adaptive thinking on the heavy reasoning agents and thinking explicitly "
                  "disabled on the short latency-sensitive ones.")
    d_bullet(doc, "Prompt caching is used on the system prompts; the cache hit rate and the share of requests "
                  "answered with no API call at all are reported by copilot/spend.py.")
    d_bullet(doc, "Without ANTHROPIC_API_KEY every agent degrades to a deterministic stub and the response's "
                  "ai.backend field reports \"stub\". Nothing breaks and nothing lies about its provenance.")
    doc.add_heading("6.3 GoalCert Hub (Module Federation)", level=2)
    d_bullet(doc, "The React front end is built as a Module Federation remote and mounted by the GoalCert Hub; "
                  "server/hub_routes.py exposes the hub-facing prediction and AR-overlay surface.")
    d_bullet(doc, "The hub authenticates with an X-API-Key that must be one of the configured NXR_API_KEYS.")
    d_bullet(doc, "CORS defaults to * for local development and the federated hub; NXR_CORS_ORIGINS locks it "
                  "to an explicit allow-list in production.")
    doc.add_heading("6.4 Field protocols", level=2)
    d_bullet(doc, "Modbus TCP and RTU via pymodbus, OPC-UA via asyncua, MQTT with Sparkplug B via paho-mqtt "
                  "and protobuf. Protocol modules are imported lazily so a missing optional dependency "
                  "disables one protocol rather than the service.")
    d_bullet(doc, "Seven built-in point-map profiles (sunspec_inverter_3ph, string_combiner, revenue_meter, "
                  "ct_clamp_panel, weather_station, mqtt_gateway, opcua_inverter) turn a device model into a "
                  "signal map without hand-writing registers; POST /profiles/{key}/build previews the result.")
    d_bullet(doc, "SunSpec discovery (POST /connectors/discover/sunspec) and OPC-UA address-space browsing "
                  "(POST /connectors/{id}/browse) support commissioning.")
    doc.add_heading("6.5 RunPod (GPU reconstruction)", level=2)
    d_bullet(doc, "apps/trellis-worker is a RunPod serverless GPU container (CUDA 12.1, 24 GB VRAM) deployed "
                  "on RunPod rather than AWS; the platform is a thin HTTPS client.")
    d_bullet(doc, "Finished artifacts are published to the blob store as each stage completes; "
                  "GET /api/jobs/{id}/result redirects to a presigned S3 URL so a 20 MB GLB streams from S3 "
                  "instead of occupying an API worker.")
    doc.add_heading("6.6 Storage (S3 / local)", level=2)
    d_bullet(doc, "One blob layer (storage/core.py) with two backends behind the same interface: S3 (or any "
                  "S3-compatible endpoint such as MinIO) when NXR_S3_BUCKET is set, otherwise the local "
                  "filesystem under NXR_DATA_DIR/blobs.")
    d_bullet(doc, "Blobs are addressed by key, never by path: <prefix>/threed/jobs/<job_id>/input/<file> and "
                  "<prefix>/threed/jobs/<job_id>/artifacts/<stage>/model.glb.")
    d_bullet(doc, "The health endpoint exercises the write path \u2014 put, read back, delete a probe object "
                  "\u2014 because a bucket you can list but not write to is the usual IAM mistake and reads "
                  "fine right up until the first upload.")

    # 7. Non-Functional Requirements
    doc.add_heading("7. Non-Functional Requirements", level=1)
    doc.add_heading("7.1 Performance", level=2)
    d_bullet(doc, "The physics runtime ticks at 1 Hz per owned tenant and publishes authoritative state every "
                  "second; follower tasks serve that state with no simulation cost.")
    d_bullet(doc, "Historian reads are served from continuous aggregates, so a 90-day chart reads roughly two "
                  "thousand pre-computed rows rather than aggregating millions of raw samples.")
    d_bullet(doc, "Compression gives 10\u201320x on telemetry \u2014 at 5,000 signals at 1 Hz that is the difference "
                  "between about 4 GB/day and about 50 GB/day of RDS storage.")
    d_bullet(doc, "Connection pooling is per task (NXR_DB_POOL_MIN/MAX, default 1\u201310); RDS Proxy multiplexes "
                  "the fleet's connections and survives failover without every task reconnecting in lockstep.")
    d_bullet(doc, "The DDL cache in db/schema.py means re-constructing a store object costs nothing, because "
                  "ChangeLog() is constructed inside request handlers and a DDL round trip per construction "
                  "would be a real cost against RDS.")
    doc.add_heading("7.2 Security", level=2)
    d_bullet(doc, "API keys are mandatory in production. With NXR_API_KEYS unset the service is open \u2014 every "
                  "/api call, including the LLM-billing copilot endpoints, is served without a key. "
                  "NXR_REQUIRE_AUTH=1 rejects keyless calls even before keys load.")
    d_bullet(doc, "Tenant isolation is enforced by a single application-level dependency covering path, query "
                  "and body, and is backed by a dedicated test module (tests/test_tenant_isolation.py, "
                  "25 tests).")
    d_bullet(doc, "Device tokens are stored only as hashes, are looked up by a unique hash index, carry an "
                  "optional expiry and an asset prefix, and can be revoked individually.")
    d_bullet(doc, "Connector credentials live inside the config JSON column and are allow-listed on the way "
                  "out by ConnectorConfig.redacted(), so a field added later cannot leak a customer's PLC "
                  "password by omission.")
    d_bullet(doc, "TLS in transit is enforced for RDS (sslmode=require, without which psycopg2 will quietly "
                  "accept an unencrypted connection) and for Neo4j Aura (neo4j+s://); S3 buckets have Block "
                  "Public Access on and are reached through the ECS task role, not access keys.")
    d_bullet(doc, "The change log is tamper-evident by construction, and appends serialise per tenant under a "
                  "PostgreSQL advisory lock so a chain cannot fork.")
    doc.add_heading("7.3 Scalability", level=2)
    d_bullet(doc, "Tasks are stateless once RDS, S3 and ElastiCache are configured, so desired count 2+ across "
                  "two availability zones and rolling deploys are safe.")
    d_bullet(doc, "Simulation load spreads across the fleet because ownership is per tenant \u2014 there is no "
                  "singleton simulation service to keep alive.")
    d_bullet(doc, "Sticky sessions are not required: agent checkpoints are in PostgreSQL, so a run interrupted "
                  "for human approval on one task resumes on another.")
    d_bullet(doc, "The known bound on write throughput is deliberate: a single tenant's change-log writes "
                  "serialise under its advisory lock. Tenants never block each other, and that is the correct "
                  "trade for a tamper-evident ledger.")
    doc.add_heading("7.4 Observability", level=2)
    d_bullet(doc, "GET /api/v1/health always returns 200 and carries component-level truth: neo4j, database, "
                  "blobs, bus, historian and twin_runtime.owned, with an overall healthy/degraded status. "
                  "It never 503s by design, so a database blip cannot roll the fleet \u2014 which is why the "
                  "deployment adds a CloudWatch alarm on the degraded status.")
    d_bullet(doc, "The bus and historian both report scale_safe, the one field an operator can alarm on before "
                  "scaling out: an in-memory bus and a non-hypertable historian both report working while "
                  "being wrong at two tasks.")
    d_bullet(doc, "Four startup posture lines \u2014 [auth], [db], [blobs], [bus] \u2014 state the configuration in "
                  "plain words on stdout and reach CloudWatch, and are the fastest way to catch a bad deploy.")
    d_bullet(doc, "GET /api/v1/stats, /api/v1/bus/stats and /api/v1/bus/events expose graph, event and "
                  "throughput counters for dashboards.")

    # 8. Deployment Architecture
    doc.add_heading("8. Deployment Architecture", level=1)
    d_para(doc, "Locally, docker-compose brings up the same shared stores the deploy uses so the production "
                "posture can be rehearsed before it is trusted:")
    d_add_table(doc, ["Service", "Image", "Port", "Role"], [
        ["neo4j", "neo4j:5-community", "7474 / 7687", "The ontology graph"],
        ["postgres", "timescale/timescaledb:2.17.2-pg16", "5432", "Records and the telemetry historian"],
        ["redis", "redis:7-alpine", "6379", "Event bus and twin ownership leases"],
        ["minio (+ minio-init)", "minio/minio, minio/mc", "9000", "S3-compatible blob store for exercising the real code path"],
        ["nextxr-twin", "Custom multi-stage Dockerfile", "8080", "API + built React UI, health check GET /api/v1/health"],
    ], col_widths=[3, 5, 2.5, 6.5])
    d_para(doc,
           "On AWS the same image runs on ECS Fargate behind an ALB at desired count 2+ across two "
           "availability zones, with RDS PostgreSQL 16 (Multi-AZ, optionally through RDS Proxy), an S3 bucket, "
           "ElastiCache Redis 7 and a managed Neo4j Aura instance. Secrets are injected from Secrets Manager "
           "through the task definition's secrets block; the task role carries the S3 permissions. "
           "See R5 and R6 for the full runbook.")
    d_bullet(doc, "Provisioning: python -m db.schema creates every relational table; "
                  "python -m tools.historian_provision --extension enables and configures TimescaleDB; "
                  "python -m graph.schema applies the Neo4j constraints and indexes. All three are idempotent.")
    d_bullet(doc, "Migration: python -m db.migrate --dry-run then python -m db.migrate moves an existing "
                  "SQLite deployment into PostgreSQL before traffic is pointed at the new stack.")
    d_bullet(doc, "Guards: NXR_REQUIRE_DB, NXR_REQUIRE_S3, NXR_REQUIRE_REDIS and NXR_REQUIRE_TIMESCALE turn "
                  "each per-task fallback into a loud startup failure. Set all four for any multi-task service.")
    d_para(doc, "Design details are indicative and confirmed during implementation.")

    out_path = os.path.join(TECHDOCS_DIR, "NextXR_HLD.docx")
    doc.save(out_path)
    print(f"  Saved: {out_path}")


# ============================================================================
# BUILD FRD (no diagrams needed)
# ============================================================================

def build_frd():
    doc = init_doc()
    add_title_page(doc, "Functional Requirements Document (FRD)",
                   "Modules \u00b7 Requirements \u00b7 Acceptance Criteria \u00b7 Workflows",
                   "FastAPI \u00b7 Neo4j \u00b7 PostgreSQL/TimescaleDB \u00b7 Redis \u00b7 S3 \u00b7 Anthropic Claude \u00b7 Docker")
    add_doc_control(doc, "Functional Requirements Document (FRD)", "Functional Requirements Document (FRD)",
                    "Initial issue covering all 14 functional modules, 74 requirements and 6 workflow specifications.")
    add_toc_placeholder(doc)

    # 1. Introduction
    doc.add_heading("1. Introduction", level=1)
    d_para(doc,
           "This document is the engineering-facing functional requirements specification for the NextXR "
           "Digital Twin Platform. It defines every user-facing capability across 14 functional modules, with "
           "acceptance criteria for each requirement. The HLD (R2) maps every requirement to its owning module "
           "and service layer, and the LLD (R3) provides the implementation detail.")
    doc.add_heading("1.1 Scope", level=2)
    d_para(doc,
           "NextXR is the ontology-driven industrial digital-twin product within the GoalCert ecosystem. It "
           "covers twin lifecycle management, the ontology graph and its governance, the machine-twin physics "
           "runtime, telemetry ingestion and history, field-protocol connectivity, solar PV analytics, the "
           "embedded AI copilot, agentic twin authoring, 3-D reconstruction and visualisation, the live event "
           "bus, authentication with tenant isolation, and platform health and observability.")
    doc.add_heading("1.2 Out of Scope", level=2)
    d_add_table(doc, ["Item", "Reason"], [
        ["GoalCert Hub frontend shell", "Documented separately; NextXR ships a Module Federation remote consumed by the Hub"],
        ["HiveMind multi-agent platform", "Separate product; references NextXR as a peer via the Hub"],
        ["SimCore simulation engine", "Separate platform product; documented in its own FRD/HLD"],
        ["TRELLIS GPU worker internals", "Deployed on RunPod outside AWS; NextXR integrates as a thin HTTPS client"],
        ["Customer SCADA/BMS replacement", "NextXR reads from field equipment and historians; it does not replace control systems"],
    ], col_widths=[5, 12])

    # 2. Actors
    doc.add_heading("2. System Actors and Permissions", level=1)
    d_add_table(doc, ["Actor", "Auth Method", "Permissions"], [
        ["Admin", "X-API-Key with role=admin",
         "Full access across all tenants when scoped {\"tenant\": \"*\"}: create and delete twins, write "
         "entities, manage devices and connectors, drive the runtime, and run every copilot agent."],
        ["Write user", "X-API-Key with role=write",
         "Read and write within the tenants the key is scoped to. May create entities, drive the twin, "
         "register devices and connectors, and run copilot agents. Cannot reach other tenants."],
        ["Read user", "X-API-Key with role=read",
         "Read-only within the tenants the key is scoped to: entities, findings, change log, history, "
         "twin state and diagnostics. Cannot mutate the graph or the runtime."],
        ["Prefix-scoped tenant owner", "X-API-Key with tenant_prefix",
         "Everything a write key can do across every tenant under the prefix, and the only key form that "
         "can create a twin \u2014 the new twin's id must land inside the creator's own scope."],
        ["Ingest device", "X-Device-Token",
         "Exactly one verb (append telemetry) in exactly one tenant, optionally narrowed to an asset prefix. "
         "Revocable on its own without rotating any human credential."],
        ["Hub gateway", "X-API-Key issued to the Hub",
         "Forwards authenticated user context from the GoalCert Hub; permissions match the key's scope and role."],
    ], col_widths=[3, 3.5, 10.5])

    # 3. Functional Overview
    doc.add_heading("3. Functional Overview", level=1)
    d_para(doc, "Fourteen in-scope modules. The \u2018Owning Route\u2019 column is the contract between this FRD "
                "and the HLD/LLD.")
    d_add_table(doc, ["Module", "Requirement IDs", "Owning Route", "Description"], [
        ["Twin Lifecycle", "FR-TWIN-01..05", "server/twins_routes.py", "Templates, create, get, list, delete twins with graph seeding"],
        ["Ontology Graph", "FR-GRAPH-01..08", "server/query_api.py, write_api.py", "Entity and relationship CRUD, topology, findings, change log"],
        ["Ontology & Governance", "FR-SCHEMA-01..07", "server/schema_routes.py", "Types, classes, properties, archetypes, SHACL validation, taxonomy closure"],
        ["Machine-Twin Runtime", "FR-RUN-01..08", "server/twin_runtime_routes.py", "State, diagnostics, prediction, projection, network, control, fault injection"],
        ["Telemetry Ingestion", "FR-INGEST-01..07", "server/ingest_routes.py", "Single and bulk ingest, device registration, rotation, enable/disable, delete"],
        ["Historian", "FR-HIST-01..05", "server/historian_routes.py", "Per-entity history and latest value, twin signals, trends, coverage statistics"],
        ["Field Connectors", "FR-CONN-01..09", "server/connector_routes.py", "Protocols, profiles, connector CRUD, start/stop, health, test, discovery, browse"],
        ["Solar PV Analytics", "FR-SOLAR-01..09", "server/solar_routes.py", "De Soto model, residual, diagnosis, triggers, heatmap, strings, energy, AR field service, training"],
        ["AI Copilot", "FR-COPILOT-01..08", "server/copilot_routes.py", "14 embedded Claude agents, spend control, keyless stub fallback"],
        ["Agentic Build", "FR-AGENT-01..07", "server/agent_routes.py", "Twin builder, bundle author, operational, plugin and accelerator graphs; plan\u2192 3-D"],
        ["3-D Platform", "FR-3D-01..05", "server/threed_platform", "Reconstruction jobs, priors, artifacts, presigned results, BIM scene cache"],
        ["Event Bus", "FR-BUS-01..03", "server/query_api.py", "Per-tenant event stream, statistics, recent events, SSE"],
        ["Auth & Tenancy", "FR-AUTH-01..05", "server/auth.py, tenancy.py", "API keys, four scope forms, three roles, central tenant enforcement"],
        ["Platform Operations", "FR-OPS-01..05", "server/query_api.py, main.py", "Health, statistics, simulated feed control, posture reporting"],
    ], col_widths=[2.8, 2.4, 3.4, 8.4])

    # 4. Functional Requirements
    doc.add_heading("4. Functional Requirements", level=1)

    doc.add_heading("4.1 FR-TWIN \u2014 Twin Lifecycle", level=2)
    d_add_req_table(doc, [
        ["FR-TWIN-01", "List the available twin templates",
         "GET /api/v1/twins/templates returns every seeding template with its key, label and the domain it builds", "Must"],
        ["FR-TWIN-02", "Create a twin from a template with a tenant id, name and domain",
         "Registry row written; entities seeded through the Graph Writer so validation, change log and bus all fire; "
         "seed_asset_id recorded; only a prefix- or admin-scoped key may create", "Must"],
        ["FR-TWIN-03", "Retrieve a twin's registry record and seeded root",
         "GET /api/v1/twins/{tenant} returns tenant_id, name, domain, description, created_at and seed_asset_id; "
         "404 for an unknown tenant; 403 when out of the caller's scope", "Must"],
        ["FR-TWIN-04", "Delete a twin",
         "DELETE /api/v1/twins/{tenant} removes the registry row and the tenant's graph entities; the change log "
         "is retained as an audit record", "Must"],
        ["FR-TWIN-05", "List the machine-twin domains the runtime has discovered",
         "GET /api/v1/twins/domains returns each SPEC's key, label, class IRI, control input, signals, units, "
         "faults, sensors and subsystems; a broken pack is skipped rather than failing the request", "Must"],
    ])

    doc.add_heading("4.2 FR-GRAPH \u2014 Ontology Graph", level=2)
    d_add_req_table(doc, [
        ["FR-GRAPH-01", "List entities for a tenant with label and paging filters",
         "GET /api/v1/entities returns nodes scoped to the caller's tenant only; label, limit and offset supported", "Must"],
        ["FR-GRAPH-02", "Retrieve one entity with its properties and canonical type",
         "GET /api/v1/entities/{node_id} returns id, label, canonical class IRI, display name, status and all properties", "Must"],
        ["FR-GRAPH-03", "Create an entity from a canonical type and property bag",
         "POST /api/v1/entities SHACL-validates before committing; a violation returns 422 with the failing shape "
         "and nothing is written", "Must"],
        ["FR-GRAPH-04", "Update an entity's properties",
         "PATCH /api/v1/entities/{node_id} re-validates, commits, writes a field-level change-log diff and publishes "
         "an update event", "Must"],
        ["FR-GRAPH-05", "Delete an entity",
         "DELETE /api/v1/entities/{node_id} removes the node, records a delete event in the chain and publishes it", "Must"],
        ["FR-GRAPH-06", "Relate two entities with an ontology object property",
         "POST /api/v1/entities/{node_id}/rel accepts a CURIE predicate resolved from the registered prefix table; "
         "an unknown prefix is rejected", "Must"],
        ["FR-GRAPH-07", "Read the tenant topology and per-entity telemetry",
         "GET /api/v1/topology returns nodes and edges for the 3-D and network views; "
         "GET /api/v1/entities/{node_id}/telemetry returns the entity's live signal values", "Must"],
        ["FR-GRAPH-08", "Read findings and the tamper-evident change log",
         "GET /api/v1/findings lists open findings; GET /api/v1/changelog and /changelog/{entity_id} return the "
         "hash-chained event history with prev_event_hash and wm_hash so a verifier can walk the chain", "Must"],
    ])

    doc.add_heading("4.3 FR-SCHEMA \u2014 Ontology and Governance", level=2)
    d_add_req_table(doc, [
        ["FR-SCHEMA-01", "Report the loaded ontology version and file set",
         "GET /api/v1/schema/version returns the platform ontology version and the TTL files that were parsed", "Must"],
        ["FR-SCHEMA-02", "Enumerate entity types and taxonomy categories",
         "GET /schema/types and /schema/categories return every class and the ten closed categories", "Must"],
        ["FR-SCHEMA-03", "Describe one class: definition, properties and behaviour bindings",
         "GET /schema/class/{name}, /class/{name}/properties and /class/{name}/behavior return the class metadata, "
         "its observable and object properties, and the behaviours bound to it", "Must"],
        ["FR-SCHEMA-04", "Enumerate object-property predicates and asset archetypes",
         "GET /schema/predicates and /schema/archetypes return the relationship vocabulary and the physics "
         "archetypes an asset can be assigned", "Must"],
        ["FR-SCHEMA-05", "Enumerate asset types available for authoring",
         "GET /schema/asset-types returns the instantiable classes with their required properties", "Should"],
        ["FR-SCHEMA-06", "Validate an arbitrary payload against the SHACL shape bundle",
         "POST /schema/validate returns conforms plus the failing shape, focus node and message for each violation", "Must"],
        ["FR-SCHEMA-07", "Enforce the closed taxonomy over the ontology itself",
         "GET /schema/governance applies nxr:TaxonomyClosureShape to the T-Box; a class declaring an eleventh "
         "category, or none at all without nxr:isStructural, is reported as a violation", "Must"],
    ])

    doc.add_heading("4.4 FR-RUN \u2014 Machine-Twin Runtime", level=2)
    d_add_req_table(doc, [
        ["FR-RUN-01", "Read a twin's live state",
         "GET /api/v1/twins/{tenant}/state returns the latest frame: every signal with value and unit, the control "
         "setting, running flag, health index and active faults", "Must"],
        ["FR-RUN-02", "Read diagnostics: per-sensor status and per-subsystem health",
         "GET /twins/{tenant}/diagnostics evaluates the SPEC's checks table (limit and direction per signal) and "
         "returns component_health per declared subsystem", "Must"],
        ["FR-RUN-03", "Read a prediction: remaining useful life and degradation forecast",
         "GET /twins/{tenant}/predict returns the pack's forward prediction with per-subsystem RUL and confidence", "Must"],
        ["FR-RUN-04", "Project a what-if scenario without mutating the live twin",
         "POST /twins/{tenant}/project accepts a control profile and returns the projected trajectory; the live "
         "state is unchanged", "Must"],
        ["FR-RUN-05", "Read network state for network-shaped domains",
         "GET /twins/{tenant}/network returns per-node and per-link state for metro, tram, EV charging and "
         "defence base twins; domains without a network model return 404", "Must"],
        ["FR-RUN-06", "Start and stop the simulation",
         "POST /twins/{tenant}/running sets the run flag; a follower task forwards the command to the lease owner "
         "rather than acting locally", "Must"],
        ["FR-RUN-07", "Inject a fault or change the control input",
         "POST /twins/{tenant}/simulate accepts a fault key from the pack's FAULTS table or a control value; the "
         "fault persists in the integrator until cleared", "Must"],
        ["FR-RUN-08", "Guarantee exactly one simulation owner per tenant",
         "The owner holds a Redis lease, ticks and persists findings once; the lease expires in about 8 s and "
         "another task adopts the published state; /health reports twin_runtime.owned", "Must"],
    ])

    doc.add_heading("4.5 FR-INGEST \u2014 Telemetry Ingestion", level=2)
    d_add_req_table(doc, [
        ["FR-INGEST-01", "Accept a single telemetry sample",
         "POST /api/v1/ingest/telemetry authenticated by X-Device-Token writes one measurement with asset, signal, "
         "value, unit, quality and timestamp", "Must"],
        ["FR-INGEST-02", "Accept a bulk telemetry batch",
         "POST /ingest/telemetry/bulk accepts an array; a replayed batch collides on the historian primary key and "
         "is skipped, so an edge agent reconnecting after a network blip cannot double-count", "Must"],
        ["FR-INGEST-03", "Drive the behaviour registry from ingested telemetry",
         "With NXR_INGEST_BEHAVIOURS=1 (default) each sample is routed through the registry so findings, diagnosis "
         "and change-log entries follow real data; setting it to 0 loads history without firing rules", "Must"],
        ["FR-INGEST-04", "Register an ingest device and return its token once",
         "POST /ingest/devices returns the plaintext token exactly once and stores only the hash; the response "
         "carries device_id, tenant, asset prefix and expiry", "Must"],
        ["FR-INGEST-05", "List devices with their liveness counters",
         "GET /ingest/devices returns each device's name, asset prefix, enabled flag, last_seen_at, last_seen_ip, "
         "samples_total and rejected_total \u2014 never the token", "Must"],
        ["FR-INGEST-06", "Rotate, disable and delete a device credential",
         "POST /ingest/devices/{id}/rotate issues a new token and invalidates the old one; "
         "POST /devices/{id}/enabled toggles it; DELETE removes it. None of these rotate any human credential", "Must"],
        ["FR-INGEST-07", "Report ingest status",
         "GET /ingest/status returns accepted and rejected counts, the last sample time and whether behaviour "
         "evaluation is enabled", "Should"],
    ])

    doc.add_heading("4.6 FR-HIST \u2014 Historian", level=2)
    d_add_req_table(doc, [
        ["FR-HIST-01", "Return an entity's measurement history over a time range",
         "GET /api/v1/entities/{node_id}/history accepts from, to and resolution and serves the smallest "
         "sufficient rollup; means are computed from count and sum so re-aggregation stays correct", "Must"],
        ["FR-HIST-02", "Return an entity's latest value per signal",
         "GET /entities/{node_id}/latest returns the most recent sample for each signal with its unit, quality and "
         "timestamp", "Must"],
        ["FR-HIST-03", "Enumerate a twin's signals with last-seen times",
         "GET /twins/{tenant}/signals returns every signal the tenant has recorded and when it was last seen, "
         "backing the tag-mapping UI and the staleness check", "Must"],
        ["FR-HIST-04", "Return multi-signal trends for charting",
         "GET /twins/{tenant}/trends accepts a signal list, range and bucket, and returns aligned series drawn "
         "from the 1m, 1h or 1d aggregate", "Must"],
        ["FR-HIST-05", "Report history coverage and data quality",
         "GET /twins/{tenant}/history/stats returns sample counts, the earliest and latest timestamps, the "
         "backend in use (timescale / postgres / sqlite) and the bad-quality share", "Must"],
    ])

    doc.add_heading("4.7 FR-CONN \u2014 Field Connectors", level=2)
    d_add_req_table(doc, [
        ["FR-CONN-01", "List supported protocols and their dependency status",
         "GET /api/v1/connectors/protocols returns modbus_tcp, modbus_rtu, opcua and mqtt with the driver each "
         "needs and whether it is importable in this deployment", "Must"],
        ["FR-CONN-02", "List the built-in point-map profiles",
         "GET /connectors/profiles returns the seven profiles (sunspec_inverter_3ph, string_combiner, "
         "revenue_meter, ct_clamp_panel, weather_station, mqtt_gateway, opcua_inverter) with their signals", "Must"],
        ["FR-CONN-03", "Preview the point map a profile would build",
         "POST /connectors/profiles/{key}/build returns the generated register or node map without persisting a "
         "connector", "Must"],
        ["FR-CONN-04", "Create, read, update and delete a connector",
         "Connector configuration is durable relational state; GET/PATCH/DELETE /connectors/{id} operate within "
         "the caller's tenant scope and never return credentials", "Must"],
        ["FR-CONN-05", "Start and stop a connector worker",
         "POST /connectors/{id}/start and /stop reconcile the running worker against the stored configuration; "
         "exactly one task owns a connector via lease so a device is not polled once per task", "Must"],
        ["FR-CONN-06", "Report connector health",
         "GET /connectors/{id}/health returns connection state, last poll time, samples and errors since start, "
         "and the last error message", "Must"],
        ["FR-CONN-07", "Test a connector configuration before committing it",
         "POST /connectors/{id}/test performs a one-shot read and returns the decoded values or the protocol error", "Must"],
        ["FR-CONN-08", "Discover SunSpec devices on a Modbus endpoint",
         "POST /connectors/discover/sunspec walks the SunSpec model chain and returns the models and base "
         "registers found", "Should"],
        ["FR-CONN-09", "Browse an OPC-UA address space",
         "POST /connectors/{id}/browse returns the node hierarchy under a starting node so points can be mapped "
         "without vendor documentation", "Should"],
    ])

    doc.add_heading("4.8 FR-SOLAR \u2014 Solar PV Analytics", level=2)
    d_para(doc, "Implements the Enterprise Solar Digital Twin specification (R8). A PV array's raw output "
                "carries almost no information about its condition \u2014 400 kW from a 500 kW array is excellent "
                "at 8 a.m. and alarming at noon \u2014 so every diagnosis is a statement about the residual "
                "between measurement and the De Soto model's clean-array baseline.")
    d_add_req_table(doc, [
        ["FR-SOLAR-01", "Evaluate the De Soto five-parameter single-diode model",
         "POST /api/v1/solar/model/evaluate returns the modelled DC power, maximum power point, open-circuit "
         "voltage and short-circuit current for given irradiance and cell temperature (\u00a74.1)", "Must"],
        ["FR-SOLAR-02", "Return the modelled I-V curve for a twin",
         "GET /solar/{tenant}/model/iv-curve returns the curve at the current operating point for the array "
         "and for a reference clean module", "Must"],
        ["FR-SOLAR-03", "Return the measured-versus-modelled residual",
         "GET /solar/{tenant}/residual returns \u0394P vs model, performance ratio, fill factor, normalised shunt "
         "resistance and string current imbalance", "Must"],
        ["FR-SOLAR-04", "Diagnose the array from its residual signatures",
         "GET /solar/{tenant}/diagnosis classifies the three \u00a74.2 signatures \u2014 symmetric output drop, "
         "string voltage collapse and shunt-resistance decay \u2014 and names the physical cause", "Must"],
        ["FR-SOLAR-05", "Return machine-readable maintenance triggers",
         "GET /solar/triggers and /solar/{tenant}/triggers return each signature with its behaviour id, condition, "
         "diagnosis, recommended action and priority, so the work-order agent acts without re-deriving meaning", "Must"],
        ["FR-SOLAR-06", "Return the array heat map and per-string detail",
         "GET /solar/{tenant}/heatmap returns per-zone performance for the 3-D overlay; /strings returns per-string "
         "voltage, current, imbalance and status with a failing string flagged", "Must"],
        ["FR-SOLAR-07", "Return energy accounting and a degradation forecast",
         "GET /solar/{tenant}/energy returns generated, exported and curtailed energy with the measured summary; "
         "/forecast returns the degradation trajectory", "Must"],
        ["FR-SOLAR-08", "Serve the immersive field-service surface (\u00a75.2)",
         "GET /solar/{tenant}/field-service/{asset_id} returns the asset's live state, its fault history and the "
         "AR overlay payload for a mobile or headset client", "Must"],
        ["FR-SOLAR-09", "Serve the multiplayer training lab (\u00a75.3)",
         "POST /solar/{tenant}/training/scenario injects a scripted fault scenario for training; "
         "POST /training/reset restores the pre-scenario state", "Should"],
    ])

    doc.add_heading("4.9 FR-COPILOT \u2014 Embedded AI Copilot", level=2)
    d_add_req_table(doc, [
        ["FR-COPILOT-01", "Narrate live sensors and explain a single asset",
         "POST /api/v1/copilot/narrate and /asset return a short plain-English reading grounded in the live frame; "
         "GET /copilot/narrate/{tenant} narrates the current live state directly", "Must"],
        ["FR-COPILOT-02", "Diagnose a fault from live diagnostics and findings",
         "POST /copilot/diagnosis returns root cause, evidence and confidence, reasoning over the same state the "
         "3-D scene renders", "Must"],
        ["FR-COPILOT-03", "Produce an engineering analysis and a cascade projection",
         "POST /copilot/analysis combines diagnostics and prediction; /cascade projects how the fault propagates "
         "through coupled subsystems", "Must"],
        ["FR-COPILOT-04", "Generate a work order, its parts list and an incident report",
         "POST /copilot/work-order returns a structured order; /procurement derives parts and lead times; "
         "/incident-report returns a formal narrative from findings", "Must"],
        ["FR-COPILOT-05", "Generate a step-by-step repair procedure",
         "POST /copilot/procedure returns an ordered procedure for a named fault on a named machine, with safety "
         "preconditions", "Must"],
        ["FR-COPILOT-06", "Support multi-turn operator and mechanic chat",
         "POST /copilot/dashboard-chat and /troubleshoot accept a message history and a twin snapshot and answer "
         "grounded in it", "Must"],
        ["FR-COPILOT-07", "Turn a prediction into an actionable alert",
         "POST /copilot/predict-alert returns a warning with the driving signal and the time horizon, or null when "
         "nothing warrants an alert", "Should"],
        ["FR-COPILOT-08", "Degrade safely without an API key and bound spend",
         "Keyless, every agent returns a deterministic stub and reports ai.backend = \"stub\"; past the configured "
         "budget cap agents fall back to the same stubs; GET /copilot/health reports mode, cost, tokens, per-agent "
         "ranking and cache hit rate", "Must"],
    ])

    doc.add_heading("4.10 FR-AGENT \u2014 Agentic Build", level=2)
    d_add_req_table(doc, [
        ["FR-AGENT-01", "Converse to author a twin",
         "POST /api/v1/agents/twin/start and /twin/message drive the twin-builder graph; GET /twin/{session_id} "
         "returns the accumulated state and the proposed specification", "Must"],
        ["FR-AGENT-02", "Author a twin from an uploaded photo or floor plan",
         "POST /agents/twin/upload accepts an image; the vision agent returns a structured TwinSpec of assets and "
         "relationships rather than a generic default", "Must"],
        ["FR-AGENT-03", "Build a 3-D building from a 2-D plan",
         "POST /agents/twin/build-from-plan (and its /start + /status/{build_id} asynchronous form) parses the plan, "
         "seeds the graph and caches the scene; GET /twin/scene/{tenant} serves it", "Must"],
        ["FR-AGENT-04", "Author and publish a capability bundle",
         "POST /agents/bundle/start, /bundle/message and /bundle/approve run the bundle-author graph; an approved "
         "bundle is persisted and is immediately loadable by every task", "Must"],
        ["FR-AGENT-05", "Run operational reasoning graphs against a live twin",
         "POST /agents/ops/diagnose, /ops/analysis and /ops/cascade execute the operational graph; "
         "GET /ops/{session_id} returns the run state", "Must"],
        ["FR-AGENT-06", "Extend a live twin with a plugin or an accelerator",
         "POST /agents/plugin/* attaches a new pack or adapter; /agents/accelerator/* assembles a vertical twin "
         "from templates; both expose start, message and state", "Should"],
        ["FR-AGENT-07", "Survive interruption for human approval",
         "Graph state is checkpointed to the checkpoints table by thread_id, so a run paused for approval on one "
         "task resumes on another with no sticky sessions", "Must"],
    ])

    doc.add_heading("4.11 FR-3D \u2014 3-D Reconstruction and Visualisation", level=2)
    d_add_req_table(doc, [
        ["FR-3D-01", "Submit a photo-to-3-D reconstruction job",
         "POST /api/v1/threed/api/jobs accepts an image and returns a job id; the job record is written to the "
         "shared store, not task disk, so polling through the load balancer cannot 404", "Must"],
        ["FR-3D-02", "Poll job status and stage progress",
         "GET /api/jobs and /api/jobs/{job_id} return status, current stage and per-stage results", "Must"],
        ["FR-3D-03", "Retrieve the generated model and intermediate artifacts",
         "GET /api/jobs/{job_id}/result redirects to a presigned S3 URL when the backend is S3, so a 20 MB GLB "
         "streams from S3 instead of occupying an API worker; /file/{path} serves individual artifacts", "Must"],
        ["FR-3D-04", "Manage reconstruction priors",
         "GET and POST /api/priors list and add the shape priors that steer reconstruction for known object classes", "Should"],
        ["FR-3D-05", "Serve a cached BIM scene for the 3-D viewer",
         "Scenes are stored in the scene_cache table keyed by tenant, so every task serves the same scene; the "
         "viewer supports wall solid/glass/hidden modes and click-to-inspect equipment panels", "Must"],
    ])

    doc.add_heading("4.12 FR-BUS \u2014 Event Bus and Live Updates", level=2)
    d_add_req_table(doc, [
        ["FR-BUS-01", "Publish every graph mutation as a tenant-scoped event",
         "Each Graph Writer commit publishes a BusEvent to nxr:events:<tenant_id> carrying event_id, entity_id, "
         "entity_type, label, action, actor and timestamp", "Must"],
        ["FR-BUS-02", "Stream live events to a client",
         "GET /api/v1/bus/stream returns a Server-Sent Events stream scoped to the caller's tenant; "
         "GET /bus/events returns the recent buffer for late joiners", "Must"],
        ["FR-BUS-03", "Report bus health and scale safety",
         "GET /bus/stats and the health payload report the backend (redis / memory / null), whether Redis is "
         "required by configuration, and scale_safe \u2014 an in-memory bus reports healthy but is not scale-safe", "Must"],
    ])

    doc.add_heading("4.13 FR-AUTH \u2014 Authentication and Tenant Isolation", level=2)
    d_add_req_table(doc, [
        ["FR-AUTH-01", "Authenticate every /api request with an API key",
         "X-API-Key is matched against NXR_API_KEYS; the dashboard at / is exempt; NXR_REQUIRE_AUTH=1 rejects a "
         "keyless call even before keys load", "Must"],
        ["FR-AUTH-02", "Enforce role-based access",
         "admin has full access across its scope; write may read and mutate; read is rejected with 403 on any "
         "mutating route", "Must"],
        ["FR-AUTH-03", "Enforce tenant scope wherever the tenant appears",
         "A single application-level dependency checks the tenant in the query string, the path parameters and "
         "the request body on every /api route; a key scoped to one tenant receives 403 for another", "Must"],
        ["FR-AUTH-04", "Support four key scope forms",
         "{\"tenant\": \"x\"}, {\"tenant_prefix\": \"x-\"}, {\"tenants\": [...]} and {\"tenant\": \"*\"} are honoured "
         "most-specific first; only a prefix or admin scope may create a twin", "Must"],
        ["FR-AUTH-05", "Authenticate devices separately from users",
         "X-Device-Token grants only telemetry append in one tenant; it is never accepted on a read, control or "
         "copilot route, and an API key is never accepted on the ingest route in its place", "Must"],
    ])

    doc.add_heading("4.14 FR-OPS \u2014 Platform Operations", level=2)
    d_add_req_table(doc, [
        ["FR-OPS-01", "Report component-level health without ever failing the request",
         "GET /api/v1/health always returns 200 with status healthy or degraded and separate neo4j, database, "
         "blobs, bus, historian and twin_runtime sections", "Must"],
        ["FR-OPS-02", "Expose scale-safety for the bus and the historian",
         "Both report scale_safe and required, so an operator can alarm before scaling out rather than "
         "discovering it as \u2018live updates sometimes stop\u2019", "Must"],
        ["FR-OPS-03", "Report platform statistics",
         "GET /api/v1/stats returns entity, finding and event counts, active twins and the ontology version", "Must"],
        ["FR-OPS-04", "Control the simulated feed",
         "POST /api/v1/feed/start, GET /feed/status and POST /feed/stop drive the deterministic demonstration "
         "feed with per-signal latest values and findings emitted", "Must"],
        ["FR-OPS-05", "State the deployment posture at startup",
         "Four stdout lines \u2014 [auth], [db], [blobs], [bus] \u2014 state in plain words whether enforcement is on "
         "and which backend each store resolved to, and reach CloudWatch", "Must"],
    ])

    # 5. Workflows
    doc.add_heading("5. Workflow Specifications", level=1)

    doc.add_heading("5.1 Telemetry to Finding", level=2)
    d_para(doc, "The primary data workflow, identical for device ingest, connector polling and the simulated feed:")
    d_bullet(doc, "1. A sample arrives \u2014 POST /api/v1/ingest/telemetry with X-Device-Token, a connector worker "
                  "poll, or the simulated feed loop.")
    d_bullet(doc, "2. The ingest pipeline authenticates the source, resolves the asset (honouring the device's "
                  "asset prefix), checks the tenant scope and normalises the sample to (tenant, asset, signal, "
                  "value, unit, quality, ts).")
    d_bullet(doc, "3. The historian writes it to the measurements hypertable. The (tenant_id, asset_id, signal, ts) "
                  "primary key makes a replayed batch a no-op, so store-and-forward from the edge cannot double-count.")
    d_bullet(doc, "4. The sample is routed into the behaviour registry, which dispatches it to every behaviour "
                  "whose watches set contains its signal. Tier A compares against the physics forward model, "
                  "Tier B against a statistical baseline, Tier C against a threshold or state rule.")
    d_bullet(doc, "5. Each emitted Finding is written through the Graph Writer: SHACL validate \u2192 commit the "
                  "Finding node to Neo4j \u2192 append a change-log event \u2192 publish a bus event.")
    d_bullet(doc, "6. The change-log append takes a PostgreSQL advisory lock for the tenant, computes wm_hash over "
                  "the canonical content including prev_event_hash, and extends the tenant's chain.")
    d_bullet(doc, "7. The BusEvent lands on nxr:events:<tenant_id>; connected SSE clients update the dashboard and "
                  "the 3-D scene, and the copilot agents can read the new state.")

    doc.add_heading("5.2 Machine-Twin Runtime Tick and Ownership", level=2)
    d_para(doc, "How live physics stays single-owner across a multi-task fleet:")
    d_bullet(doc, "1. Each task periodically attempts to acquire or renew a short Redis lease per tenant. Exactly "
                  "one succeeds and becomes the owner.")
    d_bullet(doc, "2. The owner drains the tenant's command queue \u2014 throttle changes, fault injection, start/stop "
                  "forwarded by follower tasks \u2014 and applies them to the integrator.")
    d_bullet(doc, "3. It advances the pack's forward model by one tick, accumulating wear and heat, then computes "
                  "residuals against the clean-machine baseline and per-subsystem component_health.")
    d_bullet(doc, "4. It evaluates the pack's behaviour registry and persists any findings once, through the Graph "
                  "Writer.")
    d_bullet(doc, "5. It publishes the authoritative state. Followers serve that state verbatim, so every task "
                  "reports identical values regardless of which one the load balancer picked.")
    d_bullet(doc, "6. If the owner dies the lease expires after roughly eight seconds and another task adopts the "
                  "published state, continuing the simulation rather than restarting it.")

    doc.add_heading("5.3 Build-a-Twin", level=2)
    d_para(doc, "From a conversation, a photo or a floor plan to a live twin:")
    d_bullet(doc, "1. POST /api/v1/agents/twin/start opens a session; each /twin/message turn runs the twin-builder "
                  "graph and checkpoints its state by thread_id.")
    d_bullet(doc, "2. An uploaded object photo is routed to the 3-D platform: TRELLIS on RunPod reconstructs a GLB, "
                  "artifacts are published to S3 as each stage completes, and the twin renders the real model rather "
                  "than a stand-in.")
    d_bullet(doc, "3. An uploaded floor plan is routed to the 2d-to-3d parser instead, producing a BIM scene graph "
                  "of storeys, spaces, walls and equipment.")
    d_bullet(doc, "4. The agreed specification is seeded through the Graph Writer, so validation, change log and bus "
                  "events all fire, and the resulting scene is written to scene_cache keyed by tenant.")
    d_bullet(doc, "5. If the domain matches a machine-twin SPEC the runtime picks it up on the next tick and the twin "
                  "is live \u2014 state, diagnostics, prediction and the copilot all become available with no further "
                  "configuration.")

    doc.add_heading("5.4 Authentication and Tenant Scope Enforcement", level=2)
    d_para(doc, "Applied to every request:")
    d_bullet(doc, "1. AuthMiddleware reads X-API-Key and resolves it to a key record carrying a role and a scope. "
                  "The SPA at / and its static assets are exempt.")
    d_bullet(doc, "2. With NXR_API_KEYS unset the deployment is in development posture and the built-in demo keys "
                  "load; NXR_REQUIRE_AUTH=1 closes that fail-open path and rejects keyless calls outright.")
    d_bullet(doc, "3. FastAPI resolves the route, so request.path_params is now populated. The application-level "
                  "enforce_tenant_scope dependency then extracts every tenant reference \u2014 query string, path "
                  "parameter and request body \u2014 and checks each against the key's scope.")
    d_bullet(doc, "4. Scope forms are evaluated most-specific first: exact tenant, prefix, explicit set, then admin "
                  "wildcard. A mismatch returns 403 before the handler runs.")
    d_bullet(doc, "5. Ingest routes take X-Device-Token instead, which resolves to exactly one tenant and one verb.")

    doc.add_heading("5.5 Solar Residual Diagnosis", level=2)
    d_para(doc, "How the solar twin turns weather-dependent output into a maintenance decision:")
    d_bullet(doc, "1. Measured plane-of-array irradiance, back-of-module temperature, ambient temperature and wind "
                  "speed arrive from the weather station; DC and AC electrical signals arrive from the inverter and "
                  "string combiners.")
    d_bullet(doc, "2. Cell temperature is derived (Faiman model) and the De Soto five-parameter single-diode model "
                  "computes what this array should produce at this irradiance and this cell temperature.")
    d_bullet(doc, "3. The residual layer computes \u0394P vs model, performance ratio, fill factor, normalised shunt "
                  "resistance and string current imbalance.")
    d_bullet(doc, "4. Three behaviours classify the signature: a symmetric drop with normal voltage and uniformly "
                  "suppressed current is soiling; a step voltage drop on one string with constant current is bypass "
                  "diode activation or heavy local shading; a progressive shunt-resistance decline over 90 days is "
                  "potential-induced degradation or delamination.")
    d_bullet(doc, "5. Each maps to a different intervention \u2014 a low-priority cleaning ticket, a high-priority "
                  "structural inspection with the string isolated in red, or scheduled engineering diagnostics next "
                  "quarter \u2014 and the trigger is machine-readable, so the work-order and procurement agents act "
                  "without re-deriving what it means.")

    doc.add_heading("5.6 Ontology Extension by a Domain Pack", level=2)
    d_para(doc, "Adding a vertical without touching the platform core:")
    d_bullet(doc, "1. Author packs/<domain>/<domain>-classes.ttl importing v3/core, declaring OWL classes, "
                  "observable properties, object properties and failure modes. Every class declares exactly one of "
                  "the ten taxonomy categories.")
    d_bullet(doc, "2. Author <domain>-shapes.ttl with SHACL node and property shapes constraining datatypes and "
                  "value ranges.")
    d_bullet(doc, "3. Implement physics.py (component engines plus init_state / forward / inject / residuals / "
                  "health_index and optionally network_state), behaviors.py (build_<domain>_registry) and predict.py "
                  "(component_health and predict).")
    d_bullet(doc, "4. Expose a SPEC (or a SPECS list) from __init__.py declaring the key, label, class IRI, control "
                  "input, signals, units, faults, sensors, subsystems and diagnostics checks.")
    d_bullet(doc, "5. Register the TTL files in tools/ontology_graph.py, the seeding builder in twins/service.py, the "
                  "relationship prefix in graph/writer.py, and the pack name in twins/runtime.py load_specs(). The "
                  "domain then appears as a live machine twin with no other wiring.")
    d_bullet(doc, "6. The SHACL gate validates the extended ontology, and the governance shape rejects any attempt to "
                  "introduce an eleventh taxonomy category.")

    # 6. NFRs
    doc.add_heading("6. Non-Functional Requirements", level=1)
    d_add_table(doc, ["NFR ID", "Requirement", "Target"], [
        ["NFR-01", "Physics runtime tick rate", "1 Hz per owned tenant"],
        ["NFR-02", "Authoritative state publication interval", "1 second"],
        ["NFR-03", "Twin ownership lease expiry after owner loss", "~8 seconds to adoption by another task"],
        ["NFR-04", "Historian raw retention", "30 days (NXR_HISTORIAN_RETENTION_RAW)"],
        ["NFR-05", "Rollup retention (1m / 1h / 1d)", "400 days / 1,095 days / indefinite"],
        ["NFR-06", "Telemetry compression ratio", "10\u201320x, chunks compressed after 7 days"],
        ["NFR-07", "Continuous-aggregate refresh interval", "1 min (1m), 10 min (1h), 1 hour (1d)"],
        ["NFR-08", "Hypertable chunk interval", "1 day"],
        ["NFR-09", "Database connection pool per task", "1\u201310 connections (NXR_DB_POOL_MIN/MAX)"],
        ["NFR-10", "Database connect timeout", "10 seconds"],
        ["NFR-11", "ECS desired count", "2+ tasks across two availability zones"],
        ["NFR-12", "ALB idle timeout for SSE streams", "300 seconds"],
        ["NFR-13", "Health endpoint HTTP status", "Always 200; degradation reported in the payload"],
        ["NFR-14", "Blob health probe", "Real put + read-back + delete on every health call"],
        ["NFR-15", "Copilot fallback when keyless or over budget", "Deterministic stub, ai.backend = \"stub\""],
        ["NFR-16", "Container user", "Non-root uid/gid 10001"],
        ["NFR-17", "TLS in transit", "sslmode=require (RDS), neo4j+s:// (Aura), TLS 1.3 (MQTT edge)"],
        ["NFR-18", "Test suite", "171 automated tests (physics, ingest, point maps, solar spec, tenant isolation)"],
    ], col_widths=[2, 9, 6])

    # 7. Assumptions
    doc.add_heading("7. Assumptions and Dependencies", level=1)
    d_bullet(doc, "Neo4j is reachable. Without it the application still boots and reads return empty with a "
                  "degraded health status, but creating twins and assets fails with 503. A deployment is not "
                  "\u2018done\u2019 while health reports degraded.")
    d_bullet(doc, "PostgreSQL 16 is reachable via NXR_DATABASE_URL with sslmode=require. Unset, the platform "
                  "falls back to per-task SQLite files \u2014 correct for local development and wrong on any "
                  "multi-task deployment.")
    d_bullet(doc, "The TimescaleDB extension is installed. Without it the historian still works and never "
                  "compresses and never expires anything, so the disk fills and trend queries scan raw rows. "
                  "NXR_REQUIRE_TIMESCALE=1 makes it mandatory.")
    d_bullet(doc, "NXR_S3_BUCKET and NXR_REDIS_URL are set on any deployment with more than one task, with the "
                  "matching required-flags, so blobs and live events are shared rather than task-local.")
    d_bullet(doc, "ANTHROPIC_API_KEY is configured for real copilot reasoning. Without it every agent returns a "
                  "deterministic stub and reports that it did so; nothing breaks.")
    d_bullet(doc, "RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID are configured for photo-to-3-D reconstruction; unset, "
                  "that pipeline stage stubs and the rest of the platform is unaffected.")
    d_bullet(doc, "NXR_API_KEYS is set (with NXR_REQUIRE_AUTH=1) before the service is exposed publicly. While "
                  "unset the API is open \u2014 including the LLM-billing copilot endpoints.")
    d_bullet(doc, "Optional protocol drivers (pymodbus, asyncua, paho-mqtt with protobuf) are installed for the "
                  "field protocols a deployment actually uses; a missing driver disables one protocol rather than "
                  "the service.")
    d_bullet(doc, "The GoalCert Hub consumes NextXR as a Module Federation remote; when the UI is federated, "
                  "VITE_REMOTE_BASE must be set at build time to the CloudFront origin or hub-embedded assets 404.")

    out_path = os.path.join(TECHDOCS_DIR, "NextXR_FRD.docx")
    doc.save(out_path)
    print(f"  Saved: {out_path}")


# ============================================================================
# BUILD LLD
# ============================================================================

def build_lld():
    doc = init_doc(margins_cm=2.0)
    add_title_page(doc, "Low-Level Design (LLD)",
                   "Package structure \u00b7 API routes \u00b7 Data schema \u00b7 Services \u00b7 Sequences",
                   "FastAPI \u00b7 Neo4j \u00b7 PostgreSQL/TimescaleDB \u00b7 Redis \u00b7 S3 \u00b7 Anthropic Claude \u00b7 Docker")
    add_doc_control(doc, "Low-Level Design (LLD)", "Low-Level Design (LLD)",
                    "Initial issue covering package structure, 136 API endpoints across 12 routers, "
                    "9 relational tables plus 3 continuous aggregates, the Neo4j schema, 13 service "
                    "classes, 11 machine-twin packs and the key sequences.")
    add_toc_placeholder(doc)

    # 1. Introduction
    doc.add_heading("1. Introduction", level=1)
    d_para(doc,
           "This Low-Level Design details the package structure, API routes, data schema, service "
           "implementations, pack contracts, the middleware and dependency pipeline, and the key sequences "
           "for the NextXR Digital Twin Platform. It is derived from the HLD (R2) and maps every functional "
           "requirement from the FRD (R1) to concrete code-level design.")

    # 2. Package Structure
    doc.add_heading("2. Package Structure", level=1)
    d_para(doc, "The repository root holds the deployment artefacts (Dockerfile, docker-compose.yml, "
                "requirements.txt, start.ps1) and three trees: nextxr-ontology/ (the platform), frontend/ "
                "(the React SPA built into the image) and apps/ (the 2-D-to-3-D parser, the 3-D platform "
                "source and the TRELLIS RunPod worker).")
    d_code(doc, """nextxr-ontology/
\u251c\u2500\u2500 server/                     # FastAPI application
\u2502   \u251c\u2500\u2500 main.py                 # app factory, router wiring, SPA serving, feed control
\u2502   \u251c\u2500\u2500 auth.py                 # AuthMiddleware, API keys, roles (admin/write/read)
\u2502   \u251c\u2500\u2500 tenancy.py              # enforce_tenant_scope \u2014 the ONE tenant authorisation point
\u2502   \u251c\u2500\u2500 query_api.py            # /api/v1 reads: entities, findings, changelog, stats, bus, health
\u2502   \u251c\u2500\u2500 write_api.py            # /api/v1 writes: create/update/delete entity, relate
\u2502   \u251c\u2500\u2500 schema_routes.py        # /api/v1/schema: types, classes, predicates, validate, governance
\u2502   \u251c\u2500\u2500 twins_routes.py         # /api/v1/twins: templates, CRUD
\u2502   \u251c\u2500\u2500 twin_runtime_routes.py  # /api/v1/twins: domains, state, diagnostics, predict, simulate
\u2502   \u251c\u2500\u2500 ingest_routes.py        # /api/v1/ingest: telemetry, bulk, devices
\u2502   \u251c\u2500\u2500 historian_routes.py     # /api/v1: history, latest, signals, trends, stats
\u2502   \u251c\u2500\u2500 connector_routes.py     # /api/v1/connectors: protocols, profiles, CRUD, lifecycle
\u2502   \u251c\u2500\u2500 solar_routes.py         # /api/v1/solar: De Soto model, residual, diagnosis, triggers
\u2502   \u251c\u2500\u2500 agent_routes.py         # /api/v1/agents: twin, bundle, ops, plugin, accelerator graphs
\u2502   \u251c\u2500\u2500 copilot_routes.py       # /api/v1/copilot: 14 embedded Claude agents + health
\u2502   \u251c\u2500\u2500 hub_routes.py           # /api/v1: hub predict + AR overlay
\u2502   \u2514\u2500\u2500 threed_platform/        # mounted sub-app at /api/v1/threed (jobs, priors, artifacts)
\u251c\u2500\u2500 platform/                   # Layer 2+3 ontology (core, properties, units, taxonomy,
\u2502                               #   governance, base shape, shapes, behaviour bindings)
\u251c\u2500\u2500 imports/                    # Layer 1: bfo.ttl, sosa.ttl
\u251c\u2500\u2500 packs/                      # Layer 4: one self-contained folder per domain
\u2502   \u251c\u2500\u2500 _core/physics.py        # shared physics primitives
\u2502   \u251c\u2500\u2500 turbine/  edm/  railway/  fleet/  hospital/  ev/  defence/  solar/
\u2502   \u2502                           #   *-classes.ttl, *-shapes.ttl, physics.py,
\u2502   \u2502                           #   behaviors.py, predict.py, __init__.py (SPEC/SPECS)
\u2502   \u251c\u2500\u2500 bim/  cfp/  hvac/  datacenter/     # ontology + behaviour-binding packs
\u2502   \u2514\u2500\u2500 published/              # bundles authored by the Bundle Author agent
\u251c\u2500\u2500 graph/                      # Neo4j layer
\u2502   \u251c\u2500\u2500 connection.py           # shared driver singleton
\u2502   \u251c\u2500\u2500 schema.py               # constraints + indexes derived from the ontology
\u2502   \u251c\u2500\u2500 writer.py               # GraphWriter: validate \u2192 commit \u2192 changelog \u2192 bus; Rel, PREFIXES
\u2502   \u251c\u2500\u2500 query.py                # GraphQuery: read paths used by behaviours and the API
\u2502   \u251c\u2500\u2500 crud.py  state_machine.py  sensor_defaults.py
\u2502   \u2514\u2500\u2500 gate_test.py
\u251c\u2500\u2500 twins/                      # twin registry + live runtime
\u2502   \u251c\u2500\u2500 service.py              # TwinRegistry, templates, _seed_* builders
\u2502   \u251c\u2500\u2500 runtime.py              # LiveTwin, MachineEngine, load_specs, _FrameQuery
\u2502   \u251c\u2500\u2500 coordinator.py          # one owner per twin via Redis lease + command queue
\u2502   \u2514\u2500\u2500 seed.py
\u251c\u2500\u2500 behaviors/                  # behaviour model registry
\u2502   \u251c\u2500\u2500 registry.py             # Behavior, BehaviorRegistry, Tier(A/B/C), Finding routing
\u2502   \u251c\u2500\u2500 archetypes.py  diagnosis.py
\u2502   \u251c\u2500\u2500 hvac/                   # threshold, z-score baseline, thermal physics
\u2502   \u2514\u2500\u2500 cfp/                    # power, fire, security, water, network, filter, chiller, vibration
\u251c\u2500\u2500 dynamics/                   # coupled multi-entity simulation
\u2502   \u251c\u2500\u2500 engine.py               # topological ordering, EntityContext, 1-tick cycle breaking
\u2502   \u251c\u2500\u2500 model.py  bindings.py  flows.py  registry_build.py
\u2502   \u2514\u2500\u2500 models/                 # hvac, electrical, fluid, water, it, datacenter, hospital,
\u2502                               #   transport, sensing, spaces, power_extra, default
\u251c\u2500\u2500 historian/                  # TimescaleDB telemetry store
\u2502   \u251c\u2500\u2500 schema.py               # hypertable, 1m/1h/1d continuous aggregates, compression, retention
\u2502   \u2514\u2500\u2500 core.py                 # write, query, rollup selection, info()
\u251c\u2500\u2500 ingest/                     # inbound telemetry
\u2502   \u251c\u2500\u2500 pipeline.py             # normalise \u2192 historian \u2192 behaviour registry
\u2502   \u2514\u2500\u2500 devices.py              # device registry, token hashing, rotation
\u251c\u2500\u2500 connectors/                 # field protocols
\u2502   \u251c\u2500\u2500 manager.py              # supervisor: persistence, lifecycle, ownership leases
\u2502   \u251c\u2500\u2500 base.py  pointmap.py  profiles.py
\u2502   \u2514\u2500\u2500 modbus.py  opcua.py  mqtt.py
\u251c\u2500\u2500 db/                         # relational store
\u2502   \u251c\u2500\u2500 core.py                 # pool, dialect translation, advisory locks, info()
\u2502   \u251c\u2500\u2500 schema.py               # every table's DDL in ONE place + ensure()/CLI
\u2502   \u2514\u2500\u2500 migrate.py              # SQLite \u2192 PostgreSQL migration
\u251c\u2500\u2500 changelog/service.py        # per-tenant tamper-evident hash chain
\u251c\u2500\u2500 bus/event_bus.py            # Redis Streams per tenant + in-memory fallback
\u251c\u2500\u2500 storage/core.py             # S3 / local blob layer
\u251c\u2500\u2500 copilot/                    # embedded Claude agents
\u2502   \u251c\u2500\u2500 agents.py               # 14 agents, stubs, agent_trace
\u2502   \u251c\u2500\u2500 config.py               # model policy, thinking effort
\u2502   \u2514\u2500\u2500 spend.py                # cost accounting + budget cap
\u251c\u2500\u2500 agents/                     # agentic build layer
\u2502   \u251c\u2500\u2500 engine.py               # LangGraph-compatible StateGraph + CheckpointSaver
\u2502   \u251c\u2500\u2500 registry.py             # capability bundle registry (builtin + published)
\u2502   \u251c\u2500\u2500 twin_graph.py  bundle_graph.py  operational_graph.py
\u2502   \u251c\u2500\u2500 plugin_graph.py  accelerator_graph.py
\u2502   \u2514\u2500\u2500 ifc_parser.py  hospital_layout.py  bim_support.py  gateway.py  state.py
\u251c\u2500\u2500 feed/simulate.py            # deterministic simulated feed + FindingsLoop
\u2514\u2500\u2500 tools/                      # load.py, validate.py, gate.py, ontology_graph.py,
                               #   schema_service.py, historian_provision.py, track3/4 gates""")

    # 3. Data Flow
    doc.add_heading("3. Data Flow", level=1)
    d_para(doc, "The data flow diagram below shows the complete path of a telemetry sample through the system, "
                "the machine-twin runtime tick, and the resulting event stream:")
    add_diagram(doc, PNG_DATAFLOW, "Figure 1: NextXR Telemetry-to-Finding Data Flow")

    # 4. API Routes
    doc.add_heading("4. API Routes", level=1)
    d_para(doc, "136 API endpoints. Twelve routers are registered on the application; the 3-D platform is "
                "mounted as a sub-application at /api/v1/threed. Router order matters in two places and is "
                "commented in main.py: the historian and twin-runtime routers must be registered before "
                "twins_router so their multi-segment /twins/{tenant}/... paths match before the bare "
                "/twins/{tenant}. The SPA is served from / with a catch-all fallback for client-side routing.")
    d_add_table(doc, ["Router", "Prefix", "Tag", "Endpoints"], [
        ["query_api", "/api/v1", "graph", "12"],
        ["write_api", "/api/v1", "write", "4"],
        ["schema_routes", "/api/v1/schema", "schema", "11"],
        ["ingest_routes", "/api/v1/ingest", "ingest", "8"],
        ["historian_routes", "/api/v1", "historian", "5"],
        ["connector_routes", "/api/v1/connectors", "connectors", "14"],
        ["solar_routes", "/api/v1/solar", "solar", "14"],
        ["twin_runtime_routes", "/api/v1/twins", "twin-runtime", "8"],
        ["twins_routes", "/api/v1/twins", "twins", "5"],
        ["agent_routes", "/api/v1/agents", "agents", "26"],
        ["copilot_routes", "/api/v1/copilot", "copilot", "16"],
        ["hub_routes", "/api/v1", "hub", "2"],
        ["main.py (app-level)", "/api/v1/feed", "feed", "3"],
        ["threed_platform (mounted)", "/api/v1/threed", "threed", "8"],
    ], col_widths=[4, 4, 3, 2])

    doc.add_heading("4.1 Graph Read Routes (/api/v1)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Params"], [
        ["GET", "/api/v1/entities", "List entities for a tenant", "read", "tenant, label?, limit?, offset?"],
        ["GET", "/api/v1/entities/{node_id}", "Get one entity with properties", "read", "tenant"],
        ["GET", "/api/v1/entities/{node_id}/telemetry", "Live signal values for an entity", "read", "tenant"],
        ["GET", "/api/v1/findings", "List findings", "read", "tenant, severity?, limit?"],
        ["GET", "/api/v1/changelog", "Tenant change-log chain", "read", "tenant, limit?"],
        ["GET", "/api/v1/changelog/{entity_id}", "Per-entity change history", "read", "tenant"],
        ["GET", "/api/v1/topology", "Nodes + edges for 3-D / network views", "read", "tenant"],
        ["GET", "/api/v1/stats", "Graph and platform counters", "read", "tenant?"],
        ["GET", "/api/v1/bus/stats", "Event-bus backend + throughput", "read", "\u2014"],
        ["GET", "/api/v1/bus/events", "Recent event buffer", "read", "tenant, limit?"],
        ["GET", "/api/v1/bus/stream", "Server-Sent Events stream", "read", "tenant"],
        ["GET", "/api/v1/health", "Component health (always 200)", "None", "\u2014"],
    ], col_widths=[1.5, 4.5, 4.5, 1.5, 5])

    doc.add_heading("4.2 Graph Write Routes (/api/v1)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Request Body"], [
        ["POST", "/api/v1/entities", "Create entity (SHACL-validated)", "write",
         "tenant, canonical_type, properties{}"],
        ["PATCH", "/api/v1/entities/{node_id}", "Update entity properties", "write", "tenant, properties{}"],
        ["DELETE", "/api/v1/entities/{node_id}", "Delete entity", "write", "tenant"],
        ["POST", "/api/v1/entities/{node_id}/rel", "Relate two entities", "write",
         "tenant, predicate (CURIE), target_id"],
    ], col_widths=[1.5, 4.5, 4.5, 1.5, 5])

    doc.add_heading("4.3 Schema and Governance Routes (/api/v1/schema)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Params / Body"], [
        ["GET", "/api/v1/schema/version", "Ontology version + parsed files", "read", "\u2014"],
        ["GET", "/api/v1/schema/types", "All entity classes", "read", "\u2014"],
        ["GET", "/api/v1/schema/categories", "The ten closed taxonomy categories", "read", "\u2014"],
        ["GET", "/api/v1/schema/predicates", "Object-property vocabulary", "read", "\u2014"],
        ["GET", "/api/v1/schema/class/{name}", "Class definition and metadata", "read", "\u2014"],
        ["GET", "/api/v1/schema/class/{name}/properties", "Observable + object properties", "read", "\u2014"],
        ["GET", "/api/v1/schema/class/{name}/behavior", "Behaviours bound to the class", "read", "\u2014"],
        ["GET", "/api/v1/schema/archetypes", "Physics archetypes", "read", "\u2014"],
        ["GET", "/api/v1/schema/asset-types", "Instantiable asset types", "read", "\u2014"],
        ["POST", "/api/v1/schema/validate", "SHACL-validate a payload", "read", "canonical_type, properties{}"],
        ["GET", "/api/v1/schema/governance", "Taxonomy closure over the T-Box", "read", "\u2014"],
    ], col_widths=[1.5, 5.5, 4.5, 1.5, 4])

    doc.add_heading("4.4 Twin Registry Routes (/api/v1/twins)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Request Body / Params"], [
        ["GET", "/api/v1/twins/templates", "Seeding templates", "read", "\u2014"],
        ["GET", "/api/v1/twins", "List twins in scope", "read", "\u2014"],
        ["POST", "/api/v1/twins", "Create + seed a twin", "write (prefix/admin scope)",
         "tenant_id, name, domain, description?"],
        ["GET", "/api/v1/twins/{tenant}", "Twin registry record", "read", "\u2014"],
        ["DELETE", "/api/v1/twins/{tenant}", "Delete twin and its entities", "write", "\u2014"],
    ], col_widths=[1.5, 4.5, 4, 2.5, 4.5])

    doc.add_heading("4.5 Machine-Twin Runtime Routes (/api/v1/twins)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Request Body / Params"], [
        ["GET", "/api/v1/twins/domains", "Discovered machine-twin SPECs", "read", "\u2014"],
        ["GET", "/api/v1/twins/{tenant}/state", "Live frame: signals, control, health", "read", "\u2014"],
        ["GET", "/api/v1/twins/{tenant}/diagnostics", "Sensor status + subsystem health", "read", "\u2014"],
        ["GET", "/api/v1/twins/{tenant}/predict", "RUL and degradation forecast", "read", "\u2014"],
        ["POST", "/api/v1/twins/{tenant}/project", "What-if projection (non-mutating)", "read", "control profile, horizon"],
        ["GET", "/api/v1/twins/{tenant}/network", "Network state (network-shaped domains)", "read", "\u2014"],
        ["POST", "/api/v1/twins/{tenant}/running", "Start / stop the simulation", "write", "running: bool"],
        ["POST", "/api/v1/twins/{tenant}/simulate", "Inject fault / set control", "write", "fault?, control?, clear?"],
    ], col_widths=[1.5, 5, 4.5, 1.5, 4.5])

    doc.add_heading("4.6 Ingest Routes (/api/v1/ingest)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Request Body"], [
        ["POST", "/api/v1/ingest/telemetry", "Append one sample", "X-Device-Token",
         "asset_id, signal, value, unit?, quality?, ts?"],
        ["POST", "/api/v1/ingest/telemetry/bulk", "Append a batch (idempotent)", "X-Device-Token", "samples[]"],
        ["GET", "/api/v1/ingest/status", "Accepted/rejected counters", "read", "\u2014"],
        ["POST", "/api/v1/ingest/devices", "Register a device (token returned once)", "write",
         "tenant, name, asset_prefix?, expires_at?"],
        ["GET", "/api/v1/ingest/devices", "List devices (never the token)", "read", "tenant"],
        ["POST", "/api/v1/ingest/devices/{device_id}/rotate", "Issue a new token", "write", "\u2014"],
        ["POST", "/api/v1/ingest/devices/{device_id}/enabled", "Enable / disable a device", "write", "enabled: bool"],
        ["DELETE", "/api/v1/ingest/devices/{device_id}", "Revoke a device", "write", "\u2014"],
    ], col_widths=[1.5, 5.5, 4, 2, 4])

    doc.add_heading("4.7 Historian Routes (/api/v1)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Params"], [
        ["GET", "/api/v1/entities/{node_id}/history", "Time-range history for an entity", "read",
         "tenant, signal?, from, to, resolution?"],
        ["GET", "/api/v1/entities/{node_id}/latest", "Latest value per signal", "read", "tenant"],
        ["GET", "/api/v1/twins/{tenant}/signals", "Signal catalogue + last seen", "read", "\u2014"],
        ["GET", "/api/v1/twins/{tenant}/trends", "Aligned multi-signal series", "read", "signals, from, to, bucket"],
        ["GET", "/api/v1/twins/{tenant}/history/stats", "Coverage, backend, quality share", "read", "\u2014"],
    ], col_widths=[1.5, 5, 4.5, 1.5, 4.5])

    doc.add_heading("4.8 Connector Routes (/api/v1/connectors)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Request Body / Params"], [
        ["GET", "/api/v1/connectors/protocols", "Protocols + driver availability", "read", "\u2014"],
        ["GET", "/api/v1/connectors/profiles", "Built-in point-map profiles", "read", "\u2014"],
        ["POST", "/api/v1/connectors/profiles/{key}/build", "Preview a generated point map", "read", "device params"],
        ["GET", "/api/v1/connectors", "List connectors (credentials redacted)", "read", "tenant?"],
        ["POST", "/api/v1/connectors", "Create a connector (201)", "write", "tenant, protocol, name, config{}"],
        ["GET", "/api/v1/connectors/{connector_id}", "Get one connector", "read", "\u2014"],
        ["PATCH", "/api/v1/connectors/{connector_id}", "Update configuration", "write", "partial config"],
        ["DELETE", "/api/v1/connectors/{connector_id}", "Delete a connector", "write", "\u2014"],
        ["POST", "/api/v1/connectors/{connector_id}/start", "Start the worker", "write", "\u2014"],
        ["POST", "/api/v1/connectors/{connector_id}/stop", "Stop the worker", "write", "\u2014"],
        ["GET", "/api/v1/connectors/{connector_id}/health", "Connection state + counters", "read", "\u2014"],
        ["POST", "/api/v1/connectors/{connector_id}/test", "One-shot read", "write", "\u2014"],
        ["POST", "/api/v1/connectors/discover/sunspec", "Walk the SunSpec model chain", "write", "host, port, unit_id"],
        ["POST", "/api/v1/connectors/{connector_id}/browse", "Browse the OPC-UA address space", "write", "start_node?"],
    ], col_widths=[1.5, 6, 4, 1.5, 4])

    doc.add_heading("4.9 Solar Routes (/api/v1/solar)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Spec"], [
        ["POST", "/api/v1/solar/model/evaluate", "De Soto 5-parameter evaluation", "read", "\u00a74.1"],
        ["GET", "/api/v1/solar/triggers", "Maintenance trigger catalogue", "read", "\u00a74.2"],
        ["GET", "/api/v1/solar/{tenant}/model/iv-curve", "Modelled I-V curve", "read", "\u00a74.1"],
        ["GET", "/api/v1/solar/{tenant}/residual", "\u0394P, PR, FF, R_sh, imbalance", "read", "\u00a74.2"],
        ["GET", "/api/v1/solar/{tenant}/diagnosis", "Signature classification", "read", "\u00a74.2"],
        ["GET", "/api/v1/solar/{tenant}/triggers", "Active triggers for this array", "read", "\u00a74.2"],
        ["GET", "/api/v1/solar/{tenant}/heatmap", "Per-zone performance overlay", "read", "\u00a75.1"],
        ["GET", "/api/v1/solar/{tenant}/strings", "Per-string V, I, imbalance, status", "read", "\u00a75.1"],
        ["GET", "/api/v1/solar/{tenant}/energy", "Generated / exported / curtailed", "read", "\u00a75.1"],
        ["GET", "/api/v1/solar/{tenant}/measured/summary", "Measured-signal summary", "read", "\u00a72.2"],
        ["GET", "/api/v1/solar/{tenant}/forecast", "Degradation forecast", "read", "\u00a74.2"],
        ["GET", "/api/v1/solar/{tenant}/field-service/{asset_id}", "AR field-service payload", "read", "\u00a75.2"],
        ["POST", "/api/v1/solar/{tenant}/training/scenario", "Inject a training scenario", "write", "\u00a75.3"],
        ["POST", "/api/v1/solar/{tenant}/training/reset", "Restore pre-scenario state", "write", "\u00a75.3"],
    ], col_widths=[1.5, 6.5, 4.5, 1.5, 3])

    doc.add_heading("4.10 Agent Routes (/api/v1/agents)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth"], [
        ["GET", "/api/v1/agents/info", "Agent layer capabilities and model policy", "read"],
        ["POST", "/api/v1/agents/twin/start", "Open a twin-builder session", "write"],
        ["POST", "/api/v1/agents/twin/message", "Advance the twin-builder conversation", "write"],
        ["GET", "/api/v1/agents/twin/{session_id}", "Twin-builder session state", "read"],
        ["POST", "/api/v1/agents/twin/expand", "Expand a specification into entities", "write"],
        ["POST", "/api/v1/agents/twin/upload", "Photo or plan \u2192 TwinSpec (vision)", "write"],
        ["POST", "/api/v1/agents/twin/scene", "Request a 3-D scene build", "write"],
        ["GET", "/api/v1/agents/twin/scene/{tenant}", "Fetch the cached scene", "read"],
        ["POST", "/api/v1/agents/twin/build-from-plan", "Synchronous 2-D plan \u2192 3-D building", "write"],
        ["POST", "/api/v1/agents/twin/build-from-plan/start", "Asynchronous plan build", "write"],
        ["GET", "/api/v1/agents/twin/build-from-plan/status/{build_id}", "Build progress", "read"],
        ["GET", "/api/v1/agents/twin/sample-scene/{facility}", "Sample scene for a facility type", "read"],
        ["POST", "/api/v1/agents/bundle/start", "Open a bundle-author session", "write"],
        ["POST", "/api/v1/agents/bundle/message", "Advance bundle authoring", "write"],
        ["POST", "/api/v1/agents/bundle/approve", "Approve and publish a bundle", "write"],
        ["GET", "/api/v1/agents/bundle/{session_id}", "Bundle session state", "read"],
        ["POST", "/api/v1/agents/ops/diagnose", "Operational diagnosis graph", "write"],
        ["POST", "/api/v1/agents/ops/analysis", "Operational analysis graph", "write"],
        ["POST", "/api/v1/agents/ops/cascade", "Cascade projection graph", "write"],
        ["GET", "/api/v1/agents/ops/{session_id}", "Operational session state", "read"],
        ["POST", "/api/v1/agents/plugin/start", "Open a plugin session", "write"],
        ["POST", "/api/v1/agents/plugin/message", "Advance plugin authoring", "write"],
        ["GET", "/api/v1/agents/plugin/{session_id}", "Plugin session state", "read"],
        ["POST", "/api/v1/agents/accelerator/start", "Open a vertical accelerator session", "write"],
        ["POST", "/api/v1/agents/accelerator/message", "Advance the accelerator", "write"],
        ["GET", "/api/v1/agents/accelerator/{session_id}", "Accelerator session state", "read"],
    ], col_widths=[1.5, 7.5, 6.5, 1.5])

    doc.add_heading("4.11 Copilot Routes (/api/v1/copilot)", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Thinking"], [
        ["POST", "/api/v1/copilot/build-twin/message", "Build-a-Twin conversation turn", "off"],
        ["POST", "/api/v1/copilot/build-twin/spec", "Vision \u2192 TwinSpec", "off"],
        ["GET", "/api/v1/copilot/narrate/{tenant}", "Narrate the live twin", "off"],
        ["POST", "/api/v1/copilot/narrate", "Narrate a supplied sensor frame", "off"],
        ["POST", "/api/v1/copilot/asset", "Explain one asset's condition", "off"],
        ["POST", "/api/v1/copilot/predict-alert", "Prediction \u2192 actionable alert", "off"],
        ["POST", "/api/v1/copilot/diagnosis", "Root-cause diagnosis", "adaptive"],
        ["POST", "/api/v1/copilot/analysis", "Engineering analysis", "adaptive"],
        ["POST", "/api/v1/copilot/cascade", "Fault propagation projection", "adaptive"],
        ["POST", "/api/v1/copilot/work-order", "Structured work order", "adaptive"],
        ["POST", "/api/v1/copilot/procurement", "Parts list and lead times", "adaptive"],
        ["POST", "/api/v1/copilot/incident-report", "Formal incident narrative", "adaptive"],
        ["POST", "/api/v1/copilot/procedure", "Step-by-step repair procedure", "adaptive"],
        ["POST", "/api/v1/copilot/troubleshoot", "Mechanic chat (multi-turn)", "off"],
        ["POST", "/api/v1/copilot/dashboard-chat", "Operator chat (multi-turn)", "off"],
        ["GET", "/api/v1/copilot/health", "Backend mode, spend, cache hit rate", "\u2014"],
    ], col_widths=[1.5, 7, 6, 2.5])

    doc.add_heading("4.12 Hub, Feed and 3-D Routes", level=2)
    d_add_table(doc, ["Method", "Path", "Description", "Auth", "Notes"], [
        ["POST", "/api/v1/predict", "Hub-facing prediction surface", "read", "Consumed by the GoalCert Hub"],
        ["GET", "/api/v1/assets/{asset_id}/ar-overlay", "AR overlay payload for an asset", "read", "Mobile / headset clients"],
        ["POST", "/api/v1/feed/start", "Start the simulated feed", "write", "tenant, domain, speed"],
        ["GET", "/api/v1/feed/status", "Feed counters and latest signals", "read", "\u2014"],
        ["POST", "/api/v1/feed/stop", "Stop the simulated feed", "write", "\u2014"],
        ["GET", "/api/v1/threed/api/health", "3-D platform health", "read", "Mounted sub-application"],
        ["GET", "/api/v1/threed/api/priors", "List reconstruction priors", "read", "\u2014"],
        ["POST", "/api/v1/threed/api/priors", "Add a reconstruction prior", "write", "\u2014"],
        ["POST", "/api/v1/threed/api/jobs", "Create a reconstruction job", "write", "multipart image upload"],
        ["GET", "/api/v1/threed/api/jobs", "List jobs", "read", "Record in threed_jobs, not task disk"],
        ["GET", "/api/v1/threed/api/jobs/{job_id}", "Job status and stages", "read", "\u2014"],
        ["GET", "/api/v1/threed/api/jobs/{job_id}/file/{path}", "Fetch one artifact", "read", "\u2014"],
        ["GET", "/api/v1/threed/api/jobs/{job_id}/result", "Final GLB", "read", "302 to a presigned S3 URL on S3"],
        ["GET", "/", "React SPA", "None", "Catch-all fallback for client routing"],
    ], col_widths=[1.5, 6.5, 4.5, 1.5, 3])

    # 5. Data Design
    doc.add_heading("5. Data Design", level=1)
    d_para(doc, "NextXR uses three stores with distinct jobs: Neo4j holds the twin's structure and the current "
                "value of a property; PostgreSQL holds records and, through TimescaleDB, the measurement "
                "history; Redis holds live events and ownership leases. S3 holds blobs. No store duplicates "
                "another's job.")

    doc.add_heading("5.1 Relational Store (PostgreSQL 16 / SQLite fallback)", level=2)
    d_para(doc, "db/schema.py declares every table's DDL in one place and renders it for whichever backend is "
                "live; dialect differences are confined to a single fragment table. Provision explicitly with "
                "python -m db.schema (idempotent) \u2014 the app also self-provisions on first use, and the "
                "result is cached per process because ChangeLog() is constructed inside request handlers.")
    d_add_table(doc, ["Table", "Store", "Key columns", "Purpose"], [
        ["twins", "twins", "tenant_id PK, name, domain, description, created_at, seed_asset_id",
         "The twin registry \u2014 which twins exist and what seeded them (FR-TWIN)"],
        ["events", "changelog", "seq BIGSERIAL PK, event_id UNIQUE, tenant_id, entity_id, entity_type, actor, "
                                "action, field_changes JSONB, ts, prev_event_hash, wm_hash",
         "Per-tenant tamper-evident hash chain (FR-GRAPH-08)"],
        ["published_bundles", "bundles", "bundle_id PK, name, domains JSONB, payload JSONB, tenant_id, created_at",
         "Capability bundles authored by the Bundle Author (FR-AGENT-04)"],
        ["checkpoints", "checkpoints", "thread_id PK, graph_name, state JSONB, resume_at, updated_at",
         "Agent graph checkpoints for human-in-the-loop resume (FR-AGENT-07)"],
        ["scene_cache", "scenes", "tenant_id PK, scene JSONB, updated_at",
         "BIM scene graphs for the 3-D viewer \u2014 shared, not per task (FR-3D-05)"],
        ["threed_jobs", "threed", "job_id PK, status, stage, filename, fields/stages/state JSONB, error, "
                                  "created, updated",
         "3-D reconstruction job records (FR-3D-01/02)"],
        ["ingest_devices", "devices", "device_id PK, tenant_id, name, token_hash UNIQUE, asset_prefix, enabled, "
                                      "created_at/by, expires_at, last_seen_at/ip, samples_total, rejected_total",
         "Telemetry device identities \u2014 hash only, never the token (FR-INGEST-04/05)"],
        ["connectors", "connectors", "connector_id PK, tenant_id, protocol, name, enabled, config JSONB, "
                                     "created_at, updated_at",
         "Durable field-connector configuration including the point map (FR-CONN-04)"],
    ], col_widths=[3, 2, 6.5, 6])
    d_para(doc, "Indexes: idx_twins_created (created_at DESC); idx_events_tenant (tenant_id, seq) and "
                "idx_events_entity (tenant_id, entity_id, seq); idx_threed_updated (updated DESC); "
                "idx_devices_tenant (tenant_id) and a UNIQUE idx_devices_token (token_hash) \u2014 authentication "
                "looks a device up by hash because the presented token is all there is on the wire, and without "
                "that index every ingest request is a full table scan; idx_connectors_tenant (tenant_id, enabled).")
    d_para(doc, "Optional extensions (python -m db.schema --extensions, needs rds_superuser): pgvector and "
                "PostGIS. Nothing queries them yet \u2014 they are why the architecture specifies PostgreSQL over "
                "a key-value store (geospatial asset positions, embedding search over the ontology).")

    doc.add_heading("5.2 Telemetry Historian (TimescaleDB)", level=2)
    d_para(doc, "The historian is an extension on the same PostgreSQL instance, not a second database: the same "
                "connection pool, the same backup story, no new security group. Three backends behind one API "
                "\u2014 timescale (full behaviour), postgres (correct, slower, rollups computed on the fly) and "
                "sqlite (local development).")
    d_add_table(doc, ["Object", "Type", "Definition"], [
        ["measurements", "Hypertable (1-day chunks)",
         "tenant_id TEXT, asset_id TEXT, signal TEXT, ts TIMESTAMPTZ, value DOUBLE PRECISION, unit TEXT, "
         "quality SMALLINT, source TEXT, received_at TIMESTAMPTZ"],
        ["measurements_pk", "UNIQUE INDEX",
         "(tenant_id, asset_id, signal, ts DESC) \u2014 the idempotency contract: a replayed batch collides and is "
         "skipped with ON CONFLICT DO NOTHING"],
        ["measurements_tenant_signal_ts", "INDEX",
         "(tenant_id, signal, ts DESC) \u2014 serves signal discovery and the staleness check"],
        ["measurements_1m / _1h / _1d", "Continuous aggregates",
         "tenant_id, asset_id, signal, bucket, count, sum_value, min_value, max_value, first_value, last_value, "
         "bad_count, unit \u2014 each built directly from raw, not from the tier below"],
    ], col_widths=[4, 3.5, 10])
    d_para(doc, "Quality uses the classic OPC quality byte (BAD 0, UNCERTAIN 64, GOOD 192) because a sensor "
                "reporting 0 \u00b0C with a broken wire is not the same fact as a sensor reporting 0 \u00b0C, and a "
                "model that cannot tell them apart will confidently learn from garbage. It is on the raw table "
                "from day one because it cannot be added later without reprocessing all of history.")
    d_para(doc, "The rollups store count and sum_value rather than avg, because an average cannot be "
                "re-averaged: avg(avg(x)) weights each bucket equally regardless of how many samples it held. "
                "Storing count and sum lets the API compute a correct mean at any resolution.")
    d_add_table(doc, ["Policy", "Setting", "Rationale"], [
        ["Chunk interval", "1 day", "A \u2018last hour\u2019 query touches one chunk"],
        ["Compression", "After 7 days; segment by (tenant_id, asset_id, signal), order by ts DESC",
         "Comfortably longer than the window still receiving writes; delta-encoded timestamps give 10\u201320x"],
        ["Refresh policy (1m)", "start_offset 3 h, end_offset 1 min, every 1 min",
         "Bounded look-back so a refresh does not rescan history, wide enough to absorb late edge replay"],
        ["Refresh policy (1h)", "start_offset 3 d, end_offset 1 h, every 10 min", "As above at coarser scale"],
        ["Refresh policy (1d)", "start_offset 30 d, end_offset 1 h, every 1 h", "As above at coarser scale"],
        ["Retention", "raw 30 d, 1m 400 d, 1h 1095 d, 1d forever",
         "Raw is the expensive tier and the least useful after an incident closes; the daily rollup is cheap "
         "enough to keep, which is what makes year-over-year comparison possible"],
    ], col_widths=[3.5, 6, 8])

    doc.add_heading("5.3 Graph Store (Neo4j)", level=2)
    d_para(doc, "graph/schema.py parses the ontology TTL, discovers the taxonomy categories, and applies per "
                "category: CREATE CONSTRAINT uniq_<cat>_tenant_id FOR (n:<Cat>) REQUIRE (n.tenantId, n.id) IS "
                "UNIQUE and CREATE INDEX idx_<cat>_updated_at FOR (n:<Cat>) ON (n.updatedAt). The ChangeLog "
                "label additionally carries a uniqueness constraint on (tenantId, id) and indexes on "
                "(tenantId, seq) and (tenantId, entityId).")
    d_add_table(doc, ["Element", "Shape"], [
        ["Node labels", "The ten taxonomy categories, plus :ChangeLog. Every node also carries its canonical "
                        "class IRI so the ontology type survives the label projection."],
        ["Node properties", "tenantId, id, canonicalType, displayName, status, updatedAt, plus the class's "
                            "observable properties written as live signal values by the runtime's persist()."],
        ["Relationships", "Ontology object properties, written as CURIEs resolved from graph/writer.py PREFIXES "
                          "(e.g. hvac:servesSpace, railway:feedsSection, solar:combinesString)."],
        ["Findings", "A :Finding node per emitted behaviour result, related to the asset it concerns and "
                     "carrying behaviourId, tier, severity and evidence."],
    ], col_widths=[3.5, 14])

    doc.add_heading("5.4 Event Bus (Redis Streams)", level=2)
    d_para(doc, "One stream per tenant, nxr:events:<tenant_id>, so a consumer reading one tenant's stream "
                "cannot see another's \u2014 the platform's isolation contract holds on the bus as well as in the "
                "database. Consumer groups are created per stream when an agent consumer needs one. A BusEvent "
                "mirrors the change-log event (event_id, tenant_id, entity_id, entity_type, label, action, "
                "actor, ts) so consumers get the same canonical facts without re-reading the log.")

    # 6. Service Layer
    doc.add_heading("6. Service Layer", level=1)

    doc.add_heading("6.1 GraphWriter (graph/writer.py)", level=2)
    d_bullet(doc, "The single mutation path: validate against the SHACL shape bundle, commit to Neo4j, append "
                  "the change-log event, publish the bus event. A validation failure returns before anything "
                  "is written.")
    d_bullet(doc, "Rel describes a relationship as (source, predicate, target); PREFIXES maps each pack's CURIE "
                  "prefix to its ontology namespace, so an unregistered prefix is rejected rather than silently "
                  "creating an untyped edge.")
    d_bullet(doc, "Template seeding resolves inter-template keys to real UUIDs, which is how a bundle can "
                  "declare relationships between entities that do not exist yet.")

    doc.add_heading("6.2 ChangeLog (changelog/service.py)", level=2)
    d_bullet(doc, "append() writes event_id (ULID, time-ordered and lexically sortable), tenant_id, entity_id, "
                  "entity_type, actor, action, field_changes, ts, prev_event_hash and wm_hash.")
    d_bullet(doc, "wm_hash is sha256 over the canonical content including prev_event_hash. Changing any stored "
                  "field of an old event breaks every wm_hash after it \u2014 that linkage is the tamper evidence.")
    d_bullet(doc, "The append takes a PostgreSQL advisory lock for the tenant for the length of its transaction, "
                  "so a tenant's chain cannot fork under concurrent writers. Tenants never block each other.")

    doc.add_heading("6.3 BehaviorRegistry (behaviors/registry.py)", level=2)
    d_bullet(doc, "A Behavior declares watches (signal keys), reads (graph inputs) and emits (the Finding it "
                  "produces), and implements evaluate(sample, query) \u2192 list[Finding].")
    d_bullet(doc, "The registry routes each TelemetrySample to every behaviour whose watches set contains the "
                  "sample's signal and returns the findings to its caller. It never touches Neo4j and never "
                  "writes \u2014 persistence is the caller's job, which is what lets the same registry run against "
                  "live telemetry, a simulated feed and a replayed history.")
    d_bullet(doc, "Tier(A/B/C) classifies the evidence: physics residual, statistical baseline, deterministic "
                  "rule. 48 behaviours ship across the platform and its packs.")

    doc.add_heading("6.4 MachineEngine and LiveTwin (twins/runtime.py)", level=2)
    d_bullet(doc, "load_specs() imports packs.<domain> for each configured pack and reads SPEC or SPECS; a "
                  "broken pack is skipped so one bad module never takes the runtime down.")
    d_bullet(doc, "Each tick: apply queued control actions, advance the pack's forward model, compute residuals, "
                  "derive component_health per declared subsystem, evaluate the registry, persist findings.")
    d_bullet(doc, "_FrameQuery is the read-only view a behaviour sees during evaluate(): the co-located signals "
                  "of the twin's latest frame keyed by short local name, so Tier-A cross-signal rules can read "
                  "siblings without a Neo4j round trip.")
    d_bullet(doc, "persist() writes live signal values onto the graph node properties, which is what makes the "
                  "3-D click-to-inspect panel show real values rather than seed defaults.")

    doc.add_heading("6.5 TwinCoordinator (twins/coordinator.py)", level=2)
    d_bullet(doc, "Acquires and renews a short Redis lease per tenant; only the lease holder ticks, evaluates "
                  "and persists.")
    d_bullet(doc, "Followers forward control actions to the owner through a per-tenant command queue and serve "
                  "the owner's published state.")
    d_bullet(doc, "On owner loss the lease expires in about eight seconds and another task adopts the published "
                  "state rather than restarting the simulation.")
    d_bullet(doc, "stats() feeds health.twin_runtime.owned \u2014 across the fleet each tenant must appear exactly "
                  "once. Without Redis every task believes it owns every twin, which is exactly the "
                  "duplicate-write behaviour the lease exists to prevent.")

    doc.add_heading("6.6 Historian (historian/core.py, schema.py)", level=2)
    d_bullet(doc, "ensure() is idempotent and returns a report rather than printing, so the provisioning tool "
                  "can show it and the health endpoint can summarise it.")
    d_bullet(doc, "Continuous-aggregate DDL and policy helpers cannot run inside a transaction, so a narrow "
                  "autocommit path executes exactly those statements and returns per-statement failures instead "
                  "of raising \u2014 one unsupported policy on an older TimescaleDB cannot abort the whole run.")
    d_bullet(doc, "Query selects the smallest sufficient source: raw for short ranges, then 1m, 1h or 1d.")

    doc.add_heading("6.7 IngestPipeline and DeviceRegistry (ingest/)", level=2)
    d_bullet(doc, "Authenticate the X-Device-Token by hash lookup, check enabled and expiry, apply the device's "
                  "asset prefix, normalise the sample and write it to the historian.")
    d_bullet(doc, "With NXR_INGEST_BEHAVIOURS=1 (default) the sample is then routed through the behaviour "
                  "registry so real data produces findings, diagnosis and change-log entries.")
    d_bullet(doc, "Device creation returns the plaintext token exactly once and stores only its hash \u2014 the "
                  "same posture as an SSH authorized_keys file or a GitHub personal access token.")

    doc.add_heading("6.8 ConnectorManager (connectors/manager.py)", level=2)
    d_bullet(doc, "Reconciles running workers against durable configuration on every boot, so a site's forty "
                  "connectors are still polling after a deploy.")
    d_bullet(doc, "Ownership uses the same lease pattern as the twin coordinator, and for stronger reasons: "
                  "three tasks polling one inverter triples the load on a small microcontroller, and three "
                  "masters on an RS-485 segment corrupt frames rather than merely duplicating them.")
    d_bullet(doc, "ConnectorConfig.redacted() is allow-listed rather than deny-listed, so a field added later "
                  "cannot leak a customer's PLC password by omission.")
    d_bullet(doc, "Protocol modules are imported lazily per protocol, so a missing optional driver disables one "
                  "protocol rather than the service.")

    doc.add_heading("6.9 DynamicsEngine (dynamics/engine.py)", level=2)
    d_bullet(doc, "Loads the tenant's topology from Neo4j once per run and builds inbound adjacency from the "
                  "relationship edges.")
    d_bullet(doc, "Topologically orders entities so producers update before consumers; cycles (zone \u2194 server) "
                  "are broken with a one-tick lag.")
    d_bullet(doc, "Each tick builds an EntityContext per entity, resolving upstream EntityStates by predicate "
                  "and the space it sits in, calls model.step() and collects signals.")
    d_bullet(doc, "Produced signals are emitted as TelemetrySamples into the same FindingsLoop the scripted feed "
                  "uses, so existing detection behaviours fire unchanged. Time is real-time with a speed "
                  "multiplier, so speed=60 makes one real second a simulated minute.")

    doc.add_heading("6.10 Copilot layer (copilot/)", level=2)
    d_bullet(doc, "Every agent takes plain dicts and lists \u2014 never a live twin object \u2014 so the same agent "
                  "runs against live state or a snapshot.")
    d_bullet(doc, "No agent raises: any API, network or parse failure degrades to a deterministic stub, and "
                  "agent_trace() records whether the call actually reached Claude so a stub answer is never "
                  "presented as real reasoning.")
    d_bullet(doc, "config.py holds the model policy (Sonnet 5 default, Opus configurable, per-agent thinking "
                  "effort); spend.py tracks cost, tokens, per-agent ranking and prompt-cache hit rate and "
                  "enforces the optional budget cap.")

    doc.add_heading("6.11 Agent engine and registry (agents/)", level=2)
    d_bullet(doc, "engine.py implements the slice of the LangGraph API the design uses \u2014 StateGraph, END, "
                  "CheckpointSaver \u2014 with the same names and shapes, so graph code can be swapped to the real "
                  "langgraph package by changing imports only. Nodes are callables (state) \u2192 dict merged onto "
                  "the running state.")
    d_bullet(doc, "registry.py unifies built-in bundles with those published by the Bundle Author into one API: "
                  "query(domain), load(bundle_id), publish(bundle). Because published bundles live in the "
                  "shared relational store, a bundle published by one task is immediately visible to the rest.")

    doc.add_heading("6.12 Storage (storage/core.py)", level=2)
    d_bullet(doc, "One interface, two backends: S3 when NXR_S3_BUCKET is set (including S3-compatible endpoints "
                  "such as MinIO), otherwise the local filesystem under NXR_DATA_DIR/blobs. Nothing above this "
                  "module knows which one is live.")
    d_bullet(doc, "Blobs are addressed by key, not path, which is what makes the same code correct on one task "
                  "and on twenty.")
    d_bullet(doc, "info() powers the health endpoint's blobs section and performs a real put, read-back and "
                  "delete probe.")

    doc.add_heading("6.13 Relational core (db/core.py)", level=2)
    d_bullet(doc, "A connection pool with configurable bounds (NXR_DB_POOL_MIN/MAX, connect timeout, sslmode, "
                  "application name), plus SQL translation so the same statements run on PostgreSQL and SQLite.")
    d_bullet(doc, "Advisory locks are exposed as a first-class primitive; the change log is the primary user.")
    d_bullet(doc, "ensure() is not strict by default and deliberately so: several agent graphs build their "
                  "checkpointer at module import time, and raising there when RDS is momentarily unreachable "
                  "would turn a database blip into \u2018the container will not start\u2019. The failure is logged "
                  "once and the DDL retried on the next call, so a database that comes up later self-provisions "
                  "without a restart.")

    # 7. Middleware and dependency pipeline
    doc.add_heading("7. Middleware and Dependency Pipeline", level=1)
    d_add_table(doc, ["Order", "Component", "File", "Responsibility"], [
        ["1 (outermost)", "CORSMiddleware", "FastAPI builtin",
         "Preflight handling; origins from NXR_CORS_ORIGINS, default * for local dev and the federated hub"],
        ["2", "AuthMiddleware", "server/auth.py",
         "Resolve X-API-Key to a key record (role + scope); exempt the SPA at /; honour NXR_REQUIRE_AUTH"],
        ["3 (app dependency)", "enforce_tenant_scope", "server/tenancy.py",
         "Runs after routing, so path_params is populated. Checks the tenant in query, path and body against "
         "the key's scope on every /api route"],
        ["\u2014", "Device token check", "server/ingest_routes.py",
         "Ingest routes authenticate X-Device-Token by hash and resolve exactly one tenant and one verb"],
        ["\u2014", "Neo4j-down exception handler", "server/main.py",
         "Converts ServiceUnavailable / SessionExpired / AuthError into a 503 with operator guidance"],
        ["\u2014", "Mounted sub-application", "server/threed_platform",
         "Mounted apps do not inherit app dependencies: the 3-D platform is covered by AuthMiddleware but "
         "not by enforce_tenant_scope; its job store is keyed by job id and holds no tenant column today"],
    ], col_widths=[2.5, 3.5, 3.5, 8])

    # 8. Key Sequences
    doc.add_heading("8. Key Sequences", level=1)

    doc.add_heading("8.1 Entity Write Sequence", level=2)
    d_bullet(doc, "Client \u2192 POST /api/v1/entities with tenant, canonical_type and properties.")
    d_bullet(doc, "AuthMiddleware resolves the key; enforce_tenant_scope confirms the tenant is in scope, else 403.")
    d_bullet(doc, "GraphWriter validates the payload against the SHACL shape bundle. A violation returns 422 with "
                  "the failing shape and focus node; nothing is written.")
    d_bullet(doc, "The node is committed to Neo4j under the (tenantId, id) uniqueness constraint for its "
                  "taxonomy-category label.")
    d_bullet(doc, "ChangeLog.append() takes the tenant advisory lock, computes wm_hash over the canonical content "
                  "including prev_event_hash, and extends the chain.")
    d_bullet(doc, "A BusEvent is published to nxr:events:<tenant_id>; connected SSE clients update.")

    doc.add_heading("8.2 Telemetry Ingest Sequence", level=2)
    d_bullet(doc, "Device \u2192 POST /api/v1/ingest/telemetry/bulk with X-Device-Token.")
    d_bullet(doc, "The token is hashed and looked up through the unique token index; enabled and expiry are "
                  "checked; the device's tenant and asset prefix are applied.")
    d_bullet(doc, "Samples are written to the measurements hypertable with ON CONFLICT DO NOTHING, so a replayed "
                  "buffer is a no-op.")
    d_bullet(doc, "Each sample is routed into the behaviour registry (unless NXR_INGEST_BEHAVIOURS=0).")
    d_bullet(doc, "Emitted findings follow the entity-write sequence above, so real telemetry produces the same "
                  "governed artefacts as any other mutation.")

    doc.add_heading("8.3 Machine-Twin Tick Sequence", level=2)
    d_bullet(doc, "The coordinator acquires or renews the tenant's Redis lease. Only the holder proceeds.")
    d_bullet(doc, "Queued commands (throttle, fault injection, start/stop) forwarded by follower tasks are drained "
                  "and applied.")
    d_bullet(doc, "The pack's forward model integrates one step; wear and thermal state carry forward.")
    d_bullet(doc, "Residuals are computed against the clean-machine baseline; component_health is derived per "
                  "declared subsystem; the pack's checks table produces per-sensor status.")
    d_bullet(doc, "The pack's behaviour registry is evaluated with a _FrameQuery over the latest frame; findings "
                  "are persisted once through the Graph Writer.")
    d_bullet(doc, "The authoritative state is published; follower tasks serve it verbatim.")

    doc.add_heading("8.4 Connector Poll Sequence", level=2)
    d_bullet(doc, "The manager reconciles running workers against the connectors table and acquires the "
                  "per-connector ownership lease.")
    d_bullet(doc, "The owning worker polls the device on its configured interval: Modbus register reads, an "
                  "OPC-UA subscription, or an MQTT/Sparkplug B subscription.")
    d_bullet(doc, "Raw values are decoded through the point map \u2014 scaling, word order, sign convention and unit "
                  "\u2014 into canonical signal names.")
    d_bullet(doc, "Decoded samples enter the same ingest pipeline as device telemetry, so a rule written against "
                  "the simulator fires unchanged against a real inverter.")
    d_bullet(doc, "Failures increment the connector's error counters and are reported by "
                  "GET /connectors/{id}/health without stopping the supervisor.")

    doc.add_heading("8.5 Copilot Request Sequence", level=2)
    d_bullet(doc, "Client \u2192 POST /api/v1/copilot/diagnosis with a tenant and optional machine and domain.")
    d_bullet(doc, "enforce_tenant_scope checks the tenant in the request body \u2014 the case that used to go "
                  "unchecked and allowed cross-tenant LLM reasoning.")
    d_bullet(doc, "Live diagnostics, findings and prediction are gathered from the runtime for that tenant.")
    d_bullet(doc, "The spend guard is consulted; over budget, the agent returns its deterministic stub.")
    d_bullet(doc, "Claude is called with a cached system prompt and the agent's thinking policy; the response is "
                  "parsed into the agent's declared output shape.")
    d_bullet(doc, "The response carries an ai block reporting backend (claude or stub), model, tokens and cost, "
                  "and the call is accounted to the per-agent spend ranking.")

    doc.add_heading("8.6 Startup and Health Sequence", level=2)
    d_bullet(doc, "The process resolves each store: NXR_DATABASE_URL or SQLite, NXR_S3_BUCKET or local blobs, "
                  "NXR_REDIS_URL or the in-memory bus, TimescaleDB or plain PostgreSQL.")
    d_bullet(doc, "Each required-flag is checked; a declared requirement that did not resolve fails the boot with "
                  "a one-line reason rather than degrading silently.")
    d_bullet(doc, "Four posture lines \u2014 [auth], [db], [blobs], [bus] \u2014 are printed to stdout and reach "
                  "CloudWatch.")
    d_bullet(doc, "The graph schema is applied idempotently (connectivity is verified first so an unreachable "
                  "Neo4j fails fast on one timeout rather than grinding through every constraint).")
    d_bullet(doc, "GET /api/v1/health then reports neo4j, database, blobs, bus (with scale_safe), historian (with "
                  "backend and scale_safe) and twin_runtime.owned, and an overall healthy or degraded status \u2014 "
                  "always with HTTP 200.")

    d_para(doc, "Design details are indicative and confirmed during implementation.")

    out_path = os.path.join(TECHDOCS_DIR, "NextXR_LLD.docx")
    doc.save(out_path)
    print(f"  Saved: {out_path}")


# ============================================================================
# BUILD DRD
# ============================================================================

def build_drd():
    TABLES = {
        "twins": {
            "domain": "Twin Registry",
            "purpose": "The twin registry. A twin is one isolated platform instance keyed by tenant_id; this "
                       "table holds only metadata (which twins exist, their name, domain template and seed "
                       "asset). The entities themselves live in Neo4j under the tenant_id.",
            "columns": [
                ("tenant_id", "TEXT", "NO", "-", "Primary key and the platform's isolation key"),
                ("name", "TEXT", "NO", "-", "Display name of the twin"),
                ("domain", "TEXT", "NO", "-", "Seeding template / machine-twin domain key"),
                ("description", "TEXT", "NO", "''", "Free-text description"),
                ("created_at", "TEXT", "NO", "-", "ISO-8601 UTC creation timestamp"),
                ("seed_asset_id", "TEXT", "YES", "NULL", "Node id of the asset the feed and runtime target"),
            ],
        },
        "scene_cache": {
            "domain": "Twin Registry",
            "purpose": "BIM scene graphs for the 3-D viewer. Previously one JSON file per tenant on the task's "
                       "data volume, which is per-task state: task A builds a scene, task B serves the next "
                       "request and has never seen it. Moving it here is what keeps the viewer correct above "
                       "one task.",
            "columns": [
                ("tenant_id", "TEXT", "NO", "-", "Primary key; one cached scene per twin"),
                ("scene", "JSONB", "NO", "-", "The scene graph: storeys, spaces, walls, equipment placements"),
                ("updated_at", "TIMESTAMPTZ", "NO", "CURRENT_TIMESTAMP", "Last rebuild time"),
            ],
        },
        "events": {
            "domain": "Governance",
            "purpose": "The governance change log: a per-tenant, tamper-evident hash chain of every graph "
                       "mutation. The prev_event_hash to wm_hash linkage means changing any stored field of an "
                       "old event breaks every hash after it. Appends serialise per tenant under a PostgreSQL "
                       "advisory lock so a chain cannot fork.",
            "columns": [
                ("seq", "BIGSERIAL", "NO", "auto", "Orders a tenant's chain; gaps from rolled-back transactions "
                                                   "are fine because verification walks the hash linkage"),
                ("event_id", "TEXT", "NO", "-", "ULID \u2014 time-ordered and lexically sortable; UNIQUE"),
                ("tenant_id", "TEXT", "NO", "-", "Isolation key; chains are per tenant"),
                ("entity_id", "TEXT", "NO", "-", "The node this event is about"),
                ("entity_type", "TEXT", "NO", "-", "Canonical class IRI"),
                ("actor", "TEXT", "NO", "-", "Who or what caused the mutation (user, agent, connector, runtime)"),
                ("action", "TEXT", "NO", "-", "create | update | delete"),
                ("field_changes", "JSONB", "NO", "-", "{ field: {old, new} } \u2014 queryable in place, not opaque text"),
                ("ts", "TEXT", "NO", "-", "ISO-8601 UTC event time"),
                ("prev_event_hash", "TEXT", "NO", "-", "wm_hash of the previous event in this tenant's chain"),
                ("wm_hash", "TEXT", "NO", "-", "sha256 over canonical content including prev_event_hash"),
            ],
        },
        "published_bundles": {
            "domain": "Agents & Capability",
            "purpose": "Capability bundles authored by the Bundle Author meta-agent. A bundle packages a "
                       "vertical: the ontology classes it provides, ready-made entity templates and the Tier-C "
                       "rules it ships. Persisting here is what lets a bundle published by one task load on "
                       "every other task.",
            "columns": [
                ("bundle_id", "TEXT", "NO", "-", "Primary key"),
                ("name", "TEXT", "NO", "-", "Display name of the bundle"),
                ("domains", "JSONB", "NO", "-", "Domain keywords the Composer matches on"),
                ("payload", "JSONB", "NO", "-", "Entity templates, relationship templates and rules"),
                ("tenant_id", "TEXT", "YES", "NULL", "Owning tenant; NULL for a platform-wide bundle"),
                ("created_at", "TIMESTAMPTZ", "NO", "CURRENT_TIMESTAMP", "Publication time"),
            ],
        },
        "checkpoints": {
            "domain": "Agents & Capability",
            "purpose": "Agent graph checkpoints. Because state lives here rather than in a task's memory, a run "
                       "interrupted for human approval on one task resumes on another \u2014 which is why sticky "
                       "sessions are not required.",
            "columns": [
                ("thread_id", "TEXT", "NO", "-", "Primary key; the agent session / conversation thread"),
                ("graph_name", "TEXT", "NO", "-", "Which graph this thread belongs to (twin, bundle, ops, ...)"),
                ("state", "JSONB", "NO", "-", "The merged graph state at the checkpoint"),
                ("resume_at", "TEXT", "YES", "NULL", "Node to resume from after human approval"),
                ("updated_at", "TIMESTAMPTZ", "NO", "CURRENT_TIMESTAMP", "Last write"),
            ],
        },
        "threed_jobs": {
            "domain": "3-D Reconstruction",
            "purpose": "Photo-to-GLB reconstruction job records. Previously job.json on the task's own disk, so "
                       "a browser polling job status through the load balancer got a 404 whenever the poll "
                       "landed on a task that had not run the job. The heavy outputs live in the blob store; "
                       "this is only the record.",
            "columns": [
                ("job_id", "TEXT", "NO", "-", "Primary key"),
                ("status", "TEXT", "NO", "-", "queued | running | completed | failed"),
                ("stage", "TEXT", "YES", "NULL", "Current pipeline stage"),
                ("filename", "TEXT", "YES", "NULL", "Original uploaded filename"),
                ("fields", "JSONB", "YES", "NULL", "Submitted form fields"),
                ("stages", "JSONB", "YES", "NULL", "Per-stage status and artifact keys"),
                ("state", "JSONB", "YES", "NULL", "Pipeline working state"),
                ("error", "TEXT", "YES", "NULL", "Failure detail, if any"),
                ("created", "DOUBLE PRECISION", "NO", "-", "Epoch creation time"),
                ("updated", "DOUBLE PRECISION", "NO", "-", "Epoch last-update time (indexed DESC)"),
            ],
        },
        "ingest_devices": {
            "domain": "Edge / Field",
            "purpose": "The identities that push telemetry. A device MUST NOT authenticate with a tenant API "
                       "key \u2014 those are read and write credentials for the whole twin, and a gateway in a "
                       "plant room can be unscrewed by an electrician. A device credential grants exactly one "
                       "verb in exactly one tenant and is revocable on its own. Only the token hash is stored; "
                       "the plaintext is returned once and is never recoverable.",
            "columns": [
                ("device_id", "TEXT", "NO", "-", "Primary key"),
                ("tenant_id", "TEXT", "NO", "-", "The single tenant this device may write to"),
                ("name", "TEXT", "NO", "''", "Operator-facing label"),
                ("token_hash", "TEXT", "NO", "-", "Hash of the bearer token; UNIQUE index (sensitive)"),
                ("asset_prefix", "TEXT", "NO", "''", "Narrows the device to assets under a prefix"),
                ("enabled", "INTEGER", "NO", "1", "Disable without deleting"),
                ("created_at", "TEXT", "NO", "-", "ISO-8601 UTC"),
                ("created_by", "TEXT", "NO", "''", "Key or user that registered the device"),
                ("expires_at", "TEXT", "YES", "NULL", "Optional credential expiry"),
                ("last_seen_at", "TEXT", "YES", "NULL", "Last accepted sample time"),
                ("last_seen_ip", "TEXT", "YES", "NULL", "Source address of the last sample (operational PII)"),
                ("samples_total", "DOUBLE PRECISION", "NO", "0", "Accepted sample counter"),
                ("rejected_total", "DOUBLE PRECISION", "NO", "0", "Rejected sample counter"),
            ],
        },
        "connectors": {
            "domain": "Edge / Field",
            "purpose": "Field-protocol connector configuration (Modbus / OPC-UA / MQTT). Durable configuration, "
                       "not runtime state: a site commissions dozens and expects them polling after a redeploy. "
                       "The whole config \u2014 including the point map \u2014 is one JSON column because a point map "
                       "is read, written, validated and versioned as a whole document and is never queried by "
                       "point.",
            "columns": [
                ("connector_id", "TEXT", "NO", "-", "Primary key"),
                ("tenant_id", "TEXT", "NO", "-", "Isolation key"),
                ("protocol", "TEXT", "NO", "-", "modbus_tcp | modbus_rtu | opcua | mqtt"),
                ("name", "TEXT", "NO", "''", "Operator-facing label"),
                ("enabled", "INTEGER", "NO", "1", "Whether the supervisor should run it"),
                ("config", "JSONB", "NO", "-", "Endpoint, poll interval, point map AND credentials "
                                               "(sensitive \u2014 allow-listed by redacted())"),
                ("created_at", "TEXT", "NO", "-", "ISO-8601 UTC"),
                ("updated_at", "TEXT", "NO", "-", "ISO-8601 UTC"),
            ],
        },
        "measurements": {
            "domain": "Historian",
            "purpose": "The telemetry historian's raw table \u2014 a TimescaleDB hypertable partitioned by time in "
                       "1-day chunks. Neo4j holds the CURRENT value of a property, which is the right job for a "
                       "graph and the wrong one for \u201c90 days of this sensor at 5-minute resolution\u201d.",
            "columns": [
                ("tenant_id", "TEXT", "NO", "-", "Isolation key; part of the primary key"),
                ("asset_id", "TEXT", "NO", "-", "The asset the measurement belongs to"),
                ("signal", "TEXT", "NO", "-", "Canonical signal name"),
                ("ts", "TIMESTAMPTZ", "NO", "-", "Measurement time; the hypertable partitioning column"),
                ("value", "DOUBLE PRECISION", "YES", "NULL", "The measured value"),
                ("unit", "TEXT", "NO", "''", "Unit of measure"),
                ("quality", "SMALLINT", "NO", "192", "OPC quality byte: 0 BAD, 64 UNCERTAIN, 192 GOOD"),
                ("source", "TEXT", "NO", "'api'", "api | connector | simulator | backfill"),
                ("received_at", "TIMESTAMPTZ", "NO", "now()", "Arrival time, distinct from measurement time"),
            ],
        },
        "measurements_1m / _1h / _1d": {
            "domain": "Historian",
            "purpose": "Continuous aggregates \u2014 incrementally-maintained rollups so a 90-day chart reads "
                       "roughly two thousand pre-computed rows rather than aggregating millions. Each is built "
                       "directly from raw rather than from the tier below: non-hierarchical costs more storage "
                       "and is unambiguously correct.",
            "columns": [
                ("tenant_id", "TEXT", "NO", "-", "Isolation key"),
                ("asset_id", "TEXT", "NO", "-", "Asset"),
                ("signal", "TEXT", "NO", "-", "Signal"),
                ("bucket", "TIMESTAMPTZ", "NO", "-", "time_bucket over ts at 1 minute / 1 hour / 1 day"),
                ("count", "BIGINT", "NO", "-", "Samples of at least UNCERTAIN quality with a non-null value"),
                ("sum_value", "DOUBLE PRECISION", "YES", "NULL", "Sum \u2014 stored instead of avg so a mean is "
                                                                 "correct at any resolution"),
                ("min_value / max_value", "DOUBLE PRECISION", "YES", "NULL", "Range within the bucket"),
                ("first_value / last_value", "DOUBLE PRECISION", "YES", "NULL", "Endpoints within the bucket"),
                ("bad_count", "BIGINT", "NO", "-", "BAD-quality samples, counted separately so a chart shows the "
                                                   "real signal while data quality stays visible"),
                ("unit", "TEXT", "YES", "NULL", "Unit carried through from raw"),
            ],
        },
    }

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.font.color.rgb = DARK_D

    # Title page
    for _ in range(6):
        doc.add_paragraph()
    tp = doc.add_paragraph(); tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = tp.add_run("NextXR Digital Twin Platform"); d_set_font(run, size=28, bold=True, color=NAVY_D)
    tp2 = doc.add_paragraph(); tp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = tp2.add_run("Data Requirements Document (DRD)"); d_set_font(run2, size=20, bold=True, color=NAVY_D)
    tp3 = doc.add_paragraph(); tp3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run3 = tp3.add_run(f"Version 1.0  |  {datetime.now().strftime('%B %Y')}  |  GoalCert Engineering")
    d_set_font(run3, size=12, color=GREY_D)
    doc.add_page_break()

    # 1. Introduction
    d_add_heading_styled(doc, "1. Introduction", 1)
    d_add_heading_styled(doc, "1.1 Purpose", 2)
    d_add_para_styled(doc, "This Data Requirements Document defines every persistent data entity in the NextXR "
                           "Digital Twin Platform. It is the authoritative reference for the relational schema, "
                           "the telemetry historian, the Neo4j graph model, column semantics, data types, "
                           "constraints and data-governance policies.")
    d_add_heading_styled(doc, "1.2 Scope", 2)
    d_add_para_styled(doc, "The document covers all four stores: 9 relational tables plus 3 continuous "
                           "aggregates on PostgreSQL 16 with TimescaleDB, the Neo4j property-graph model shaped "
                           "by the closed ten-category ontology taxonomy, the Redis Streams event bus, and the "
                           "S3 blob key space. Each store has exactly one job and none duplicates another's.")

    # 2. Data Architecture Overview
    d_add_heading_styled(doc, "2. Data Architecture Overview", 1)
    d_add_para_styled(doc, "NextXR is multi-tenant by construction. Every relational table carries a tenant_id "
                           "(or is itself keyed by it), every Neo4j node carries tenantId as half of its "
                           "uniqueness constraint, every Redis stream is named for its tenant, and every blob "
                           "key is written under the owning job. There is deliberately no cross-tenant foreign "
                           "key anywhere in the schema.")
    d_add_table_alt(doc, ["Store", "Technology", "Holds", "Why this store"], [
        ["Relational", "PostgreSQL 16 (SQLite fallback)",
         "Twin registry, change log, bundles, checkpoints, scene cache, 3-D jobs, devices, connectors",
         "Managed, backed-up, multi-writer; PITR matters because the change log is an audit record"],
        ["Historian", "TimescaleDB extension on the same instance",
         "measurements hypertable + 1m/1h/1d rollups",
         "An extension, not a second database: same pool, same backups, no new security group"],
        ["Graph", "Neo4j 5 (Aura in production)",
         "Entities, relationships, findings, current property values",
         "The twin's structure is a graph; traversal and topology are its native queries"],
        ["Event bus", "Redis 7 Streams",
         "One stream per tenant, plus twin ownership leases and command queues",
         "Live fan-out to every task and a lease primitive with a short, reliable expiry"],
        ["Blobs", "S3 (local filesystem fallback)",
         "Generated GLBs and 3-D job artifacts",
         "Key-addressed so any task serves any model; presigned URLs keep large files off API workers"],
    ], col_widths=[2.5, 3.5, 5, 6])
    doc.add_paragraph()

    d_add_heading_styled(doc, "2.1 Entity Relationship Diagram", 2)
    d_add_para_styled(doc, "The diagram below shows all relational tables grouped by domain, with primary keys "
                           "and the graph model's ten closed taxonomy categories:")
    add_diagram(doc, PNG_ERD, "Figure 1: NextXR Data Model \u2014 9 Relational Tables, 3 Rollups and the Graph Model")

    d_add_para_styled(doc, "The relational schema is organised into six domains:")
    domains = [
        ("Twin Registry", "twins, scene_cache", "Which twins exist and the 3-D scene each renders"),
        ("Governance", "events", "The per-tenant tamper-evident change log"),
        ("Agents & Capability", "published_bundles, checkpoints", "Agent-authored bundles and resumable graph state"),
        ("3-D Reconstruction", "threed_jobs", "Photo-to-GLB job records (artifacts live in S3)"),
        ("Edge / Field", "ingest_devices, connectors", "Telemetry device identities and field-protocol configuration"),
        ("Historian", "measurements + measurements_1m/_1h/_1d", "Raw measurements and their incremental rollups"),
    ]
    d_add_table_alt(doc, ["Domain", "Tables", "Purpose"], domains, col_widths=[3.5, 6, 7.5])
    doc.add_paragraph()

    # 3. Data Dictionary
    doc.add_page_break()
    d_add_heading_styled(doc, "3. Data Dictionary", 1)
    d_add_para_styled(doc, "Complete column-level specification for every table, grouped by domain. Types are "
                           "shown for the PostgreSQL dialect; db/schema.py renders the SQLite equivalents "
                           "(JSONB \u2192 TEXT, TIMESTAMPTZ \u2192 TEXT, DOUBLE PRECISION \u2192 REAL, "
                           "BIGSERIAL \u2192 INTEGER AUTOINCREMENT) from a single fragment table.")

    current_domain = None
    domain_idx = 0
    for tname, tinfo in TABLES.items():
        if tinfo["domain"] != current_domain:
            current_domain = tinfo["domain"]
            domain_idx += 1
            d_add_heading_styled(doc, f"3.{domain_idx} {current_domain}", 2)

        d_add_heading_styled(doc, tname, 3)
        d_add_para_styled(doc, tinfo["purpose"], size=9, color=GREY_D, space_after=4)
        d_add_table_alt(doc, ["Column", "Type", "Nullable", "Default", "Description"], tinfo["columns"],
                        col_widths=[3.5, 2.8, 1.5, 2.2, 7])
        doc.add_paragraph()

    # 4. Graph data model
    doc.add_page_break()
    d_add_heading_styled(doc, "4. Graph Data Model (Neo4j)", 1)
    d_add_para_styled(doc, "The graph is shaped by the ontology, not by an ORM. graph/schema.py parses the "
                           "Turtle files, discovers the taxonomy categories and applies the constraints and "
                           "indexes, so the graph schema always matches the ontology it was derived from.")

    d_add_heading_styled(doc, "4.1 The Closed Taxonomy", 2)
    d_add_para_styled(doc, "The ten categories are fixed at platform version v3. Every entity class must declare "
                           "exactly one via nxr:taxonomyCategory and may never introduce an eleventh \u2014 the "
                           "machine-checkable form of \u201cdomain packs extend within the taxonomy, never beyond "
                           "it\u201d. The rule is enforced by nxr:TaxonomyClosureShape against the ontology itself, "
                           "which is why it is applied by SchemaService.validate_governance() rather than on "
                           "every tenant write.")
    d_add_table_alt(doc, ["Category (node label)", "Definition", "Representative classes"],
                    TAXONOMY_CATEGORIES, col_widths=[3.2, 7.3, 6.5])
    doc.add_paragraph()
    d_add_para_styled(doc, "Structural classes \u2014 StateMachine, State, Transition, QuantitativeResult and "
                           "TaxonomyCategory itself \u2014 are marked nxr:isStructural true and are exempt from the "
                           "category requirement, because they are internal plumbing rather than top-level "
                           "entities.")

    d_add_heading_styled(doc, "4.2 Node and Relationship Shape", 2)
    d_add_table_alt(doc, ["Element", "Definition"], [
        ["Uniqueness constraint", "One per category: FOR (n:<Category>) REQUIRE (n.tenantId, n.id) IS UNIQUE. "
                                  "Tenant isolation is therefore a property of the key, not of a query filter."],
        ["Index", "One per category on n.updatedAt, serving feed and change-detection queries."],
        ["Core node properties", "tenantId, id, canonicalType (the OWL class IRI), displayName, status, "
                                 "updatedAt."],
        ["Live signal properties", "The class's observable properties, written onto the node by the machine-twin "
                                   "runtime's persist() so the 3-D inspector shows measured values rather than "
                                   "seed defaults."],
        ["Relationships", "Ontology object properties written as CURIEs and resolved through graph/writer.py "
                          "PREFIXES \u2014 an unregistered prefix is rejected rather than creating an untyped edge."],
        [":Finding nodes", "One per emitted behaviour result, carrying behaviourId, tier (A/B/C), severity and "
                           "evidence, related to the asset it concerns."],
        [":ChangeLog label", "The eleventh label: unique on (tenantId, id), indexed on (tenantId, seq) for chain "
                             "walks and on (tenantId, entityId) for per-entity history."],
    ], col_widths=[4, 13])

    d_add_heading_styled(doc, "4.3 Ontology Layers", 2)
    d_add_table_alt(doc, ["Layer", "Artefacts", "Governs"], [
        ["1 \u2014 Imported upper ontologies", "imports/bfo.ttl, imports/sosa.ttl",
         "Continuant/occurrent distinctions and the sensor/observation vocabulary"],
        ["2 \u2014 NextXR core", "platform/nxr-classes.ttl, nxr-properties.ttl, nxr-units.ttl",
         "Core entity classes, observable and object properties, units"],
        ["3 \u2014 Governance", "platform/nxr-taxonomy.ttl, nxr-governance.ttl, nxr-base-shape.ttl, "
                                "nxr-shapes.ttl, nxr-behavior-bindings.ttl",
         "The closed category set, its enforcement shape, base entity shapes and behaviour bindings"],
        ["4 \u2014 Domain packs", "packs/<domain>/<domain>-classes.ttl and -shapes.ttl",
         "Per-domain classes, observable properties, failure modes and value constraints"],
    ], col_widths=[4, 7, 6])

    # 5. Data Retention and Privacy
    doc.add_page_break()
    d_add_heading_styled(doc, "5. Data Retention, Integrity & Privacy", 1)

    d_add_heading_styled(doc, "5.1 Retention Policies", 2)
    d_add_para_styled(doc, "Retention is declared once as a policy rather than maintained as cron jobs. Every "
                           "tier is overridable per deployment for a regulatory retention floor.")
    d_add_table_alt(doc, ["Data", "Retention", "Override", "Rationale"], [
        ["measurements (raw)", "30 days", "NXR_HISTORIAN_RETENTION_RAW",
         "The expensive tier and the least useful once an incident is closed"],
        ["measurements_1m", "400 days", "NXR_HISTORIAN_RETENTION_1M", "Just over a year of minute resolution"],
        ["measurements_1h", "1,095 days", "NXR_HISTORIAN_RETENTION_1H", "Three years of hourly resolution"],
        ["measurements_1d", "Indefinite", "NXR_HISTORIAN_RETENTION_1D",
         "Cheap enough to keep forever, which is what makes year-over-year comparison possible"],
        ["Compression", "Chunks compressed after 7 days", "COMPRESS_AFTER",
         "Longer than the window still receiving writes, so late edge replay is not penalised"],
        ["events (change log)", "Retained indefinitely", "\u2014",
         "An audit record; RDS point-in-time recovery is the point of choosing PostgreSQL here"],
        ["S3 3-D artifacts", "Lifecycle rule expiring artifacts after 30\u201390 days", "Bucket lifecycle policy",
         "Intermediate stage images are debugging aids, not product data"],
        ["threed_jobs", "Retained with the job record", "\u2014",
         "Small metadata; keeps a completed job addressable after its artifacts expire"],
    ], col_widths=[3.2, 3.5, 4, 6.3])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.2 Integrity and Idempotency", 2)
    d_add_table_alt(doc, ["Mechanism", "Where", "Guarantee"], [
        ["Hash chain", "events.prev_event_hash \u2192 events.wm_hash",
         "Tamper evidence: altering any stored field of an old event breaks every hash after it"],
        ["Advisory lock", "ChangeLog.append(), per tenant",
         "A tenant's chain cannot fork under concurrent writers; tenants never block each other"],
        ["Composite primary key", "measurements (tenant_id, asset_id, signal, ts)",
         "A replayed batch collides and is skipped, so edge store-and-forward cannot double-count"],
        ["Uniqueness constraint", "Neo4j (tenantId, id) per category label",
         "No duplicate entity within a tenant; cross-tenant collision is impossible by construction"],
        ["Unique token index", "ingest_devices.token_hash",
         "Authentication by hash is an index lookup, not a full table scan"],
        ["Ownership lease", "Redis, per tenant (twins) and per connector",
         "Exactly one writer of live findings and exactly one poller of a device"],
        ["SHACL validation", "GraphWriter, before every commit",
         "A shape violation returns 422 and nothing is written"],
        ["Taxonomy closure", "nxr:TaxonomyClosureShape over the T-Box",
         "A pack cannot introduce an eleventh top-level category"],
    ], col_widths=[3.5, 5, 8.5])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.3 Deletion Semantics", 2)
    d_add_para_styled(doc, "There are deliberately no cross-tenant foreign keys, so deletion is scoped rather "
                           "than cascaded through referential actions:")
    d_add_table_alt(doc, ["Action", "Effect"], [
        ["DELETE /api/v1/twins/{tenant}", "Removes the registry row, the tenant's Neo4j entities and the "
                                          "tenant's scene_cache row. The change log is retained as an audit "
                                          "record of what existed."],
        ["DELETE /api/v1/entities/{node_id}", "Removes the node and its relationships, and appends a delete "
                                              "event to the chain. History is never rewritten."],
        ["DELETE /api/v1/ingest/devices/{id}", "Revokes the credential immediately. Measurements already "
                                               "written are retained under the historian's retention policy."],
        ["DELETE /api/v1/connectors/{id}", "Stops the worker and removes the configuration, including the "
                                           "stored credentials."],
        ["Historian retention", "Raw and rollup rows are dropped by policy per tier; the drop is a "
                                "chunk-level operation, not a row-by-row delete."],
        ["S3 lifecycle", "3-D artifacts expire on the bucket lifecycle rule; the job record remains."],
    ], col_widths=[5.5, 11.5])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.4 Sensitive Data and PII", 2)
    d_add_para_styled(doc, "Columns requiring special handling. The platform's primary data is industrial "
                           "telemetry rather than personal data, so the sensitive set is small and mostly "
                           "consists of credentials.")
    pii = [
        ("ingest_devices.token_hash", "Credential hash",
         "Only the hash is stored; the plaintext is returned once at creation and is never recoverable"),
        ("ingest_devices.last_seen_ip", "Operational PII",
         "Source address of the last accepted sample; useful for revocation decisions, retained with the device"),
        ("ingest_devices.created_by", "Operator identity", "Which key or user registered the device"),
        ("connectors.config", "Customer credentials",
         "Contains PLC / OPC-UA / MQTT passwords and certificates. Allow-listed by ConnectorConfig.redacted() "
         "on every API response, so a field added later cannot leak by omission"),
        ("NXR_API_KEYS", "Platform credentials",
         "Never in the image or in the task definition's environment block \u2014 Secrets Manager only"),
        ("events.actor", "Operator identity",
         "Who or what caused a mutation; part of the audit record and retained indefinitely"),
        ("events.field_changes", "Business data",
         "Field-level before/after values; may carry customer-named assets and locations"),
        ("Twin entity properties", "Customer facility data",
         "Asset names, locations and capacities describe a customer site; tenant-scoped by key, never joined "
         "across tenants"),
    ]
    d_add_table_alt(doc, ["Column / item", "Classification", "Handling"], pii, col_widths=[4.5, 3.5, 9])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.5 Encryption and Transport", 2)
    d_add_bullet_styled(doc, "At rest: RDS storage encryption (KMS), S3 default encryption (SSE-S3), Neo4j Aura "
                             "managed encryption.")
    d_add_bullet_styled(doc, "In transit: sslmode=require for RDS \u2014 without it psycopg2 will quietly accept an "
                             "unencrypted connection; neo4j+s:// for Aura; TLS for ElastiCache; TLS 1.3 on the "
                             "MQTT edge gateway.")
    d_add_bullet_styled(doc, "S3 buckets have Block Public Access on; presigned URLs still work, and object "
                             "access is granted to the ECS task role rather than to access keys in the "
                             "environment.")

    # Footer
    doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("NextXR Digital Twin Platform -- Data Requirements Document v1.0")
    d_set_font(run, size=9, color=GREY_D)

    out_path = os.path.join(TECHDOCS_DIR, "NextXR_DRD.docx")
    doc.save(out_path)
    print(f"  Saved: {out_path}")


# ============================================================================
# BUILD AWS ARCHITECTURE
# ============================================================================

def build_aws():
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.font.color.rgb = DARK_D

    # Title page
    for _ in range(6):
        doc.add_paragraph()
    tp = doc.add_paragraph(); tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = tp.add_run("NextXR Digital Twin Platform"); d_set_font(run, size=28, bold=True, color=NAVY_D)
    tp2 = doc.add_paragraph(); tp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = tp2.add_run("AWS Architecture Document"); d_set_font(run2, size=20, bold=True, color=NAVY_D)
    tp3 = doc.add_paragraph(); tp3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run3 = tp3.add_run(f"Version 1.0  |  {datetime.now().strftime('%B %Y')}  |  GoalCert Engineering")
    d_set_font(run3, size=12, color=GREY_D)
    doc.add_page_break()

    # 1. Introduction
    d_add_heading_styled(doc, "1. Introduction", 1)
    d_add_para_styled(doc, "This document defines the AWS cloud architecture for the NextXR Digital Twin "
                           "Platform. NextXR is an ontology-driven industrial digital-twin platform built on "
                           "FastAPI that lets organisations stand up live, semantically-governed twins of "
                           "facilities and machines, drive them from real field telemetry, reason about their "
                           "condition, and act on them through an embedded AI copilot.")
    d_add_para_styled(doc, "The system is a single-container monolith: one FastAPI process serves 136 REST "
                           "endpoints across 12 routers and the built React UI, with the GPU reconstruction "
                           "step offloaded to an external RunPod endpoint. The image is deliberately "
                           "Fargate-ready \u2014 non-root, $PORT-driven, healthchecked, and serving its own "
                           "frontend, so a first deploy needs no S3 or CloudFront for static hosting.")
    d_add_para_styled(doc, "Section numbers in the deployment runbook (R6, AWS_DEPLOYMENT.md) are stable and "
                           "referenced by the Dockerfile, .env.example and the auth notes; this document "
                           "mirrors that structure.")

    # 2. Architecture Overview
    d_add_heading_styled(doc, "2. Architecture Overview", 1)
    d_add_para_styled(doc, "Route 53 fronts an optional CloudFront distribution and an Application Load "
                           "Balancer, which targets an ECS Fargate service running N copies of the "
                           "nextxr-twin container on port 8080. The task is stateless; all durable state lives "
                           "in four shared stores.")

    d_add_heading_styled(doc, "2.1 Infrastructure Architecture", 2)
    add_diagram(doc, PNG_INFRA, "Figure 1: NextXR AWS Infrastructure Architecture")

    d_add_heading_styled(doc, "2.2 Core Components", 2)
    components = [
        ("ECS Fargate", "The nextxr-twin container: 136 API endpoints, the React UI, the 1 Hz machine-twin "
                        "runtime, the connector supervisor and the embedded copilot. Desired count 2+."),
        ("RDS PostgreSQL 16", "Records and the telemetry historian: 8 platform tables plus the measurements "
                              "hypertable and its three rollups. Multi-AZ; db.t4g.medium + gp3 is ample."),
        ("ElastiCache Redis 7", "The live event bus (one stream per tenant), twin ownership leases and "
                                "per-tenant command queues."),
        ("S3", "Blobs: generated GLBs and 3-D job artifacts, addressed by key so any task serves any model."),
        ("Neo4j Aura", "The twin's graph: entities, relationships and findings. Managed outside AWS (or "
                       "self-hosted on EC2 with an EBS volume)."),
        ("ALB", "HTTPS termination and health checks on GET /api/v1/health. Idle timeout raised for SSE."),
        ("RDS Proxy", "Optional but recommended: multiplexes the fleet's connections, survives failover "
                      "without the fleet reconnecting in lockstep, and can hold IAM credentials."),
        ("CloudWatch", "Container logs (the four posture lines reach stdout) and alarms on the health "
                       "payload's degraded status."),
        ("RunPod (external)", "Serverless GPU running TRELLIS for photo-to-3-D reconstruction. Deliberately "
                              "off AWS \u2014 ECS-GPU / g5 instances are expensive for a bursty workload."),
    ]
    d_add_table_alt(doc, ["Component", "Description"], components, col_widths=[4, 13])
    doc.add_paragraph()

    d_add_heading_styled(doc, "2.3 Component Architecture", 2)
    add_diagram(doc, PNG_COMPONENT, "Figure 2: NextXR Component Architecture")

    d_add_heading_styled(doc, "2.4 Request Flow", 2)
    d_add_para_styled(doc, "Client \u2192 ALB (HTTPS/443) \u2192 ECS Fargate (:8080) \u2192 Neo4j / RDS / ElastiCache / "
                           "S3. SSE connections (/api/v1/bus/stream) are long-lived HTTP streams held open by "
                           "the ALB with an idle timeout of 300 seconds. Blob downloads bypass the API: "
                           "GET /api/jobs/{id}/result returns a 302 to a presigned S3 URL, so a 20 MB GLB "
                           "streams from S3 rather than occupying an API worker.")

    d_add_heading_styled(doc, "2.5 Data Flow", 2)
    add_diagram(doc, PNG_DATAFLOW, "Figure 3: NextXR Telemetry-to-Finding Data Flow")

    d_add_heading_styled(doc, "2.6 Build and Image", 2)
    d_add_para_styled(doc, "docker build -t nextxr-twin . produces a multi-stage image:")
    d_add_bullet_styled(doc, "Stage 1 \u2014 node:20-slim builds frontend/dist (Vite, Module Federation remote).")
    d_add_bullet_styled(doc, "Stage 2 \u2014 python:3.12-slim installs requirements.txt, copies nextxr-ontology/ and "
                             "the built dist, runs as non-root uid/gid 10001, exposes :8080 and healthchecks "
                             "GET /api/v1/health.")
    d_add_para_styled(doc, "The container serves the UI at /, so the first deploy needs no static hosting. Add "
                           "CloudFront only when federating the UI into the GoalCert Hub \u2014 then "
                           "VITE_REMOTE_BASE must be set at build time to the CloudFront origin, or "
                           "hub-embedded assets 404.")

    # 3. Network Architecture
    d_add_heading_styled(doc, "3. Network Architecture", 1)
    d_add_heading_styled(doc, "3.1 VPC Layout", 2)
    d_add_para_styled(doc, "Single VPC (10.0.0.0/16) across two availability zones:")
    subnets = [
        ("Public Subnet A", "10.0.1.0/24", "eu-west-1a", "ALB, NAT gateway"),
        ("Public Subnet B", "10.0.2.0/24", "eu-west-1b", "ALB (multi-AZ)"),
        ("Private Subnet A", "10.0.10.0/24", "eu-west-1a", "ECS Fargate tasks, RDS primary, RDS Proxy"),
        ("Private Subnet B", "10.0.20.0/24", "eu-west-1b", "ECS Fargate tasks, RDS standby, ElastiCache"),
    ]
    d_add_table_alt(doc, ["Subnet", "CIDR", "AZ", "Resources"], subnets, col_widths=[3.5, 3, 3, 7.5])
    doc.add_paragraph()
    d_add_para_styled(doc, "RDS (5432) and ElastiCache (6379) live in the private/isolated subnets and are "
                           "never publicly addressable. Add an S3 gateway VPC endpoint so blob traffic skips "
                           "the NAT gateway's per-GB charge \u2014 with GLB payloads this is a material saving, "
                           "not a rounding error.")

    d_add_heading_styled(doc, "3.2 Security Groups", 2)
    sgs = [
        ("sg-alb", "ALB", "Inbound: 443 from 0.0.0.0/0; Outbound: 8080 to sg-ecs"),
        ("sg-ecs", "ECS Fargate tasks", "Inbound: 8080 from sg-alb; Outbound: 5432 to sg-rds, 6379 to "
                                        "sg-redis, 443 to Neo4j Aura / Anthropic / RunPod, plus the field "
                                        "protocol ports a deployment actually polls"),
        ("sg-rds", "RDS PostgreSQL 16", "Inbound: 5432 from sg-ecs and sg-rdsproxy; Outbound: none"),
        ("sg-redis", "ElastiCache Redis 7", "Inbound: 6379 from sg-ecs; Outbound: none"),
        ("sg-rdsproxy", "RDS Proxy", "Inbound: 5432 from sg-ecs; Outbound: 5432 to sg-rds"),
    ]
    d_add_table_alt(doc, ["Security Group", "Attached To", "Rules"], sgs, col_widths=[3, 3.5, 10.5])
    doc.add_paragraph()

    # 4. Service Configuration
    d_add_heading_styled(doc, "4. Service Configuration", 1)
    d_add_heading_styled(doc, "4.1 ECS Task Definition", 2)
    task_config = [
        ("CPU", "512 (0.5 vCPU)", "1024 (1 vCPU)"),
        ("Memory", "1024 MB", "2048 MB"),
        ("Container Port", "8080", "8080"),
        ("Container User", "uid/gid 10001 (baked into the image)", "uid/gid 10001"),
        ("Desired Count", "1", "2+ across two AZs"),
        ("Health Check", "GET /api/v1/health", "GET /api/v1/health"),
        ("Volumes", "None required", "None required (EFS optional \u2014 \u00a75.5)"),
        ("Logging", "awslogs \u2192 CloudWatch, PYTHONUNBUFFERED=1", "awslogs \u2192 CloudWatch"),
    ]
    d_add_table_alt(doc, ["Parameter", "Dev/Staging", "Production"], task_config, col_widths=[4, 6, 7])
    doc.add_paragraph()
    d_add_para_styled(doc, "Two distinct IAM identities: the execution role pulls the image and injects "
                           "secrets; the task role is the application's own identity and carries S3 object "
                           "access on the blob bucket.")

    d_add_heading_styled(doc, "4.2 Environment Variables", 2)
    d_add_para_styled(doc, "Plain environment (task definition environment: block):")
    env_vars = [
        ("PORT", "8080", "ALB target port"),
        ("NEO4J_URI", "neo4j+s://<aura-id>.databases.neo4j.io", "Managed Neo4j"),
        ("NEO4J_USER", "neo4j", "Password is a secret"),
        ("NXR_S3_BUCKET", "nextxr-twin-blobs", "Blob store; access via the task role"),
        ("NXR_S3_PREFIX", "prod", "Optional \u2014 share one bucket across environments"),
        ("NXR_REDIS_URL", "redis://...cache.amazonaws.com:6379/0", "Event bus and ownership leases"),
        ("NXR_REQUIRE_DB", "1", "Refuse to start on the SQLite fallback"),
        ("NXR_REQUIRE_S3", "1", "Refuse to start on local-disk blobs"),
        ("NXR_REQUIRE_REDIS", "1", "Refuse to start on the in-memory bus"),
        ("NXR_REQUIRE_TIMESCALE", "1", "Refuse to start on a plain (non-hypertable) historian"),
        ("NXR_REQUIRE_AUTH", "1", "Reject keyless /api calls even before keys load"),
        ("NXR_DB_POOL_MAX", "10", "Per-task ceiling; server-side total is tasks x this"),
        ("NXR_DB_SSLMODE", "require", "Unless already in the URL"),
        ("NXR_CORS_ORIGINS", "https://app...,https://hub...", "Explicit allow-list"),
        ("NXR_CLAUDE_MODEL", "claude-sonnet-5", "Copilot model override"),
        ("NXR_INGEST_BEHAVIOURS", "1", "Drive the behaviour registry from ingested telemetry"),
    ]
    d_add_table_alt(doc, ["Variable", "Value", "Notes"], env_vars, col_widths=[4.5, 6, 6.5])
    doc.add_paragraph()

    d_add_heading_styled(doc, "4.3 Auto-Scaling", 2)
    scaling = [
        ("Minimum Tasks", "1", "2"),
        ("Maximum Tasks", "2", "6"),
        ("Scale-out metric", "CPU > 70%", "CPU > 60% OR RequestCount > 1000/min"),
        ("Scale-in metric", "CPU < 30%", "CPU < 25%"),
        ("Sticky sessions", "Not required", "Not required \u2014 agent checkpoints are in PostgreSQL"),
        ("Simulation load", "Single task owns every twin",
         "Ownership is per tenant, so simulation spreads across the fleet"),
    ]
    d_add_table_alt(doc, ["Parameter", "Dev/Staging", "Production"], scaling, col_widths=[4, 5, 8])
    doc.add_paragraph()
    d_add_para_styled(doc, "Multi-task is safe because the task is stateless and live physics has exactly one "
                           "owner per tenant (a short Redis lease). That was the single biggest architectural "
                           "constraint in the earlier SQLite/EFS design and it is gone.")

    # 5. Data Tier
    doc.add_page_break()
    d_add_heading_styled(doc, "5. Data Tier", 1)
    d_add_heading_styled(doc, "5.1 RDS PostgreSQL 16", 2)
    rds = [
        ("Engine", "PostgreSQL 16.x, plain RDS (not Aurora): cheaper at this size, flat I/O pricing, and the "
                   "same engine runs on-prem for a customer who needs it"),
        ("Instance (Dev)", "db.t4g.small (2 vCPU, 2 GB)"),
        ("Instance (Prod)", "db.t4g.medium (2 vCPU, 4 GB) + gp3 \u2014 ample for metadata and an audit log"),
        ("Storage", "20 GB gp3 (dev), 100 GB gp3 (prod), autoscaling enabled"),
        ("Multi-AZ", "No (dev), Yes (prod)"),
        ("Backups", "Automated with PITR \u2014 the change log is an audit record, which is the point"),
        ("Encryption", "AES-256 via KMS at rest; sslmode=require in transit"),
        ("Extensions", "timescaledb (required in production), pgvector and postgis (optional, "
                       "python -m db.schema --extensions)"),
        ("Schema", "8 platform tables + measurements hypertable + 3 continuous aggregates (see R4)"),
        ("Pooling", "Per-task pool 1\u201310; RDS Proxy in front, sized against the instance's max_connections "
                    "with headroom for migrations and bastion sessions"),
    ]
    d_add_table_alt(doc, ["Parameter", "Value"], rds, col_widths=[4, 13])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.2 TimescaleDB (the telemetry historian)", 2)
    d_add_para_styled(doc, "The historian is an extension on the RDS instance above, not a second database. "
                           "That property matters more than raw benchmark numbers: a dedicated time-series "
                           "database is faster in isolation and strictly worse to operate, and an architecture "
                           "review will ask about operations.")
    ts = [
        ("Hypertable", "measurements, 1-day chunks, partitioned on ts"),
        ("Continuous aggregates", "measurements_1m, _1h, _1d \u2014 count and sum rather than avg"),
        ("Compression", "After 7 days; segment by (tenant_id, asset_id, signal), order by ts DESC \u2014 10\u201320x"),
        ("Retention", "raw 30 d, 1m 400 d, 1h 1095 d, 1d indefinite; all overridable per deployment"),
        ("Storage impact", "At 5,000 signals @ 1 Hz, compression is the difference between ~4 GB/day and "
                           "~50 GB/day"),
        ("Provisioning", "python -m tools.historian_provision --extension (needs rds_superuser); --check "
                         "reports and changes nothing"),
        ("Guard", "NXR_REQUIRE_TIMESCALE=1 \u2014 without the extension the historian works, never compresses "
                  "and never expires anything, so the disk fills silently"),
    ]
    d_add_table_alt(doc, ["Parameter", "Value"], ts, col_widths=[4, 13])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.3 ElastiCache Redis 7", 2)
    redis = [
        ("Engine", "Redis 7.x (OSS), Multi-AZ"),
        ("Node Type", "cache.t4g.micro (dev), cache.t4g.small (prod)"),
        ("Use Cases", "Per-tenant event streams (nxr:events:<tenant_id>), twin ownership leases, per-tenant "
                      "command queues, connector ownership leases"),
        ("Encryption", "TLS in transit"),
        ("Guard", "NXR_REQUIRE_REDIS=1 \u2014 without Redis every task believes it owns every twin, which "
                  "duplicates every finding and every change-log event"),
    ]
    d_add_table_alt(doc, ["Parameter", "Value"], redis, col_widths=[4, 13])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.4 S3 Bucket", 2)
    s3 = [
        ("Bucket", "nextxr-twin-blobs (NXR_S3_PREFIX separates environments)"),
        ("Key space", "<prefix>/threed/jobs/<job_id>/input/<original> and "
                      "<prefix>/threed/jobs/<job_id>/artifacts/<stage>/model.glb"),
        ("Access", "ECS task role: s3:GetObject, s3:PutObject, s3:DeleteObject on arn:aws:s3:::<bucket>/* and "
                   "s3:ListBucket on the bucket. No access keys in the environment"),
        ("Public access", "Block Public Access ON \u2014 presigned URLs still work"),
        ("Encryption", "Default SSE-S3 (AES-256); versioning optional"),
        ("Lifecycle", "Expire threed/jobs/*/artifacts/ after 30\u201390 days \u2014 intermediate stage images are "
                      "debugging aids, not product data"),
        ("Health probe", "GET /api/v1/health puts, reads back and deletes a probe object, because a bucket you "
                         "can list but not write to is the usual IAM mistake and reads fine until the first "
                         "upload"),
        ("Guard", "NXR_REQUIRE_S3=1 \u2014 without it, the task that ran a reconstruction has the GLB and every "
                  "other task 404s a model the user just watched being generated"),
    ]
    d_add_table_alt(doc, ["Parameter", "Value"], s3, col_widths=[4, 13])
    doc.add_paragraph()

    d_add_heading_styled(doc, "5.5 Neo4j and optional EFS", 2)
    d_add_bullet_styled(doc, "Neo4j Aura is recommended (least operations): set NEO4J_URI to the neo4j+s:// "
                             "endpoint and put NEO4J_PASSWORD in Secrets Manager. Neo4j on EC2/ECS with an EBS "
                             "volume is the alternative \u2014 more control, more operations.")
    d_add_bullet_styled(doc, "Rotate off the local-development default password baked into "
                             "graph/connection.py and docker-compose.yml.")
    d_add_bullet_styled(doc, "Reads degrade to empty and writes return 503 while Neo4j is unreachable. A "
                             "deploy is not done while /api/v1/health reports degraded.")
    d_add_bullet_styled(doc, "EFS is optional once S3 is configured \u2014 nothing durable is left on the volume. "
                             "Keep it only to let 3-D scratch survive a mid-job task replacement (the job does "
                             "not resume anyway). If mounted, the access point must use uid/gid 10001 or the "
                             "first write to /data fails.")

    # 6. Security
    doc.add_page_break()
    d_add_heading_styled(doc, "6. Security", 1)
    d_add_heading_styled(doc, "6.1 IAM Roles", 2)
    iam = [
        ("ECS Task Execution Role", "ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer, "
                                    "logs:CreateLogStream, logs:PutLogEvents, secretsmanager:GetSecretValue, "
                                    "ssm:GetParameters"),
        ("ECS Task Role", "s3:GetObject, s3:PutObject, s3:DeleteObject on arn:aws:s3:::<bucket>/*; "
                          "s3:ListBucket on the bucket. Optionally rds-db:connect for RDS IAM authentication"),
        ("CodeBuild Role", "ecr:PutImage, ecr:BatchCheckLayerAvailability, s3:GetObject, logs:*"),
        ("Deployment Role", "ecs:RegisterTaskDefinition, ecs:UpdateService, iam:PassRole (scoped to the two "
                            "task roles)"),
    ]
    d_add_table_alt(doc, ["Role", "Permissions"], iam, col_widths=[4.5, 12.5])
    doc.add_paragraph()

    d_add_heading_styled(doc, "6.2 Secrets Manager", 2)
    d_add_para_styled(doc, "All sensitive configuration is injected through the task definition's secrets: "
                           "block \u2014 never in environment:, where it shows in the console and in "
                           "describe-task-definition, and never baked into the image (.dockerignore already "
                           "excludes .env files).")
    secrets = [
        ("nextxr/database-url", "PostgreSQL connection string \u2014 a secret because it embeds the password. "
                                "Alternatively use RDS IAM authentication, or store only the password and "
                                "assemble the URL in an entrypoint"),
        ("nextxr/api-keys", "NXR_API_KEYS \u2014 the JSON array of {key, tenant, role, name} that closes the "
                            "fail-open hole"),
        ("nextxr/neo4j-password", "Neo4j Aura password"),
        ("nextxr/anthropic-api-key", "Claude API key for the copilot layer"),
        ("nextxr/runpod-api-key", "RunPod API key for GPU reconstruction"),
        ("nextxr/runpod-endpoint-id", "RunPod serverless endpoint id"),
        ("nextxr/replicate-api-token", "Optional alternative reconstruction backend"),
    ]
    d_add_table_alt(doc, ["Secret Name", "Description"], secrets, col_widths=[5, 12])
    doc.add_paragraph()

    d_add_heading_styled(doc, "6.3 Security Posture Checklist", 2)
    posture = [
        ("API keys mandatory", "With NXR_API_KEYS unset the service is OPEN \u2014 every /api call, including the "
                               "LLM-billing copilot endpoints, is served without a key. Set the keys and "
                               "NXR_REQUIRE_AUTH=1"),
        ("Demo keys never in production", "The built-in demo keys load only when NXR_API_KEYS is unset, i.e. "
                                          "never in a correctly-configured deployment"),
        ("Tenant isolation", "A single application-level dependency checks the tenant in query, path and body "
                             "on every /api route; covered by a dedicated 25-test module"),
        ("Device credentials", "Telemetry devices use X-Device-Token, not API keys \u2014 one verb, one tenant, "
                               "individually revocable, hash-only storage"),
        ("CORS", "Default is *; set NXR_CORS_ORIGINS to the real frontend and hub origins"),
        ("Database in transit", "sslmode=require \u2014 without it psycopg2 quietly accepts an unencrypted "
                                "connection"),
        ("Database at rest", "Private/isolated subnets, security group open to the task SG only, KMS "
                             "encryption, password rotated through Secrets Manager or replaced by IAM auth"),
        ("S3", "Block Public Access ON; task-role object access scoped to one bucket"),
        ("Health alarm", "The health check never 503s by design, so add a CloudWatch alarm on the payload's "
                         "degraded status \u2014 it reports database, blobs, bus, historian and neo4j separately, "
                         "so the alarm can say which"),
        ("Copilot spend", "Set a budget cap on any deployment reachable by people you do not control \u2014 "
                          "copilot traffic bills to your key, and the cap is the only thing that bounds it"),
    ]
    d_add_table_alt(doc, ["Control", "Detail"], posture, col_widths=[4.5, 12.5])

    # 7. Cost Estimation
    doc.add_page_break()
    d_add_heading_styled(doc, "7. Cost Estimation", 1)
    d_add_para_styled(doc, "Indicative monthly figures, eu-west-1 on-demand pricing, excluding data-transfer "
                           "spikes. Neo4j Aura and RunPod are billed outside AWS and shown separately.")
    d_add_heading_styled(doc, "7.1 Development/Staging (~$118/month)", 2)
    dev_costs = [
        ("ECS Fargate (0.5 vCPU, 1 GB, 1 task)", "$18"),
        ("RDS db.t4g.small (single-AZ, 20 GB gp3, incl. TimescaleDB)", "$28"),
        ("ElastiCache cache.t4g.micro", "$12"),
        ("ALB (minimal traffic)", "$17"),
        ("S3 + CloudWatch + ECR + Secrets + data transfer", "$14"),
        ("Neo4j Aura (free / smallest paid tier)", "$0\u201365"),
        ("Subtotal, AWS only", "~$89"),
        ("Total with a small Aura instance", "~$118"),
    ]
    d_add_table_alt(doc, ["Resource", "Monthly Cost"], dev_costs, col_widths=[11, 6])
    doc.add_paragraph()

    d_add_heading_styled(doc, "7.2 Production (~$470/month)", 2)
    prod_costs = [
        ("ECS Fargate (1 vCPU, 2 GB, 2 tasks)", "$72"),
        ("RDS db.t4g.medium (Multi-AZ, 100 GB gp3, incl. TimescaleDB)", "$130"),
        ("RDS Proxy", "$22"),
        ("ElastiCache cache.t4g.small (Multi-AZ)", "$36"),
        ("ALB (moderate traffic)", "$25"),
        ("NAT Gateway (1 AZ; S3 gateway endpoint keeps blob traffic off it)", "$32"),
        ("S3 + CloudWatch + ECR + Secrets + data transfer", "$43"),
        ("Neo4j Aura Professional", "$110"),
        ("Subtotal, AWS only", "~$360"),
        ("Total with Aura", "~$470"),
    ]
    d_add_table_alt(doc, ["Resource", "Monthly Cost"], prod_costs, col_widths=[11, 6])
    doc.add_paragraph()
    d_add_para_styled(doc, "Not included and usage-based: (1) Anthropic API for the copilot \u2014 Sonnet 5 at "
                           "roughly $3/MTok input and $15/MTok output; moderate operator use across a handful "
                           "of twins is approximately $40\u2013120/month, and the budget cap in copilot/spend.py "
                           "is what bounds it. (2) RunPod serverless GPU for reconstruction \u2014 billed per "
                           "second of GPU time, typically a few cents per model. (3) Historian storage growth "
                           "\u2014 with compression and the retention policies the raw tier is bounded; without "
                           "the TimescaleDB extension it is not.", size=10, color=GREY_D)

    # 8. Deployment Pipeline
    doc.add_page_break()
    d_add_heading_styled(doc, "8. Deployment Pipeline", 1)
    d_add_para_styled(doc, "CI/CD uses AWS-native services: CodePipeline, CodeBuild, ECR and ECS rolling "
                           "deployments. Rehearse the whole shape locally first \u2014 docker compose runs the "
                           "same three shared stores (PostgreSQL 16, Redis 7, MinIO) plus Neo4j, so the "
                           "production posture can be exercised before it is trusted.")
    stages = [
        ("1. Source", "GitHub webhook on push to the release branch"),
        ("2. Build", "docker build (multi-stage: Vite frontend, then the Python runtime), tag with the git SHA, "
                     "push to ECR"),
        ("3. Test", "pytest \u2014 171 tests covering De Soto physics, ingest/historian, point maps, the solar "
                    "specification and tenant isolation"),
        ("4. Provision", "python -m db.schema and python -m tools.historian_provision from a bastion or "
                         "ECS-exec session inside the VPC (RDS is not publicly reachable); both idempotent"),
        ("5. Deploy", "ECS rolling update: register the new task definition, drain old tasks"),
        ("6. Verify", "ALB health check must pass, then read the four posture lines in CloudWatch: [auth], "
                      "[db], [blobs], [bus]"),
        ("7. Confirm ownership", "Check twin_runtime.owned across tasks \u2014 no tenant should appear in two "
                                 "tasks' lists at the same time"),
        ("8. Alarms", "CloudWatch alarms on the health payload's degraded status, on bus.scale_safe and "
                      "historian.scale_safe, on ECS task CPU/memory and on RDS connections"),
    ]
    d_add_table_alt(doc, ["Stage", "Description"], stages, col_widths=[4, 13])
    doc.add_paragraph()
    d_add_para_styled(doc, "Rollback is an ECS service update back to the previous task definition. Because "
                           "schema provisioning is additive and idempotent, a rollback of the application does "
                           "not require a schema rollback. Migrating an existing SQLite deployment is a "
                           "separate, one-time step: python -m db.migrate --dry-run, then python -m db.migrate, "
                           "before traffic is pointed at the new stack.")

    # Footer
    doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("NextXR Digital Twin Platform -- AWS Architecture Document v1.0")
    d_set_font(run, size=9, color=GREY_D)

    out_path = os.path.join(TECHDOCS_DIR, "NextXR_AWS_Architecture.docx")
    doc.save(out_path)
    print(f"  Saved: {out_path}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("NextXR Digital Twin Technical Document Generator")
    print("=" * 60)

    print("\n[1/2] Generating diagram PNGs...")
    generate_infrastructure_png()
    generate_component_png()
    generate_dataflow_png()
    generate_erd_png()

    print("\n[2/2] Generating DOCX documents with embedded diagrams...")
    build_hld()
    build_frd()
    build_lld()
    build_drd()
    build_aws()

    print("\n" + "=" * 60)
    print("All documents generated successfully!")
    print(f"Output directory: {TECHDOCS_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
