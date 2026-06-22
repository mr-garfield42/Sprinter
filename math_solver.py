import argparse
import base64
import io
import json
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import simpledialog, ttk

import requests
from PIL import ImageGrab

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
    "gemini-2.5-flash-lite",  # ~1000/day, 30/min — most free throughput
    "gemini-2.5-flash",       # stronger reasoning, lower free quota
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
BOX_MIN_AREA = 250      # bounding-box px^2 — reject specks
BOX_MAX_AREA = 120000   # reject huge blue regions (panels, banners)
BOX_MIN_ASPECT = 0.4    # width / height
BOX_MAX_ASPECT = 6.0

PROMPT = (
    "Look at this screenshot and find any math problem, equation, or word problem. "
    "Respond with ONLY the final answer — no explanation, no steps, no working out. "
    "If there are multiple problems, put each answer on its own line. "
    "Write the answer in plain linear/calculator notation that can be typed on a "
    "keyboard: use ^ for exponents (e.g. x^2), / for fractions (e.g. 3/4), * for "
    "multiplication, and pi for the constant pi. For square roots write sqrt(...) "
    "with the radicand ALWAYS in parentheses, e.g. sqrt(2), sqrt(x+1), 2*sqrt(3). "
    "Do NOT use Unicode superscripts/symbols, LaTeX, or \\frac."
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


def find_answer_box(img, box_hsv):
    """Locate the answer box by its outline color.

    ``box_hsv`` is the calibrated outline color ``[h, s, v]`` (OpenCV HSV).
    Returns the box center ``(cx, cy)`` in image pixels, or ``None`` unless
    exactly one plausible box is found (0 or >1 candidates -> skip auto-fill).
    """
    if not HAS_AUTOFILL or not box_hsv:
        return None
    rgb = np.array(img.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = box_hsv
    lower = np.array([max(h - BOX_H_TOL, 0), max(s - BOX_S_TOL, 0), max(v - BOX_V_TOL, 0)])
    upper = np.array([min(h + BOX_H_TOL, 179), min(s + BOX_S_TOL, 255), min(v + BOX_V_TOL, 255)])
    mask = cv2.inRange(hsv, lower, upper)
    # Close small gaps so the outline forms one connected contour
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
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
        candidates.append((x + w // 2, y + ht // 2))
    if len(candidates) == 1:
        return candidates[0]
    return None


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

    def _autofill(self, img, offset, answer):
        """Click the box, type the answer, click Check. Returns a status string."""
        if not HAS_AUTOFILL:
            return "Auto-fill needs: pip install pyautogui opencv-python"
        box_hsv = get_config_value("box_color_hsv") or DEFAULT_BOX_HSV
        lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
        if len(lines) != 1:
            return f"{len(lines)} answers — auto-fill skipped"
        box = find_answer_box(img, box_hsv)
        if box is None:
            return "Box not found — answer shown"
        sx, sy = offset[0] + box[0], offset[1] + box[1]
        check = get_config_value("check_button")
        sqrt_button = get_config_value("sqrt_button")
        needs_sqrt = any(k == "button" and v == "sqrt" for k, v in math_keyseq(lines[0]))
        if needs_sqrt and not sqrt_button:
            return "√ button not set — fill skipped"
        try:
            pyautogui.click(sx, sy)
            time.sleep(0.15)
            type_math(lines[0], sqrt_button=sqrt_button)
            time.sleep(0.15)
            if check:
                pyautogui.click(check[0], check[1])
                return "Filled & submitted"
            pyautogui.press("enter")
            return "Filled & submitted (Enter)"
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
        answer = None
        try:
            raw = solve_math(self.api_key, img, self.model)
            record_use()
            display = raw.strip()
            answer = display
        except requests.exceptions.HTTPError as e:
            display = f"API error {e.response.status_code}: {e.response.text[:200]}"
        except requests.exceptions.ConnectionError:
            display = "Connection error — check your internet connection."
        except requests.exceptions.Timeout:
            display = "Request timed out. Try again."
        except Exception as e:
            display = f"Error: {e}"
        print(display)
        self.root.after(0, lambda: self._finish(display, img, offset, answer))

    def _finish(self, display, img, offset, answer):
        self._set_result(display)
        self._update_usage()
        status = "Ready"
        if answer and self.auto_fill_var.get():
            status = self._autofill(img, offset, answer)
        self.status.set(status)
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
