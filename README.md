# Sprinter — Screen Math Solver

A small desktop tool that captures the active window, sends the screenshot to
Google Gemini (vision), and shows the answer to any math problem on screen —
arithmetic, algebra, and word problems.

## Setup

1. Install Python 3.9+.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Get a **free** Gemini API key at https://aistudio.google.com/apikey
   (no credit card required).

## Usage

**Easiest:** double-click **`Run Sprinter.bat`**. It checks the dependencies
(installing them if needed) and launches the GUI with no console window — handy
on Windows machines that have no `.py` file association set up.

Or run it directly:

```
python math_solver.py
```

- A small always-on-top window appears in the top-right corner.
- On first run, paste your Gemini API key (saved locally to `config.json`).
- Click **Scan & Solve**, then switch to the window with the math problem
  within the 3-second countdown.
- The answer appears in the window's **Answer** box (and in the terminal).

The bottom of the window shows your daily usage against the Gemini free-tier
limit (resets at midnight US Pacific).

## Auto-fill (optional)

For self-study/practice, Sprinter can type the answer into the on-screen answer
box and click **Check** for you. The answer box is **found automatically** by
its color (a fixed pure blue), even though it moves between problems — no
calibration needed for it. The Check button is a fixed position you set once.

**One-time calibration** (buttons in the window):

1. **Set Check ✓** — click it, then hover your cursor over the **Check** button
   before the countdown ends. Its position is saved.
2. **Set √** *(optional)* — only needed if your answers include square roots.
   Square roots can't be typed on platforms like DeltaMath; they're inserted by
   clicking a palette button. Click **Set √**, then hover over that button.
   Sprinter clicks it automatically when an answer contains `sqrt(...)`.

The line under the buttons shows whether each is `set`. If an answer needs a
square root but **√** isn't calibrated, auto-fill is skipped ("√ button not
set").

**Use it:** tick **Auto-fill answer & submit**, then **Scan & Solve** as usual.
After solving, Sprinter clicks the box, types the answer, and clicks Check.

**Safety / limits:**

- **Abort any time** by slamming the mouse cursor into a screen corner
  (PyAutoGUI failsafe).
- Auto-fill only fires when there's **exactly one** answer and **exactly one**
  box is detected; otherwise it just shows the answer ("Box not found" /
  "N answers — auto-fill skipped").
- Sprinter types answers in linear notation (`^` for exponents, `/` for
  fractions) and presses the Right arrow to exit each exponent/fraction block,
  so **MathQuill-style fields** (e.g. DeltaMath) render correctly. Square roots
  are inserted by clicking the calibrated **√** button (see above). Functions
  beyond `sqrt` that also require a palette button aren't handled yet.
- Box detection assumes the fixed blue outline color and a primary-display
  setup; a different theme (override with **Set box color**) or a multi-monitor
  layout may need adjustment/testing.
- Calibration is saved in `config.json` (`auto_fill`, `box_color_hsv`,
  `check_button`).

### Options

```
python math_solver.py --api-key YOUR_KEY          # pass key directly
python math_solver.py --model gemini-2.5-flash     # stronger reasoning, lower free quota
python math_solver.py --daily-limit 250            # adjust the usage-counter cap
```

The default model is **gemini-2.5-flash-lite** — a free vision model with the
highest free-tier throughput (~1,000 requests/day, 30/min). If you find answers
less accurate on harder problems, switch to `gemini-2.5-flash` for stronger
reasoning at a lower free quota.

You don't need the flag for this: the window has a **Model** dropdown to switch
between models on the fly. Your choice is saved, so when one model's daily quota
runs out you can flip to another and back again later when it resets.

## Notes

- `config.json` holds your API key and usage counter and is **gitignored** —
  never commit it.
- The usage counter is tracked locally; Google does not expose a live
  remaining-quota value.
