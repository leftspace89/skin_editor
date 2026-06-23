# Reskinning weapon textures with Nano Banana (Gemini image) — UV-preserving prompt

Goal: take an **existing** weapon diffuse texture (which is already laid out for the
model's UVs) and have an image model repaint it in a new theme **without moving,
rotating, or rescaling anything**, so the result still maps perfectly onto the model.

This works because we do **image-to-image** editing: the original texture is the
structural reference, and we only restyle its surface. Generating a texture from a
text prompt alone will NOT line up with the UVs.

---

## Workflow with this tool

1. **Load** the weapon and pick the skin/piece you want to reskin (the **Map**
   dropdown selects body vs scope).
2. **File ▸ Export UV Layout (PNG guide)…** → writes `<piece>_<role>_UV.png`: the
   current texture with the UV wireframe drawn on top. This is your *reference image*
   and your *alignment check*.
3. Also keep the **original diffuse** itself as a clean reference. Either:
   - open the source `..._D.dds` in an editor and save a PNG, or
   - export the UV guide and, in your editor, hide/erase the wireframe layer.
4. Feed the **original texture** (clean) as the base image to Nano Banana / Gemini
   image with the prompt below. Optionally also attach the `_UV.png` guide and tell
   the model to **keep every feature inside the same wireframe cell**.
5. Save the result as a PNG at the **same resolution** as the original (1024×1024 for
   these weapons).
6. Back in the tool: **File ▸ Replace Active Map from Image…** (Ctrl+R) → pick your
   PNG. Orbit/inspect; the new skin should land exactly where the old one was.
7. **File ▸ Export / Compile DDS…** (Ctrl+E). Leave **"Copy spec alpha from original"
   ON** — image models don't produce the spec mask (it lives in the diffuse alpha),
   so the tool restores it from the original. Optionally tune Brightness/Spec.

---

## Master prompt (copy, fill in the ONE `{THEME}` slot)

Paste this whole block into Nano Banana / Gemini image **with the original texture
attached as the input image**, and replace `{THEME}` with your idea (see the worked
example below).

> ROLE: You are retexturing a **game weapon texture atlas** — a flat, UV-unwrapped
> diffuse map, NOT a photo or a render of a gun. The attached image is the current
> atlas. Produce a new atlas that is **pixel-for-pixel UV-aligned** to it.
>
> TASK: Repaint the surface in this theme — **{THEME}** — while keeping every region
> exactly where it is.
>
> KEEP IDENTICAL (do not change):
> - The **position, size, rotation, and outline of every region/island.** Do not move,
>   scale, rotate, mirror, crop, re-pack, or re-center anything. A pixel that was part
>   of the grip stays the grip; a pixel that was background stays background.
> - The **canvas size and aspect** (output the same WxH as the input, square).
> - **Seams and edges** between parts, so the texture still wraps without tearing.
> - Small functional markings (text, serials, arrows) unless the theme says otherwise.
> - Empty/background atlas areas — leave them the same flat neutral color; never add
>   new shapes, props, borders, or scenery in unused space.
>
> CHANGE ONLY (inside each existing region): the **material, color palette, surface
> detail, wear, and decals** to express "{THEME}".
>
> DO NOT: render in 3D or perspective; add a light source, cast shadows, ambient
> occlusion, vignette, or baked highlights; add text, labels, logos, watermarks, or a
> frame. Output one flat image, edge to edge.
>
> If unsure whether a change moves a feature, prefer keeping it in place.

### Iterating
- Output too glossy/contrasty? add: *"keep it flat and even; no baked lighting or
  highlights — shading is applied separately by the engine."*
- Features drifting? attach the exported `_UV.png` guide and add: *"the green lines are
  UV island boundaries — keep all paint inside the same cells and do not draw the
  lines in the output."*

---

## How to replace `{THEME}` — worked example

`{THEME}` is just a short phrase describing the *surface style* you want. Give it a
**material + color + finish**, optionally wear/pattern/decals. Keep it about the
surface, not about shape or scene.

Good `{THEME}` values:

| Idea | `{THEME}` text to paste |
| --- | --- |
| Arctic camo | `arctic winter camouflage — white base with light-grey and pale-blue digital blocks, matte finish, lightly scuffed` |
| Gold luxury | `polished gold plating with fine engraved floral filigree, clean and reflective metal` |
| Cyberpunk | `dark carbon-fiber body with glowing cyan circuit traces and small neon-magenta accents, matte with subtle tech panels` |
| Rusted wasteland | `weathered post-apocalyptic steel, chipped red paint, orange rust streaks, grime in the recesses` |
| Jungle camo | `jungle camouflage — olive green, brown and black irregular blotches, worn matte coating` |
| Tiger skin | `orange tiger fur pattern with bold black stripes following the part shapes, matte` |

**Before (template line):**

> TASK: Repaint the surface in this theme — **{THEME}** — while keeping every region
> exactly where it is.

**After (filled in for the Cyberpunk idea):**

> TASK: Repaint the surface in this theme — **dark carbon-fiber body with glowing cyan
> circuit traces and small neon-magenta accents, matte with subtle tech panels** —
> while keeping every region exactly where it is.

You only edit that one phrase; leave every other line of the master prompt as-is.

## Tips for alignment

- If the model drifts features, attach the `_UV.png` guide too and add: *"Use the green
  wireframe only as a region guide — keep your paint inside the same cells; do not draw
  the wireframe in the output."*
- Generate at the native resolution; upscaling after the fact softens seams.
- If a region comes back shifted, mask just that region in your editor and re-run the
  edit on the crop, then paste back — the tool's UV panel + brush ring let you verify
  exactly which texels a surface uses.
- DXT5 needs dimensions that are multiples of 4 (1024 is fine). Keep it square.

## Why the spec mask matters
S2 weapon `_D` textures are DXT5 where the **alpha channel = specular mask** (bright =
shiny, dark = matte), not transparency. AI output will be opaque, so always export with
**Copy spec alpha from original** (default ON) — otherwise the whole skin reads as
uniformly glossy in-game. To author shininess yourself, paint it with the editor's
**spec** brush before compiling.
