"""
app.py — standalone FastAPI server for the 2-D → 3-D feature.

Endpoints:
  GET  /api/health              — backend + which vision model is active
  POST /api/parse  {data,...}   — parse an uploaded plan image -> nxr-scene/1
  GET  /api/sample/{facility}   — a no-LLM sample twin (works without any key)

Run:  uvicorn app:app --reload --port 8000   (from this backend/ folder)
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import gateway
import plan_parser

app = FastAPI(title="NextXR 2D→3D")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


class ParseReq(BaseModel):
    data: str                         # data URL (PNG/JPEG); PDFs are rasterised client-side
    filename: str = "plan.png"
    facility: Optional[str] = None
    floors: int = 1


@app.get("/api/health")
def health():
    gw = gateway.get_gateway()
    return {"ok": True, "llm": gw.stats(),
            "anthropic": gw._anthropic is not None,
            "vision": "claude" if gw._anthropic is not None else
                      ("gpt-4o" if gw.backend == "openai" else "none (set OPENAI/ANTHROPIC key)")}


@app.post("/api/parse")
def parse(req: ParseReq):
    if not req.data:
        raise HTTPException(400, "Provide an image data URL in `data`.")
    try:
        scene = plan_parser.parse_plan(req.data, req.filename, req.facility, req.floors)
    except Exception as e:
        raise HTTPException(500, f"parse failed: {e}")
    return {"scene": scene}


@app.get("/api/sample/{facility}")
def sample(facility: str, floors: int = 1):
    return {"scene": plan_parser.sample_scene(facility, floors)}
