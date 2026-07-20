"""
Converts a VRM1.0 file (VRoid Studio export) into a TalkingHead-compatible
.glb, by renaming skeleton bones and facial morph targets in place.

Why this exists: TalkingHead (met4citizen/TalkingHead) expects a Mixamo-style
skeleton (bone names like "Hips", "Spine", "LeftForeArm", ...) and Oculus-style
viseme morph targets (e.g. "viseme_aa"), because that's what Ready Player Me
exports use. VRoid Studio's VRM1.0 export uses different naming for both
(VRM/Unity-Humanoid bone names like "J_Bip_C_Hips", and VRoid's own
"Fcl_MTH_A"-style morph target names). This script bridges the two, deriving
the mapping directly from the file's own VRMC_vrm.humanoid.humanBones and
VRMC_vrm.expressions.preset blocks (both are part of the VRM1.0 spec, so this
works against any VRM1.0 export, not just this specific character) rather
than hardcoding VRoid's internal naming conventions.

What gets renamed:
  - ~50 skeleton bone nodes (VRM humanoid bone name -> Mixamo-style name)
  - The scene's root node -> "Armature" (matches TalkingHead's modelRoot
    default, so every avatar processed by this script can share one config
    value instead of needing a per-avatar modelRoot override)
  - 5 vowel mouth shapes + 2 blink shapes -> Oculus viseme / ARKit names
    (aa/ih/ou/ee/oh -> viseme_aa/I/U/E/O, blinkLeft/blinkRight ->
    eyeBlinkLeft/eyeBlinkRight)

What's intentionally left alone:
  - The 9 consonant visemes (PP, FF, TH, DD, kk, CH, SS, nn, RR) and "sil"
    have no VRoid equivalent - TalkingHead just won't find those morph
    targets and skips them silently, so consonant-heavy speech animates a
    little less richly than vowel-heavy speech. Not a bug, a known tradeoff
    (see design_plans/TALKING_AVATAR_AND_ROADMAP.md).
  - Everything else about the file (geometry, textures, materials, the
    binary buffer) is copied through byte-for-byte untouched. Only text
    inside the JSON chunk is modified.

Usage (single file):
    python scripts/vrm_to_talkinghead.py static/avatar/Sulafat.vrm static/avatar/Sulafat.glb

Usage (batch - process every .vrm in a folder):
    python scripts/vrm_to_talkinghead.py --batch static/avatar/raw_vrm static/avatar

The output filename in batch mode reuses the input's stem, e.g.
raw_vrm/Sulafat.vrm -> static/avatar/Sulafat.glb - so naming source .vrm
files after the voice they're paired with (see VOICE_OPTIONS in main.py)
means the output lands ready to reference by voice name, no extra mapping
step needed.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

# VRM1.0 humanBones key -> TalkingHead/Mixamo-style bone name.
# Left side is the VRM1.0 spec's standardized bone vocabulary (present in
# any conformant VRM1.0 export, not just VRoid) - see
# https://github.com/vrm-c/vrm-specification/tree/master/specification/VRMC_vrm-1.0
BONE_MAP = {
    "hips": "Hips",
    "spine": "Spine",
    "chest": "Spine1",
    "upperChest": "Spine2",
    "neck": "Neck",
    "head": "Head",
    "leftEye": "LeftEye",
    "rightEye": "RightEye",
    "leftShoulder": "LeftShoulder",
    "leftUpperArm": "LeftArm",
    "leftLowerArm": "LeftForeArm",
    "leftHand": "LeftHand",
    "rightShoulder": "RightShoulder",
    "rightUpperArm": "RightArm",
    "rightLowerArm": "RightForeArm",
    "rightHand": "RightHand",
    "leftUpperLeg": "LeftUpLeg",
    "leftLowerLeg": "LeftLeg",
    "leftFoot": "LeftFoot",
    "leftToes": "LeftToeBase",
    "rightUpperLeg": "RightUpLeg",
    "rightLowerLeg": "RightLeg",
    "rightFoot": "RightFoot",
    "rightToes": "RightToeBase",
}
# Fingers follow a regular pattern (VRM: Metacarpal/Proximal/Intermediate/
# Distal or Proximal/Intermediate/Distal depending on the thumb; Mixamo:
# always Finger1/2/3) - generated instead of hand-listed to avoid 40 lines
# of repetition and the copy-paste mistakes that come with it.
_VRM_FINGER_JOINTS = {
    "Thumb": ["Metacarpal", "Proximal", "Distal"],
    "Index": ["Proximal", "Intermediate", "Distal"],
    "Middle": ["Proximal", "Intermediate", "Distal"],
    "Ring": ["Proximal", "Intermediate", "Distal"],
    "Little": ["Proximal", "Intermediate", "Distal"],
}
_MIXAMO_FINGER_NAME = {"Thumb": "Thumb", "Index": "Index", "Middle": "Middle", "Ring": "Ring", "Little": "Pinky"}
for _side_vrm, _side_mixamo in (("left", "Left"), ("right", "Right")):
    for _finger, _joints in _VRM_FINGER_JOINTS.items():
        for _i, _joint in enumerate(_joints, start=1):
            vrm_key = f"{_side_vrm}{_finger}{_joint}"
            mixamo_name = f"{_side_mixamo}Hand{_MIXAMO_FINGER_NAME[_finger]}{_i}"
            BONE_MAP[vrm_key] = mixamo_name

# VRM1.0 expression preset name -> TalkingHead blend shape name.
# Only entries present here get renamed; anything else in the file's
# expression list (e.g. "happy", "angry" - VRM1.0 also standardizes these
# emotion presets) is left as-is. Not wired into TalkingHead's mood system
# yet - see design_plans/TALKING_AVATAR_AND_ROADMAP.md - but left untouched
# rather than stripped, in case that gets built later.
EXPRESSION_MAP = {
    "aa": "viseme_aa",
    "ih": "viseme_I",
    "ou": "viseme_U",
    "ee": "viseme_E",
    "oh": "viseme_O",
    "blinkLeft": "eyeBlinkLeft",
    "blinkRight": "eyeBlinkRight",
}

ARMATURE_ROOT_NAME = "Armature"  # TalkingHead's default `modelRoot` option


def _read_glb(path: Path) -> tuple[dict, bytes]:
    """Returns (json_chunk_as_dict, raw_bin_chunk_bytes). Only supports the
    two-chunk JSON+BIN layout, which is what every VRoid/VRM1.0 export uses
    (a GLB with no BIN chunk, e.g. a glTF referencing external buffers,
    isn't something VRoid produces - if this ever hits that case it fails
    loudly below rather than silently producing a broken file).
    """
    data = path.read_bytes()
    magic, version, total_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{path} is not a GLB file (bad magic header)")

    offset = 12
    json_chunk = None
    bin_chunk = b""
    while offset < total_length:
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        chunk_data = data[offset + 8: offset + 8 + chunk_length]
        if chunk_type == b"JSON":
            json_chunk = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == b"BIN\x00":
            bin_chunk = chunk_data
        offset += 8 + chunk_length

    if json_chunk is None:
        raise ValueError(f"{path}: no JSON chunk found")
    return json_chunk, bin_chunk


def _write_glb(path: Path, json_chunk: dict, bin_chunk: bytes) -> None:
    json_bytes = json.dumps(json_chunk, separators=(",", ":")).encode("utf-8")
    # glTF binary spec: each chunk is padded to a 4-byte boundary - JSON
    # with trailing spaces (0x20), BIN with trailing zero bytes.
    json_pad = (-len(json_bytes)) % 4
    json_bytes += b" " * json_pad
    bin_pad = (-len(bin_chunk)) % 4
    bin_chunk_padded = bin_chunk + b"\x00" * bin_pad

    total_length = 12 + (8 + len(json_bytes)) + (8 + len(bin_chunk_padded))

    with path.open("wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total_length))
        f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
        f.write(json_bytes)
        f.write(struct.pack("<I4s", len(bin_chunk_padded), b"BIN\x00"))
        f.write(bin_chunk_padded)


def convert(input_path: Path, output_path: Path) -> None:
    j, bin_chunk = _read_glb(input_path)

    vrmc = j.get("extensions", {}).get("VRMC_vrm")
    if vrmc is None:
        raise ValueError(
            f"{input_path}: no VRMC_vrm extension found - this doesn't look like a "
            "VRM1.0 file (VRM0.0 uses a different 'VRM' extension key, not handled "
            "by this script)."
        )

    nodes = j["nodes"]
    renamed_bones = []
    missing_bones = []

    # --- 1. Rename skeleton bones ---
    human_bones = vrmc.get("humanoid", {}).get("humanBones", {})
    for vrm_name, entry in human_bones.items():
        node_idx = entry.get("node")
        if node_idx is None:
            continue
        target_name = BONE_MAP.get(vrm_name)
        if target_name is None:
            continue  # VRM bones with no Mixamo equivalent (jaw, leftEye, etc.) - not needed by TalkingHead, left alone
        old_name = nodes[node_idx].get("name")
        nodes[node_idx]["name"] = target_name
        renamed_bones.append((vrm_name, old_name, target_name))

    for required_vrm_key in BONE_MAP:
        if required_vrm_key not in human_bones:
            missing_bones.append(required_vrm_key)

    # --- 2. Rename the scene root to "Armature", and reparent any other
    # top-level scene nodes under it ---
    #
    # TalkingHead finds blend shapes by traversing *inside* the armature
    # node only (this.armature.traverse(...) in showAvatar) - it never
    # looks at scene siblings. VRM keeps skinned meshes (Face/Body/Hair
    # here) as separate top-level scene nodes alongside the skeleton root,
    # not nested under it - so without this step, TalkingHead's traversal
    # never reaches the meshes and throws "Blend shapes not found" even
    # though the morph targets are correctly named. Reparenting is safe
    # here because all of these nodes have identity transforms (confirmed
    # by inspecting a real export) - if a future export had non-identity
    # transforms on these nodes this could shift them visually, so this
    # assumption is asserted below rather than applied blindly.
    scene_idx = j.get("scene", 0)
    scene_root_nodes = j["scenes"][scene_idx]["nodes"]
    root_node_idx = scene_root_nodes[0]
    old_root_name = nodes[root_node_idx].get("name")
    nodes[root_node_idx]["name"] = ARMATURE_ROOT_NAME

    reparented = []
    for sibling_idx in scene_root_nodes[1:]:
        sibling = nodes[sibling_idx]
        if any(k in sibling for k in ("translation", "rotation", "scale")):
            raise ValueError(
                f"Node {sibling_idx} ({sibling.get('name')!r}) has a non-identity transform - "
                "reparenting it under the armature root is not safe to do blindly here. "
                "This export doesn't match the identity-transform assumption this script "
                "relies on; needs a manual look before converting."
            )
        reparented.append(sibling_idx)
    nodes[root_node_idx].setdefault("children", [])
    nodes[root_node_idx]["children"].extend(reparented)
    j["scenes"][scene_idx]["nodes"] = [root_node_idx]

    # --- 3. Rename mouth/blink morph targets ---
    renamed_expressions = []
    missing_expressions = []
    expr_preset = vrmc.get("expressions", {}).get("preset", {})
    for expr_name, target_name in EXPRESSION_MAP.items():
        expr = expr_preset.get(expr_name)
        if expr is None:
            missing_expressions.append(expr_name)
            continue
        binds = expr.get("morphTargetBinds", [])
        if not binds:
            missing_expressions.append(expr_name)
            continue
        for bind in binds:
            node_idx = bind["node"]
            target_idx = bind["index"]
            mesh_idx = nodes[node_idx].get("mesh")
            if mesh_idx is None:
                continue
            for primitive in j["meshes"][mesh_idx].get("primitives", []):
                target_names = primitive.get("extras", {}).get("targetNames")
                if target_names and target_idx < len(target_names):
                    old = target_names[target_idx]
                    target_names[target_idx] = target_name
                    renamed_expressions.append((expr_name, old, target_name))

    _write_glb(output_path, j, bin_chunk)

    # --- Report ---
    print(f"{input_path.name} -> {output_path.name}")
    print(f"  Root node: {old_root_name!r} -> {ARMATURE_ROOT_NAME!r}")
    print(f"  Bones renamed: {len(renamed_bones)}/{len(BONE_MAP)}")
    if missing_bones:
        print(f"  ! Bones not found in this file's humanBones (skipped): {missing_bones}")
    print(f"  Blend shapes renamed: {len(renamed_expressions)}/{len(EXPRESSION_MAP)}")
    if missing_expressions:
        print(f"  ! Expressions not found in this file (skipped): {missing_expressions}")
    if len(renamed_bones) < len(BONE_MAP) or missing_expressions:
        print("  -> Some names were skipped. The avatar may still load, but movement/lip-sync")
        print("     for the missing parts won't work. Check the file was exported as VRM1.0.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Input .vrm file, or a folder of .vrm files with --batch")
    parser.add_argument("output", type=Path, help="Output .glb file, or a folder with --batch")
    parser.add_argument("--batch", action="store_true", help="Treat input/output as folders; convert every .vrm found")
    args = parser.parse_args()

    if args.batch:
        if not args.input.is_dir():
            sys.exit(f"--batch: {args.input} is not a directory")
        args.output.mkdir(parents=True, exist_ok=True)
        vrm_files = sorted(args.input.glob("*.vrm"))
        if not vrm_files:
            sys.exit(f"--batch: no .vrm files found in {args.input}")
        print(f"Found {len(vrm_files)} .vrm file(s) to convert.\n")
        for vrm_path in vrm_files:
            out_path = args.output / f"{vrm_path.stem}.glb"
            try:
                convert(vrm_path, out_path)
            except Exception as e:
                print(f"  FAILED: {type(e).__name__}: {e}")
            print()
    else:
        convert(args.input, args.output)


if __name__ == "__main__":
    main()
