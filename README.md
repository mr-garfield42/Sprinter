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

**Multiple boxes:** if a problem has more than one answer blank (e.g. a system of
equations, `x = ___` and `y = ___`, or a "quotient + remainder/divisor" division
answer), Sprinter detects all the boxes and fills each one. The AI reports where
each blank sits on screen, and Sprinter matches every answer to the nearest box
by position — so each value lands in the right blank even when they're laid out
two-dimensionally (e.g. an inline quotient next to a higher remainder numerator,
which a simple top-to-bottom ordering would swap). It then clicks **Check** once
to submit them together. Auto-fill only proceeds when the number of answers
matches the number of boxes detected; otherwise it just shows the answers ("N
answers, M boxes — auto-fill skipped").

**Safety / limits:**

- **Abort any time** by slamming the mouse cursor into a screen corner
  (PyAutoGUI failsafe).
- Auto-fill only fires when the **number of answers matches the number of boxes**
  detected (one box, or several); otherwise it just shows the answer ("Box not
  found" / "N answers, M boxes — auto-fill skipped").
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

**Troubleshooting "Box not found":**

- Sprinter is a desktop app — code changes only take effect after you **fully
  close and relaunch** it (it doesn't hot-reload).
- Click the **Debug** button (next to *Key*), then switch to your math window
  during the countdown. It makes **no API call** and writes a report into the
  Answer box: how many blue regions it found and, for each, whether it's usable
  as a box (or why it was rejected — *too small/big/tall/wide*). It also saves
  `debug_mask.png` (just the matched outlines in black/white — no readable screen
  content) next to the app, so you can see exactly what detection is keying on.
- *0 blue regions* → the outline color doesn't match; re-run **Set box color**,
  hovering precisely on a box edge. *Regions found but dropped* → the size/aspect
  thresholds need tuning for your boxes (tell us the numbers from the report).

### Options

```
python math_solver.py --api-key YOUR_KEY          # pass key directly
python math_solver.py --model gemini-2.5-flash     # stronger reasoning, lower free quota
python math_solver.py --daily-limit 250            # adjust the usage-counter cap
```

The default model is **gemini-2.5-flash-lite** — a free vision model with the
highest free-tier throughput (~1,000 requests/day, 30/min), but it's also the
**weakest at reading dense math notation** (superscripts, radicals, fractions).
For problems with exponents/roots/fractions, switch to a stronger model:

- `gemini-2.5-flash` — much better at parsing notation, still a decent free quota.
- `gemini-2.5-pro` — the most capable (best at reading compact notation
  correctly), but the **lowest** free quota — best saved for the tricky ones.

You don't need the flag for this: the window has a **Model** dropdown to switch
between models on the fly. Your choice is saved, so when one model's daily quota
runs out you can flip to another and back again later when it resets.

**Tip:** most wrong answers on expand/simplify problems come from the model
*mis-reading* a tiny superscript or radical, not from bad algebra. **Zoom in your
browser (Ctrl + +) so the expression is large** before scanning — bigger, clearer
notation is read far more reliably.

## Notes

- The model is asked to **show its working first, then list each final answer**;
  Sprinter extracts just the answers to display/type. Letting it reason makes a
  big difference on problems that need calculation (exponentials, logs, multi-step
  arithmetic) — but it's still an AI and **can be wrong**, so check answers on
  anything that matters. For harder problems, switch to `gemini-2.5-flash` (the
  **Model** dropdown) for stronger reasoning.
- `config.json` holds your API key and usage counter and is **gitignored** —
  never commit it.
- The usage counter is tracked locally; Google does not expose a live
  remaining-quota value.
