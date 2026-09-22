"""Quantify the GlassDemo multiscale backdrop-blur VNC fixture.

The opt-in fixture leaves the real material=13, WithinWindow
NSVisualEffectView untouched and draws three black/white stripe frequencies
behind it.  This tool compares exposed sharp rows with rows covered by that
effect in a raw 32-bit B,G,R,0 RFB capture.
"""

import argparse
import cmath
import math
import os


def parse_range(value):
    try:
        start, end = (int(part, 0) for part in value.split(":", 1))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("range must be START:END")
    if start < 0 or end <= start:
        raise argparse.ArgumentTypeError("range must be increasing")
    return start, end


def grayscale_profile(frame, width, x_range, y_range):
    x_start, x_end = x_range
    y_start, y_end = y_range
    rows = y_end - y_start
    result = []
    for x in range(x_start, x_end):
        total = 0.0
        for y in range(y_start, y_end):
            offset = (y * width + x) * 4
            total += sum(frame[offset:offset + 3]) / 3.0
        result.append(total / rows)
    return result


def profile_metrics(values, period):
    count = len(values)
    fundamental = 2.0 * abs(sum(
        value * cmath.exp(-2j * math.pi * index / period)
        for index, value in enumerate(values))) / count
    mean_abs_dx = sum(abs(values[index] - values[index - 1])
                      for index in range(1, count)) / (count - 1)
    return fundamental, mean_abs_dx, min(values), max(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", help="32-bit B,G,R,0 framebuffer from vnc_capture.py")
    parser.add_argument("--width", type=int, default=2388)
    parser.add_argument("--height", type=int, default=1668)
    parser.add_argument("--stripe-x", type=int, default=338)
    parser.add_argument("--stripe-width", type=int, default=1752)
    parser.add_argument("--effect-x", type=int, default=394)
    parser.add_argument("--effect-width", type=int, default=1640)
    parser.add_argument("--sharp-y", type=parse_range, default=(1100, 1136))
    parser.add_argument("--effect-y", type=parse_range, default=(1160, 1200))
    args = parser.parse_args()

    expected = args.width * args.height * 4
    actual = os.path.getsize(args.raw)
    if actual != expected:
        parser.error("raw size is %#x, expected %#x for %dx%d BGRA" %
                     (actual, expected, args.width, args.height))
    with open(args.raw, "rb") as source:
        frame = source.read()

    section_width = args.stripe_width // 3
    effect_end = args.effect_x + args.effect_width
    periods = (("high-4pt", 16), ("mid-16pt", 64), ("low-48pt", 192))
    results = {}
    for section, (name, period) in enumerate(periods):
        section_start = args.stripe_x + section * section_width
        section_end = (args.stripe_x + (section + 1) * section_width
                       if section < 2 else args.stripe_x + args.stripe_width)
        usable_start = max(section_start, args.effect_x)
        usable_end = min(section_end, effect_end)
        x_start = ((usable_start + period - 1) // period) * period
        cycles = (usable_end - x_start) // period
        if cycles < 2:
            parser.error("not enough complete %d-pixel cycles in section %s" %
                         (period, name))
        x_range = (x_start, x_start + cycles * period)

        sharp = profile_metrics(grayscale_profile(
            frame, args.width, x_range, args.sharp_y), period)
        effect = profile_metrics(grayscale_profile(
            frame, args.width, x_range, args.effect_y), period)
        suppression_db = (20.0 * math.log10(sharp[0] / effect[0])
                          if effect[0] else math.inf)
        results[name] = (sharp, effect, suppression_db)
        print("%s x=%d:%d period=%dpx" %
              (name, x_range[0], x_range[1], period))
        print("  sharp fundamental=%.3f mean_abs_dx=%.3f min=%.2f max=%.2f" %
              sharp)
        print("  effect fundamental=%.3f mean_abs_dx=%.3f min=%.2f max=%.2f" %
              effect)
        print("  suppression=%.2f dB" % suppression_db)

    high_sharp, high_effect, high_db = results["high-4pt"]
    low_sharp, low_effect, low_db = results["low-48pt"]
    source_is_sharp = (high_sharp[2] <= 5.0 and high_sharp[3] >= 250.0 and
                       low_sharp[2] <= 5.0 and low_sharp[3] >= 250.0)
    high_is_filtered = high_db >= 40.0
    low_still_influences_output = low_effect[0] >= 5.0
    low_is_filtered = low_db >= 6.0
    passed = (source_is_sharp and high_is_filtered and
              low_still_influences_output and low_is_filtered)
    print("BLUR_AB_RESULT source_sharp=%s high_filtered=%s "
          "low_backdrop_influence=%s low_filtered=%s pass=%s" %
          (source_is_sharp, high_is_filtered, low_still_influences_output,
           low_is_filtered, passed))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
