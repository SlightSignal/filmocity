"""Reproducible preparation benchmark; no encoding/native UI performance claim."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
import editing_workflow as current


def load_baseline():
    path = ROOT / 'benchmarks/baselines/editing-workflow-before.py'
    spec = importlib.util.spec_from_file_location('story_before', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fixture(minutes):
    count = minutes * 150
    words = [{'w': f'word{i}' + ('.' if i % 12 == 11 else ''), 's': round(i * .4, 5), 'e': round(i * .4 + .3, 5), 'p': .95} for i in range(count)]
    seq = {'id': 's', 'name': 'Long interview', 'width': 640, 'height': 360, 'fps': 30,
        'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [
            {'id': f'c{i}', 'media_id': 'm', 'start': i * 30, 'in_': 0, 'out': 30, 'speed': 1} for i in range(minutes * 2)]}],
        'transcript': words, 'captions': [{'id': f'cap{i}', 'text': ' '.join(w['w'] for w in words[i:i+7]), 'start': words[i]['s'], 'end': words[min(i+6,count-1)]['e']} for i in range(0,count,7)],
        'markers': [{'id': f'm{i}', 'time': i * 60 + 1, 'name': f'Minute {i}'} for i in range(minutes)]}
    project = {'id': 'p', 'name': 'Fixture', 'version': 3, 'media': {'m': {'id': 'm', 'duration': 30, 'has_video': True}}, 'sequences': [seq]}
    action = {'action': 'story', 'sequence': 's', 'words': [i for i in range(count) if (i // 12) % 3 == 0], 'padding': .08}
    return project, action


def semantic(sequence):
    seq = copy.deepcopy(sequence); seq.pop('id', None); seq.pop('transcript_basis', None)
    groups = {}
    for track in seq['tracks']:
        for clip in track['clips']:
            clip.pop('id', None)
            if clip.get('group'): clip['group'] = groups.setdefault(clip['group'], len(groups))
    for marker in seq.get('markers', []): marker.pop('id', None)
    for caption in seq.get('captions', []): caption.pop('id', None)
    return seq


def measure(module, project, action):
    real_slice = module.chunk_sequence; copied_words = calls = 0
    def slice(*args, **kwargs):
        nonlocal copied_words, calls
        chunk = real_slice(*args, **kwargs); calls += 1; copied_words += len(chunk.get('transcript', [])); return chunk
    module.chunk_sequence = slice
    try:
        start = time.perf_counter(); result, reply = module.build(project, action); elapsed = time.perf_counter() - start
    finally: module.chunk_sequence = real_slice
    seq = next(s for s in result['sequences'] if s['id'] == reply['sequence'])
    digest = hashlib.sha256(json.dumps(semantic(seq), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'seconds': elapsed, 'slice_calls': calls, 'unused_word_records_copied_in_slices': copied_words, 'semantic_sha256': digest,
            'output_clips': sum(len(t['clips']) for t in seq['tracks']), 'output_words': len(seq['transcript'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--minutes', nargs='+', type=int, default=[3,20,120]); args = parser.parse_args()
    if args.output.exists(): parser.error('Use a new output path; existing evidence is never overwritten')
    if any(n < 1 or n > 120 for n in args.minutes): parser.error('Fixture duration must be 1–120 minutes')
    baseline = load_baseline(); cases = []
    for minutes in args.minutes:
        project, action = fixture(minutes); before = copy.deepcopy(project)
        a, b = measure(baseline, project, action), measure(current, project, action)
        if project != before or a['semantic_sha256'] != b['semantic_sha256']: raise RuntimeError('Semantic equivalence/input immutability failed')
        cases.append({'minutes': minutes, 'words': len(project['sequences'][0]['transcript']), 'before': a, 'after': b, 'speedup': a['seconds']/b['seconds']})
        print(f'{minutes} min: {a["seconds"]:.4f}s -> {b["seconds"]:.4f}s; identical edit', flush=True)
    report = {'format':1,'host':platform.platform(),'python':platform.python_version(),'repeats':1,
        'scope':'Synthetic CPU preparation only; excludes model inference, encoding, project saving and native/browser latency.',
        'baseline_sha256':hashlib.sha256((ROOT/'benchmarks/baselines/editing-workflow-before.py').read_bytes()).hexdigest(),
        'current_sha256':hashlib.sha256((ROOT/'backend/editing_workflow.py').read_bytes()).hexdigest(), 'cases':cases}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2); stream.write('\n')

if __name__ == '__main__': main()
