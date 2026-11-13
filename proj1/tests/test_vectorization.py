"""Verify each build vectorizes what it is supposed to, and nothing else.

Checks compiler reports (GCC -fopt-info, Clang -Rpass) against the actual
disassembly of every kernel's main loop, then asserts the per-config
expectations the experiments rely on. Run after `make`.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vecinfo as v  # noqa: E402

BUILDS = v.builds()
if not BUILDS:
    pytest.skip("no builds found -- run `make` first", allow_module_level=True)

KERNELS = v.kernels()
UNIT_STRIDE = ["dot_simd", "dot_acc2", "dot_acc4", "dot_acc8", "mul", "mul_aligned", "mul_pred",
               "stencil", "bw_read", "bw_write", "bw_scale", "bw_triad"]
WIDTH = {"default": 128, "native256": 256, "native512": 512, "native512fm": 512}
_cache = {}


def info(build, dtype, kernel):
    key = (build, dtype)
    if key not in _cache:
        _cache[key] = (v.disasm(build), v.report_summary(build, dtype))
    dis, rep = _cache[key]
    insns = dis[f"{kernel}_{dtype}"]
    status, width_bytes, msgs = rep.get(kernel, ("none", 0, []))
    return {"insns": insns, "loop": v.loop_stats(v.main_loop(insns)), "status": status,
            "report_bits": width_bytes * 8, "msgs": msgs}


def cfg(build):
    return build.split("-", 1)[1]


def cc(build):
    return build.split("-", 1)[0]


ALL = [(b, d, k) for b in BUILDS for d in v.DTYPES for k in KERNELS]


@pytest.mark.parametrize("build,dtype,kernel", ALL)
def test_report_matches_disassembly(build, dtype, kernel):
    """The compiler's own claim must agree with the instructions it emitted:
    'vectorized at W bits' <=> main loop does SIMD work on W-bit registers."""
    i = info(build, dtype, kernel)
    asm_vec = v.is_vector_loop(i["loop"])
    assert (i["status"] == "vectorized") == asm_vec, (i["status"], i["loop"], i["msgs"][:2])
    if asm_vec:
        assert i["report_bits"] == i["loop"].width


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) == "scalar"])
def test_scalar_build_has_no_simd(build):
    """The scalar baseline must be truly scalar: no packed arithmetic, gathers
    or ymm/zmm anywhere in any kernel (catches pragmas that force
    vectorization despite -fno-(tree-)vectorize)."""
    for d in v.DTYPES:
        for k in KERNELS:
            st = v.function_stats(info(build, d, k)["insns"])
            assert st.packed_fp == st.int_simd == st.gathers == 0, (d, k, st)
            assert st.width <= 128, (d, k, "ymm/zmm in scalar build")


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) == "default"])
def test_default_build_is_sse_only(build):
    for d in v.DTYPES:
        for k in KERNELS:
            assert v.function_stats(info(build, d, k)["insns"]).width <= 128, (d, k)


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) in WIDTH])
@pytest.mark.parametrize("dtype", v.DTYPES)
def test_unit_stride_kernels_use_full_width(build, dtype):
    want = WIDTH[cfg(build)]
    for k in UNIT_STRIDE:
        i = info(build, dtype, k)
        assert i["status"] == "vectorized" and i["loop"].width == want, (k, i["loop"].width)


# ---------------------------------------- negative example (negative) --
@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "gcc" and cfg(b) in
                                   ("default", "native256", "native512")])
@pytest.mark.parametrize("dtype", ["f32", "f64"])
def test_gcc_dot_plain_is_inorder_reduction(build, dtype):
    """GCC says 'loop vectorized' but the adds stay a serial scalar chain:
    one packed multiply, then lanes x scalar vaddss/vaddsd, no packed add."""
    i = info(build, dtype, "dot_plain")
    lp = i["loop"]
    lanes = lp.width // (32 if dtype == "f32" else 64)
    assert i["status"] == "vectorized"
    assert lp.packed_fp == 1, lp.mnems  # the vmulps/vmulpd
    assert not any(re.match(r"^v?addp[sd]$", m) for m in lp.mnems)
    assert sum(1 for m in lp.mnems if re.match(r"^v?adds[sd]$", m)) == lanes


@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "clang" and cfg(b) in
                                   ("default", "native256", "native512")])
@pytest.mark.parametrize("dtype", ["f32", "f64"])
def test_clang_refuses_dot_plain(build, dtype):
    i = info(build, dtype, "dot_plain")
    assert i["status"] == "missed"
    rep = (v.BUILD / build / f"kernels_{dtype}.remarks.txt").read_text()
    assert "cannot prove it is safe to reorder floating-point operations" in rep


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) != "scalar"])
def test_int_dot_plain_vectorizes(build):
    """Integer addition is associative, so no pragma/fast-math is needed."""
    i = info(build, "i32", "dot_plain")
    assert i["status"] == "vectorized" and i["loop"].int_simd > 0


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) == "native512fm"])
@pytest.mark.parametrize("dtype", ["f32", "f64"])
def test_fast_math_fixes_dot_plain(build, dtype):
    lp = info(build, dtype, "dot_plain")["loop"]
    assert lp.width == 512 and lp.scalar_fp == 0
    assert any(re.match(r"^v(addp[sd]|fmadd\d+p[sd])$", m) for m in lp.mnems)


# --------------------------------------------- reductions (multiacc) --
def accumulators(lp_body):
    """Distinct destination registers of packed add/FMA in the main loop."""
    regs = set()
    for x in lp_body:
        if re.match(r"^v(addp[sd]|fmadd\d+p[sd]|paddd)$", x.mnem):
            regs.add(x.ops.split(",")[-1].strip())
    return regs


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) in ("native256", "native512")])
@pytest.mark.parametrize("k,want", [("dot_acc2", 2), ("dot_acc4", 4), ("dot_acc8", 8)])
def test_multi_accumulator_dots_have_k_chains(build, k, want):
    body = v.main_loop(info(build, "f32", k)["insns"])
    assert len(accumulators(body)) >= want


@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "gcc" and cfg(b) in
                                   ("native256", "native512")])
def test_gcc_dot_simd_single_accumulator(build):
    """Documents why GCC's dot_simd is add-latency bound in L1."""
    body = v.main_loop(info(build, "f32", "dot_simd")["insns"])
    assert len(accumulators(body)) == 1


# --------------------------------------------------- strides / gathers --
@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) in ("native256", "native512")])
@pytest.mark.parametrize("dtype", ["f32", "i32"])
def test_gather_kernels_use_gather_instructions(build, dtype):
    for k in ("dot_gather", "mul_gather"):
        assert info(build, dtype, k)["loop"].gathers > 0, k


@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "gcc" and cfg(b) == "native512"])
@pytest.mark.parametrize("k", ["dot_s2", "dot_s4", "mul_s2", "mul_s4"])
def test_gcc_constant_stride_uses_permutes_not_gathers(build, k):
    lp = info(build, "f32", k)["loop"]
    assert lp.permutes > 0 and lp.gathers == 0


@pytest.mark.parametrize("build", [b for b in BUILDS if cfg(b) == "default"])
def test_sse2_has_no_gather(build):
    for d in v.DTYPES:
        assert info(build, d, "mul_gather")["status"] != "vectorized"


# ---------------------------------------------------- tails (align) --
@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "clang" and cfg(b) in
                                   ("native256", "native512")])
def test_clang_mul_pred_folds_tail_with_masks(build):
    """Predicated tail folding: masked vector ops, no scalar epilogue at all."""
    i = info(build, "f32", "mul_pred")
    assert i["loop"].masked > 0
    assert v.function_stats(i["insns"]).scalar_fp == 0


@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "clang" and cfg(b) == "native512"])
def test_clang_mul_has_scalar_epilogue(build):
    """Contrast for the above: the default tail strategy keeps scalar code."""
    i = info(build, "f32", "mul")
    assert i["loop"].masked == 0
    assert v.function_stats(i["insns"]).scalar_fp > 0


@pytest.mark.parametrize("build", [b for b in BUILDS if cc(b) == "gcc" and cfg(b) == "native512"])
def test_gcc_mul_pred_identical_to_mul(build):
    """GCC 11 ignores the clang pragma: same instruction stream."""
    a = [x.mnem for x in info(build, "f32", "mul")["insns"]]
    b = [x.mnem for x in info(build, "f32", "mul_pred")["insns"]]
    assert a == b
