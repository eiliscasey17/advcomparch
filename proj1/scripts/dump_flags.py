#!/usr/bin/env python3
"""Dump the exact compiler flags (from config/flags.mk via make) and compiler
versions as JSON -> results/env/flags.json."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_var(name):
    out = subprocess.run(["make", "-s", "-C", str(ROOT), "--no-print-directory",
                          f"print-{name}"], capture_output=True, text=True)
    return out.stdout.strip()


def version(cc):
    return subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.splitlines()[0]


def resolved_march(cc, flags):
    """What -march=native actually turns into."""
    if "clang" in cc:
        out = subprocess.run([cc, *flags.split(), "-###", "-x", "c", "-c", "/dev/null"],
                             capture_output=True, text=True).stderr
        toks = out.replace('"', "").split()
        return toks[toks.index("-target-cpu") + 1] if "-target-cpu" in toks else None
    out = subprocess.run([cc, *flags.split(), "-Q", "--help=target"],
                         capture_output=True, text=True).stdout
    keep = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] in ("-march=", "-mtune=", "-mprefer-vector-width="):
            keep[parts[0].rstrip("=")] = parts[1] if len(parts) > 1 else ""
    return keep


def main():
    compilers = make_var("COMPILERS").split()
    configs = make_var("CONFIGS").split()
    base = make_var("KERNEL_BASE")
    out = {"kernel_base": base, "fixed_cc": make_var("FIXED_CC"),
           "fixed_cflags": make_var("FIXED_CFLAGS"), "ref_cflags": make_var("REF_CFLAGS"),
           "compilers": {}, "builds": {}}
    for c in compilers:
        cc = make_var(f"CC_{c}")
        out["compilers"][c] = {"path": cc, "version": version(cc)}
        for f in configs:
            flags = make_var(f"CFG_{c}_{f}")
            out["builds"][f"{c}-{f}"] = {"cc": cc, "flags": f"{base} {flags}",
                                         "resolved": resolved_march(cc, flags)}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
