// Skeletal build of specular_glow.fx (animated emissive glow that blooms via ScreenGlow).
// This is the variant weapons use. The #define makes the shared rigid source skin the
// mesh against the bone palette (mModelObjectNodes); the engine sets mObjectToClip for
// whichever context it's drawn in, so the SAME file glows correctly for both the
// world / 3rd-person weapon AND the first-person (PV) view model.
//
// Compile (legacy fx_2_0 -> use the DirectX SDK June 2010 fxc):
//   fxc /T fx_2_0 /Fo specular_glow.fxo specular_glow.fx
#define SKELETAL_MATERIAL
#include "..\..\rigid\Solid\specular_glow.fx"
