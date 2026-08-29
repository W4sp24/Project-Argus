Notation rules (these are narrower than usual, because the answer is JSON):

- **Double every backslash.** JSON reads a backslash as an escape, so LaTeX
  must be written `\\frac{1}{2}`, `\\alpha`, `\\sum`. A single backslash either
  breaks the whole reply or silently turns `\f`, `\t`, `\b`, `\r` and `\n` into
  invisible control characters.
- **Inline `$ ... $` only.** No `$$` display blocks anywhere — every field here
  is rendered inside a line of a question, where a display block breaks the
  layout around it.
- **`q` and each `options` entry is a single line.** No newlines in either.
- **`answer` carries no notation at all** for a `short` or `problem` question:
  no `$`, no backslash. It is compared against a plain-text box a student typed
  into, and nobody types `\\frac{1}{2}` — write `1/2`. For an `mcq`, `answer`
  must instead be one of the `options` strings, character for character.
- Put the mathematics in `explanation`, and in `q` where the question needs it.
- Write a literal dollar sign as `\\$`.
