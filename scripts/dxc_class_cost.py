"""DXC time by console instruction class, on HLSL samples dumped by XCL (#1269).

    XCL_DUMP_HLSL=<dump> XCL_DUMP_EVERY=400 xcl shaders <pack> --recomp
    python -I scripts/dxc_class_cost.py <dump> <work folder>

For each class (SMEM, buffer, image, LDS, VALU float / integer / compare, SALU), every
instruction of the class keeps only its register writes, as opaque per-lane `io.Load`s, so
DXC still sees the same registers live but not the instruction's code; the difference to the
unchanged program is that class's DXC time (an upper bound: what its bodies cost). Each
program is compiled twice per variant with XCL's flags and the faster run is kept.
`XCL_DXC` overrides the dxc.exe path.
"""
import os, re, subprocess, sys, time
dump, out = sys.argv[1], sys.argv[2]
DXC = os.environ.get("XCL_DXC", r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\dxc.exe")
MARK = re.compile(r'^( *)// [0-9a-f]{4,}: (\S+)')
ASSIGN = re.compile(r'^( *)((?:[sv]\d+)|vcc_lo|vcc_hi|exec_lo|exec_hi|scc|m0) = .*;\s*$')
def cls(m):
    if m.startswith(("s_cbranch", "s_branch", "s_endpgm", "s_setpc")): return None
    if m.startswith(("s_load", "s_buffer_load")): return "smem"
    if m.startswith(("buffer_", "tbuffer_")): return "vmem_buffer"
    if m.startswith("image_"): return "image"
    if m.startswith(("global_", "flat_", "scratch_")): return "global"
    if m.startswith("ds_"): return "lds"
    if m.startswith("exp"): return None
    if m.startswith("v_cmp"): return "valu_cmp"
    if m.startswith("v_") and re.search(r'_f(16|32|64)|fma|mac|legacy|_rcp|_rsq|_sqrt|_exp|_log|_sin|_cos|_fract|_floor|_ceil|_trunc|_rndne|med3_f|min_f|max_f', m): return "valu_fp"
    if m.startswith("v_"): return "valu_int"
    if m.startswith("s_"): return "salu"
    return None
CLASSES = ["smem", "vmem_buffer", "image", "global", "lds", "valu_fp", "valu_int", "valu_cmp", "salu"]
def ablate(src, target):
    lines = src.split("\n"); out = []; i = 0; k = 0; done = kept = 0
    while i < len(lines):
        m = MARK.match(lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        ind = len(m.group(1)); j = i + 1
        while j < len(lines) and not MARK.match(lines[j]) and (len(lines[j]) - len(lines[j].lstrip(' ')) >= ind) and lines[j].strip():
            j += 1
        body = lines[i+1:j]
        if cls(m.group(2)) == target:
            bal = all(l.count("{") == l.count("}") for l in body)
            if bal:
                out.append(lines[i])
                for l in body:
                    a = ASSIGN.match(l)
                    if a:
                        k += 1
                        out.append(f"{a.group(1)}{a.group(2)} = io.Load((({k}u << 6) + lane_id) * 4u);")
                done += 1
            else:
                out.extend(lines[i:j]); kept += 1
        else:
            out.extend(lines[i:j])
        i = j
    return "\n".join(out), done, kept
def opt(n): return "-O0" if n > 8<<20 else "-O1" if n > 1<<20 else "-O3"
def run(path, prof, o, reps):
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        r = subprocess.run([DXC, "-E", "main", "-T", prof, "-HV", "2021", o, "-Fo", path + ".dxil", path], capture_output=True, text=True)
        dt = time.perf_counter() - t
        if r.returncode: return None, r.stderr[:300]
        best = dt if best is None else min(best, dt)
    return best, os.path.getsize(path + ".dxil")
os.makedirs(out, exist_ok=True)
tot = {c: [0.0, 0, 0, 0] for c in CLASSES}; base_t = 0.0; nfail = {c: 0 for c in CLASSES}
for f in sorted(os.listdir(dump)):
    if not f.endswith(".hlsl"): continue
    src = open(os.path.join(dump, f), encoding="utf8").read()
    prof = "_".join(f[:-5].split("_")[-3:]); o = opt(len(src.encode()))
    p = os.path.join(out, f); open(p, "w", encoding="utf8", newline="").write(src)
    tb, db = run(p, prof, o, 2); row = [f, "%.3f" % tb, db]
    for c in CLASSES:
        s2, done, kept = ablate(src, c)
        if done == 0:
            row.append("-"); tot[c][0] += tb; continue
        q = p + "." + c + ".hlsl"; open(q, "w", encoding="utf8", newline="").write(s2)
        tc, dc = run(q, prof, o, 2)
        if tc is None:
            nfail[c] += 1; row.append("FAIL"); print("FAIL", f, c, dc, file=sys.stderr); tot[c][0] += tb; continue
        tot[c][0] += tc; tot[c][1] += done; tot[c][2] += kept; tot[c][3] += dc
        row.append("%.3f" % tc)
    base_t += tb
    print(*row, sep="\t", flush=True)
print("baseline dxc s %.2f" % base_t)
for c in CLASSES:
    t, done, kept, d = tot[c]
    print("%-12s ablated %7d kept %5d fail %2d  dxc %.2f s  saves %.2f s (%.1f%%)" % (c, done, kept, nfail[c], t, base_t - t, (base_t - t) * 100 / base_t))
