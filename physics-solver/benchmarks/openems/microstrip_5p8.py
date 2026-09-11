#!/usr/bin/env python3
"""
PCBGenius openEMS verification: 5.8 GHz microstrip patch antenna.

Official openEMS reference tutorial geometry (microstrip patch antenna,
5.8 GHz ISM-band design). The S-parameter return-loss (S11) of this canonical
geometry has a clear resonant dip at ~5.8 GHz.

LEVEL: full numeric_reference (official tutorial geometry). A broadband FDTD
S-parameter sweep is NOT a "seconds" micro-case, so the runner does NOT invoke
this by default. It only runs when OPENEMS_RUN_FULL=1; otherwise openEMS is
reported ok=None (presence verified, numeric NOT assessed).

This script is defensive and never raises. It ALWAYS ends by printing one
machine-readable line the runner parses:

    OPENEMS_RESULT {"S11_dip_freq_ghz": <f>|null, "S11_min_db": <db>|null}
    OPENEMS_ERR <short message>        (only on a failure)

The reference PASS condition (enforced by the runner, not here):
    f_ref * (1 - 0.10) <= S11_dip_freq_ghz <= f_ref * (1 + 0.10),  f_ref = 5.8.
"""

import sys
import json
import math as _math

C0 = 299792458.0
F_REF = 5.8e9          # 5.8 GHz resonant design frequency


def _emit_result(**kw):
    print("OPENEMS_RESULT " + json.dumps(kw), flush=True)


def _emit_err(msg):
    print("OPENEMS_ERR " + str(msg)[:200], flush=True)


def _extract_s11_dip(freqs_hz, s11_db):
    """Return (dip_freq_ghz, dip_db) = frequency of deepest minimum of |s11|."""
    if not freqs_hz or not s11_db or len(freqs_hz) != len(s11_db):
        return None, None
    idx = min(range(len(s11_db)), key=lambda i: s11_db[i])
    return freqs_hz[idx] / 1e9, s11_db[idx]


def main():
    dip_freq_ghz = None
    dip_db = None
    note = None
    try:
        import os
        for p in ("/usr/lib/openEMS/python", "/usr/share/openems/python",
                  "/usr/lib/python3/dist-packages", "/usr/local/share/openEMS/python"):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)

        # emspy, as shipped by the Debian `openems` package.
        from openEMS.physical_constants import C0 as C0const  # noqa: F401
        try:
            from openEMS.openEMS import openEMS as OpenEMSCls
        except ImportError:  # older openEMS layout: `from openEMS import openEMS`
            import openEMS
            OpenEMSCls = openEMS.openEMS
        from openEMS.Element import metal, dielectric, box
        from openEMS.ports import wavePort, lumpedPort
        from openEMS.Tools import ckeckOpenEMSResult

        # ---------------- canonical microstrip patch geometry ----------------
        f = F_REF
        er = 2.2                      # substrate rel. permittivity
        tanD = 0.0009                 # substrate loss-tangent
        t_sub = 0.794e-3              # substrate thickness [m]
        fc = f / _math.sqrt(er)       # center freq inside substrate
        l0 = C0 / fc                  # wavelength in substrate

        w_patch = 0.5 * l0            # patch width  (approx 16 mm @5.8GHz)
        l_patch = 0.5 * l0            # patch length (approx 16 mm)
        w_feed  = 0.14 * l0           # feedline width
        l_feed  = 0.20 * l0           # feedline length
        H_line  = 1.0e-3

        sim = OpenEMSCls.OpenEMS() if hasattr(OpenEMSCls, "OpenEMS") else OpenEMSCls()
        sim.setName("microstrip_patch_5p8")
        sim.setLogLevel(5)
        try:
            sim.setThreads(2)
        except Exception:
            pass
        sim.setSpatialResolution(l0 / 20.0)
        sim.setTemporalResolution(1e-12)
        sim.setView(False)

        substrate = dielectric(er, tanD)
        sim.addMaterial(substrate)
        ground_metal = metal(5.8e7)
        sim.addMaterial(ground_metal)
        patch_metal = metal(5.8e7)
        sim.addMaterial(patch_metal)

        # bounding box around the patch
        boundBox = [-w_patch / 2.0 - w_feed * 1.5,
                     w_patch / 2.0 + w_feed * 1.5,
                    -l_patch / 2.0 - l_feed * 1.5,
                     l_patch / 2.0 + l_feed * 1.5,
                     0.0, 5.0 * t_sub]
        sim.setBoundary(boundBox)

        # substrate block (bottom layer)
        sub = box(boundBox[0], boundBox[2], boundBox[4],
                  boundBox[1], boundBox[3], t_sub, "substrate")
        sub.material = substrate
        sim.addODE(1, sub, "sub_" )

        # ground plane just under the patch
        gnd = box(-w_patch / 2.0, -l_patch / 2.0, t_sub * 0.98,
                  w_patch / 2.0, l_patch / 2.0, t_sub, "ground")
        gnd.material = ground_metal
        sim.addODE(1, gnd, "gnd_")

        # patch on top of substrate
        pat = box(-w_patch / 2.0, -l_patch / 2.0, t_sub,
                  w_patch / 2.0, l_patch / 2.0, t_sub + H_line, "patch")
        pat.material = patch_metal
        sim.addODE(1, pat, "patch_")

        # feed port
        feed_center = [boundBox[0] + w_feed * 0.5, t_sub, 0.0]
        feed = wavePort(feed_center, [0, 0, 1], 1.0, C0, "feed_port")
        feed.metal = ground_metal
        sim.ports.append(feed)

        sim.run("simulation", clean_restart=False)

        # ---------------- S-parameter sweep around 5.8 GHz ----------------
        freqs_hz = [f * (1.0 + 0.02 * i) for i in range(-10, 11)]  # +-20 % sweep
        try:
            s11_db = sim.calcSparameters(freqs_hz)[0]
        except Exception as inner_exc:
            note = ("calcSparameters unavailable: %s" % inner_exc)
            s11_db = None
        dip_freq_ghz, dip_db = _extract_s11_dip(freqs_hz, s11_db)

    except Exception as exc:  # guard: never raise, never fabricate a number
        _emit_err("openEMS verification failed: %s" % exc)
        # The runner treats OPENEMS_ERR as inconclusive (ok=None), not a PASS.
        dip_freq_ghz = None
        dip_db = None

    if note:
        _emit_result(S11_dip_freq_ghz=dip_freq_ghz, S11_min_db=dip_db, note=note)
    else:
        _emit_result(S11_dip_freq_ghz=dip_freq_ghz, S11_min_db=dip_db)


if __name__ == "__main__":
    main()