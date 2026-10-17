"""Parse compiler vectorization reports and disassembly for the kernel builds.

Used by tests/test_vectorization.py (assertions) and scripts/vec_evidence.py
(concise evidence files for the write-up).

Key ideas
- Each dtype is its own TU including src/kernels_body.inc, so a report line
  "kernels_body.inc:LINE" maps to the FN(name) whose body contains LINE, and
  the dtype comes from which report file (kernels_<dtype>.*) it is in.
- The *main loop* of a function is found from backward branches in its
  disassembly; among all loops we take the one using the widest registers
  (ties -> the first), which is the vector body when one exists.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY = ROOT / "src" / "kernels_body.inc"
BUILD = ROOT / "build"
DTYPES = ("f32", "f64", "i32")

FN_RE = re.compile(r"^NOINLINE\s.*\bFN\((\w+)\)\(")


def kernel_line_map(body=BODY):
    """[(start_line, kernel_name)] in file order."""
    out = []
    for i, line in enumerate(body.read_text().splitlines(), 1):
        m = FN_RE.match(line)
        if m:
            out.append((i, m.group(1)))
    return out


FOR_RE = re.compile(r"^\s*for \(")


def kernel_loops(body=BODY):
    """kernel -> [line of each `for` statement in its body], in order. The
    first one is the kernel's main loop (later ones are remainder loops)."""
    starts = kernel_line_map(body)
    out = {n: [] for _, n in starts}
    for i, line in enumerate(body.read_text().splitlines(), 1):
        if FOR_RE.match(line):
            k = kernel_for_line(i, starts)
            if k:
                out[k].append(i)
    return out


def kernel_for_line(line, starts):
    name = None
    for s, n in starts:
        if s <= line:
            name = n
    return name


# ------------------------------------------------------------------ reports --
@dataclass
class Remark:
    kernel: str
    line: int
    status: str        # "vectorized" | "missed"
    width_bytes: int   # GCC: vector bytes; Clang: lanes*elem bytes; 0 if missed
    text: str


GCC_OPT = re.compile(r"kernels_body\.inc:(\d+):\d+: optimized: loop vectorized using (\d+) byte vectors")
GCC_MISS = re.compile(r"kernels_body\.inc:(\d+):\d+: missed: (couldn't vectorize loop|not vectorized:.*)")
CL_OPT = re.compile(r"kernels_body\.inc:(\d+):\d+: remark: vectorized loop \(vectorization width: (\d+), interleaved count: (\d+)\)")
CL_MISS = re.compile(r"kernels_body\.inc:(\d+):\d+: remark: (loop not vectorized.*?)(?: \[-R|$)")
CL_ANALYSIS = re.compile(r"kernels_body\.inc:(\d+):\d+: remark: (.*?) \[-Rpass-analysis=loop-vectorize\]")

ELEM = {"f32": 4, "f64": 8, "i32": 4}


def parse_report(build, dtype):
    """All loop-level remarks for one (build, dtype), mapped to kernels."""
    starts = kernel_line_map()
    d = BUILD / build
    out = []
    gcc = d / f"kernels_{dtype}.optinfo.txt"
    clang = d / f"kernels_{dtype}.remarks.txt"
    if gcc.exists():
        for t in gcc.read_text().splitlines():
            m = GCC_OPT.search(t)
            if m:
                ln = int(m.group(1))
                out.append(Remark(kernel_for_line(ln, starts), ln, "vectorized", int(m.group(2)), t))
                continue
            m = GCC_MISS.search(t)
            if m:
                ln = int(m.group(1))
                out.append(Remark(kernel_for_line(ln, starts), ln, "missed", 0, m.group(2)))
    elif clang.exists():
        for t in clang.read_text().splitlines():
            m = CL_OPT.search(t)
            if m:
                ln = int(m.group(1))
                out.append(Remark(kernel_for_line(ln, starts), ln, "vectorized",
                                  int(m.group(2)) * ELEM[dtype], t.split("remark: ")[1]))
                continue
            m = CL_MISS.search(t) or CL_ANALYSIS.search(t)
            if m:
                ln = int(m.group(1))
                out.append(Remark(kernel_for_line(ln, starts), ln, "missed", 0, m.group(2)))
    return [r for r in out if r.kernel]


def main_loop_lines(body=BODY):
    """kernel -> (lines accepted for GCC, lines accepted for Clang).

    Where each compiler attributes a kernel's MAIN (first) loop:
      GCC   - the `for` line, or the first body line of a multi-line loop.
              The `#pragma omp simd` line carries remarks for GCC's internal
              per-lane reduction helper loops, which are always vectorized and
              say nothing about the user loop, so it is NOT accepted.
      Clang - the `for` line, or the pragma line when one precedes the loop
              (Clang reports omp-simd / clang-loop loops there)."""
    lines = body.read_text().splitlines()
    out = {}
    for k, fors in kernel_loops(body).items():
        if not fors:
            continue
        f = fors[0]
        gcc = {f}
        if lines[f - 1].rstrip().endswith("{"):
            gcc.add(f + 1)
        clang = set(gcc)
        prev = lines[f - 2]
        if "OMP_SIMD_RED" in prev or "PRED_PRAGMA" in prev:
            clang.add(f - 1)
        out[k] = (gcc, clang)
    return out


def report_summary(build, dtype):
    """kernel -> (status, vector bytes, messages) for the kernel's MAIN loop
    (see main_loop_lines for which remark locations count)."""
    accept = main_loop_lines()
    which = 1 if build.startswith("clang") else 0
    agg = {}
    for r in parse_report(build, dtype):
        if r.kernel not in accept or r.line not in accept[r.kernel][which]:
            continue
        st, w, msgs = agg.get(r.kernel, ("missed", 0, []))
        if r.status == "vectorized":
            st, w = "vectorized", max(w, r.width_bytes)
        if r.text not in msgs:
            msgs.append(r.text)
        agg[r.kernel] = (st, w, msgs)
    return agg


# --------------------------------------------------------------- disassembly --
@dataclass
class Insn:
    addr: int
    mnem: str
    ops: str

    @property
    def width(self):
        if "zmm" in self.ops:
            return 512
        if "ymm" in self.ops:
            return 256
        if "xmm" in self.ops:
            return 128
        return 0


INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$")
SYM_RE = re.compile(r"^[0-9a-f]+ <(\w+)>:$")


def disasm(build):
    """symbol -> [Insn] from build/<build>/kernels.objdump.txt."""
    syms, cur = {}, None
    for line in (BUILD / build / "kernels.objdump.txt").read_text().splitlines():
        m = SYM_RE.match(line)
        if m:
            cur = syms.setdefault(m.group(1), [])
            continue
        m = INSN_RE.match(line)
        if m and cur is not None:
            ops = m.group(3).split("#")[0].strip()
            cur.append(Insn(int(m.group(1), 16), m.group(2), ops))
    return syms


BRANCH_RE = re.compile(r"^j[a-z]+$")
PACKED_FP = re.compile(r"^v?(add|sub|mul|div|max|min|fn?madd\d+|fn?msub\d+)p[sd]$")
SCALAR_FP = re.compile(r"^v?(add|sub|mul|div|max|min|fn?madd\d+|fn?msub\d+)s[sd]$")
INT_SIMD = re.compile(r"^v?p(add|sub|mul|madd)[a-z]*$")
GATHER = re.compile(r"^v(p)?gather")
VEC_MOVE = re.compile(r"^v?mov(up[sd]|ap[sd]|dq[au]\d*|dq[au])$")
PERMUTE = re.compile(r"^v(perm|shuf|unpck|pshuf|blend|insert|extract|broadcast)")


def loops(insns):
    """[(start_idx, end_idx)] for every backward branch inside the function."""
    by_addr = {x.addr: i for i, x in enumerate(insns)}
    out = []
    for i, x in enumerate(insns):
        if BRANCH_RE.match(x.mnem):
            tgt = x.ops.split()[0] if x.ops else ""
            try:
                t = int(tgt, 16)
            except ValueError:
                continue
            if t <= x.addr and t in by_addr:
                out.append((by_addr[t], i))
    return out


def _packed_arith(body):
    return sum(1 for x in body if x.width and (PACKED_FP.match(x.mnem) or INT_SIMD.match(x.mnem)
                                               or GATHER.match(x.mnem)))


def main_loop(insns):
    """The vector body if one exists: rank loops by widest register, then by
    whether they contain packed arithmetic at all, then by position (first
    wins). A scalar-SSE epilogue (mulss on xmm) loses to a packed xmm loop."""
    best, key = None, None
    for s, e in loops(insns):
        body = insns[s:e + 1]
        k = (max((x.width for x in body), default=0), _packed_arith(body) > 0, -s)
        if key is None or k > key:
            best, key = body, k
    return best


@dataclass
class LoopStats:
    width: int = 0               # widest register in the main loop
    packed_fp: int = 0
    scalar_fp: int = 0
    int_simd: int = 0            # packed integer arithmetic (vpaddd, vpmulld, ...)
    gathers: int = 0
    permutes: int = 0
    masked: int = 0              # instructions with an AVX-512 {%kN} write-mask
    vec_moves: int = 0           # full-vector loads/stores to memory (vmovups, vmovdqu32, ...)
    n_insns: int = 0
    mnems: list = field(default_factory=list)


def loop_stats(body):
    st = LoopStats()
    if not body:
        return st
    st.n_insns = len(body)
    for x in body:
        st.mnems.append(x.mnem)
        st.width = max(st.width, x.width)
        if PACKED_FP.match(x.mnem):
            st.packed_fp += 1
        elif SCALAR_FP.match(x.mnem):
            st.scalar_fp += 1
        elif INT_SIMD.match(x.mnem) and x.width:
            st.int_simd += 1
        if GATHER.match(x.mnem):
            st.gathers += 1
        if PERMUTE.match(x.mnem):
            st.permutes += 1
        if re.search(r"\{%k[1-7]\}", x.ops):
            st.masked += 1
        if VEC_MOVE.match(x.mnem) and x.width and "(" in x.ops:  # memory, not reg copies
            st.vec_moves += 1
    return st


def is_vector_loop(st):
    """Does a main loop do SIMD work (packed arithmetic, gathers, or -- for a
    pure store loop like bw_write -- full-vector moves)?"""
    return st.packed_fp + st.int_simd + st.gathers + st.vec_moves > 0


def function_stats(insns):
    """Same counters over the whole function (prologue/epilogue included)."""
    return loop_stats(insns)


def builds():
    return sorted(p.name for p in BUILD.glob("*-*") if (p / "kernels.objdump.txt").exists())


def kernels():
    return [n for _, n in kernel_line_map()]
