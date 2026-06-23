// specular_glow.fx
//
// specular.fx + an animated EMISSIVE GLOW that blooms in-shader, lit by the character
// light so it's not black on the first-person view model. Plus an optional MOVING FLOW
// pattern (a bright band that sweeps across the weapon) - set fFlowAmount > 0 to enable;
// fFlowAmount = 0 (default) is the plain steady/pulsing glow.
//
// Compile (legacy fx_2_0 -> DirectX SDK June 2010 fxc):
//   fxc /T fx_2_0 /Fo specular_glow.fxo specular_glow.fx

#include "..\..\sdk\basedefs.fxh"
#include "..\..\sdk\skeletal.fxh"
#include "..\..\sdk\dx9lights.fxh"
#include "..\..\sdk\transforms.fxh"
#include "..\..\sdk\depthencode.fxh"
#include "..\..\sdk\time.fxh"

//--------------------------------------------------------------------
struct MaterialVertex
{
    float3	Position	: POSITION;
    float3	Normal		: NORMAL;
    float2	TexCoord	: TEXCOORD0;
	float3	Tangent		: TANGENT;
	float3	Binormal	: BINORMAL;
	DECLARE_SKELETAL_WEIGHTS
};
DECLARE_VERTEX_FORMAT(MaterialVertex);
DECLARE_DESCRIPTION("Specular weapon shader with an animated emissive glow + optional moving flow pattern.");
DECLARE_DOCUMENATION("Shaders\\Docs\\specular\\main.htm");
DECLARE_PARENT_MATERIAL(0, "specular_dx8.fxi");

//--------------------------------------------------------------------
MIPARAM_SURFACEFLAGS;
MIPARAM_FLOAT(fMaxSpecularPower, 64, "Maximum specular power.");
MIPARAM_FLOAT(fGlowScale, 3.0, "Glow intensity (multiplies the emissive map).");
MIPARAM_FLOAT(fGlowPulseSpeed, 0.8, "Glow pulse speed in cycles/second (Hz). 0 = steady.");
MIPARAM_FLOAT(fGlowPulseAmount, 0.4, "Glow pulse depth 0..1. 0 = steady; 1 = fades off and back.");
MIPARAM_FLOAT(fGlowBlur, 0.006, "In-shader glow bleed radius (UV). Soft halo. 0 = crisp.");
// Moving flow pattern (a bright band sweeping across the weapon over the glow areas).
MIPARAM_FLOAT(fFlowAmount, 0.0, "Moving pattern strength 0..1. 0 = off (plain glow); 1 = full sweeping band.");
MIPARAM_FLOAT(fFlowSpeed, 0.3, "Moving pattern speed (Hz). Sign flips direction.");
MIPARAM_FLOAT(fFlowFreq, 3.0, "Moving pattern frequency = number of bands across the weapon.");
MIPARAM_FLOAT(fFlowDir, 1.0, "Sweep axis. UV space: 0 = U, 1 = V. Object space: 0 = X, 1 = Y, 2 = Z.");
MIPARAM_FLOAT(fFlowSpace, 0.0, "Flow/scroll/tile space: 0 = UV (per-texture); 1 = object space (triplanar) so the pattern flows the SAME physical direction across the whole weapon, ignoring UV seams.");
// Fresnel rim glow (view-dependent energy edge).
MIPARAM_FLOAT(fRimScale, 0.0, "Fresnel rim glow strength (glowing edge that shifts as the weapon turns). 0 = off.");
MIPARAM_FLOAT(fRimPower, 3.0, "Fresnel rim tightness. Higher = thinner edge.");
// Emissive tiling: repeats the emissive/glow map across the weapon (grid square size).
MIPARAM_FLOAT(fEmissiveTile, 1.0, "Emissive/pattern tiling. 1 = as authored; higher = more, smaller grid squares; lower = fewer, larger.");
// Scrolling energy: pans the emissive sample along the flow axis over time.
MIPARAM_FLOAT(fScrollSpeed, 0.0, "Scrolling energy speed. Pans the emissive along the flow axis. 0 = off.");
// Hue cycle: rotates the glow color over time.
MIPARAM_FLOAT(fHueSpeed, 0.0, "Glow hue-cycle speed (Hz). 0 = off (keeps the emissive's own colors).");
// Tint: multiplies the emissive/pattern so a white pattern can glow any chosen color.
MIPARAM_VECTOR(vGlowTint, 1.0, 1.0, 1.0, "Glow/pattern color tint (multiplies the emissive). White = the texture's own color.");
MIPARAM_TEXTURE(tDiffuseMap, 0, 0, "", true, "Diffuse map.");
MIPARAM_TEXTURE(tEmissiveMap, 0, 1, "", false, "Emissive/glow map. Bright areas glow; black areas don't.");
MIPARAM_TEXTURE(tSpecularMap, 0, 2, "", false, "Specular map.");
MIPARAM_TEXTURE(tNormalMap, 0, 3, "", false, "Normal map.");

SAMPLER_WRAP(sDiffuseMapSampler, tDiffuseMap);
SAMPLER_WRAP(sEmissiveMapSampler, tEmissiveMap);
SAMPLER_WRAP(sSpecularMapSampler, tSpecularMap);
sampler sNormalMapSampler = sampler_state
{
	texture = <tNormalMap>;
	AddressU = Wrap; AddressV = Wrap; MipFilter = Linear;
};

// Character lights the engine sets for view/hand models (vObjectLightColor is ~0 there,
// which made specular.fx render the weapon black). Prefer whichever is populated.
shared float4 vPVCharacterObjectLightColor;
shared float4 vHHCharacterObjectLightColor;

//--------------------------------------------------------------------
float3x3 GetInverseTangentSpace(MaterialVertex Vert)
{
	return GetInverseTangentSpace(SKIN_VECTOR(Vert.Tangent, Vert), SKIN_VECTOR(Vert.Binormal, Vert), SKIN_VECTOR(Vert.Normal, Vert));
}
float3 GetPosition(MaterialVertex Vert) { return SKIN_POINT(Vert.Position, Vert); }
float3 GetSurfaceNormal_Unit(float2 vCoord) { return normalize(tex2D(sNormalMapSampler, vCoord).xyz - 0.5); }
float4 GetMaterialDiffuse(float2 vCoord) { return tex2D(sDiffuseMapSampler, vCoord); }
float4 GetMaterialSpecular(float2 vCoord) { return tex2D(sSpecularMapSampler, vCoord); }
float4 GetMaterialEmissive(float2 vCoord) { return tex2D(sEmissiveMapSampler, vCoord); }

half4 GetWeaponLight()
{
	if (dot(vPVCharacterObjectLightColor.rgb, half3(1,1,1)) > 0.0001) return vPVCharacterObjectLightColor;
	if (dot(vHHCharacterObjectLightColor.rgb, half3(1,1,1)) > 0.0001) return vHHCharacterObjectLightColor;
	return vObjectLightColor;
}

float GetGlowFactor()
{
	float fPhase = fTime * 60.0 * fGlowPulseSpeed * 6.2831853;
	return fGlowScale * (1.0 - fGlowPulseAmount * (0.5 - 0.5 * sin(fPhase)));
}

// Rotate a color's hue by angle a (radians) around the grey axis. a = 0 -> unchanged.
float3 HueShift(float3 col, float a)
{
	float3 k = float3(0.57735, 0.57735, 0.57735);
	float c = cos(a), s = sin(a);
	return col * c + cross(k, col) * s + k * dot(k, col) * (1.0 - c);
}

// Object-space flow axis + a perpendicular axis (cardinal, picked by fFlowDir).
float3 FlowAxis3()
{
	return (fFlowDir < 0.5) ? float3(1,0,0) : (fFlowDir < 1.5) ? float3(0,1,0) : float3(0,0,1);
}
float3 FlowPerp3()
{
	return (fFlowDir < 0.5) ? float3(0,1,0) : (fFlowDir < 1.5) ? float3(0,0,1) : float3(1,0,0);
}

// Fetch the emissive/pattern by UV (per-texture) or by OBJECT-SPACE planar projection
// (fFlowSpace >= 0.5). Object mode builds 2D coords from the model position along the flow
// axis + a perpendicular axis and scrolls along the flow axis, so the pattern flows ONE
// consistent physical direction across the whole weapon (faces parallel to the flow stretch,
// but the direction is uniform). Object units are large, so use a SMALL fEmissiveTile here.
float3 SampleEmissive(float2 uv, float3 wp)
{
	float fScroll = fTime * 60.0 * fScrollSpeed;
	// UV planar
	float2 uvScroll = lerp(float2(1,0), float2(0,1), saturate(fFlowDir)) * fScroll;
	float3 uvE = GetMaterialEmissive(uv * fEmissiveTile + uvScroll).xyz;
	// object-space planar (u = along flow axis, v = perpendicular)
	float2 oc = float2(dot(wp, FlowAxis3()), dot(wp, FlowPerp3())) * fEmissiveTile
			  + float2(fScroll, 0.0);
	float3 obE = GetMaterialEmissive(oc).xyz;
	return lerp(uvE, obE, step(0.5, fFlowSpace));
}

// Moving band sweeping across the weapon. fFlowAmount 0 -> returns 1 (no effect). The
// sweep coordinate is UV (lerp u/v) or object-space (distance along the flow axis) so the
// band travels one consistent physical direction in world mode.
float GetFlowMask(float2 uv, float3 wp)
{
	float uvC = lerp(uv.x, uv.y, saturate(fFlowDir));
	float wC  = dot(wp, FlowAxis3());
	float coord = lerp(uvC, wC, step(0.5, fFlowSpace));
	float phase = (coord * fFlowFreq - fTime * 60.0 * fFlowSpeed) * 6.2831853;
	float band = 0.5 + 0.5 * sin(phase);
	band *= band; band *= band;   // ^4 via muls (cheaper than pow)
	return lerp(1.0, band, fFlowAmount);
}

//////////////////////////////////////////////////////////////////////////////
// Ambient
//////////////////////////////////////////////////////////////////////////////
struct PSData_Ambient
{
	float4 Position : POSITION;
	float2 TexCoord : TEXCOORD0;
	float3 NormalOS : TEXCOORD1;   // skinned object-space normal (for the fresnel rim)
	float3 ViewOS   : TEXCOORD2;   // object-space view vector (eye - position)
	float3 ObjPos   : TEXCOORD3;   // model-space position (for object-space planar flow)
};

PSData_Ambient Ambient_VS(MaterialVertex IN)
{
	PSData_Ambient OUT;
	float3 p = GetPosition(IN);
	OUT.Position = TransformToClipSpace(p);
	OUT.TexCoord = IN.TexCoord;
	OUT.NormalOS = SKIN_VECTOR(IN.Normal, IN);
	OUT.ViewOS   = vObjectSpaceEyePos - p;
	OUT.ObjPos   = IN.Position;     // bind-pose model space: stable across animation
	return OUT;
}

float4 Ambient_PS(PSData_Ambient IN) : COLOR
{
	float4 vResult = float4(0,0,0,1);
	float4 vDiffuseColor = GetMaterialDiffuse(IN.TexCoord);

	// Emissive/pattern: UV or object-space triplanar (fFlowSpace), tiled + scrolled.
	float3 vEmis = SampleEmissive(IN.TexCoord, IN.ObjPos) * GetFlowMask(IN.TexCoord, IN.ObjPos);

	// Color: a cycling RAINBOW when fHueSpeed != 0, else the fixed tint. We MULTIPLY by a
	// generated color (not a hue-rotation) because rotating a white/grey pattern's hue
	// does nothing - white patterns would never change color.
	float3 vRainbow = 0.5 + 0.5 * cos(6.2831853 * (fTime * 60.0 * fHueSpeed + float3(0.0, 0.3333, 0.6667)));
	float3 vColor = lerp(vGlowTint, vRainbow, step(0.0001, abs(fHueSpeed)));
	vEmis *= vColor;

	// Fresnel rim: glowing edge that brightens at grazing angles (view-dependent).
	float fRim = pow(1.0 - saturate(dot(normalize(IN.NormalOS), normalize(IN.ViewOS))), fRimPower);
	vEmis += fRim * fRimScale;

	float3 vGlow = vEmis * GetGlowFactor();

	vResult.xyz = GetWeaponLight().xyz * vDiffuseColor.xyz + vGlow;
	vResult.w = vDiffuseColor.w;
	return vResult;
}

technique Ambient
{
	pass Draw
	{
		VertexShader = compile vs_1_1 Ambient_VS();
		PixelShader = compile ps_2_0 Ambient_PS();
	}
}

//////////////////////////////////////////////////////////////////////////////
// Point light
//////////////////////////////////////////////////////////////////////////////
struct PSData_Point
{
	float4 Position		: POSITION;
	float2 TexCoord		: TEXCOORD0;
	float3 LightVector	: TEXCOORD1;
	float3 EyeVector	: TEXCOORD2;
};

PSData_Point Point_VS(MaterialVertex IN)
{
	PSData_Point OUT;
	GetVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector);
	return OUT;
}

float4 Point_PS(PSData_Point IN) : COLOR
{
	return GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord),
		GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord),
		GetLightDiffuseColor().xyz, GetLightSpecularColor(), fMaxSpecularPower);
}

technique Point
{
	pass Draw { VertexShader = compile vs_1_1 Point_VS(); PixelShader = compile ps_2_0 Point_PS(); }
}

//////////////////////////////////////////////////////////////////////////////
// Point Fill light
//////////////////////////////////////////////////////////////////////////////
struct PSData_PointFill
{
	float4 Position								: POSITION;
	float2 TexCoord								: TEXCOORD0;
	float3 LightVector[NUM_POINT_FILL_LIGHTS]	: TEXCOORD1;
};

PSData_PointFill PointFill_VS(MaterialVertex IN)
{
	PSData_PointFill OUT;
	GetPointFillVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector);
	return OUT;
}

float4 PointFill_PS(PSData_PointFill IN) : COLOR
{
	return GetPointFillPixelColor(IN.LightVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord));
}

technique PointFill
{
	pass Draw { VertexShader = compile vs_1_1 PointFill_VS(); PixelShader = compile ps_2_0 PointFill_PS(); }
}

//////////////////////////////////////////////////////////////////////////////
// Spot projector
//////////////////////////////////////////////////////////////////////////////
struct PSData_SpotProjector
{
	float4 Position			: POSITION;
	float2 TexCoord			: TEXCOORD0;
	float3 LightVector		: TEXCOORD1;
	float3 EyeVector		: TEXCOORD2;
	float4 LightMapCoord	: TEXCOORD3;
	float2 ClipPlanes		: TEXCOORD4;
};

PSData_SpotProjector SpotProjector_VS(MaterialVertex IN)
{
	PSData_SpotProjector OUT;
	float3 vPosition = GetPosition(IN);
	GetVertexAttributes(vPosition, GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector);
	OUT.LightMapCoord = GetSpotProjectorTexCoord(vPosition);
	OUT.ClipPlanes = GetSpotProjectorClipInterpolants(vPosition);
	return OUT;
}

float4 SpotProjector_PS(PSData_SpotProjector IN) : COLOR
{
	float4 vPixelColor = GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord),
		GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord),
		DX9GetSpotProjectorDiffuseColor(IN.LightMapCoord), DX9GetSpotProjectorSpecularColor(IN.LightMapCoord), fMaxSpecularPower);
	return vPixelColor * DX9GetSpotProjectorClipResult(IN.ClipPlanes, IN.LightMapCoord);
}

technique SpotProjector
{
	pass Draw { VertexShader = compile vs_1_1 SpotProjector_VS(); PixelShader = compile ps_2_0 SpotProjector_PS(); }
}

//////////////////////////////////////////////////////////////////////////////
// Cube projector
//////////////////////////////////////////////////////////////////////////////
struct PSData_CubeProjector
{
	float4 Position			: POSITION;
	float2 TexCoord			: TEXCOORD0;
	float3 LightVector		: TEXCOORD1;
	float3 EyeVector		: TEXCOORD2;
	float3 LightMapCoord	: TEXCOORD3;
};

PSData_CubeProjector CubeProjector_VS(MaterialVertex IN)
{
	PSData_CubeProjector OUT;
	float3 vPosition = GetPosition(IN);
	GetVertexAttributes(vPosition, GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector);
	OUT.LightMapCoord = GetCubeProjectorTexCoord(vPosition);
	return OUT;
}

float4 CubeProjector_PS(PSData_CubeProjector IN) : COLOR
{
	return GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord),
		GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord),
		GetCubeProjectorDiffuseColor(IN.LightMapCoord), GetCubeProjectorSpecularColor(IN.LightMapCoord), fMaxSpecularPower);
}

technique CubeProjector
{
	pass Draw { VertexShader = compile vs_1_1 CubeProjector_VS(); PixelShader = compile ps_2_0 CubeProjector_PS(); }
}

//////////////////////////////////////////////////////////////////////////////
// Directional
//////////////////////////////////////////////////////////////////////////////
struct PSData_Directional
{
	float4 Position		: POSITION;
	float2 TexCoord		: TEXCOORD0;
	float3 LightVector	: TEXCOORD1;
	float3 EyeVector	: TEXCOORD2;
	float3 TexSpace		: TEXCOORD3;
};

PSData_Directional Directional_VS(MaterialVertex IN)
{
	PSData_Directional OUT;
	GetDirectionalVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector, OUT.TexSpace);
	return OUT;
}

float4 Directional_PS(PSData_Directional IN) : COLOR
{
	return GetDirectionalLitPixelColor(normalize(IN.LightVector), IN.TexSpace, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord),
		GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord), fMaxSpecularPower);
}

technique Directional
{
	pass Draw { VertexShader = compile vs_1_1 Directional_VS(); PixelShader = compile ps_2_0 Directional_PS(); }
}

// Depth encoding support
ENCODE_DEPTH_DEFAULT(MaterialVertex)
