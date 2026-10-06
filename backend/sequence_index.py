"""Request-local sequence snapshots with indexed, order-preserving interval selection.

An index belongs to one captured sequence and is never retained across edits.
Chunk output is copied separately so rendering cannot mutate the snapshot.
"""
import copy
import json


class IntervalIndex:
    def __init__(self, entries):
        entries = sorted(entries, key=lambda item: (item[0], item[2]))

        def build(lo, hi):
            if lo >= hi:
                return None
            mid = (lo + hi) // 2
            item = entries[mid]
            left, right = build(lo, mid), build(mid + 1, hi)
            end = max(item[1], left[0] if left else item[1], right[0] if right else item[1])
            return end, item, left, right

        self.root = build(0, len(entries))

    def overlapping(self, start, end):
        found = []

        def visit(node):
            if node is None or node[0] <= start:
                return
            _, item, left, right = node
            visit(left)
            if item[0] < end:
                if item[1] > start:
                    found.append(item)
                visit(right)

        visit(self.root)
        # Timeline storage order matters for overlapping layers and cache keys.
        return [item[3] for item in sorted(found, key=lambda item: item[2])]


class SequenceIndex:
    def __init__(self, sequence, duration):
        self.source = sequence
        snapshot = json.loads(json.dumps(sequence))
        self.metadata = {k: v for k, v in snapshot.items() if k not in ('tracks', 'captions', 'markers')}
        self.tracks = []
        for track in snapshot['tracks']:
            intervals = IntervalIndex((c['start'], c['start'] + duration(c), i, c)
                                      for i, c in enumerate(track['clips']))
            self.tracks.append(({k: v for k, v in track.items() if k != 'clips'}, intervals))
        self.captions = IntervalIndex((c['start'], c['end'], i, c)
                                     for i, c in enumerate(snapshot.get('captions') or []))

    def select(self, sequence, start, end):
        if sequence is not self.source:
            raise ValueError('A sequence index belongs to its captured source sequence.')
        result = copy.deepcopy(self.metadata)
        result.update(in_point=None, out_point=None, markers=[], tracks=[])
        for metadata, intervals in self.tracks:
            track = copy.deepcopy(metadata)
            track['clips'] = copy.deepcopy(intervals.overlapping(start + 1e-6, end - 1e-6))
            result['tracks'].append(track)
        result['captions'] = copy.deepcopy(self.captions.overlapping(start, end))
        return result
