import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Float, PerspectiveCamera, RoundedBox, Sparkles } from "@react-three/drei";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { CountryThemeProfile } from "../lib/themeProfiles";
import type { SetupSessionState, SetupTranscriptTurn } from "../types";

interface SetupRoomViewportProps {
  session: SetupSessionState | null;
  themeMode?: "light" | "dark";
  themeProfile?: CountryThemeProfile;
  loading?: boolean;
  launching?: boolean;
  caption?: SetupTranscriptTurn;
  detailsOpen?: boolean;
}

function clip(text: string | undefined, limit: number) {
  const cleaned = (text ?? "").trim().replace(/\s+/g, " ");
  if (!cleaned) {
    return "";
  }
  return cleaned.length > limit ? `${cleaned.slice(0, limit - 1)}…` : cleaned;
}

function speakerLabel(turn?: SetupTranscriptTurn) {
  if (!turn) {
    return "Orchestrator";
  }
  if (turn.speaker === "user") {
    return "You";
  }
  if (turn.speaker === "system") {
    return "System";
  }
  return "Orchestrator";
}

function blendColor(left: string, right: string, weight: number) {
  return `#${new THREE.Color(left).lerp(new THREE.Color(right), weight).getHexString()}`;
}

function setupPalette(themeMode: "light" | "dark", themeProfile: CountryThemeProfile) {
  if (themeMode === "light") {
    const warmWall = blendColor(themeProfile.wallWarmth, "#d8c4ac", 0.34);
    const warmFog = blendColor(themeProfile.wallWarmth, "#cfbaa1", 0.28);
    const warmInset = blendColor(themeProfile.wallWarmth, "#a48162", 0.36);
    const warmFloor = blendColor(themeProfile.loadingTone, "#6e513a", 0.58);
    const warmGlow = blendColor(themeProfile.loadingTone, "#a8703e", 0.72);
    const warmPedestal = blendColor(themeProfile.wallWarmth, "#b79677", 0.34);
    return {
      background: warmFog,
      fog: warmFog,
      wall: warmWall,
      wallInset: warmInset,
      floor: warmFloor,
      floorGlow: warmGlow,
      pedestal: warmPedestal,
      trim: blendColor(themeProfile.accent, "#8e6542", 0.36),
      trimSoft: blendColor(themeProfile.loadingTone, "#9c724f", 0.54),
      accent: blendColor(themeProfile.fill, "#708ea5", 0.28),
      halo: themeProfile.halo,
      figure: blendColor(themeProfile.halo, "#f6e6cb", 0.26),
      caption: "rgba(244, 235, 223, 0.9)",
      captionBorder: "rgba(154, 113, 69, 0.26)",
    };
  }
  const darkBase = blendColor("#130d0b", themeProfile.loadingTone, 0.12);
  const darkWall = blendColor("#281b16", themeProfile.wallWarmth, 0.14);
  const darkInset = blendColor("#3a281f", themeProfile.wallWarmth, 0.1);
  return {
    background: darkBase,
    fog: darkBase,
    wall: darkWall,
    wallInset: darkInset,
    floor: blendColor("#6d513d", themeProfile.loadingTone, 0.18),
    floorGlow: blendColor("#9c7756", themeProfile.loadingTone, 0.32),
    pedestal: blendColor("#4d392d", themeProfile.wallWarmth, 0.12),
    trim: themeProfile.accent,
    trimSoft: blendColor("#7b5b41", themeProfile.loadingTone, 0.18),
    accent: themeProfile.fill,
    halo: themeProfile.halo,
    figure: blendColor("#e9d6b2", themeProfile.halo, 0.14),
    caption: "rgba(17, 12, 10, 0.78)",
    captionBorder: "rgba(223, 182, 125, 0.18)",
  };
}

function SetupRoomScene({
  palette,
  active,
}: {
  palette: ReturnType<typeof setupPalette>;
  active: boolean;
}) {
  const figureRef = useRef<THREE.Group>(null);
  const batonRef = useRef<THREE.Mesh>(null);
  const haloRef = useRef<THREE.Mesh>(null);

  useFrame((state) => {
    const t = state.clock.getElapsedTime();
    if (figureRef.current) {
      figureRef.current.position.y = 2.72 + Math.sin(t * 1.12) * 0.08;
      figureRef.current.rotation.y = Math.sin(t * 0.24) * 0.1;
    }
    if (batonRef.current) {
      batonRef.current.rotation.z = -0.68 + Math.sin(t * 1.85) * 0.22;
      batonRef.current.rotation.x = Math.cos(t * 1.6) * 0.08;
    }
    if (haloRef.current) {
      const scale = 1 + (active ? 0.09 : 0.04) * (0.5 + Math.sin(t * 1.3) * 0.5);
      haloRef.current.scale.setScalar(scale);
      haloRef.current.rotation.z += 0.0018;
    }
  });

  return (
    <>
      <color attach="background" args={[palette.background]} />
      <fog attach="fog" args={[palette.fog, 10, 34]} />
      <PerspectiveCamera makeDefault position={[0, 2.72, 10.2]} fov={35} />
      <ambientLight intensity={0.98} color={palette.figure} />
      <directionalLight position={[4, 8, 6]} intensity={2.45} color={palette.halo} castShadow shadow-mapSize-width={1024} shadow-mapSize-height={1024} />
      <pointLight position={[0, 5.8, 0.5]} intensity={active ? 46 : 28} distance={18} color={palette.halo} />
      <pointLight position={[0, 2.7, -8]} intensity={7.4} distance={24} color={palette.accent} />

      <mesh position={[0, -0.1, -4]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[28, 34]} />
        <meshStandardMaterial color={palette.floor} roughness={0.92} metalness={0.04} />
      </mesh>
      <mesh position={[0, 0.01, -3]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[4.5, 10.2, 72]} />
        <meshBasicMaterial color={palette.floorGlow} transparent opacity={0.18} />
      </mesh>
      <mesh position={[0, 0.015, -3.8]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[2.2, 3.86, 72]} />
        <meshBasicMaterial color={palette.trim} transparent opacity={0.18} />
      </mesh>

      <RoundedBox args={[18, 9, 0.4]} radius={0.16} position={[0, 4.25, -10]} receiveShadow castShadow>
        <meshStandardMaterial color={palette.wall} roughness={0.96} metalness={0.02} />
      </RoundedBox>
      <RoundedBox args={[0.5, 9, 10.5]} radius={0.14} position={[-9, 4.15, -7.3]} receiveShadow castShadow>
        <meshStandardMaterial color={palette.wall} roughness={0.96} metalness={0.02} />
      </RoundedBox>
      <RoundedBox args={[0.5, 9, 10.5]} radius={0.14} position={[9, 4.15, -7.3]} receiveShadow castShadow>
        <meshStandardMaterial color={palette.wall} roughness={0.96} metalness={0.02} />
      </RoundedBox>
      <RoundedBox args={[4.6, 5.4, 0.35]} radius={0.1} position={[0, 4.1, -9.74]} castShadow>
        <meshStandardMaterial color={palette.wallInset} roughness={0.94} />
      </RoundedBox>
      <RoundedBox args={[6.2, 6.24, 0.18]} radius={0.16} position={[0, 4.06, -9.52]} castShadow>
        <meshStandardMaterial color={blendColor(palette.wallInset, palette.trim, 0.18)} roughness={0.9} />
      </RoundedBox>
      <mesh position={[0, 4.18, -9.36]}>
        <circleGeometry args={[1.54, 48]} />
        <meshBasicMaterial color={palette.halo} transparent opacity={active ? 0.18 : 0.1} />
      </mesh>
      <mesh position={[0, 4.18, -9.28]}>
        <ringGeometry args={[1.68, 2.12, 48]} />
        <meshBasicMaterial color={palette.trim} transparent opacity={0.16} />
      </mesh>
      <RoundedBox args={[8.8, 0.38, 0.34]} radius={0.12} position={[0, 6.82, -9.58]} castShadow receiveShadow>
        <meshStandardMaterial color={palette.trim} roughness={0.5} metalness={0.16} />
      </RoundedBox>
      <RoundedBox args={[10.8, 0.16, 0.24]} radius={0.08} position={[0, 7.16, -9.44]} castShadow receiveShadow>
        <meshStandardMaterial color={blendColor(palette.trim, "#f0d2a7", 0.12)} roughness={0.54} metalness={0.12} />
      </RoundedBox>
      <RoundedBox args={[8.2, 0.24, 0.28]} radius={0.08} position={[0, 1.18, -8.88]} castShadow receiveShadow>
        <meshStandardMaterial color={palette.trimSoft} roughness={0.68} />
      </RoundedBox>

      {[-6.6, -2.2, 2.2, 6.6].map((x) => (
        <group key={x} position={[x, 0, -8.9]}>
          <mesh position={[0, 3.5, 0]} castShadow>
            <cylinderGeometry args={[0.22, 0.32, 6.2, 24]} />
            <meshStandardMaterial color={palette.trimSoft} roughness={0.8} />
          </mesh>
          <mesh position={[0, 6.75, 0]} castShadow>
            <cylinderGeometry args={[0.48, 0.36, 0.48, 24]} />
            <meshStandardMaterial color={palette.trim} roughness={0.42} metalness={0.14} />
          </mesh>
          <mesh position={[0, 0.45, 0]} castShadow>
            <cylinderGeometry args={[0.58, 0.42, 0.52, 24]} />
            <meshStandardMaterial color={palette.trim} roughness={0.42} metalness={0.12} />
          </mesh>
        </group>
      ))}

      <RoundedBox args={[6.6, 0.9, 3.9]} radius={0.18} position={[0, 0.45, -4.6]} castShadow receiveShadow>
        <meshStandardMaterial color={palette.pedestal} roughness={0.88} />
      </RoundedBox>
      <RoundedBox args={[3.6, 0.65, 2.1]} radius={0.16} position={[0, 1.1, -4.2]} castShadow receiveShadow>
        <meshStandardMaterial color={palette.pedestal} roughness={0.84} />
      </RoundedBox>
      <RoundedBox args={[9.8, 0.14, 0.56]} radius={0.06} position={[0, 0.92, -2.18]} castShadow receiveShadow>
        <meshStandardMaterial color={blendColor(palette.trimSoft, palette.trim, 0.32)} roughness={0.76} />
      </RoundedBox>
      <RoundedBox args={[1.8, 0.42, 1.2]} radius={0.14} position={[0, 1.5, -3.9]} castShadow>
        <meshStandardMaterial color={palette.trim} roughness={0.5} metalness={0.12} />
      </RoundedBox>
      {[-3.4, -1.7, 0, 1.7, 3.4].map((x, index) => (
        <mesh key={`setup-footlight-${x}`} position={[x, 0.98, -2.02]} castShadow>
          <sphereGeometry args={[0.08 + (index === 2 ? 0.02 : 0), 14, 14]} />
          <meshStandardMaterial color={index % 2 === 0 ? palette.halo : palette.accent} emissive={index % 2 === 0 ? palette.halo : palette.accent} emissiveIntensity={0.32} roughness={0.2} />
        </mesh>
      ))}

      <Float floatIntensity={0.24} rotationIntensity={0.12} speed={1.4}>
        <group ref={figureRef} scale={[1.18, 1.18, 1.18]}>
          <mesh ref={haloRef} position={[0, 1.28, -4.18]} rotation={[Math.PI / 2, 0, 0]}>
            <torusGeometry args={[1.18, 0.08, 18, 64]} />
            <meshBasicMaterial color={palette.halo} transparent opacity={0.78} />
          </mesh>
          <mesh position={[0, 1.72, -4.02]} castShadow>
            <sphereGeometry args={[0.34, 32, 32]} />
            <meshStandardMaterial color={palette.figure} roughness={0.38} metalness={0.06} emissive={palette.halo} emissiveIntensity={0.06} />
          </mesh>
          <mesh position={[0, 1.04, -4.08]} castShadow>
            <capsuleGeometry args={[0.38, 1.25, 10, 16]} />
            <meshStandardMaterial color={palette.figure} roughness={0.46} metalness={0.04} emissive={palette.halo} emissiveIntensity={0.08} />
          </mesh>
          <mesh position={[-0.6, 1.12, -4.02]} rotation={[0, 0, 0.45]} castShadow>
            <capsuleGeometry args={[0.12, 0.96, 8, 10]} />
            <meshStandardMaterial color={palette.figure} roughness={0.42} />
          </mesh>
          <mesh position={[0.64, 1.12, -4.02]} rotation={[0, 0, -0.24]} castShadow>
            <capsuleGeometry args={[0.12, 1.08, 8, 10]} />
            <meshStandardMaterial color={palette.figure} roughness={0.42} />
          </mesh>
          <mesh ref={batonRef} position={[1.12, 1.45, -3.92]} rotation={[0, 0, -0.68]} castShadow>
            <cylinderGeometry args={[0.024, 0.032, 1.44, 12]} />
            <meshStandardMaterial color={palette.trim} roughness={0.3} metalness={0.4} emissive={palette.halo} emissiveIntensity={0.04} />
          </mesh>
          <mesh position={[-0.22, 0.08, -4.08]} rotation={[0.14, 0, 0]} castShadow>
            <boxGeometry args={[1.12, 0.26, 0.56]} />
            <meshStandardMaterial color={palette.trimSoft} roughness={0.82} />
          </mesh>
        </group>
      </Float>

      {[
        [-4.8, 2.3, -1.5],
        [4.8, 2.3, -1.5],
        [-6.1, 1.6, -6.2],
        [6.1, 1.6, -6.2],
      ].map((position, index) => (
        <Float key={`${position[0]}-${position[2]}`} floatIntensity={0.16} rotationIntensity={0.12} speed={1.1 + index * 0.08}>
          <mesh position={position as [number, number, number]} castShadow>
            <sphereGeometry args={[0.24 + (index % 2) * 0.04, 24, 24]} />
            <meshStandardMaterial color={index % 2 === 0 ? palette.halo : palette.accent} emissive={index % 2 === 0 ? palette.halo : palette.accent} emissiveIntensity={0.22} roughness={0.2} />
          </mesh>
        </Float>
      ))}

      <mesh position={[-3.85, 0.92, -1.7]} rotation={[0, 0.28, 0]} castShadow>
        <boxGeometry args={[1.3, 1.7, 0.42]} />
        <meshStandardMaterial color={palette.trimSoft} roughness={0.76} />
      </mesh>
      <mesh position={[3.85, 0.92, -1.7]} rotation={[0, -0.28, 0]} castShadow>
        <boxGeometry args={[1.3, 1.7, 0.42]} />
        <meshStandardMaterial color={palette.trimSoft} roughness={0.76} />
      </mesh>
      <mesh position={[-3.85, 1.76, -1.72]} rotation={[-0.5, 0.28, 0]}>
        <planeGeometry args={[1.18, 0.78]} />
        <meshStandardMaterial color={palette.wall} roughness={0.84} />
      </mesh>
      <mesh position={[3.85, 1.76, -1.72]} rotation={[-0.5, -0.28, 0]}>
        <planeGeometry args={[1.18, 0.78]} />
        <meshStandardMaterial color={palette.wall} roughness={0.84} />
      </mesh>

      {[-7.1, 7.1].map((x, index) => (
        <group key={`pit-lantern-${x}`} position={[x, 5.42, -2.18]}>
          <mesh castShadow>
            <cylinderGeometry args={[0.08, 0.08, 1.6, 14]} />
            <meshStandardMaterial color={palette.trimSoft} roughness={0.52} metalness={0.22} />
          </mesh>
          <mesh position={[0, -1.04, 0]} castShadow>
            <sphereGeometry args={[0.22, 18, 18]} />
            <meshStandardMaterial color={index === 0 ? palette.halo : palette.accent} emissive={index === 0 ? palette.halo : palette.accent} emissiveIntensity={0.36} roughness={0.22} />
          </mesh>
        </group>
      ))}

      {[-8.52, 8.52].map((x) => (
        <mesh key={`side-bench-${x}`} position={[x, 0.56, -0.82]} rotation={[0, x < 0 ? 0.14 : -0.14, 0]} castShadow receiveShadow>
          <boxGeometry args={[2.1, 0.54, 0.82]} />
          <meshStandardMaterial color={palette.trimSoft} roughness={0.84} />
        </mesh>
      ))}

      {[-6.2, 6.2].map((x, index) => (
        <group key={`music-stand-${x}`} position={[x, 0.18, -2.28]} rotation={[0, x < 0 ? 0.18 : -0.18, 0]}>
          <mesh position={[0, 0.76, 0]} castShadow>
            <cylinderGeometry args={[0.05, 0.06, 1.32, 12]} />
            <meshStandardMaterial color={palette.trimSoft} roughness={0.64} />
          </mesh>
          <mesh position={[0, 1.48, 0.04]} rotation={[-0.34, 0, 0]} castShadow>
            <boxGeometry args={[0.84, 0.12, 0.58]} />
            <meshStandardMaterial color={index === 0 ? palette.trim : palette.wallInset} roughness={0.62} />
          </mesh>
          <mesh position={[index === 0 ? -0.32 : 0.3, 0.42, 0.24]} rotation={[0.2, 0, index === 0 ? -0.42 : 0.4]} castShadow>
            <capsuleGeometry args={[0.08, 0.72, 5, 8]} />
            <meshStandardMaterial color={index === 0 ? palette.halo : palette.accent} roughness={0.34} metalness={0.16} />
          </mesh>
        </group>
      ))}

      {[-9.02, 9.02].map((x, index) => (
        <Drape
          key={`setup-drape-${x}`}
          position={[x, 2.84, -9.36]}
          color={index === 0 ? palette.trim : palette.accent}
          scale={[0.92, 1.46, 1]}
        />
      ))}

      <Sparkles count={36} scale={[13, 6.5, 8]} position={[0, 4.1, -4.8]} size={2.1} color={palette.halo} speed={0.35} opacity={0.36} />
      <ContactShadows position={[0, 0.01, -4]} opacity={0.34} scale={18} blur={2.6} far={16} />
    </>
  );
}

function Drape({
  position,
  color,
  scale = [1, 1, 1],
}: {
  position: [number, number, number];
  color: string;
  scale?: [number, number, number];
}) {
  return (
    <group position={position} scale={scale}>
      <mesh castShadow>
        <planeGeometry args={[0.9, 3.3, 1, 10]} />
        <meshStandardMaterial color={color} roughness={0.88} side={THREE.DoubleSide} />
      </mesh>
      {[-0.28, 0, 0.28].map((x, index) => (
        <mesh key={x} position={[x, -0.06, 0.05]} rotation={[0, 0, index === 1 ? 0 : x * 0.2]} castShadow>
          <cylinderGeometry args={[0.06, 0.08, 3.18, 8]} />
          <meshStandardMaterial color={blendColor(color, "#20140d", 0.18)} roughness={0.82} />
        </mesh>
      ))}
    </group>
  );
}

export function SetupRoomViewport({
  session,
  themeMode = "light",
  themeProfile,
  loading = false,
  launching = false,
  caption,
  detailsOpen = false,
}: SetupRoomViewportProps) {
  const palette = useMemo(
    () =>
      setupPalette(
        themeMode,
        themeProfile ?? {
          accent: "#c99256",
          fill: "#88afc1",
          halo: "#e4c690",
          wallWarmth: "#e7ddcf",
          loadingTone: "#b87a48",
        },
      ),
    [themeMode, themeProfile],
  );
  const headline = session?.draft.title?.trim() || "AGI Transition Command";
  const subline = session
    ? `${session.draft.country || "United States"} · ${session.draft.persona_count} citizens · ${session.draft.stage_count} stages`
    : "Booting setup chamber";
  const scopeLine =
    clip(session?.draft.topic_lens || session?.draft.region_focus || session?.draft.premise || session?.guidance?.chamber_reply, 84) ||
    "Broad national AGI transition. Add a lens only if you want one.";
  const captionText = clip(caption?.text || session?.guidance?.chamber_reply, 150);

  return (
    <section className="scene scene--setup immersive-stage__scene setup-room">
      <div className="scene__canvas setup-room__canvas">
        <Canvas dpr={[1, 1.35]} shadows="percentage" gl={{ antialias: true }}>
          <SetupRoomScene palette={palette} active={loading || launching || Boolean(session)} />
        </Canvas>
      </div>
      {detailsOpen ? (
        <div className="scene__hud scene__hud--setup-room">
          <span className="scene__eyebrow">{launching ? "Launching world" : loading ? "Opening chamber" : "Setup chamber"}</span>
          <strong className="setup-room__headline">{headline}</strong>
          <p>{subline}</p>
          <div className="scene__chips scene__chips--setup">
            <span>{session?.guidance?.readiness === "needs_input" ? "Needs one more nudge" : "Ready to launch"}</span>
            <span>{session?.draft.country || "United States"}</span>
          </div>
          <p className="setup-room__scope">{scopeLine}</p>
        </div>
      ) : null}
      {captionText && !detailsOpen ? (
        <div className={`setup-room__caption ${detailsOpen ? "setup-room__caption--dimmed" : ""}`} style={{ background: palette.caption, borderColor: palette.captionBorder }}>
          <span>{speakerLabel(caption)}</span>
          <p>{captionText}</p>
        </div>
      ) : null}
    </section>
  );
}
