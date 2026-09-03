"use client";

import Markdown from "@/components/Markdown";

/**
 * One flip-card, and the accessibility contract it must keep.
 *
 * The faces are `<div>`s and the flip is a separate button beneath them. They
 * used to be two `<button>`s, which stopped working the moment a face rendered
 * markdown (recorded 2026-08-28, and the reasons have not changed):
 *
 *   - Each carried an `aria-label` built from the raw card text, and an
 *     `aria-label` overrides descendant content. Once a face is typeset, a
 *     screen reader would read the LaTeX source — "dollar backslash frac open
 *     brace" — which is worse than the plain text it replaced.
 *   - A markdown link inside a `<button>` is invalid HTML: an interactive
 *     element cannot contain another. Firefox activates the button when the
 *     link is clicked.
 *   - Both faces stay mounted (`backface-visibility`, not `display: none`), so
 *     both were focusable and both were in the accessibility tree, one of them
 *     invisible.
 *
 * KaTeX marks its own visual tree `aria-hidden` and exposes MathML beside it,
 * so the accessible answer is to render the content and get out of its way.
 *
 * `data-testid` still carries the structural flip-state signal for e2e, since
 * `backface-visibility` is not something a visibility assertion can see.
 */
export default function CardFace({
  front,
  back,
  hint,
  flipped,
  onFlip,
  className = "h-56",
}: {
  front: string;
  back: string;
  hint?: string | null;
  flipped: boolean;
  onFlip: () => void;
  className?: string;
}) {
  return (
    <div className={`flip-card ${className}`}>
      <div data-testid="flashcard-inner" className={`flip-card-inner ${flipped ? "is-flipped" : ""}`}>
        <div
          data-testid="flashcard-front"
          aria-hidden={flipped}
          {...(flipped ? { inert: true } : {})}
          onClick={onFlip}
          className="flip-card-face flip-card-front flex w-full cursor-pointer items-center justify-center overflow-auto border border-line bg-sunken p-6 text-center text-lead text-ink-bright"
        >
          <Markdown text={front} className="text-lead" />
        </div>
        <div
          data-testid="flashcard-back"
          aria-hidden={!flipped}
          {...(flipped ? {} : { inert: true })}
          onClick={onFlip}
          className="flip-card-face flip-card-back flex w-full cursor-pointer items-center justify-center overflow-auto border border-[var(--ac)] bg-[var(--ac-bg)] p-6 text-center text-lead text-ink-bright"
        >
          <Markdown text={back} className="text-lead" />
        </div>
      </div>
      {hint ? (
        <p className="mt-1 font-mono text-meta text-ink-faint">
          <span className="text-[var(--ac)]">hint</span> :: {hint}
        </p>
      ) : null}
    </div>
  );
}
