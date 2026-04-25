#!/usr/bin/env python3
"""Generate Premiere Pro .prproj from combined EDL data."""

import json
import os
import sys
import uuid


TICKS_PER_SECOND = 254016000000


def seconds_to_ticks(seconds: float) -> int:
    return int(seconds * TICKS_PER_SECOND)


def ticks_to_timecode(ticks: int) -> str:
    total_seconds = ticks / TICKS_PER_SECOND
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    f = int((total_seconds % 1) * 30)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def generate_prproj(
    clips: list,
    source_files: dict,
    output_path: str,
    width: int = 2560,
    height: int = 1380,
    fps_num: int = 24,
    fps_den: int = 1,
    sample_rate: int = 48000,
    sequence_name: str = "Smart Cut",
):
    """Generate a Premiere Pro .prproj file.

    Args:
        clips: list of dicts with keys: tape, src_in_tc, src_out_tc, rec_start_tc, rec_end_tc, src_start_sec, src_end_sec
        source_files: dict mapping tape name to file path (e.g. {"V1": "v1.mp4"})
        output_path: output .prproj file path
    """
    # Generate unique object IDs
    obj_counter = [10]
    def new_id():
        obj_counter[0] += 1
        return str(obj_counter[0])

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<PrPremiereProject version="58.0" objectID="1" classID="2">')

    # --- Project ---
    lines.append(f'  <Project objectID="2" classID="3">')

    # --- ProjectItems (media sources) ---
    lines.append(f'    <ProjectItems objectID="3" classID="4">')

    media_ids = {}
    for tape, filepath in source_files.items():
        mid = new_id()
        media_ids[tape] = mid
        file_url = "file:///" + os.path.abspath(filepath).replace("\\", "/")
        duration_ticks = seconds_to_ticks(3600)  # placeholder, PR will detect actual
        lines.append(f'      <ProjectItem objectID="{new_id()}" classID="5" typeID="0">')
        lines.append(f'        <Name>{os.path.basename(filepath)}</Name>')
        lines.append(f'        <MediaSource objectID="{mid}" classID="6">')
        lines.append(f'          <ExternalMedia>')
        lines.append(f'            <FilePath>{file_url}</FilePath>')
        lines.append(f'          </ExternalMedia>')
        lines.append(f'        </MediaSource>')
        lines.append(f'        <Duration>{duration_ticks}</Duration>')
        lines.append(f'      </ProjectItem>')

    lines.append(f'    </ProjectItems>')

    # --- Sequence ---
    seq_id = new_id()
    lines.append(f'    <Sequence objectID="{seq_id}" classID="7">')
    lines.append(f'      <Name>{sequence_name}</Name>')

    # --- Format ---
    lines.append(f'      <Format>')
    lines.append(f'        <VideoFrameRate>')
    lines.append(f'          <Timebase>{fps_num}/{fps_den}</Timebase>')
    lines.append(f'          <Ntsc>false</Ntsc>')
    lines.append(f'        </VideoFrameRate>')
    lines.append(f'        <Width>{width}</Width>')
    lines.append(f'        <Height>{height}</Height>')
    lines.append(f'        <SampleRate>{sample_rate}</SampleRate>')
    lines.append(f'        <AudioLayoutType>stereo</AudioLayoutType>')
    lines.append(f'        <AudioChannelType>stereo</AudioChannelType>')
    lines.append(f'        <Fields>0</Fields>')
    lines.append(f'      </Format>')

    # --- Media ---
    lines.append(f'      <Media>')

    # Video Track
    lines.append(f'        <VideoTrack TrackIndex="0">')
    for clip in clips:
        cid = new_id()
        tape = clip["tape"]
        src_in = seconds_to_ticks(clip["src_start_sec"])
        src_out = seconds_to_ticks(clip["src_end_sec"])
        start = seconds_to_ticks(clip["rec_start_sec"])
        end = seconds_to_ticks(clip["rec_end_sec"])
        lines.append(f'          <ClipItem objectID="{cid}" classID="8">')
        lines.append(f'            <Name>{os.path.basename(source_files[tape])}</Name>')
        lines.append(f'            <SourceTrackIndex>0</SourceTrackIndex>')
        lines.append(f'            <SourceInPoint>{src_in}</SourceInPoint>')
        lines.append(f'            <SourceOutPoint>{src_out}</SourceOutPoint>')
        lines.append(f'            <InPoint>{start}</InPoint>')
        lines.append(f'            <OutPoint>{end}</OutPoint>')
        lines.append(f'            <StartTime>{start}</StartTime>')
        lines.append(f'            <EndTime>{end}</EndTime>')
        lines.append(f'            <MediaSource objectRef="{media_ids[tape]}"/>')
        lines.append(f'            <Components>')
        lines.append(f'              <Component>')
        lines.append(f'                <DescriptiveMetadata/>')
        lines.append(f'                <Properties>')
        lines.append(f'                  <FrameRate>')
        lines.append(f'                    <Timebase>{fps_num}/{fps_den}</Timebase>')
        lines.append(f'                    <Ntsc>false</Ntsc>')
        lines.append(f'                  </FrameRate>')
        lines.append(f'                </Properties>')
        lines.append(f'              </Component>')
        lines.append(f'            </Components>')
        lines.append(f'          </ClipItem>')
    lines.append(f'        </VideoTrack>')

    # Audio Track
    lines.append(f'        <AudioTrack TrackIndex="0">')
    for clip in clips:
        cid = new_id()
        tape = clip["tape"]
        src_in = seconds_to_ticks(clip["src_start_sec"])
        src_out = seconds_to_ticks(clip["src_end_sec"])
        start = seconds_to_ticks(clip["rec_start_sec"])
        end = seconds_to_ticks(clip["rec_end_sec"])
        lines.append(f'          <ClipItem objectID="{cid}" classID="9">')
        lines.append(f'            <Name>{os.path.basename(source_files[tape])}</Name>')
        lines.append(f'            <SourceTrackIndex>0</SourceTrackIndex>')
        lines.append(f'            <SourceInPoint>{src_in}</SourceInPoint>')
        lines.append(f'            <SourceOutPoint>{src_out}</SourceOutPoint>')
        lines.append(f'            <InPoint>{start}</InPoint>')
        lines.append(f'            <OutPoint>{end}</OutPoint>')
        lines.append(f'            <StartTime>{start}</StartTime>')
        lines.append(f'            <EndTime>{end}</EndTime>')
        lines.append(f'            <MediaSource objectRef="{media_ids[tape]}"/>')
        lines.append(f'            <Components>')
        lines.append(f'              <Component>')
        lines.append(f'                <DescriptiveMetadata/>')
        lines.append(f'                <Properties>')
        lines.append(f'                  <FrameRate>')
        lines.append(f'                    <Timebase>{fps_num}/{fps_den}</Timebase>')
        lines.append(f'                    <Ntsc>false</Ntsc>')
        lines.append(f'                  </FrameRate>')
        lines.append(f'                </Properties>')
        lines.append(f'              </Component>')
        lines.append(f'            </Components>')
        lines.append(f'          </ClipItem>')
    lines.append(f'        </AudioTrack>')

    lines.append(f'      </Media>')
    lines.append(f'    </Sequence>')
    lines.append(f'  </Project>')
    lines.append(f'</PrPremiereProject>')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Generated: {output_path} ({len(clips)} clips)")
    return output_path


def parse_combined_edl(edl_path: str) -> list:
    """Parse combined EDL into clip list with seconds."""
    clips = []
    with open(edl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("TITLE"):
                continue
            parts = line.split()
            if len(parts) < 8:
                continue

            def tc_to_sec(tc):
                h, m, s, fr = tc.split(":")
                return int(h) * 3600 + int(m) * 60 + int(s) + int(fr) / 30

            clips.append({
                "tape": parts[1],
                "src_in_tc": parts[4],
                "src_out_tc": parts[5],
                "rec_start_tc": parts[6],
                "rec_end_tc": parts[7],
                "src_start_sec": tc_to_sec(parts[4]),
                "src_end_sec": tc_to_sec(parts[5]),
                "rec_start_sec": tc_to_sec(parts[6]),
                "rec_end_sec": tc_to_sec(parts[7]),
            })
    return clips


if __name__ == "__main__":
    # Example: generate from combined.edl
    sc = sys.argv[1] if len(sys.argv) > 1 else "."
    clips = parse_combined_edl(f"{sc}/combined.edl")

    source_files = {
        "V1": f"{sc}/v1/v1.mp4",
        "V2": f"{sc}/v2/v2.mp4",
        "V3": f"{sc}/v3/v3.mp4",
    }

    generate_prproj(
        clips=clips,
        source_files=source_files,
        output_path=f"{sc}/smart_cut.prproj",
        sequence_name="Smart Cut",
    )
