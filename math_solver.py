import argparse
import base64
import io
import itertools
import json
import os
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import simpledialog, ttk

import requests
from PIL import Image, ImageGrab

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

# Auto-fill stack (mouse/keyboard control + box detection). Optional: the base
# solver still runs without these installed.
try:
    import cv2
    import numpy as np
    import pyautogui

    pyautogui.FAILSAFE = True  # slam cursor to a screen corner to abort
    HAS_AUTOFILL = True
except ImportError:
    HAS_AUTOFILL = False

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
# Google Gemini, via its OpenAI-compatible endpoint. Free tier includes vision.
# Get a free API key at https://aistudio.google.com/apikey
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL = "gemini-2.5-flash-lite"  # free-tier vision model, highest free throughput; override with --model
# Vision-capable Gemini models shown in the in-app dropdown (highest free quota first).
MODELS = [
    "gemini-2.5-flash-lite",  # ~1000/day, 30/min — most free throughput, weakest
    "gemini-2.5-flash",       # stronger reasoning, lower free quota
    "gemini-2.5-pro",         # most capable — best at reading dense notation; lowest free quota
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
GUI_TITLE = "Math Solver"
COUNTDOWN_SECONDS = 3
DAILY_LIMIT = 1000  # gemini-2.5-flash-lite free-tier requests/day (approx); override with --daily-limit

# Answer-box detection (OpenCV HSV ranges: H 0-179, S/V 0-255). The box outline
# is a fixed pure blue on this platform, so detection works with no calibration;
# "Set box color" can override DEFAULT_BOX_HSV if yours differs.
DEFAULT_BOX_HSV = [120, 255, 255]
BOX_H_TOL = 12
BOX_S_TOL = 80
BOX_V_TOL = 90
BOX_MIN_AREA = 100      # bounding-box px^2 — reject specks (ALEKS empty boxes are ~220)
BOX_MAX_AREA = 120000   # reject huge blue regions (panels, banners)
BOX_MIN_ASPECT = 0.4    # width / height
BOX_MAX_ASPECT = 6.0

# The model is told to reason first (greatly improves accuracy on anything that
# needs calculation), then emit each final answer on its own ``ANSWER:`` line.
# parse_answers() pulls those lines out for display/auto-fill, discarding the
# working. ANSWER_RE must stay in sync with the format requested here.
PROMPT = (
    "Look at this screenshot and find every math problem, equation, or word "
    "problem that has an answer to fill in. Solve each one. "
    "FIRST, read the expression carefully and write out exactly what each radical, "
    "fraction bar, exponent, and parenthesis covers — for example, whether a root "
    "sits over only the numerator or over the entire fraction, and which factors "
    "are inside vs. outside it. Treat any small raised number as an EXPONENT on the "
    "symbol it sits on: z^3 means z cubed (z to the third power), NOT 3 times z, and "
    "y^7 means y to the seventh, NOT 7 times y — a raised digit is never a separate "
    "factor or coefficient. Mis-reading this structure (radical scope, or an "
    "exponent confused for a coefficient) is the most common mistake, so transcribe "
    "it carefully before computing. THEN work through the calculation step by step, "
    "carefully and accurately, especially with arithmetic, exponentials, "
    "logarithms, and decimals.\n\n"
    "Then, at the very END of your reply, write the final answers: one line per "
    "answer blank, each line starting with 'ANSWER:' followed by just the answer. "
    "List them in the order the blanks appear on screen: strictly top to bottom, "
    "going left to right only between blanks at the same height. A blank that sits "
    "higher is listed first even when it is farther right — e.g. in a 'quotient + "
    "remainder/divisor' answer the remainder (the fraction's numerator, sitting "
    "higher) comes BEFORE the quotient on the main line. "
    "On each line, also report where that blank is on screen so it can be matched "
    "to the correct box: write the line as 'ANSWER (x%,y%): value', where x is the "
    "horizontal position (0 = far left, 100 = far right) and y is the vertical "
    "position (0 = top, 100 = bottom) of the CENTER of that blank in the "
    "screenshot. For example, a remainder numerator box on the upper right might "
    "be 'ANSWER (75%,30%): -2x^2-4' and the quotient box on the main line "
    "'ANSWER (25%,45%): 9x-9'. "
    "If a problem says to round, round exactly as instructed. Give only "
    "the value — no units, labels, or variable names like 'x =' .\n\n"
    "Write each answer in plain linear/calculator notation that can be typed on a "
    "keyboard: use ^ for exponents (e.g. x^2), / for fractions (e.g. 3/4), * for "
    "multiplication, and pi for the constant pi. For square roots write sqrt(...) "
    "with the radicand ALWAYS in parentheses, e.g. sqrt(2), sqrt(x+1), 2*sqrt(3). "
    "Do NOT use Unicode superscripts/symbols, LaTeX, or \\frac."
)
# Matches a final-answer line like "ANSWER: 15" or, with a position hint,
# "ANSWER (75%,30%): 15" (case-insensitive, tolerant of an index and ** markdown
# bold). Groups: (1) x%, (2) y% — both optional — and (3) the answer text after
# the colon. The (x, y) hint locates the blank's center as a percent of the
# screenshot, letting auto-fill match answers to boxes by position (see
# assign_answers_to_boxes) — robust even when blanks are laid out 2-D, e.g. an
# inline quotient next to a higher remainder numerator.
ANSWER_RE = re.compile(
    r"(?i)^answer\s*\d*\s*"
    r"(?:[\(\[]\s*(\d{1,3})\s*%?\s*,\s*(\d{1,3})\s*%?\s*[\)\]]\s*)?"
    r":\s*(.+)$"
)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except IOError as e:
        print(f"Warning: could not save config to {CONFIG_FILE}: {e}")


def load_api_key():
    return load_config().get("api_key", "")


def save_api_key(key):
    cfg = load_config()
    cfg["api_key"] = key
    save_config(cfg)


def get_config_value(key, default=None):
    return load_config().get(key, default)


def set_config_value(key, value):
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)


def _today():
    """Today's date as an ISO string, in US Pacific (when Gemini quotas reset)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def get_usage():
    """Number of requests made today (resets daily)."""
    cfg = load_config()
    if cfg.get("usage_date") == _today():
        return cfg.get("usage_count", 0)
    return 0


def record_use():
    """Increment today's usage counter and return the new count."""
    cfg = load_config()
    today = _today()
    if cfg.get("usage_date") != today:
        cfg["usage_date"] = today
        cfg["usage_count"] = 0
    cfg["usage_count"] = cfg.get("usage_count", 0) + 1
    save_config(cfg)
    return cfg["usage_count"]


def capture_active_window():
    """Capture the active window and its top-left screen offset.

    Returns ``(img, (left, top))`` so detected coordinates inside ``img`` can be
    mapped back to absolute screen coordinates. Falls back to a full-screen grab
    with offset ``(0, 0)``.
    """
    if HAS_PYGETWINDOW:
        try:
            win = gw.getActiveWindow()
            if win and win.title and win.title != GUI_TITLE:
                left = win.left
                top = win.top
                right = win.left + win.width
                bottom = win.top + win.height
                if right > left and bottom > top:
                    return ImageGrab.grab(bbox=(left, top, right, bottom)), (left, top)
        except Exception:
            pass
    # Fallback: full screen, primary-display origin
    return ImageGrab.grab(), (0, 0)


def _box_mask(img, box_hsv):
    """Binary mask of pixels matching the box outline color, or ``None`` when
    detection is unavailable / no color is set. Small gaps in the outline are
    closed so each box forms one connected contour."""
    if not HAS_AUTOFILL or not box_hsv:
        return None
    rgb = np.array(img.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = box_hsv
    lower = np.array([max(h - BOX_H_TOL, 0), max(s - BOX_S_TOL, 0), max(v - BOX_V_TOL, 0)])
    upper = np.array([min(h + BOX_H_TOL, 179), min(s + BOX_S_TOL, 255), min(v + BOX_V_TOL, 255)])
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)


def find_answer_boxes(img, box_hsv):
    """Locate every answer box by its outline color, in reading order.

    ``box_hsv`` is the calibrated outline color ``[h, s, v]`` (OpenCV HSV).
    Returns a list of box centers ``[(cx, cy), ...]`` in image pixels, sorted
    top-to-bottom then left-to-right. That ordering lines the boxes up with the
    AI's answers, which are listed in the same reading order, so the first answer
    fills the first box and so on. Returns ``[]`` if detection is unavailable or
    no plausible box is found.
    """
    mask = _box_mask(img, box_hsv)
    if mask is None:
        return []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        x, y, w, ht = cv2.boundingRect(c)
        area = w * ht
        if area < BOX_MIN_AREA or area > BOX_MAX_AREA:
            continue
        aspect = w / ht if ht else 0
        if aspect < BOX_MIN_ASPECT or aspect > BOX_MAX_ASPECT:
            continue
        candidates.append((x + w // 2, y + ht // 2, ht))
    return _reading_order(candidates)


def assign_answers_to_boxes(boxes, positions, size):
    """Match detected boxes to answers by their on-screen position.

    ``boxes`` are box centers ``[(cx, cy), ...]`` in image pixels (reading
    order); ``positions`` are the matching per-answer hints
    ``[(x%, y%) | None, ...]`` parsed from the reply; ``size`` is the image
    ``(width, height)``. Returns ``perm`` where box ``i`` should be filled with
    answer ``perm[i]`` — the assignment minimizing total box-to-hint distance.
    Returns ``None`` (caller falls back to plain reading-order pairing) when any
    hint is missing, the counts differ, or there are more boxes than is cheap to
    match.

    Matching on position — not a 1-D sorted order — is what makes a 2-D layout
    reliable: an inline quotient box and a higher remainder-numerator box differ
    clearly in x even when their y nearly coincides, so each answer still lands in
    the right box however close the two blanks are vertically.
    """
    n = len(boxes)
    if n == 0 or n != len(positions) or any(p is None for p in positions):
        return None
    if n > 6:  # brute-force over n! permutations; real problems have 1-3 blanks
        return None
    w, h = size
    pred = [(x / 100.0 * w, y / 100.0 * h) for (x, y) in positions]
    best, best_cost = None, None
    for perm in itertools.permutations(range(n)):
        cost = sum(
            (bx - pred[perm[i]][0]) ** 2 + (by - pred[perm[i]][1]) ** 2
            for i, (bx, by) in enumerate(boxes)
        )
        if best_cost is None or cost < best_cost:
            best, best_cost = perm, cost
    return best


def diagnose_boxes(img, box_hsv):
    """Explain what box detection sees on ``img``, for debugging "Box not found".

    Returns ``(report, mask)`` where ``report`` is a human-readable string listing
    every colored region found and whether it passed the area/aspect filters (and
    if not, why), and ``mask`` is the binary color mask (or ``None``). The summary
    line comes first so it's readable even in a small text box.
    """
    mask = _box_mask(img, box_hsv)
    if mask is None:
        return ("No detection: opencv/numpy missing, or no box color set.", None)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    details, passed = [], 0
    for c in sorted(contours, key=lambda c: cv2.boundingRect(c)[1]):  # top to bottom
        x, y, w, ht = cv2.boundingRect(c)
        area = w * ht
        aspect = w / ht if ht else 0
        why = []
        if area < BOX_MIN_AREA:
            why.append("too small")
        if area > BOX_MAX_AREA:
            why.append("too big")
        if aspect < BOX_MIN_ASPECT:
            why.append("too tall")
        if aspect > BOX_MAX_ASPECT:
            why.append("too wide")
        passed += not why
        tag = "OK" if not why else "drop: " + ", ".join(why)
        details.append(f"  {w}x{ht}px area={area} aspect={aspect:.1f}  [{tag}]")
    summary = (
        f"Box color HSV {list(box_hsv)}\n"
        f"{len(contours)} blue region(s) found, {passed} usable as box(es)."
    )
    return ("\n".join([summary, *details]), mask)


def _reading_order(boxes):
    """Sort ``(cx, cy, h)`` boxes top-to-bottom then left-to-right.

    Two boxes share a row only when their vertical centers nearly coincide
    (within ~a quarter of the median box height); within a row they go
    left-to-right, and rows go top to bottom. A blank that sits clearly higher
    than its neighbour — most often a fraction's *numerator*, like the remainder
    box in a "quotient + remainder/divisor" answer — is therefore ordered before
    a blank on the main line, not merged into its row and sorted left-to-right.
    Half the box height (the old tolerance) merged those two and reversed them,
    so they stopped lining up with the model's top-to-bottom answer order and the
    answers swapped boxes. Returns ``(cx, cy)`` with the height dropped. (This is
    only the fallback ordering; auto-fill prefers position matching — see
    assign_answers_to_boxes.)
    """
    if not boxes:
        return []
    heights = sorted(b[2] for b in boxes)
    row_tol = max(heights[len(heights) // 2] // 4, 6)  # ~a quarter of the median height
    rows = []
    for b in sorted(boxes, key=lambda b: b[1]):  # top to bottom
        for row in rows:
            if abs(b[1] - row[0][1]) <= row_tol:
                row.append(b)
                break
        else:
            rows.append([b])
    ordered = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda b: b[0]))  # left to right within a row
    return [(b[0], b[1]) for b in ordered]


def _read_paren(expr, i):
    """Index just past the balanced ``(...)`` group starting at ``expr[i] == '('``."""
    depth, n = 0, len(expr)
    while i < n:
        if expr[i] == "(":
            depth += 1
        elif expr[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _read_operand(expr, i):
    """Read the operand starting at ``expr[i]``: a balanced ``(...)`` group, a run
    of alphanumerics / ``.`` / ``,``, or a function call like ``sqrt(...)`` (an
    identifier immediately followed by a paren group). Returns ``(text, next)``."""
    start, n = i, len(expr)
    if i < n and expr[i] == "(":
        end = _read_paren(expr, i)
        return expr[start:end], end
    while i < n and (expr[i].isalnum() or expr[i] in ".,"):
        i += 1
    if i < n and expr[i] == "(":  # identifier followed by (...) -> function call
        i = _read_paren(expr, i)
    return expr[start:i], i


def math_keyseq(expr):
    """Plan keystrokes for a linear math expression in a MathQuill-style field.

    Returns a list of ``("type", text)`` / ``("key", name)`` actions. After the
    operand of a ``^`` (exponent) or ``/`` (fraction), a Right-arrow is emitted
    to exit the script/fraction block so following characters land at baseline.
    Operands are processed recursively, so exponents/fractions nested inside a
    parenthesized operand (e.g. ``(2*y^3*(y-6))``) also get their block-exit.
    """
    # Math is space-insensitive, and a space key can exit the current MathQuill
    # block (leaving exponents/denominators empty). Drop all whitespace.
    expr = "".join(expr.split())
    actions = []
    _emit_math(expr, actions)
    return actions


def _emit_math(expr, actions):
    """Append keystroke actions for ``expr`` into ``actions`` (recursive)."""
    buf = []

    def flush():
        if buf:
            actions.append(("type", "".join(buf)))
            buf.clear()

    i, n = 0, len(expr)
    while i < n:
        # sqrt(...) can't be typed — it must be inserted via a palette button.
        if expr[i:i + 4].lower() == "sqrt" and i + 4 < n and expr[i + 4] == "(":
            flush()
            actions.append(("button", "sqrt"))
            i += 4
            operand, i = _read_operand(expr, i)  # the (...) radicand group
            inner = operand[1:-1] if operand[:1] == "(" and operand[-1:] == ")" else operand
            _emit_math(inner, actions)  # radicand (recurse: nested roots/powers)
            actions.append(("key", "right"))  # exit the radical
            continue
        ch = expr[i]
        if ch in "^/":
            buf.append(ch)
            flush()
            i += 1
            operand, i = _read_operand(expr, i)
            _emit_math(operand, actions)  # recurse: inner ^ / get their exits
            actions.append(("key", "right"))  # exit superscript / denominator
        else:
            buf.append(ch)
            i += 1
    flush()


def type_math(expr, sqrt_button=None, interval=0.03):
    """Type ``expr`` into the focused math field using MathQuill navigation.

    ``sqrt_button`` is the screen ``[x, y]`` of the platform's square-root
    palette button; ``sqrt`` actions click it (roots can't be typed). After the
    click the radical is inserted with the cursor in the radicand, so typing
    continues into the field.
    """
    for kind, val in math_keyseq(expr):
        if kind == "type":
            pyautogui.write(val, interval=interval)
        elif kind == "key":
            pyautogui.press(val)
        elif kind == "button" and val == "sqrt" and sqrt_button:
            pyautogui.click(sqrt_button[0], sqrt_button[1])
            time.sleep(0.12)


def parse_answers(raw):
    """Pull the final answers — and any position hints — out of the model's reply.

    The model reasons first, then lists each final answer on its own line
    prefixed with ``ANSWER:`` (see ``PROMPT``), optionally carrying the blank's
    on-screen location as ``ANSWER (x%,y%): value``. Returns a list of
    ``(value, pos)`` in the order listed — ``pos`` is ``(x, y)`` in percent of
    the screenshot, or ``None`` when the model gave no hint — with the prefix and
    any surrounding ``**`` bold stripped. Returns ``[]`` for an off-format reply
    with no ANSWER lines.
    """
    out = []
    for line in raw.splitlines():
        s = line.strip().strip("*").strip()  # drop **bold** wrapping the line
        m = ANSWER_RE.match(s)
        if not m:
            continue
        val = m.group(3).strip().strip("*").strip()
        if not val:
            continue
        pos = None
        if m.group(1) is not None and m.group(2) is not None:
            pos = (int(m.group(1)), int(m.group(2)))
        out.append((val, pos))
    return out


def answers_display(parsed, raw):
    """Text for the Answer box: one value per line, or the whole reply trimmed
    when the model didn't use the ``ANSWER:`` format (so the user still sees
    something)."""
    return "\n".join(val for val, _ in parsed) if parsed else raw.strip()


def image_to_base64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def solve_math(api_key, img, model=MODEL):
    b64 = image_to_base64(img)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


class MathSolverApp:
    def __init__(self, root, api_key, model=MODEL, daily_limit=DAILY_LIMIT):
        self.root = root
        self.api_key = api_key
        self.model = model
        self.daily_limit = daily_limit
        self._scanning = False

        root.title(GUI_TITLE)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        # Position in top-right corner
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        root.geometry(f"320x445+{sw - 340}+20")

        self.status = tk.StringVar(value="Ready")
        tk.Label(root, textvariable=self.status, font=("Segoe UI", 10), pady=4).pack()

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=2)

        self.scan_btn = tk.Button(
            btn_frame,
            text="Scan & Solve",
            font=("Segoe UI", 10, "bold"),
            bg="#4CAF50",
            fg="white",
            relief="flat",
            padx=12,
            pady=6,
            command=self.start_countdown,
        )
        self.scan_btn.pack(side=tk.LEFT, padx=4)

        tk.Button(
            btn_frame,
            text="Key",
            font=("Segoe UI", 9),
            relief="flat",
            padx=6,
            pady=6,
            command=self.change_key,
        ).pack(side=tk.LEFT, padx=4)

        # Debug: report what box detection sees (diagnoses "Box not found").
        self.debug_btn = tk.Button(
            btn_frame,
            text="Debug",
            font=("Segoe UI", 9),
            relief="flat",
            padx=6,
            pady=6,
            command=self.debug_detect,
        )
        self.debug_btn.pack(side=tk.LEFT, padx=4)

        # Model picker — switch AI models on the fly; the choice persists in config.
        model_frame = tk.Frame(root)
        model_frame.pack(pady=(4, 0))
        tk.Label(model_frame, text="Model:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.model_var = tk.StringVar(value=self.model)
        choices = list(MODELS)
        if self.model not in choices:
            choices.insert(0, self.model)
        self.model_menu = ttk.Combobox(
            model_frame,
            textvariable=self.model_var,
            values=choices,
            state="readonly",
            width=22,
            font=("Segoe UI", 9),
        )
        self.model_menu.pack(side=tk.LEFT)
        self.model_menu.bind("<<ComboboxSelected>>", self._on_model_change)

        # Auto-fill: type the answer into the box and click Check automatically.
        self.auto_fill_var = tk.BooleanVar(value=bool(get_config_value("auto_fill", False)))
        self.autofill_chk = tk.Checkbutton(
            root,
            text="Auto-fill answer & submit",
            variable=self.auto_fill_var,
            font=("Segoe UI", 9),
            command=self._toggle_autofill,
        )
        self.autofill_chk.pack(pady=(2, 0))

        # One-time calibration: sample the box outline color, set Check position.
        calib = tk.Frame(root)
        calib.pack(pady=2)
        self.box_btn = tk.Button(
            calib, text="Set box color", font=("Segoe UI", 8),
            relief="flat", command=self.calibrate_box_color,
        )
        self.box_btn.pack(side=tk.LEFT, padx=3)
        self.check_btn = tk.Button(
            calib, text="Set Check ✓", font=("Segoe UI", 8),
            relief="flat", command=self.calibrate_check_button,
        )
        self.check_btn.pack(side=tk.LEFT, padx=3)
        self.sqrt_btn = tk.Button(
            calib, text="Set √", font=("Segoe UI", 8),
            relief="flat", command=self.calibrate_sqrt_button,
        )
        self.sqrt_btn.pack(side=tk.LEFT, padx=3)

        self.calib_status = tk.StringVar()
        tk.Label(root, textvariable=self.calib_status, font=("Segoe UI", 8), fg="#888").pack()
        self._update_calib_labels()

        if not HAS_AUTOFILL:
            self.autofill_chk.config(state=tk.DISABLED)
            self.box_btn.config(state=tk.DISABLED)
            self.check_btn.config(state=tk.DISABLED)
            self.sqrt_btn.config(state=tk.DISABLED)
            self.calib_status.set("Auto-fill needs: pip install pyautogui opencv-python")

        # Answer display (read-only, selectable so the answer can be copied)
        tk.Label(root, text="Answer", font=("Segoe UI", 8), fg="#888").pack()
        self.result = tk.Text(
            root,
            height=5,
            width=32,
            font=("Segoe UI", 13, "bold"),
            wrap="word",
            state="disabled",
            relief="flat",
            bg="#f5f5f5",
            padx=6,
            pady=6,
        )
        self.result.pack(fill="both", expand=True, padx=8, pady=2)

        # Usage counter
        self.usage = tk.StringVar()
        tk.Label(root, textvariable=self.usage, font=("Segoe UI", 8), fg="#666").pack(
            pady=(0, 4)
        )
        self._update_usage()

    def _set_result(self, text):
        self.result.config(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.config(state="disabled")

    def _update_usage(self):
        used = get_usage()
        left = max(self.daily_limit - used, 0)
        self.usage.set(f"Uses today: {used} / {self.daily_limit}   ({left} left)")

    def _toggle_autofill(self):
        set_config_value("auto_fill", self.auto_fill_var.get())

    def _on_model_change(self, event=None):
        self.model = self.model_var.get()
        set_config_value("model", self.model)
        self.status.set(f"Model: {self.model}")
        self.model_menu.selection_clear()

    def _update_calib_labels(self):
        color = "set" if get_config_value("box_color_hsv") else "auto"
        check = "set" if get_config_value("check_button") else "—"
        root = "set" if get_config_value("sqrt_button") else "—"
        self.calib_status.set(f"box {color} · check {check} · √ {root}")

    # --- Calibration (hover the OS cursor over the target during the countdown) ---

    def calibrate_box_color(self):
        self._start_calibration("Hover over a box OUTLINE", self._sample_box_color)

    def calibrate_check_button(self):
        self._start_calibration("Hover over the Check button", self._sample_check_button)

    def calibrate_sqrt_button(self):
        self._start_calibration("Hover over the √ (square root) button", self._sample_sqrt_button)

    def _start_calibration(self, instruction, done_cb):
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.config(state=tk.DISABLED)
        self._calib_countdown(instruction, done_cb, COUNTDOWN_SECONDS)

    def _calib_countdown(self, instruction, done_cb, remaining):
        if remaining > 0:
            self.status.set(f"{instruction}... {remaining}")
            self.root.after(1000, self._calib_countdown, instruction, done_cb, remaining - 1)
        else:
            done_cb()
            self.scan_btn.config(state=tk.NORMAL)
            self._scanning = False

    def _sample_box_color(self):
        """Sample the outline color from a small patch under the cursor."""
        x, y = pyautogui.position()
        r = 10
        img = ImageGrab.grab(bbox=(x - r, y - r, x + r, y + r))
        hsv = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2HSV).reshape(-1, 3)
        # Keep colored pixels (the outline) — drop near-white interior / dark text
        colored = hsv[(hsv[:, 1] > 60) & (hsv[:, 2] > 40)]
        if len(colored) == 0:
            self.status.set("No colored pixel — hover right on the box edge")
            return
        h, s, v = (int(np.median(colored[:, i])) for i in range(3))
        set_config_value("box_color_hsv", [h, s, v])
        self._update_calib_labels()
        self.status.set(f"Box color set (HSV {h},{s},{v})")

    def _sample_check_button(self):
        x, y = pyautogui.position()
        set_config_value("check_button", [int(x), int(y)])
        self._update_calib_labels()
        self.status.set(f"Check button set ({int(x)},{int(y)})")

    def _sample_sqrt_button(self):
        x, y = pyautogui.position()
        set_config_value("sqrt_button", [int(x), int(y)])
        self._update_calib_labels()
        self.status.set(f"√ button set ({int(x)},{int(y)})")

    def _autofill(self, img, offset, parsed):
        """Fill each detected box with its answer, then click Check once.

        ``parsed`` is the list of ``(value, pos)`` from ``parse_answers``. With N
        boxes and N answers, each answer is matched to a box by its position hint
        (``assign_answers_to_boxes``); without hints it falls back to pairing in
        reading order. Check is then clicked once to submit them all. Returns a
        status string.
        """
        if not HAS_AUTOFILL:
            return "Auto-fill needs: pip install pyautogui opencv-python"
        box_hsv = get_config_value("box_color_hsv") or DEFAULT_BOX_HSV
        values = [v for v, _ in parsed]
        positions = [p for _, p in parsed]
        if not values:
            return "No answer to fill"
        boxes = find_answer_boxes(img, box_hsv)
        if not boxes:
            return "Box not found — answer shown"
        if len(boxes) != len(values):
            return f"{len(values)} answers, {len(boxes)} boxes — auto-fill skipped"
        # Match answers to boxes by position when the model gave hints; otherwise
        # keep the reading-order pairing (i-th answer -> i-th box).
        perm = assign_answers_to_boxes(boxes, positions, img.size)
        box_values = (
            [values[perm[i]] for i in range(len(boxes))] if perm else list(values)
        )
        check = get_config_value("check_button")
        sqrt_button = get_config_value("sqrt_button")
        needs_sqrt = any(
            k == "button" and v == "sqrt"
            for line in box_values
            for k, v in math_keyseq(line)
        )
        if needs_sqrt and not sqrt_button:
            return "√ button not set — fill skipped"
        try:
            for (bx, by), line in zip(boxes, box_values):
                pyautogui.click(offset[0] + bx, offset[1] + by)
                time.sleep(0.15)
                type_math(line, sqrt_button=sqrt_button)
                time.sleep(0.15)
            n = len(boxes)
            suffix = f" ({n} boxes)" if n > 1 else ""
            if check:
                pyautogui.click(check[0], check[1])
                return f"Filled & submitted{suffix}"
            pyautogui.press("enter")
            return f"Filled & submitted (Enter){suffix}"
        except pyautogui.FailSafeException:
            return "Aborted (failsafe)"
        except Exception as e:
            return f"Auto-fill error: {e}"

    def change_key(self):
        key = simpledialog.askstring(
            "API Key",
            "Enter your Google Gemini API key\n(free at aistudio.google.com/apikey):",
            show="*",
            parent=self.root,
        )
        if key:
            self.api_key = key.strip()
            save_api_key(self.api_key)
            print("API key updated.")

    def start_countdown(self):
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.config(state=tk.DISABLED)
        self._countdown(COUNTDOWN_SECONDS)

    def _countdown(self, remaining):
        if remaining > 0:
            self.status.set(f"Switch to your window... {remaining}")
            self.root.after(1000, self._countdown, remaining - 1)
        else:
            self.status.set("Capturing...")
            self.root.after(50, self._do_capture)

    def _do_capture(self):
        img, offset = capture_active_window()
        self.status.set("Solving...")
        threading.Thread(target=self._call_api, args=(img, offset), daemon=True).start()

    def _call_api(self, img, offset):
        parsed = []
        try:
            raw = solve_math(self.api_key, img, self.model)
            record_use()
            print(raw)  # full reply (incl. working) for the console; GUI shows answers only
            parsed = parse_answers(raw)
            display = answers_display(parsed, raw)
        except requests.exceptions.HTTPError as e:
            display = f"API error {e.response.status_code}: {e.response.text[:200]}"
        except requests.exceptions.ConnectionError:
            display = "Connection error — check your internet connection."
        except requests.exceptions.Timeout:
            display = "Request timed out. Try again."
        except Exception as e:
            display = f"Error: {e}"
        print(display)
        self.root.after(0, lambda: self._finish(display, img, offset, parsed))

    def _finish(self, display, img, offset, parsed):
        self._set_result(display)
        self._update_usage()
        status = "Ready"
        if parsed and self.auto_fill_var.get():
            status = self._autofill(img, offset, parsed)
        self.status.set(status)
        self.scan_btn.config(state=tk.NORMAL)
        self._scanning = False

    # --- Debug: diagnose box detection (no API call) ---

    def debug_detect(self):
        """Capture the active window and report what box detection sees."""
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.config(state=tk.DISABLED)
        self._debug_countdown(COUNTDOWN_SECONDS)

    def _debug_countdown(self, remaining):
        if remaining > 0:
            self.status.set(f"Switch to your window... {remaining}")
            self.root.after(1000, self._debug_countdown, remaining - 1)
        else:
            self.status.set("Capturing for debug...")
            self.root.after(50, self._do_debug)

    def _do_debug(self):
        img, _ = capture_active_window()
        box_hsv = get_config_value("box_color_hsv") or DEFAULT_BOX_HSV
        report, mask = diagnose_boxes(img, box_hsv)
        # Save only the mask — it's just the matched outlines (black/white), so it
        # contains no readable screen content. The full screenshot is never written.
        try:
            if mask is not None:
                here = os.path.dirname(os.path.abspath(__file__))
                Image.fromarray(mask).save(os.path.join(here, "debug_mask.png"))
                report += "\nSaved debug_mask.png (matched outlines only — no screen content)."
        except Exception as e:
            report += f"\n(could not save debug mask: {e})"
        print(report)
        self._set_result(report)
        self.status.set("Debug done — see Answer box")
        self.scan_btn.config(state=tk.NORMAL)
        self._scanning = False


def main():
    parser = argparse.ArgumentParser(description="Screen Math Solver")
    parser.add_argument("--api-key", help="Google Gemini API key")
    parser.add_argument(
        "--model",
        default=None,
        help=f"model name; one-off override of the saved/default model ({MODEL})",
    )
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=DAILY_LIMIT,
        help=f"free-tier requests/day for the usage counter (default: {DAILY_LIMIT})",
    )
    args = parser.parse_args()

    api_key = (args.api_key or "").strip() or load_api_key()
    # Model priority: explicit --model (one-off) > last saved choice > default.
    model = args.model or get_config_value("model") or MODEL

    root = tk.Tk()

    if not api_key:
        api_key = simpledialog.askstring(
            "API Key",
            "Enter your Google Gemini API key\n(get one free at aistudio.google.com/apikey):",
            show="*",
            parent=root,
        ) or ""
        api_key = api_key.strip()
        if api_key:
            save_api_key(api_key)

    if not HAS_PYGETWINDOW:
        print(
            "Warning: pygetwindow not installed — falling back to full-screen capture.\n"
            "Install it with:  pip install pygetwindow"
        )

    app = MathSolverApp(root, api_key, model, args.daily_limit)
    print(f"Math Solver running. Click 'Scan & Solve' then switch to your math window.")
    root.mainloop()


if __name__ == "__main__":
    main()
