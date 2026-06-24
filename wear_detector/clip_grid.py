# ABOUTME: Build a labeling clip grid — slice a recording into short audio+frame clips for a human to mark.
# ABOUTME: Turns "I can't hear it" into ground truth: open index.html, tick faults, export labels.csv.
import os
import sys

from wear_detector import frames
from wear_detector.fault_bundle import write_clip


def clip_windows(total_s, clip_s):
    """[(index, t0, t1), ...] covering [0, total_s) in clip_s steps (last clip may be short)."""
    out = []
    i, t = 1, 0.0
    while t < total_s - 1e-6:
        out.append((i, t, min(t + clip_s, total_s)))
        i += 1
        t += clip_s
    return out


def _audio_duration_s(wav_path):
    import wave
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / w.getframerate()


_HTML_HEAD = """<!doctype html><meta charset=utf-8>
<title>Fault labeling — {name}</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:1.5rem;background:#1d1f23;color:#e7e7e7}}
 h1{{font-size:18px}} .bar{{position:sticky;top:0;background:#1d1f23;padding:.6rem 0;border-bottom:1px solid #333}}
 button{{background:#1D9E75;color:#fff;border:0;padding:.5rem .9rem;border-radius:6px;cursor:pointer;font-size:14px}}
 table{{border-collapse:collapse;width:100%;margin-top:.5rem}}
 td,th{{border-bottom:1px solid #2c2f34;padding:.4rem .5rem;vertical-align:middle;text-align:left}}
 img{{width:160px;border-radius:4px;display:block}} audio{{width:240px}}
 tr.clip.marked{{background:#3a2a1f}} input[type=text]{{width:14rem;background:#15171a;color:#e7e7e7;border:1px solid #333;border-radius:4px;padding:.3rem}}
 select{{background:#15171a;color:#e7e7e7;border:1px solid #333;border-radius:4px;padding:.3rem}}
 .t{{color:#9aa0a6;font-variant-numeric:tabular-nums}}
</style>
<h1>Fault labeling — {name} <span class=t>({n} clips × {clip_s:.0f}s)</span></h1>
<div class=bar>
 Tick <b>fault</b>, pick a type, add notes — then
 <button onclick=exportCSV()>⬇ Export labels.csv</button>
 &nbsp;<span id=count class=t>0 marked</span>
</div>
<table><thead><tr><th>#</th><th>time</th><th>camera</th><th>listen</th><th>fault?</th><th>type</th><th>notes</th></tr></thead><tbody>
"""

_HTML_TAIL = """</tbody></table>
<script>
 function upd(){let m=[...document.querySelectorAll('tr.clip')].filter(tr=>tr.querySelector('.fault').checked);
   m.forEach(tr=>tr.classList.add('marked'));
   document.querySelectorAll('tr.clip').forEach(tr=>{if(!tr.querySelector('.fault').checked)tr.classList.remove('marked')});
   document.getElementById('count').textContent=m.length+' marked';}
 document.addEventListener('change',upd);
 function exportCSV(){let rows=[['clip','t_start_s','t_end_s','fault','type','notes']];
   document.querySelectorAll('tr.clip').forEach(tr=>{const f=tr.querySelector('.fault').checked?1:0,
     ty=tr.querySelector('.type').value, no=tr.querySelector('.notes').value.replace(/,/g,';');
     if(f||ty!=='-'||no)rows.push([tr.dataset.n,tr.dataset.t0,tr.dataset.t1,f,ty,no]);});
   const blob=new Blob([rows.map(r=>r.join(',')).join('\\n')],{type:'text/csv'});
   const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='labels.csv';a.click();}
</script>
"""


def build_grid(csv_path, wav_path, frames_src, out_dir, clip_s=5.0):
    """Slice wav into clip_s clips with a frame each; write index.html (player grid) + index.csv."""
    os.makedirs(os.path.join(out_dir, "clips"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)
    index = frames.parse_frame_index(frames_src)
    origin = frames.recording_origin_us(csv_path)
    wins = clip_windows(_audio_duration_s(wav_path), clip_s)

    rows = []
    for i, t0, t1 in wins:
        mid = 0.5 * (t0 + t1)
        clip = os.path.join("clips", f"clip{i:03d}.wav")
        img = os.path.join("frames", f"clip{i:03d}.jpg")
        write_clip(wav_path, os.path.join(out_dir, clip), t0, t1)
        fr, _dt = frames.nearest_frame(index, frames.audio_time_to_host_us(mid, origin))
        frames.extract_frames(frames_src, [fr[2]], os.path.join(out_dir, "frames"))
        os.replace(os.path.join(out_dir, "frames", os.path.basename(fr[2])),
                   os.path.join(out_dir, img))
        rows.append((i, t0, t1, clip, img))

    name = os.path.basename(wav_path)
    html = [_HTML_HEAD.format(name=name, n=len(rows), clip_s=clip_s)]
    types = "".join(f"<option>{t}</option>" for t in
                    ("-", "squeal", "knock", "grind", "rattle", "handling", "other"))
    for i, t0, t1, clip, img in rows:
        html.append(
            f'<tr class=clip data-n="{i}" data-t0="{t0:.1f}" data-t1="{t1:.1f}">'
            f'<td>{i}</td><td class=t>{t0:5.1f}–{t1:5.1f}s</td>'
            f'<td><img loading=lazy src="{img}"></td>'
            f'<td><audio controls preload=none src="{clip}"></audio></td>'
            f'<td><input type=checkbox class=fault></td>'
            f'<td><select class=type>{types}</select></td>'
            f'<td><input type=text class=notes placeholder="what you hear"></td></tr>')
    html.append(_HTML_TAIL)
    with open(os.path.join(out_dir, "index.html"), "w") as fh:
        fh.write("\n".join(html))

    with open(os.path.join(out_dir, "index.csv"), "w") as fh:
        fh.write("clip,t_start_s,t_end_s,clip_file,frame_file\n")
        for i, t0, t1, clip, img in rows:
            fh.write(f"{i},{t0:.1f},{t1:.1f},{clip},{img}\n")
    return rows


def main(csv_path, wav_path, frames_src, out_dir, clip_s=5.0):
    rows = build_grid(csv_path, wav_path, frames_src, out_dir, float(clip_s))
    print(f"clip grid: {len(rows)} clips of {clip_s}s -> {out_dir}")
    print(f"open {os.path.join(out_dir, 'index.html')} in a browser, mark faults, Export labels.csv")


if __name__ == "__main__":
    main(*sys.argv[1:6])
