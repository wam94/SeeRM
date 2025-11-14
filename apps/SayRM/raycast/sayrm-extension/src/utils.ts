export function splitLines(input: string): string[] {
  return input
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function formatTimestamp(input: string): string {
  const date = new Date(input);
  if (isNaN(date.getTime())) {
    return input;
  }
  return date.toLocaleString();
}

