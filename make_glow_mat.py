#!/usr/bin/env python3
"""make_glow_mat.py - retarget a weapon .Mat00 to the animated-glow shader.

Rebuilds a weapon material so it uses shaders\\skeletal\\Solid\\specular_glow.fx (the
custom pulsing-emissive shader) and references an emissive/glow map. The emissive's
bright areas then pulse and bloom via the engine's ScreenGlow - the in-game version of
the skin_edit preview's glow beam. Works for both the world material and the
first-person (PV_) view material (assign it to both).

Usage:
    python make_glow_mat.py <weapon.Mat00> <emissive.dds-game-relative> [out.Mat00]
        [--diffuse PATH] [--spec PATH] [--scale 3.0] [--speed 0.8] [--amount 0.4]

Paths inside a .Mat00 are GAME-RELATIVE with backslashes, e.g.
    tex\\weapons\\SN_AWM300\\SN_AWM300-myskin_EM.dds
If --diffuse is omitted the original material's diffuse is kept.
"""
import argparse
import mat00io


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mat00', help='existing weapon .Mat00 to clone')
    ap.add_argument('emissive', help='emissive/glow map, game-relative backslash path')
    ap.add_argument('out', nargs='?', help='output .Mat00 (default: <name>_glow.Mat00)')
    ap.add_argument('--diffuse', help='override diffuse map (else keep original)')
    ap.add_argument('--spec', help='optional specular map for a light-reactive flare')
    ap.add_argument('--scale', type=float, default=3.0, help='fGlowScale (glow intensity)')
    ap.add_argument('--speed', type=float, default=0.8, help='fGlowPulseSpeed in Hz (0 = steady)')
    ap.add_argument('--amount', type=float, default=0.4, help='fGlowPulseAmount 0..1 (pulse depth)')
    args = ap.parse_args()

    orig = open(args.mat00, 'rb').read()
    _, _, props = mat00io.parse(orig)
    diffuse = args.diffuse
    if not diffuse:  # keep the original diffuse if none given
        for n, t, v in props:
            if n.lower() == 'tdiffusemap' and t == mat00io.T_STRING:
                diffuse = v
                break
    if not diffuse:
        raise SystemExit('no tDiffuseMap in source and --diffuse not given')

    maps = {'tdiffusemap': diffuse, 'temissivemap': args.emissive}
    if args.spec:
        maps['tspecularmap'] = args.spec

    data = mat00io.build_glow(mat00io.GLOW_PULSE_SHADER, orig, maps)
    # overlay the requested glow knobs on top of build_glow's defaults
    ver, shader, outprops = mat00io.parse(data)
    knobs = {'fglowscale': args.scale, 'fglowpulsespeed': args.speed,
             'fglowpulseamount': args.amount}
    outprops = [(n, t, knobs[n.lower()] if n.lower() in knobs else v)
                for (n, t, v) in outprops]
    data = mat00io.build(ver, shader, outprops)

    out = args.out or (args.mat00.rsplit('.', 1)[0] + '_glow.Mat00')
    open(out, 'wb').write(data)
    print('wrote %s\n  shader  : %s\n  diffuse : %s\n  emissive: %s\n  glow    : scale=%s speed=%sHz amount=%s'
          % (out, shader, diffuse, args.emissive, args.scale, args.speed, args.amount))


if __name__ == '__main__':
    main()
