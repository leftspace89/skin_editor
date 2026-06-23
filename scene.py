"""scene.py - moderngl GPU resources for the loaded weapon: one shared interleaved
VBO, a VAO+IBO per material draw-group, and the preview program. Per-pane drawing
sets the MVP and renders every group with its TextureSet's GL textures."""
import numpy as np
import moderngl

import mathutil as mu
import shaders
import textures

# interleaved vertex: pos3 nrm3 uv2 tan3 bin3  (14 floats / 56 bytes)
_VFMT = '3f 3f 2f 3f 3f'
_VATTRS = ['a_position', 'a_normal', 'a_uv0', 'a_tangent', 'a_binormal']

_AX = {'x': (1.0, 0.25, 0.25), 'y': (0.45, 0.85, 0.2), 'z': (0.3, 0.55, 1.0)}


# Vector-letter strokes in a local [-0.5,0.5] box: list of (x0,y0,x1,y1) segments.
_GLYPH = {
    'x': [(-.4, -.5, .4, .5), (-.4, .5, .4, -.5)],
    'y': [(-.4, .5, 0, .05), (.4, .5, 0, .05), (0, .05, 0, -.5)],
    'z': [(-.4, .5, .4, .5), (.4, .5, -.4, -.5), (-.4, -.5, .4, -.5)],
}


class Gizmo:
    """A small Blender-style XYZ axis triad drawn in a viewport corner. Shows the
    current camera orientation (lines for +axes, dots for +/- tips, billboarded
    X/Y/Z letters at the tips)."""
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=shaders.GIZMO_VERTEX,
                                fragment_shader=shaders.GIZMO_FRAGMENT)
        lines, pts = [], []
        self._axes = (('x', (1, 0, 0)), ('y', (0, 1, 0)), ('z', (0, 0, 1)))
        for ax, vec in self._axes:
            c = _AX[ax]
            lines += [(0, 0, 0, *c), (*vec, *c)]
            pts.append((*vec, *c))                                          # + tip
            pts.append((-vec[0], -vec[1], -vec[2], *[v * 0.5 for v in c]))  # - tip dim
        self.line_vbo = ctx.buffer(np.array(lines, 'f4').tobytes())
        self.pt_vbo = ctx.buffer(np.array(pts, 'f4').tobytes())
        self.line_vao = ctx.vertex_array(self.prog, [(self.line_vbo, '3f 3f', 'a_pos', 'a_col')])
        self.pt_vao = ctx.vertex_array(self.prog, [(self.pt_vbo, '3f 3f', 'a_pos', 'a_col')])
        # dynamic buffer for the billboarded letters (rebuilt each frame)
        self.lbl_vbo = ctx.buffer(reserve=6 * 4 * 64)   # plenty for 3 letters
        self.lbl_vao = ctx.vertex_array(self.prog, [(self.lbl_vbo, '3f 3f', 'a_pos', 'a_col')])

    def _label_verts(self, mvp):
        """Project each axis tip to clip space and emit upright letter strokes."""
        scale = 0.16
        out = []
        for ax, vec in self._axes:
            tip = np.array([vec[0] * 1.28, vec[1] * 1.28, vec[2] * 1.28, 1.0], 'f4')
            clip = mvp @ tip
            if clip[3] <= 1e-5:
                continue
            cx, cy = clip[0] / clip[3], clip[1] / clip[3]
            c = _AX[ax]
            for x0, y0, x1, y1 in _GLYPH[ax]:
                out.append((cx + x0 * scale, cy + y0 * scale, 0, *c))
                out.append((cx + x1 * scale, cy + y1 * scale, 0, *c))
        return np.array(out, 'f4') if out else None

    def draw(self, fbo, cam, corner, dpr):
        """corner = (x, y, size) in physical pixels (GL bottom-up)."""
        x, y, s = corner
        prev_vp = fbo.viewport
        fbo.viewport = (int(x), int(y), int(s), int(s))
        # rotation-only view (no translation) + ortho so the triad keeps a fixed size
        eye = mu.normalize(cam.eye() - cam.target)
        view = mu.look_at(eye * 3.0, mu.vec3(0, 0, 0), cam._up())
        proj = mu.ortho(-1.5, 1.5, -1.5, 1.5, -10, 10)
        mvp = mu.mul(proj, view)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.prog['u_is_point'].value = 0
        self.prog['u_point'].value = 9.0 * dpr
        self.prog['u_mvp'].write(mu.gl(mvp).tobytes())
        self.ctx.line_width = 2.0
        self.line_vao.render(moderngl.LINES)
        self.prog['u_is_point'].value = 1
        self.ctx.enable(moderngl.PROGRAM_POINT_SIZE)
        self.pt_vao.render(moderngl.POINTS)
        # billboarded letters: feed clip-space coords with an identity transform
        verts = self._label_verts(mvp)
        if verts is not None:
            self.lbl_vbo.write(verts.tobytes())
            self.prog['u_is_point'].value = 0
            self.prog['u_mvp'].write(mu.gl(mu.identity()).tobytes())
            self.ctx.line_width = 1.5
            self.lbl_vao.render(moderngl.LINES, vertices=len(verts))
        self.ctx.enable(moderngl.DEPTH_TEST)
        fbo.viewport = prev_vp


class RenderSettings:
    def __init__(self):
        self.light_dir = mu.normalize(mu.vec3(0.4, 0.7, 0.6))
        self.light_color = mu.vec3(1.0, 0.98, 0.95)
        self.ambient = mu.vec3(0.20, 0.21, 0.24)
        self.view_mode = 0          # 0 lit,1 diffuse,2 normal,3 rough,4 spec,5 flat,6 emissive
        self.flip_v = False
        self.invert_roughness = False
        self.use_alpha_spec = True  # weapon _D stores spec mask in alpha
        self.headlight = True       # light follows the camera
        self.glow = 1.5             # emissive/glow intensity in the preview (0 = off)
        # Animated glow ("glowanim" = specular_glow.fx). When glow_anim is on the preview
        # pulses u_glow with the SAME formula the shader uses, so what you see matches
        # in-game. `time` is real seconds, advanced by the viewport's animation timer.
        self.glow_anim = False
        self.glow_speed = 0.8       # pulse speed, cycles/sec (Hz) -> fGlowPulseSpeed
        self.glow_amount = 0.4      # pulse depth 0..1            -> fGlowPulseAmount
        self.time = 0.0             # seconds, driven by GLViewport while glow_anim
        # Moving flow pattern (a band sweeping across the weapon) -> fFlow* in the shader.
        self.flow_amount = 0.0      # 0 = off (plain glow)
        self.flow_speed = 0.3       # Hz
        self.flow_freq = 3.0        # bands across the weapon
        self.flow_dir = 1.0         # 0 = along U, 1 = along V
        # Fresnel rim / scrolling energy / hue cycle -> fRim*, fScrollSpeed, fHueSpeed.
        self.rim_scale = 0.0        # 0 = off
        self.rim_power = 3.0
        self.scroll_speed = 0.0     # 0 = off
        self.hue_speed = 0.0        # Hz, 0 = off
        self.emissive_tile = 1.0    # emissive/pattern tiling (grid size); 1 = as authored
        self.flow_space = 0.0       # 0 = UV flow; 1 = object-space (triplanar) flow
        self.glow_tint = (1.0, 1.0, 1.0)   # emissive/pattern color tint -> vGlowTint
        self.effect_mode = 0        # 0 = glow, 1 = fire, 2 = lightning (preview shader branch)

    def effective_glow(self):
        """Preview glow intensity for the current frame. Mirrors specular_glow.fx's
        GetGlowFactor(): a pulse riding between (1-amount) and 1 times the base glow."""
        if not self.glow_anim:
            return float(self.glow)
        import math
        phase = self.time * self.glow_speed * 2.0 * math.pi
        pulse = 1.0 - self.glow_amount * (0.5 - 0.5 * math.sin(phase))
        return float(self.glow) * pulse


class Scene:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=shaders.VERTEX,
                                fragment_shader=shaders.FRAGMENT)
        self.model = None
        self.vbo = None
        self.groups = []            # [{name, ibo, vao, texset}]
        self._bg = (0.13, 0.14, 0.16)

    def release(self):
        for g in self.groups:
            try:
                g['vao'].release(); g['ibo'].release()
            except Exception:
                pass
        if self.vbo:
            self.vbo.release()
        self.groups = []
        self.vbo = None

    def load(self, loaded_model):
        self.release()
        self.model = loaded_model
        self.vbo = self.ctx.buffer(np.ascontiguousarray(loaded_model.vertices).tobytes())
        for g in loaded_model.draw_groups:
            ts = g['texset']
            if not ts.buffers:
                textures.load_textureset(ts)
            textures.upload_textureset(self.ctx, ts)
            faces = np.ascontiguousarray(g['faces'].astype('i4'))
            ibo = self.ctx.buffer(faces.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(self.vbo, _VFMT, *_VATTRS)], ibo, index_element_size=4)
            self.groups.append({'name': g['name'], 'ibo': ibo, 'vao': vao, 'texset': ts})

    def flush_dirty(self):
        for g in self.groups:
            textures.flush_dirty(g['texset'])

    def draw(self, view, proj, cam_pos, settings):
        if not self.groups:
            return
        prog = self.prog
        model = mu.identity()
        mvp = mu.mul(proj, view, model)
        prog['u_mvp'].write(mu.gl(mvp).tobytes())
        prog['u_model'].write(mu.gl(model).tobytes())
        prog['u_normal_mat'].write(mu.gl(mu.normal_matrix(model)).tobytes())
        prog['u_cam_pos'].value = tuple(float(x) for x in cam_pos)
        ldir = cam_pos - (self.model.center if self.model is not None else mu.vec3(0, 0, 0)) \
            if settings.headlight else settings.light_dir
        prog['u_light_dir'].value = tuple(float(x) for x in mu.normalize(ldir))
        prog['u_light_color'].value = tuple(float(x) for x in settings.light_color)
        prog['u_ambient'].value = tuple(float(x) for x in settings.ambient)
        prog['u_use_alpha_spec'].value = 1 if settings.use_alpha_spec else 0
        prog['u_invert_roughness'].value = 1 if settings.invert_roughness else 0
        prog['u_view_mode'].value = int(settings.view_mode)
        prog['u_flip_v'].value = 1 if settings.flip_v else 0
        try:
            prog['u_glow'].value = settings.effective_glow()
        except KeyError:
            pass
        for name, val in (('u_time', float(settings.time)),
                          ('u_flow_amount', float(settings.flow_amount)),
                          ('u_flow_speed', float(settings.flow_speed)),
                          ('u_flow_freq', float(settings.flow_freq)),
                          ('u_flow_dir', float(settings.flow_dir)),
                          ('u_rim_scale', float(settings.rim_scale)),
                          ('u_rim_power', float(settings.rim_power)),
                          ('u_scroll_speed', float(settings.scroll_speed)),
                          ('u_hue_speed', float(settings.hue_speed)),
                          ('u_emissive_tile', float(settings.emissive_tile)),
                          ('u_flow_space', float(settings.flow_space)),
                          ('u_effect_mode', float(settings.effect_mode)),
                          ('u_glow_scale', float(settings.glow)),       # raw intensity
                          ('u_pulse_speed', float(settings.glow_speed)),
                          ('u_pulse_amount', float(settings.glow_amount))):
            try:
                prog[name].value = val
            except KeyError:
                pass
        try:
            prog['u_glow_tint'].value = tuple(float(x) for x in settings.glow_tint)
        except KeyError:
            pass

        for g in self.groups:
            ts = g['texset']
            _bind(ts.gltex.get('diffuse'), 0, prog, 'u_diffuse')
            _bind(ts.gltex.get('normal'), 1, prog, 'u_normal')
            _bind(ts.gltex.get('roughness'), 2, prog, 'u_roughness')
            _bind(ts.gltex.get('spec'), 3, prog, 'u_spec')
            _bind(ts.gltex.get('emissive'), 4, prog, 'u_emissive')
            g['vao'].render()


def _bind(tex, unit, prog, uniform):
    if tex is None:
        return
    tex.use(unit)
    try:
        prog[uniform].value = unit
    except KeyError:
        pass


class BloomPipeline:
    """Screen-space bloom so the emissive glow actually bleeds past edges (a real
    glow, not just brighter pixels). The scene is rendered into an MRT framebuffer
    (color + emissive); the emissive is Gaussian-blurred at half res and screen-added
    back over the scene when compositing to the screen."""
    def __init__(self, ctx):
        self.ctx = ctx
        self.blur = ctx.program(vertex_shader=shaders.FSQUAD_VERTEX,
                                fragment_shader=shaders.BLUR_FRAGMENT)
        self.comp = ctx.program(vertex_shader=shaders.FSQUAD_VERTEX,
                                fragment_shader=shaders.COMPOSITE_FRAGMENT)
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], 'f4')
        self.qvbo = ctx.buffer(quad.tobytes())
        self.blur_vao = ctx.vertex_array(self.blur, [(self.qvbo, '2f', 'a_pos')])
        self.comp_vao = ctx.vertex_array(self.comp, [(self.qvbo, '2f', 'a_pos')])
        self.size = None
        self.scene_fbo = self.color_only = self.emis_only = None
        self.color_tex = self.emis_tex = self.depth_tex = None
        self.ping_fbo = [None, None]
        self.ping_tex = [None, None]

    def _mktex(self, w, h):
        t = self.ctx.texture((w, h), 4)
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        t.repeat_x = t.repeat_y = False
        return t

    def ensure_size(self, w, h):
        w, h = max(1, w), max(1, h)
        if self.size == (w, h):
            return
        for o in (self.scene_fbo, self.color_only, self.emis_only,
                  self.color_tex, self.emis_tex, self.depth_tex,
                  *self.ping_fbo, *self.ping_tex):
            try:
                o and o.release()
            except Exception:
                pass
        self.color_tex = self._mktex(w, h)
        self.emis_tex = self._mktex(w, h)
        self.depth_tex = self.ctx.depth_texture((w, h))
        self.scene_fbo = self.ctx.framebuffer([self.color_tex, self.emis_tex], self.depth_tex)
        self.color_only = self.ctx.framebuffer([self.color_tex], self.depth_tex)
        self.emis_only = self.ctx.framebuffer([self.emis_tex])
        hw, hh = max(1, w // 2), max(1, h // 2)
        self.ping_tex = [self._mktex(hw, hh) for _ in range(2)]
        self.ping_fbo = [self.ctx.framebuffer([t]) for t in self.ping_tex]
        self.size = (w, h)
        self._half = (hw, hh)

    def composite(self, target_fbo, target_viewport, iterations=3, radius=2.0,
                  strength=1.0):
        """Blur the emissive into bloom and composite (scene + bloom) into the target
        framebuffer/viewport. Call after the scene has been drawn into scene_fbo."""
        ctx = self.ctx
        ctx.disable(moderngl.DEPTH_TEST)
        hw, hh = self._half
        # iterative separable blur, ping-ponging between the two half-res buffers
        src = self.emis_tex
        dst = 0
        for _ in range(max(1, iterations)):
            # horizontal
            self.ping_fbo[dst].use()
            src.use(0); self.blur['u_tex'].value = 0
            self.blur['u_dir'].value = (radius / hw, 0.0)
            self.blur_vao.render(moderngl.TRIANGLE_STRIP)
            # vertical
            self.ping_fbo[1 - dst].use()
            self.ping_tex[dst].use(0); self.blur['u_tex'].value = 0
            self.blur['u_dir'].value = (0.0, radius / hh)
            self.blur_vao.render(moderngl.TRIANGLE_STRIP)
            src = self.ping_tex[1 - dst]
            dst = 1 - dst
        bloom = src
        # composite onto the real screen
        target_fbo.use()
        target_fbo.scissor = None
        target_fbo.viewport = target_viewport
        self.color_tex.use(0); self.comp['u_scene'].value = 0
        bloom.use(1); self.comp['u_bloom'].value = 1
        self.comp['u_strength'].value = float(strength)
        self.comp_vao.render(moderngl.TRIANGLE_STRIP)
        ctx.enable(moderngl.DEPTH_TEST)
