export function cognitiveLoadColor(value: number, alpha = 1) {
  const load = Math.max(0, Math.min(1, value));
  const green = [139, 207, 146];
  const yellow = [227, 199, 101];
  const red = [217, 110, 98];
  const [from, to, progress] =
    load <= 0.5
      ? [green, yellow, load * 2]
      : [yellow, red, (load - 0.5) * 2];
  const rgb = from.map((channel, index) =>
    Math.round(channel + (to[index] - channel) * progress),
  );
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}
