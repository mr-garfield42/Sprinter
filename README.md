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

### Options

```
python math_solver.py --api-key YOUR_KEY      # pass key directly
python math_solver.py --model gemini-2.5-pro  # use a different model
python math_solver.py --daily-limit 250       # adjust the usage-counter cap
```

## Notes

- `config.json` holds your API key and usage counter and is **gitignored** —
  never commit it.
- The usage counter is tracked locally; Google does not expose a live
  remaining-quota value.
