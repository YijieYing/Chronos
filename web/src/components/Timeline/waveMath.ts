import type { TimelineTask } from "../../types";

// Inspiration/creative work → execution/operational work.
const paletteStart = [124, 205, 108];
const paletteEnd = [55, 116, 211];

export function spectrumColor(position: number, alpha = 1): string {
  const value = Math.max(0, Math.min(1, position));
  const rgb = paletteStart.map((start, index) =>
    Math.round(start + (paletteEnd[index] - start) * value),
  );
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

export function waveformAreaPath(
  start: number,
  end: number,
  baseline: number,
  amplitude: number,
  seed: number,
): string {
  const width = Math.max(2, end - start);
  const steps = Math.max(12, Math.ceil(width / 5));
  const upper: Array<[number, number]> = [];
  const lower: Array<[number, number]> = [];
  for (let index = 0; index <= steps; index += 1) {
    const progress = index / steps;
    const x = start + progress * width;
    const envelope = 0.45 + Math.sin(progress * Math.PI) * 0.55;
    const texture =
      0.46 +
      Math.abs(Math.sin(index * 1.73 + seed) * 0.29) +
      Math.abs(Math.sin(index * 0.47 + seed * 2.1) * 0.2) +
      Math.abs(Math.cos(index * 2.8 + seed) * 0.08);
    const magnitude = amplitude * envelope * texture;
    upper.push([x, baseline - magnitude]);
    lower.push([x, baseline + magnitude]);
  }
  return [
    `M ${upper[0][0]} ${upper[0][1]}`,
    ...upper.slice(1).map(([x, y]) => `L ${x} ${y}`),
    ...lower.reverse().map(([x, y]) => `L ${x} ${y}`),
    "Z",
  ].join(" ");
}

export function fixedWavePath(
  start: number,
  end: number,
  baseline: number,
  amplitude: number,
): string {
  return [
    `M ${start} ${baseline + amplitude}`,
    `L ${start} ${baseline - amplitude}`,
    `L ${end} ${baseline - amplitude}`,
    `L ${end} ${baseline + amplitude}`,
    "Z",
  ].join(" ");
}

export function taskSeed(task: TimelineTask): number {
  return [...task.id].reduce((total, character) => total + character.charCodeAt(0), 0) % 31;
}
