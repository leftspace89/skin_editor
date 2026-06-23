"""shaders.py - GLSL sources for the weapon preview.

A single program does tangent-space normal mapping + roughness-modulated
Blinn-Phong so brightness/spec/diffuse/normal/roughness edits are visible live in
every pane. Channel-isolate view modes let you inspect a single map.
"""

VERTEX = """
#version 330 core
in vec3 a_position;
in vec3 a_normal;
in vec2 a_uv0;
in vec3 a_tangent;
in vec3 a_binormal;

uniform mat4 u_mvp;
uniform mat4 u_model;
uniform mat3 u_normal_mat;
uniform int  u_flip_v;

out vec3 v_world_pos;
out vec3 v_normal;
out vec3 v_tangent;
out vec3 v_binormal;
out vec2 v_uv;

void main() {
    gl_Position = u_mvp * vec4(a_position, 1.0);
    v_world_pos = (u_model * vec4(a_position, 1.0)).xyz;
    v_normal   = normalize(u_normal_mat * a_normal);
    v_tangent  = normalize(u_normal_mat * a_tangent);
    v_binormal = normalize(u_normal_mat * a_binormal);
    v_uv = (u_flip_v == 1) ? vec2(a_uv0.x, 1.0 - a_uv0.y) : a_uv0;
}
"""

FRAGMENT = """
#version 330 core
in vec3 v_world_pos;
in vec3 v_normal;
in vec3 v_tangent;
in vec3 v_binormal;
in vec2 v_uv;

uniform sampler2D u_diffuse;
uniform sampler2D u_normal;
uniform sampler2D u_roughness;
uniform sampler2D u_spec;
uniform sampler2D u_emissive;

uniform vec3 u_cam_pos;
uniform vec3 u_light_dir;     // direction TO the light (world space)
uniform vec3 u_light_color;
uniform vec3 u_ambient;

uniform int  u_use_alpha_spec;   // 1: spec mask is diffuse alpha; 0: use u_spec map
uniform int  u_invert_roughness;
uniform int  u_view_mode;        // 0 lit,1 diffuse,2 normal,3 rough,4 spec,5 flat,6 emissive
uniform float u_glow;            // emissive/glow intensity (0 = off). Matches engine glow.
uniform float u_time;            // seconds, for animated glow/flow (matches shader fTime*60)
uniform float u_flow_amount;     // 0 = off; >0 = sweeping band over the glow (matches fFlow*)
uniform float u_flow_speed;      // Hz
uniform float u_flow_freq;       // bands across the weapon
uniform float u_flow_dir;        // 0 = along U, 1 = along V
uniform float u_rim_scale;       // fresnel rim glow strength (0 = off)
uniform float u_rim_power;       // fresnel rim tightness
uniform float u_scroll_speed;    // scrolling-energy speed (pans emissive along flow axis)
uniform float u_hue_speed;       // glow hue-cycle speed (Hz)
uniform float u_emissive_tile;   // emissive/pattern tiling (grid square size); 1 = as authored
uniform float u_flow_space;      // 0 = UV; 1 = object-space (triplanar) flow/scroll/tile
uniform vec3  u_glow_tint;       // emissive/pattern color tint (white = texture color)
uniform float u_effect_mode;     // 0 = glow, 1 = fire, 2 = lightning
uniform float u_glow_scale;      // raw intensity (fGlowScale) for fire/lightning
uniform float u_pulse_speed;     // raw flicker speed (fGlowPulseSpeed)
uniform float u_pulse_amount;    // raw flicker depth (fGlowPulseAmount)

// MRT: target 0 = final color, target 1 = the glow source (blurred into bloom by the
// bloom pipeline). Extra outputs are ignored when rendered into a 1-attachment FBO.
layout(location = 0) out vec4 frag_color;
layout(location = 1) out vec4 emis_out;

// Object-space flow axis + perpendicular (cardinal), matches FlowAxis3()/FlowPerp3().
vec3 flow_axis3() {
    return (u_flow_dir < 0.5) ? vec3(1,0,0) : (u_flow_dir < 1.5) ? vec3(0,1,0) : vec3(0,0,1);
}
vec3 flow_perp3() {
    return (u_flow_dir < 0.5) ? vec3(0,1,0) : (u_flow_dir < 1.5) ? vec3(0,0,1) : vec3(1,0,0);
}

// Emissive fetch: UV planar or object-space planar projection (matches SampleEmissive()).
vec3 sample_emissive(vec2 uv, vec3 wp) {
    float scroll = u_time * u_scroll_speed;
    vec2 uvScroll = mix(vec2(1,0), vec2(0,1), clamp(u_flow_dir, 0.0, 1.0)) * scroll;
    vec3 uvE = texture(u_emissive, uv * u_emissive_tile + uvScroll).rgb;
    vec2 oc = vec2(dot(wp, flow_axis3()), dot(wp, flow_perp3())) * u_emissive_tile + vec2(scroll, 0.0);
    vec3 obE = texture(u_emissive, oc).rgb;
    return mix(uvE, obE, step(0.5, u_flow_space));
}

// Moving band that sweeps across the weapon (matches specular_glow.fx GetFlowMask).
float flow_mask(vec2 uv, vec3 wp) {
    float uvC = mix(uv.x, uv.y, clamp(u_flow_dir, 0.0, 1.0));
    float wC  = dot(wp, flow_axis3());
    float coord = mix(uvC, wC, step(0.5, u_flow_space));
    float phase = (coord * u_flow_freq - u_time * u_flow_speed) * 6.2831853;
    float band = pow(0.5 + 0.5 * sin(phase), 4.0);
    return mix(1.0, band, u_flow_amount);
}

// Rotate a color's hue by angle a (radians) around the grey axis (matches HueShift()).
vec3 hue_shift(vec3 col, float a) {
    vec3 k = vec3(0.57735);
    float c = cos(a), s = sin(a);
    return col * c + cross(k, col) * s + k * dot(k, col) * (1.0 - c);
}

// sin-free hash (matches Hash11 in specular_electric.fx) so preview == in-game positions.
float Hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }

// Fire (matches specular_fire.fx): two upward-scrolling flame-noise layers -> fire ramp.
vec3 fire_glow(vec2 uv) {
    float t = u_time * u_scroll_speed;
    vec2 fuv = uv * u_emissive_tile;
    float n1 = texture(u_emissive, fuv + vec2(0.0, -t)).r;
    float n2 = texture(u_emissive, fuv * 1.7 + vec2(0.13, -t * 1.6)).r;
    float f = clamp(n1 * n2 * 2.2, 0.0, 1.0);
    f *= 1.0 - u_pulse_amount * (0.5 - 0.5 * sin(u_time * u_pulse_speed * 6.2831853));
    vec3 fire = clamp(vec3(f * 3.0, f * 3.0 - 1.0, f * 3.0 - 2.0), 0.0, 1.0) * u_glow_tint;
    return fire * u_glow_scale;
}
// Lightning (matches specular_electric.fx): intermittent strikes, instant flash + smooth
// fade-out, bolt position fixed per strike (moves only while dark), white-core/blue glow.
vec3 electric_glow(vec2 uv) {
    float phase = u_time * u_pulse_speed;
    float id = floor(phase);
    float w  = fract(phase);
    // one strike per slot: random position, instant flash, slow fade (repositions while dark)
    float fire = step(1.0 - u_pulse_amount, fract(id * 0.0731 + 0.13));
    float env  = exp(-w * 4.0) * fire;
    vec2 off = vec2(fract(id * 0.1031), fract(id * 0.2417));
    float crack = pow(clamp(texture(u_emissive, uv * u_emissive_tile + off).r, 0.0, 1.0), 1.5);
    vec3 boltCol = mix(vec3(0.25, 0.45, 1.0), vec3(1.0, 1.0, 1.0), crack);
    return crack * env * boltCol * u_glow_tint * u_glow_scale;
}

void main() {
    emis_out = vec4(0.0, 0.0, 0.0, 1.0);
    vec4 diff = texture(u_diffuse, v_uv);
    vec3 albedo = diff.rgb;
    vec3 emissive = texture(u_emissive, v_uv).rgb;

    // tangent-space normal
    vec3 nt = texture(u_normal, v_uv).rgb * 2.0 - 1.0;
    mat3 TBN = mat3(normalize(v_tangent), normalize(v_binormal), normalize(v_normal));
    vec3 N = normalize(TBN * nt);

    float rough = texture(u_roughness, v_uv).r;
    if (u_invert_roughness == 1) rough = 1.0 - rough;
    float spec_mask = (u_use_alpha_spec == 1) ? diff.a : texture(u_spec, v_uv).r;

    if (u_view_mode == 1) { frag_color = vec4(albedo, 1.0); return; }
    if (u_view_mode == 2) { frag_color = vec4(texture(u_normal, v_uv).rgb, 1.0); return; }
    if (u_view_mode == 3) { frag_color = vec4(vec3(rough), 1.0); return; }
    if (u_view_mode == 4) { frag_color = vec4(vec3(spec_mask), 1.0); return; }
    // The model loader swaps verts (-x,-z,y) for display; undo it so object-space flow
    // axes (fFlowDir X/Y/Z) match the ENGINE's raw model space -> editor == in-game.
    vec3 obj_pos = vec3(-v_world_pos.x, v_world_pos.z, -v_world_pos.y);
    float fm = flow_mask(v_uv, obj_pos);
    // color: cycling rainbow when hue on, else the fixed tint (MULTIPLY, not hue-rotate,
    // so white patterns actually change color) - matches specular_glow.fx.
    vec3 rainbow = 0.5 + 0.5 * cos(6.2831853 * (u_time * u_hue_speed + vec3(0.0, 0.3333, 0.6667)));
    vec3 col = mix(u_glow_tint, rainbow, step(0.0001, abs(u_hue_speed)));
    // emissive/pattern: UV or object-space planar, tiled + scrolled, colored
    vec3 emis_s = sample_emissive(v_uv, obj_pos) * col;
    // fresnel rim glow (view-dependent edge)
    float rim = pow(1.0 - clamp(dot(normalize(v_normal), normalize(u_cam_pos - v_world_pos)), 0.0, 1.0),
                    u_rim_power) * u_rim_scale;
    // glow contribution: (colored emissive + rim) * band * intensity
    vec3 glow = (emis_s + rim) * fm * u_glow;
    // elemental modes override the glow contribution
    if (u_effect_mode > 1.5)      glow = electric_glow(v_uv);
    else if (u_effect_mode > 0.5) glow = fire_glow(v_uv);
    if (u_view_mode == 6) { frag_color = vec4(glow, 1.0); emis_out = vec4(glow, 1.0); return; }

    vec3 Nl = (u_view_mode == 5) ? normalize(v_normal) : N;
    vec3 L = normalize(u_light_dir);
    vec3 V = normalize(u_cam_pos - v_world_pos);
    vec3 H = normalize(L + V);

    float ndl = max(dot(Nl, L), 0.0);
    float shininess = mix(8.0, 256.0, clamp(1.0 - rough, 0.0, 1.0));
    float ndh = max(dot(Nl, H), 0.0);
    float spec = pow(ndh, shininess) * spec_mask;

    vec3 color = u_ambient * albedo
               + u_light_color * (albedo * ndl + vec3(spec));
    // simple rim/fill so unlit backsides aren't pure black
    color += albedo * 0.06;
    // emissive glow (scrolled + rim + hue, computed above): added unlit like the
    // engine's Ambient-pass emissive, so it reads bright regardless of lighting.
    color += glow;
    frag_color = vec4(color, 1.0);
    emis_out = vec4(glow, 1.0);   // bloom source = the glow contribution only
}
"""

# Fullscreen-quad post-process passes (bloom): blur + composite.
FSQUAD_VERTEX = """
#version 330 core
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
"""

# Separable Gaussian blur (run once horizontal, once vertical per iteration).
BLUR_FRAGMENT = """
#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;
uniform vec2 u_dir;   // texel-space step (1/size, 0) or (0, 1/size), scaled by radius
void main() {
    float w[5] = float[](0.2270270, 0.1945946, 0.1216216, 0.0540541, 0.0162162);
    vec3 c = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; ++i) {
        c += texture(u_tex, v_uv + u_dir * float(i)).rgb * w[i];
        c += texture(u_tex, v_uv - u_dir * float(i)).rgb * w[i];
    }
    frag_color = vec4(c, 1.0);
}
"""

# Composite scene + blurred bloom (screen-add so the glow blooms past edges).
COMPOSITE_FRAGMENT = """
#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_scene;
uniform sampler2D u_bloom;
uniform float u_strength;
void main() {
    vec3 s = texture(u_scene, v_uv).rgb;
    vec3 b = texture(u_bloom, v_uv).rgb * u_strength;
    // screen blend keeps highlights from blowing out to flat white
    vec3 outc = 1.0 - (1.0 - s) * (1.0 - b);
    frag_color = vec4(outc, 1.0);
}
"""

# Flat-color program for overlays (brush ring, UV wire) drawn in clip space.
OVERLAY_VERTEX = """
#version 330 core
in vec2 a_pos;
uniform vec2 u_offset;
uniform vec2 u_scale;
void main() { gl_Position = vec4(a_pos * u_scale + u_offset, 0.0, 1.0); }
"""

OVERLAY_FRAGMENT = """
#version 330 core
uniform vec4 u_color;
out vec4 frag_color;
void main() { frag_color = u_color; }
"""

# Navigation axis gizmo (Blender-style triad): colored lines + tip points.
GIZMO_VERTEX = """
#version 330 core
in vec3 a_pos;
in vec3 a_col;
uniform mat4 u_mvp;
uniform float u_point;
out vec3 v_col;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    gl_PointSize = u_point;
    v_col = a_col;
}
"""

GIZMO_FRAGMENT = """
#version 330 core
in vec3 v_col;
uniform int u_is_point;
out vec4 frag_color;
void main() {
    if (u_is_point == 1) {
        vec2 d = gl_PointCoord - vec2(0.5);
        if (dot(d, d) > 0.25) discard;   // round the point sprites
    }
    frag_color = vec4(v_col, 1.0);
}
"""
