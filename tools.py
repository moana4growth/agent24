"""Function tools + session context for MoodBreak.

All tools receive RunContextWrapper[SessionCtx] so they can talk to the
websocket client (interactive tools) and write artifacts to disk.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import base64

from agents import RunContextWrapper, ToolOutputImage, ToolOutputText, function_tool


# ---------------------------------------------------------------- session ctx
@dataclass
class SessionCtx:
    session_id: str
    out_dir: Path
    send: Callable[[dict], Awaitable[None]]          # -> main app client
    broadcast_raw: Callable[[dict], Awaitable[None]]  # -> raw viewers
    artifacts: dict = field(default_factory=dict)
    # future used by interactive tools to wait for the user's reply
    pending_user: Optional[asyncio.Future] = None

    async def wait_user(self, timeout: float = 180.0, fallback: Any = None) -> Any:
        loop = asyncio.get_running_loop()
        self.pending_user = loop.create_future()
        try:
            return await asyncio.wait_for(self.pending_user, timeout)
        except asyncio.TimeoutError:
            return fallback
        finally:
            self.pending_user = None

    def resolve_user(self, payload: Any) -> bool:
        if self.pending_user and not self.pending_user.done():
            self.pending_user.set_result(payload)
            return True
        return False


# ------------------------------------------------------------------ planning
@function_tool
async def submit_plan(
    ctx: RunContextWrapper[SessionCtx], steps: list[str], rationale: str
) -> str:
    """Declare YOUR execution plan for this specific input before acting.

    The plan must be adapted to this input (skip / add / reorder steps with
    reasons). It is shown live to the audience as proof of dynamic planning.

    Args:
        steps: ordered list of intended actions, e.g. "researcher: deep dive food-delivery conventions".
        rationale: why THIS plan for THIS input (what you skipped/added and why).
    """
    await ctx.context.send({"type": "plan", "steps": steps, "rationale": rationale})
    return "Plan recorded and displayed. Proceed."


# --------------------------------------------------------------- interaction
@function_tool
async def present_mood_cards(
    ctx: RunContextWrapper[SessionCtx], cards_json: str, recommended_id: str
) -> str:
    """Present 2-3 mood-direction cards to the user and wait for their pick.

    Use ONCE per project, after research. Each card is a lightweight mood
    direction (not a full design). Always state your own recommendation.

    Args:
        cards_json: JSON array of cards:
            [{"id":"a","name":"...","keywords":["..."],"palette":["#hex",...],
              "typography":"display+body font pairing (real names)",
              "layout_hint":"one-line layout paradigm",
              "anchors":["real-world reference 1","reference 2"],
              "vibe_sentence":"...","rationale":"why this fits",
              "preview_html":"self-contained mini HTML strip (~2KB) RENDERED in
               this mood: real webfonts via <link>, display headline, one body
               line, palette blocks, one button. This is what sells the mood."}]
        recommended_id: id of the card YOU recommend (with rationale inside that card).
    """
    try:
        cards = json.loads(cards_json)
    except json.JSONDecodeError as e:
        return f"cards_json is not valid JSON: {e}. Fix and retry."
    await ctx.context.send(
        {"type": "mood_cards", "cards": cards, "recommended": recommended_id}
    )
    picked = await ctx.context.wait_user(fallback={"selected": recommended_id, "note": "timeout -> auto-selected your recommendation"})
    return f"User selection: {json.dumps(picked, ensure_ascii=False)}"


@function_tool
async def ask_user(
    ctx: RunContextWrapper[SessionCtx], question: str, options: list[str]
) -> str:
    """Ask the user ONLY when input signals genuinely conflict or something
    essential is missing. Do NOT use for confirmations — decide yourself.

    Args:
        question: the single question to ask.
        options: 2-4 short answer options (user may also type freely).
    """
    await ctx.context.send({"type": "ask", "question": question, "options": options})
    ans = await ctx.context.wait_user(fallback={"answer": options[0] if options else "", "note": "timeout -> defaulted to first option; proceed with best judgment"})
    return f"User answered: {json.dumps(ans, ensure_ascii=False)}"


# ----------------------------------------------------------------- artifacts
@function_tool
async def save_artifact(
    ctx: RunContextWrapper[SessionCtx], filename: str, content: str, kind: str
) -> str:
    """Save one deliverable file and push it to the client UI.

    Call once PER file (keeps outputs small and streaming visible).

    Args:
        filename: e.g. "variant_a.html", "design-tokens.json", "DESIGN.md", "screen_home.html".
        content: full file content.
        kind: one of "brandbook" | "variant" | "tokens" | "designmd" | "screen" | "other".
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    path = ctx.context.out_dir / safe
    path.write_text(content, encoding="utf-8")
    ctx.context.artifacts[safe] = kind
    await ctx.context.send(
        {"type": "artifact", "filename": safe, "kind": kind, "content": content}
    )
    return f"Saved {safe} ({len(content)} chars)."


@function_tool
async def read_artifact(ctx: RunContextWrapper[SessionCtx], filename: str) -> str:
    """Read a previously saved artifact (for critique or revision)."""
    path = ctx.context.out_dir / filename
    if not path.exists():
        return f"Not found. Available: {list(ctx.context.artifacts)}"
    return path.read_text(encoding="utf-8")


@function_tool
async def list_artifacts(ctx: RunContextWrapper[SessionCtx]) -> str:
    """List all artifacts saved so far in this session."""
    return json.dumps(ctx.context.artifacts, ensure_ascii=False)


# -------------------------------------------------------------- verification
def _lum(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


@function_tool
def check_contrast(fg_hex: str, bg_hex: str) -> str:
    """WCAG contrast ratio between a foreground and background color.

    Returns ratio + AA pass/fail for normal (4.5:1) and large (3:1) text.
    """
    try:
        l1, l2 = _lum(fg_hex), _lum(bg_hex)
    except (ValueError, IndexError):
        return "Invalid hex color(s)."
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    return json.dumps(
        {"ratio": round(ratio, 2), "AA_normal": ratio >= 4.5, "AA_large": ratio >= 3.0}
    )


_CLICHES: list[tuple[str, str]] = [
    (r"#(6d28d9|7c3aed|8b5cf6|a855f7|9333ea|c084fc|667eea|764ba2)",
     "Trademark AI purple/violet palette"),
    (r"linear-gradient\(\s*135deg[^)]*#(667eea|764ba2|8b5cf6|a855f7)",
     "The 135deg purple gradient hero — most recognizable AI-gen pattern"),
    (r"font-family[^;]*(Inter|Poppins)",
     "Default AI font stack (Inter/Poppins) used as primary typeface"),
    (r"(🚀|✨|🎉|💡|🔥)", "Decorative emoji in UI copy"),
    (r"border-radius:\s*(12|16)px", "Uniform 12-16px rounding on everything (check: is every element the same pill?)"),
    (r"box-shadow:\s*0\s+4px\s+6px", "Tailwind default shadow copied verbatim"),
    (r"(Unlock|Supercharge|Elevate|Seamless[ly]*)\b", "AI marketing-speak wording"),
    (r"grid-template-columns:\s*repeat\(3,", "Reflexive 3-card feature grid (allowed only if layout rationale exists)"),
]


@function_tool
def scan_cliches(html: str) -> str:
    """Scan HTML/CSS for known generative-AI design cliches (static blacklist).

    Returns JSON list of {pattern, why, count}. Empty list = clean.
    This is the *minimum verifiable bar*; apply your own judgment on top.
    """
    hits = []
    for pat, why in _CLICHES:
        found = re.findall(pat, html, flags=re.IGNORECASE)
        if found:
            hits.append({"pattern": pat, "why": why, "count": len(found)})
    return json.dumps(hits, ensure_ascii=False)


_DOMAIN_CHECKLISTS = {
    "food_delivery": ["search bar", "category navigation", "menu/dish cards with price", "cart entry point", "reorder/recent", "ETA or delivery-state affordance"],
    "admin_dashboard": ["data table with sort/filter", "bulk actions", "global search", "kpi summary", "sidebar or top nav with sections", "row-level detail access"],
    "ecommerce": ["search", "product grid with price", "cart", "filters/sort", "product detail entry", "checkout CTA"],
    "booking": ["date/time picker", "availability display", "confirmation summary", "cancellation/modify path"],
    "education": ["progress indicator", "lesson/course list", "continue-where-left-off", "achievement/feedback"],
    "finance": ["balance/summary card", "transaction list", "security cues", "primary action (transfer/pay)"],
    "social": ["feed", "composer entry", "profile access", "notification affordance"],
    "health": ["metric summary", "trend/history view", "logging entry point", "privacy cues"],
}


@function_tool
def domain_checklist(domain: str) -> str:
    """Look up must-keep functional conventions for a product domain.

    These are conventions to PRESERVE (users need them), as opposed to the
    aesthetic cliches we intentionally break. If the domain is missing or
    only partially matches, extend the list with your own judgment and say so.

    Args:
        domain: e.g. "food_delivery", "admin_dashboard", "ecommerce",
                "booking", "education", "finance", "social", "health".
    """
    key = domain.lower().strip().replace("-", "_").replace(" ", "_")
    if key in _DOMAIN_CHECKLISTS:
        return json.dumps({"domain": key, "required": _DOMAIN_CHECKLISTS[key]}, ensure_ascii=False)
    # fuzzy contains
    for k, v in _DOMAIN_CHECKLISTS.items():
        if k in key or key in k:
            return json.dumps({"domain": k, "required": v, "note": "fuzzy match"}, ensure_ascii=False)
    return json.dumps({"domain": key, "required": [], "note": "unknown domain — derive required elements yourself from research"}, ensure_ascii=False)


def _download_image(url: str) -> str | None:
    """Download an image and return a data URL, or None on failure."""
    import requests

    try:
        r = requests.get(
            url, timeout=6, stream=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("image/"):
            return None
        data = r.content
        if len(data) > 4_000_000:
            return None
        return f"data:{ctype.split(';')[0]};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


@function_tool
async def search_reference_images(
    ctx: RunContextWrapper[SessionCtx], query: str, count: int
) -> list:
    """Search the live web for REAL design reference images and SEE them.

    Returns actual images into your context — use your eyes: autopsy their
    layout structure, palette, typography, texture, then translate into rules.
    Use specific visual queries ("kinfolk magazine spread layout",
    "swiss grid poster 1960s", "brutalist web design"), not generic ones.

    Args:
        query: image search query (English works best).
        count: how many images to return (2-4 recommended).
    """
    count = max(1, min(int(count), 4))
    try:
        from ddgs import DDGS

        def _search():
            with DDGS() as d:
                return list(d.images(query, max_results=12))

        results = await asyncio.to_thread(_search)
    except Exception as e:
        return f"Image search unavailable ({type(e).__name__}: {e}). Proceed from your own knowledge of this style, and say you did so."

    outputs: list = []
    previews: list[str] = []
    for item in results:
        if len(previews) >= count:
            break
        url = item.get("image") or item.get("thumbnail")
        if not url:
            continue
        data_url = await asyncio.to_thread(_download_image, url)
        if not data_url:
            continue
        previews.append(data_url)
        outputs.append(ToolOutputImage(image_url=data_url, detail="low"))

    if not outputs:
        return f"Search ran but no images could be downloaded for '{query}'. Try a different query or proceed from knowledge."

    # show the audience what the agent is looking at
    await ctx.context.send({"type": "ref_images", "query": query, "images": previews})
    outputs.insert(0, ToolOutputText(text=f"{len(previews)} live reference images for '{query}' — study their structure, palette, type:"))
    return outputs


_FONT_CATALOG = [
    {"name": "Pretendard", "mood": ["neutral", "modern", "product"], "css": "https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css", "family": "Pretendard"},
    {"name": "SUIT", "mood": ["clean", "tech", "geometric"], "css": "https://cdn.jsdelivr.net/gh/sun-typeface/SUIT/fonts/static/woff2/SUIT.css", "family": "SUIT"},
    {"name": "Noto Serif KR", "mood": ["editorial", "literary", "premium"], "css": "https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@300;500;700&display=swap", "family": "'Noto Serif KR', serif"},
    {"name": "Nanum Myeongjo", "mood": ["classic", "print", "quiet"], "css": "https://fonts.googleapis.com/css2?family=Nanum+Myeongjo:wght@400;700;800&display=swap", "family": "'Nanum Myeongjo', serif"},
    {"name": "Gowun Batang", "mood": ["warm", "essay", "kinfolk", "slow"], "css": "https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&display=swap", "family": "'Gowun Batang', serif"},
    {"name": "Gowun Dodum", "mood": ["soft", "friendly", "calm"], "css": "https://fonts.googleapis.com/css2?family=Gowun+Dodum&display=swap", "family": "'Gowun Dodum', sans-serif"},
    {"name": "Hahmlet", "mood": ["editorial", "contemporary-serif", "fashion"], "css": "https://fonts.googleapis.com/css2?family=Hahmlet:wght@300;500;700&display=swap", "family": "'Hahmlet', serif"},
    {"name": "IBM Plex Sans KR", "mood": ["technical", "swiss", "grid"], "css": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;500;700&display=swap", "family": "'IBM Plex Sans KR', sans-serif"},
    {"name": "Black Han Sans", "mood": ["loud", "retro", "poster", "brutalist"], "css": "https://fonts.googleapis.com/css2?family=Black+Han+Sans&display=swap", "family": "'Black Han Sans', sans-serif"},
    {"name": "Do Hyeon", "mood": ["retro", "signage", "bold"], "css": "https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap", "family": "'Do Hyeon', sans-serif"},
    {"name": "Song Myung", "mood": ["vintage", "editorial", "title"], "css": "https://fonts.googleapis.com/css2?family=Song+Myung&display=swap", "family": "'Song Myung', serif"},
    {"name": "Stylish", "mood": ["playful", "handwritten-ish", "light"], "css": "https://fonts.googleapis.com/css2?family=Stylish&display=swap", "family": "'Stylish', sans-serif"},
    {"name": "East Sea Dokdo", "mood": ["raw", "zine", "handwriting"], "css": "https://fonts.googleapis.com/css2?family=East+Sea+Dokdo&display=swap", "family": "'East Sea Dokdo', cursive"},
    {"name": "Nanum Brush Script", "mood": ["brush", "analog", "human"], "css": "https://fonts.googleapis.com/css2?family=Nanum+Brush+Script&display=swap", "family": "'Nanum Brush Script', cursive"},
    {"name": "Space Grotesk (Latin)", "mood": ["display-latin", "tech", "contemporary"], "css": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap", "family": "'Space Grotesk', sans-serif"},
]


@function_tool
def font_catalog(mood_keywords: list[str]) -> str:
    """Curated Korean-capable webfont catalog with CDN links, filtered by mood.

    Use this to pick REAL, loadable fonts instead of defaulting to safe choices.
    Pair a display face with a body face; unexpected pairings break AI-look.

    Args:
        mood_keywords: e.g. ["editorial","kinfolk"] or ["retro","brutalist"].
                       Pass an empty list to get the full catalog.
    """
    kws = [k.lower() for k in mood_keywords]
    if not kws:
        return json.dumps(_FONT_CATALOG, ensure_ascii=False)
    scored = []
    for f in _FONT_CATALOG:
        score = sum(1 for k in kws for m in f["mood"] if k in m or m in k)
        scored.append((score, f))
    scored.sort(key=lambda x: -x[0])
    top = [f for s, f in scored if s > 0] or _FONT_CATALOG
    return json.dumps(top[:8], ensure_ascii=False)


def to_jsonable(obj: Any) -> Any:
    """Best-effort raw serialization of SDK stream objects (NO rewriting of content)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(exclude_none=True)
        except Exception:
            pass
    if dataclasses.is_dataclass(obj):
        try:
            return dataclasses.asdict(obj)
        except Exception:
            pass
    return str(obj)


def now_ms() -> int:
    return int(time.time() * 1000)
