// specular_electric.fx
//
// Lightning / electric crackle on a weapon: a branching crack texture flashes (sharp strobe)
// and the arcs JUMP to new positions over time, in a hot blue-white. Character-lit base.
// Assign a crack texture (Tex\Patterns\lightning.dds) as the EMISSIVE map.
//
// Reuses the standard glow params (Effects panel / .Mat00 wiring unchanged):
//   fGlowScale = arc brightness, fGlowPulseSpeed = flash/jump rate, fGlowPulseAmount = flash depth,
//   fEmissiveTile = arc scale, vGlowTint = arc colour (multiplied with a blue-white base).
//
// Compile (fx_2_0, DirectX SDK June 2010 fxc):  fxc /T fx_2_0 /Fo specular_electric.fxo specular_electric.fx

#include "..\..\sdk\basedefs.fxh"
#include "..\..\sdk\skeletal.fxh"
#include "..\..\sdk\dx9lights.fxh"
#include "..\..\sdk\transforms.fxh"
#include "..\..\sdk\depthencode.fxh"
#include "..\..\sdk\time.fxh"

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
DECLARE_DESCRIPTION("Lightning/electric: flashing branching arcs that jump over time. Use lightning.dds as the emissive.");
DECLARE_DOCUMENATION("Shaders\\Docs\\specular\\main.htm");
DECLARE_PARENT_MATERIAL(0, "specular_dx8.fxi");

MIPARAM_SURFACEFLAGS;
MIPARAM_FLOAT(fMaxSpecularPower, 64, "Maximum specular power.");
MIPARAM_FLOAT(fGlowScale, 3.0, "Arc brightness.");
MIPARAM_FLOAT(fGlowPulseSpeed, 1.5, "Strike rate (strikes/sec). Lower = slower, more visible fade.");
MIPARAM_FLOAT(fGlowPulseAmount, 0.85, "Strike frequency 0..1 (fraction of slots that actually fire; rest rest dark).");
MIPARAM_FLOAT(fScrollSpeed, 0.0, "Unused for electric.");
MIPARAM_FLOAT(fEmissiveTile, 2.0, "Arc scale (tiling of the crack texture).");
MIPARAM_VECTOR(vGlowTint, 1.0, 1.0, 1.0, "Arc colour tint (multiplied with a blue-white base).");
MIPARAM_TEXTURE(tDiffuseMap, 0, 0, "", true, "Diffuse map.");
MIPARAM_TEXTURE(tEmissiveMap, 0, 1, "", false, "Crack/arc map (e.g. lightning.dds).");
MIPARAM_TEXTURE(tSpecularMap, 0, 2, "", false, "Specular map.");
MIPARAM_TEXTURE(tNormalMap, 0, 3, "", false, "Normal map.");

SAMPLER_WRAP(sDiffuseMapSampler, tDiffuseMap);
SAMPLER_WRAP(sEmissiveMapSampler, tEmissiveMap);
SAMPLER_WRAP(sSpecularMapSampler, tSpecularMap);
sampler sNormalMapSampler = sampler_state { texture = <tNormalMap>; AddressU = Wrap; AddressV = Wrap; MipFilter = Linear; };

shared float4 vPVCharacterObjectLightColor;
shared float4 vHHCharacterObjectLightColor;

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

// sin-free hash (Dave Hoskins). The frac(sin(x)*N) hash needs precision ps_2_0's sin
// doesn't have for large x, so the strike position/gate got STUCK in-game; this is stable.
float Hash11(float p)
{
	p = frac(p * 0.1031);
	p *= p + 33.33;
	p *= p + p;
	return frac(p);
}

//////////////////////////////////////////////////////////////////////////////
// Ambient = the lightning
//////////////////////////////////////////////////////////////////////////////
struct PSData_Ambient { float4 Position : POSITION; float2 TexCoord : TEXCOORD0; };

PSData_Ambient Ambient_VS(MaterialVertex IN)
{
	PSData_Ambient OUT;
	OUT.Position = TransformToClipSpace(GetPosition(IN));
	OUT.TexCoord = IN.TexCoord;
	return OUT;
}

float4 Ambient_PS(PSData_Ambient IN) : COLOR
{
	float4 vResult = float4(0,0,0,1);
	float4 vDiffuseColor = GetMaterialDiffuse(IN.TexCoord);

	// Each "slot" is one potential strike. The bolt's POSITION is fixed for the whole slot
	// (re-randomized only between slots, while it's dark), so you never see the texture
	// slide/teleport mid-flash.
	float phase = fTime * 60.0 * fGlowPulseSpeed;
	float id = floor(phase);
	float w  = frac(phase);                 // 0..1 progress through this strike

	// One strike per slot: a bolt at a RANDOM position flashes instantly then fades slowly,
	// reaching ~dark before the slot ends - so the position only changes while invisible.
	float fire = step(1.0 - fGlowPulseAmount, frac(id * 0.0731 + 0.13));
	float env  = exp(-w * 4.0) * fire;          // instant flash at w=0, slow visible fade-out
	float2 off = float2(frac(id * 0.618034), frac(id * 0.381966));   // golden-ratio: well-spread per strike
	float crack = pow(saturate(GetMaterialEmissive(IN.TexCoord * fEmissiveTile + off).x), 1.5);

	// White-hot core, blue glow on the thinner parts.
	float3 boltCol = lerp(float3(0.25, 0.45, 1.0), float3(1.0, 1.0, 1.0), crack);
	float3 elec = crack * env * boltCol * vGlowTint;
	vResult.xyz = GetWeaponLight().xyz * vDiffuseColor.xyz + elec * fGlowScale;
	vResult.w = vDiffuseColor.w;
	return vResult;
}

technique Ambient { pass Draw { VertexShader = compile vs_1_1 Ambient_VS(); PixelShader = compile ps_2_0 Ambient_PS(); } }

//////////////////////////////////////////////////////////////////////////////
// Light passes (standard specular lighting; lightning lives in Ambient)
//////////////////////////////////////////////////////////////////////////////
struct PSData_Point { float4 Position:POSITION; float2 TexCoord:TEXCOORD0; float3 LightVector:TEXCOORD1; float3 EyeVector:TEXCOORD2; };
PSData_Point Point_VS(MaterialVertex IN){ PSData_Point OUT; GetVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector); return OUT; }
float4 Point_PS(PSData_Point IN):COLOR{ return GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord), GetLightDiffuseColor().xyz, GetLightSpecularColor(), fMaxSpecularPower); }
technique Point { pass Draw { VertexShader = compile vs_1_1 Point_VS(); PixelShader = compile ps_2_0 Point_PS(); } }

struct PSData_PointFill { float4 Position:POSITION; float2 TexCoord:TEXCOORD0; float3 LightVector[NUM_POINT_FILL_LIGHTS]:TEXCOORD1; };
PSData_PointFill PointFill_VS(MaterialVertex IN){ PSData_PointFill OUT; GetPointFillVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector); return OUT; }
float4 PointFill_PS(PSData_PointFill IN):COLOR{ return GetPointFillPixelColor(IN.LightVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord)); }
technique PointFill { pass Draw { VertexShader = compile vs_1_1 PointFill_VS(); PixelShader = compile ps_2_0 PointFill_PS(); } }

struct PSData_SpotProjector { float4 Position:POSITION; float2 TexCoord:TEXCOORD0; float3 LightVector:TEXCOORD1; float3 EyeVector:TEXCOORD2; float4 LightMapCoord:TEXCOORD3; float2 ClipPlanes:TEXCOORD4; };
PSData_SpotProjector SpotProjector_VS(MaterialVertex IN){ PSData_SpotProjector OUT; float3 vP=GetPosition(IN); GetVertexAttributes(vP, GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector); OUT.LightMapCoord=GetSpotProjectorTexCoord(vP); OUT.ClipPlanes=GetSpotProjectorClipInterpolants(vP); return OUT; }
float4 SpotProjector_PS(PSData_SpotProjector IN):COLOR{ float4 c=GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord), DX9GetSpotProjectorDiffuseColor(IN.LightMapCoord), DX9GetSpotProjectorSpecularColor(IN.LightMapCoord), fMaxSpecularPower); return c * DX9GetSpotProjectorClipResult(IN.ClipPlanes, IN.LightMapCoord); }
technique SpotProjector { pass Draw { VertexShader = compile vs_1_1 SpotProjector_VS(); PixelShader = compile ps_2_0 SpotProjector_PS(); } }

struct PSData_CubeProjector { float4 Position:POSITION; float2 TexCoord:TEXCOORD0; float3 LightVector:TEXCOORD1; float3 EyeVector:TEXCOORD2; float3 LightMapCoord:TEXCOORD3; };
PSData_CubeProjector CubeProjector_VS(MaterialVertex IN){ PSData_CubeProjector OUT; float3 vP=GetPosition(IN); GetVertexAttributes(vP, GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector); OUT.LightMapCoord=GetCubeProjectorTexCoord(vP); return OUT; }
float4 CubeProjector_PS(PSData_CubeProjector IN):COLOR{ return GetLitPixelColor(IN.LightVector, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord), GetCubeProjectorDiffuseColor(IN.LightMapCoord), GetCubeProjectorSpecularColor(IN.LightMapCoord), fMaxSpecularPower); }
technique CubeProjector { pass Draw { VertexShader = compile vs_1_1 CubeProjector_VS(); PixelShader = compile ps_2_0 CubeProjector_PS(); } }

struct PSData_Directional { float4 Position:POSITION; float2 TexCoord:TEXCOORD0; float3 LightVector:TEXCOORD1; float3 EyeVector:TEXCOORD2; float3 TexSpace:TEXCOORD3; };
PSData_Directional Directional_VS(MaterialVertex IN){ PSData_Directional OUT; GetDirectionalVertexAttributes(GetPosition(IN), GetInverseTangentSpace(IN), IN.TexCoord, OUT.Position, OUT.TexCoord, OUT.LightVector, OUT.EyeVector, OUT.TexSpace); return OUT; }
float4 Directional_PS(PSData_Directional IN):COLOR{ return GetDirectionalLitPixelColor(normalize(IN.LightVector), IN.TexSpace, IN.EyeVector, GetSurfaceNormal_Unit(IN.TexCoord), GetMaterialDiffuse(IN.TexCoord), GetMaterialSpecular(IN.TexCoord), fMaxSpecularPower); }
technique Directional { pass Draw { VertexShader = compile vs_1_1 Directional_VS(); PixelShader = compile ps_2_0 Directional_PS(); } }

ENCODE_DEPTH_DEFAULT(MaterialVertex)
