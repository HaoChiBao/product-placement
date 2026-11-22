/** Episode time the result clip starts at. Wan may clamp a long moment. */
export function playbackOrigin(momentStart: number, clipStart?: number | null): number {
  return typeof clipStart === "number" && Number.isFinite(clipStart) ? clipStart : momentStart
}

/** Episode time the result clip ends at. */
export function playbackEnd(momentEnd: number, clipEnd?: number | null): number {
  return typeof clipEnd === "number" && Number.isFinite(clipEnd) ? clipEnd : momentEnd
}
