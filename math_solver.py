import argparse
import base64
import io
import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import simpledialog

import requests
from PIL import ImageGrab

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
# Google Gemini, via its OpenAI-compatible endpoint. Free tier includes vision.
# Get a free API key at https://aistudio.google.com/apikey
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL = "gemini-2.5-flash"  # free-tier vision model; override with --model
GUI_TITLE = "Math Solver"
COUNTDOWN_SECONDS = 3
DAILY_LIMIT = 1500  # gemini-2.5-flash free-tier requests/day; override with --daily-limit

PROMPT = (
    "Look at this screenshot and find any math problem, equation, or word problem. "
    "Respond with ONLY the final answer — no explanation, no steps, no working out. "
    "If there are multiple problems, put each answer on its own line."
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


def _active_window_bbox_windows():
    """Active-window bounds (left, top, right, bottom) in pixels, or None."""
    try:
        import pygetwindow as gw

        win = gw.getActiveWindow()
        if win and win.title and win.title != GUI_TITLE:
            left, top = win.left, win.top
            right, bottom = left + win.width, top + win.height
            if right > left and bottom > top:
                return (left, top, right, bottom)
    except Exception:
        pass
    return None


def _active_window_bbox_macos():
    """Frontmost-window bounds (left, top, right, bottom) in logical points, or None.

    Uses Quartz directly. Our own always-on-top GUI sits at a floating window
    level (layer != 0), so filtering on the normal layer skips it.
    """
    try:
        import Quartz

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListExcludeDesktopElements
            | Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )
        for win in windows:
            if win.get("kCGWindowLayer") != 0:
                continue
            name = win.get(Quartz.kCGWindowName, "") or ""
            if name == GUI_TITLE:
                continue
            bounds = win.get("kCGWindowBounds")
            if not bounds:
                continue
            left, top = int(bounds["X"]), int(bounds["Y"])
            right, bottom = left + int(bounds["Width"]), top + int(bounds["Height"])
            if right > left and bottom > top:
                return (left, top, right, bottom)
    except Exception:
        pass
    return None


def capture_active_window():
    """Capture the currently active window. Falls back to full screen."""
    full = ImageGrab.grab()  # whole screen; pixels (Retina-scaled on macOS)

    if IS_WIN:
        bbox = _active_window_bbox_windows()
        if bbox:
            try:
                return ImageGrab.grab(bbox=bbox)
            except Exception:
                pass
        return full

    if IS_MAC:
        bbox = _active_window_bbox_macos()
        if bbox:
            # Quartz reports logical points; ImageGrab returns physical pixels.
            # Scale the crop box so it lines up on Retina (and non-Retina) displays.
            scale = 1.0
            try:
                import Quartz

                logical_w = Quartz.CGDisplayBounds(
                    Quartz.CGMainDisplayID()
                ).size.width
                if logical_w:
                    scale = full.width / logical_w
            except Exception:
                pass
            left, top, right, bottom = (int(v * scale) for v in bbox)
            try:
                return full.crop((left, top, right, bottom))
            except Exception:
                pass
        return full

    return full


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
        root.geometry(f"300x260+{sw - 320}+20")

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
        img = capture_active_window()
        self.status.set("Solving...")
        threading.Thread(target=self._call_api, args=(img,), daemon=True).start()

    def _call_api(self, img):
        try:
            answer = solve_math(self.api_key, img, self.model)
            record_use()
            display = answer.strip()
        except requests.exceptions.HTTPError as e:
            display = f"API error {e.response.status_code}: {e.response.text[:200]}"
        except requests.exceptions.ConnectionError:
            display = "Connection error — check your internet connection."
        except requests.exceptions.Timeout:
            display = "Request timed out. Try again."
        except Exception as e:
            display = f"Error: {e}"
        print(display)
        self.root.after(0, lambda: self._finish(display))

    def _finish(self, display):
        self._set_result(display)
        self._update_usage()
        self._reset()

    def _reset(self):
        self.status.set("Ready")
        self.scan_btn.config(state=tk.NORMAL)
        self._scanning = False


def main():
    parser = argparse.ArgumentParser(description="Screen Math Solver")
    parser.add_argument("--api-key", help="Google Gemini API key")
    parser.add_argument("--model", default=MODEL, help=f"model name (default: {MODEL})")
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=DAILY_LIMIT,
        help=f"free-tier requests/day for the usage counter (default: {DAILY_LIMIT})",
    )
    args = parser.parse_args()

    api_key = (args.api_key or "").strip() or load_api_key()

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

    # Warn if the per-window capture helper for this OS is unavailable; the app
    # still works, it just captures the whole screen instead of one window.
    if IS_WIN:
        try:
            import pygetwindow  # noqa: F401
        except ImportError:
            print(
                "Note: pygetwindow not installed — capturing the full screen.\n"
                "For active-window capture:  pip install pygetwindow"
            )
    elif IS_MAC:
        try:
            import Quartz  # noqa: F401
        except ImportError:
            print(
                "Note: pyobjc (Quartz) not installed — capturing the full screen.\n"
                "For active-window capture:  pip install pyobjc-framework-Quartz"
            )

    app = MathSolverApp(root, api_key, args.model, args.daily_limit)
    print(f"Math Solver running. Click 'Scan & Solve' then switch to your math window.")
    root.mainloop()


if __name__ == "__main__":
    main()
