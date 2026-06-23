// specular_fire.fx
//
// Animated FIRE on a weapon: two flame-noise layers scroll upward and are mapped through a
// black->red->orange->yellow->white fire ramp, flickering over time. Character-lit base so
// the weapon still shades normally; the fire is added (and blooms via ScreenGlow on world).
// Assign a flame-noise texture (Tex\Patterns\flame.dds) as the EMISSIVE map.
//
// Reuses the standard glow params so the Effects panel / .Mat00 wiring is unchanged:
//   fGlowScale = fire intensity, fScrollSpeed = rise speed, fEmissiveTile = flame scale,
//   fGlowPulseSpeed/Amount = flicker, vGlowTint = fire colour.
//
// Compile (fx_2_0, DirectX SDK June 2010 fxc):  fxc /T fx_2_0 /Fo specular_fire.fxo specular_fire.fx

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
DECLARE_DESCRIPTION("Animated fire: scrolling flame noise through a fire colour ramp. Use flame.dds as the emissive.");
DECLARE_DOCUMENATION("Shaders\\Docs\\specular\\main.htm");
DECLARE_PARENT_MATERIAL(0, "specular_dx8.fxi");

MIPARAM_SURFACEFLAGS;
MIPARAM_FLOAT(fMaxSpecularPower, 64, "Maximum specular power.");
MIPARAM_FLOAT(fGlowScale, 3.0, "Fire intensity.");
MIPARAM_FLOAT(fGlowPulseSpeed, 6.0, "Flicker speed (Hz).");
MIPARAM_FLOAT(fGlowPulseAmount, 0.35, "Flicker depth 0..1.");
MIPARAM_FLOAT(fScrollSpeed, 0.6, "Flame rise speed (scrolls the noise upward).");
MIPARAM_FLOAT(fEmissiveTile, 2.0, "Flame scale (tiling of the flame noise).");
MIPARAM_VECTOR(vGlowTint, 1.0, 1.0, 1.0, "Fire colour tint (white = natural fire colours).");
MIPARAM_TEXTURE(tDiffuseMap, 0, 0, "", true, "Diffuse map.");
MIPARAM_TEXTURE(tEmissiveMap, 0, 1, "", false, "Flame-noise map (e.g. flame.dds). Its luminance drives the flames.");
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

//////////////////////////////////////////////////////////////////////////////
// Ambient = the fire
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

	// Two flame-noise layers scrolling upward (-V) at different rates -> licking flames.
	float t = fTime * 60.0 * fScrollSpeed;
	float2 fuv = IN.TexCoord * fEmissiveTile;
	float n1 = GetMaterialEmissive(fuv + float2(0.00, -t)).x;
	float n2 = GetMaterialEmissive(fuv * 1.7 + float2(0.13, -t * 1.6)).x;
	float f = saturate(n1 * n2 * 2.2);

	// flicker
	f *= 1.0 - fGlowPulseAmount * (0.5 - 0.5 * sin(fTime * 60.0 * fGlowPulseSpeed * 6.2831853));

	// fire ramp: black -> red -> orange/yellow -> white, then tint
	float3 fire = saturate(float3(f * 3.0, f * 3.0 - 1.0, f * 3.0 - 2.0)) * vGlowTint;

	vResult.xyz = GetWeaponLight().xyz * vDiffuseColor.xyz + fire * fGlowScale;
	vResult.w = vDiffuseColor.w;
	return vResult;
}

technique Ambient { pass Draw { VertexShader = compile vs_1_1 Ambient_VS(); PixelShader = compile ps_2_0 Ambient_PS(); } }

//////////////////////////////////////////////////////////////////////////////
// Light passes (standard specular lighting; fire lives in Ambient)
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
