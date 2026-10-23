import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  new URL("./design-system.css", import.meta.url),
  "utf8",
);
function token(name: string): string {
  const value = css.match(new RegExp(`--${name}:\\s*(#[a-f0-9]+);`, "i"))?.[1];
  if (!value) throw new Error(`Missing color token: ${name}`);
  return value;
}
function luminance(color: string): number {
  const full =
    color.length === 4
      ? color
          .slice(1)
          .split("")
          .map((char) => char + char)
          .join("")
      : color.slice(1);
  const linear = [0, 2, 4].map((start) => {
    const channel = parseInt(full.slice(start, start + 2), 16) / 255;
    return channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(a: string, b: string): number {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("workspace semantic palette", () => {
  it.each(["#ffffff", "#f7f8f5", "#f2f5ef", "#eef2eb"])(
    "keeps body and supporting text readable on %s",
    (background) => {
      expect(contrast(token("ink"), background)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(token("muted"), background)).toBeGreaterThanOrEqual(4.5);
    },
  );
  it.each([
    ["success-ink", "#edf4e8"],
    ["warning-ink", "#f8f0e0"],
    ["danger-ink", "#f7ece8"],
  ])(
    "keeps %s status text readable without relying on color alone",
    (name, background) => {
      expect(contrast(token(name), background)).toBeGreaterThanOrEqual(4.5);
    },
  );
  it.each(["#ffffff", "#f7f8f5", "#f2f5ef"])(
    "preserves a visible control boundary on %s",
    (background) => {
      expect(
        contrast(token("control-border"), background),
      ).toBeGreaterThanOrEqual(3);
    },
  );
  it("keeps the primary action and focus color visible", () => {
    expect(contrast(token("green"), "#ffffff")).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("green"), token("soft"))).toBeGreaterThanOrEqual(3);
  });
});
