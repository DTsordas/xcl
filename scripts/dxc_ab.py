"""DXC time and DXIL size of two XCL builds on the same HLSL samples (#1269).

    XCL_DUMP_HLSL=<a> XCL_DUMP_EVERY=400 xcl-main.exe shaders <pack> --recomp
    XCL_DUMP_HLSL=<b> XCL_DUMP_EVERY=400 xcl-pr.exe shaders <pack> --recomp
    python -I scripts/dxc_ab.py <a> <b>

Programs dumped by both (same name hash) are compiled with XCL's flags (-HV 2021, -O by
source size as in dxc.rs), best of 2; totals overall and per stage. `XCL_DXC` overrides
the dxc.exe path. Set XCL_RECOMP_WINDOWS=4 on both dumps to measure the multi-window form.
"""
import os, subprocess, sys, time, statistics
a_dir, b_dir = sys.argv[1], sys.argv[2]
DXC = os.environ.get("XCL_DXC", r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\dxc.exe")
def opt(n): return "-O0" if n > 8<<20 else "-O1" if n > 1<<20 else "-O3"
def run(path, prof, reps=2):
    o = opt(os.path.getsize(path)); best = None
    for _ in range(reps):
        t = time.perf_counter()
        r = subprocess.run([DXC, "-E", "main", "-T", prof, "-HV", "2021", o, "-Fo", path + ".dxil", path], capture_output=True, text=True)
        dt = time.perf_counter() - t
        if r.returncode: return None, 0, r.stderr[:200]
        best = dt if best is None else min(best, dt)
    return best, os.path.getsize(path + ".dxil"), ""
names = sorted(set(f for f in os.listdir(a_dir) if f.endswith(".hlsl")) & set(f for f in os.listdir(b_dir) if f.endswith(".hlsl")))
only = (set(os.listdir(a_dir)) ^ set(os.listdir(b_dir)))
by = {}; tot = [0, 0, 0, 0, 0, 0]; ratios = []; same = 0
for f in names:
    prof = "_".join(f[:-5].split("_")[-3:]); stage = f.split("_")[1]
    pa, pb = os.path.join(a_dir, f), os.path.join(b_dir, f)
    if open(pa, "rb").read() == open(pb, "rb").read():
        same += 1
    ta, da, ea = run(pa, prof); tb, db, eb = run(pb, prof)
    if ta is None or tb is None:
        print("FAIL", f, ea or eb, flush=True); continue
    print(f, os.path.getsize(pa), os.path.getsize(pb), "%.3f %.3f" % (ta, tb), da, db, sep="\t", flush=True)
    s = by.setdefault(stage, [0, 0.0, 0.0, 0, 0]); s[0] += 1; s[1] += ta; s[2] += tb; s[3] += da; s[4] += db
    tot = [tot[0] + os.path.getsize(pa), tot[1] + os.path.getsize(pb), tot[2] + ta, tot[3] + tb, tot[4] + da, tot[5] + db]
    ratios.append(tb / ta)
print("programs", len(names), "identical HLSL", same, "not in both", sorted(x for x in only if x.endswith('.hlsl')))
print("HLSL %d -> %d B (%.1f%%)" % (tot[0], tot[1], (tot[1] - tot[0]) * 100 / tot[0]))
print("DXC %.2f -> %.2f s (%.1f%%), median ratio %.3f" % (tot[2], tot[3], (tot[3] - tot[2]) * 100 / tot[2], statistics.median(ratios)))
print("DXIL %d -> %d B (%.1f%%)" % (tot[4], tot[5], (tot[5] - tot[4]) * 100 / tot[4]))
for k, s in sorted(by.items()):
    print("  %s: %d programs, DXC %.2f -> %.2f s (%.1f%%), DXIL %d -> %d B" % (k, s[0], s[1], s[2], (s[2] - s[1]) * 100 / s[1], s[3], s[4]))
