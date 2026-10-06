"""Exact reversible changes, including normalization and removed/null properties.

Paths are token arrays rather than display strings. Stable object IDs guard
indexed paths; unrelated untracked edits cannot redirect undo to another clip.
Legacy operation entries remain readable by the server's legacy undo adapter.
"""
import copy


class HistoryConflict(ValueError):
    pass


def _equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal(a[key], b[key]) for key in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return a == b


def changes_between(before, after):
    changes = []

    def visit(a, b, path, anchors):
        if _equal(a, b):
            return
        if isinstance(a, dict) and isinstance(b, dict):
            if a.get('id') is not None and a.get('id') == b.get('id'):
                anchors = anchors + [{'path': path, 'id': a['id']}]
            for key in sorted(a.keys() | b.keys()):
                if not path and key == 'updated':
                    continue
                if key in a and key in b:
                    visit(a[key], b[key], path + [key], anchors)
                else:
                    changes.append({'path': path + [key], 'anchors': anchors,
                                    'before': {'exists': key in a, 'value': copy.deepcopy(a.get(key))},
                                    'after': {'exists': key in b, 'value': copy.deepcopy(b.get(key))}})
            return
        if (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(isinstance(x, dict) and isinstance(y, dict) and x.get('id') is not None
                        and x.get('id') == y.get('id') for x, y in zip(a, b))):
            for index, (x, y) in enumerate(zip(a, b)):
                visit(x, y, path + [index], anchors)
            return
        changes.append({'path': path, 'anchors': anchors,
                        'before': {'exists': True, 'value': copy.deepcopy(a)},
                        'after': {'exists': True, 'value': copy.deepcopy(b)}})

    visit(before, after, [], [])
    return changes


def _at(document, path):
    value = document
    for key in path:
        value = value[key]
    return value


def apply_changes(document, changes, direction):
    """Validate every expected value before applying any part of undo/redo."""
    expected, target = ('after', 'before') if direction == 'undo' else ('before', 'after')
    if direction not in ('undo', 'redo'):
        raise ValueError('Unknown history direction')
    try:
        for change in changes:
            path = change['path']
            if not path:
                raise HistoryConflict('Cannot replace the project root from history')
            for anchor in change.get('anchors', []):
                if _at(document, anchor['path']).get('id') != anchor['id']:
                    raise HistoryConflict('The sequence, track or clip changed identity')
            parent, key = _at(document, path[:-1]), path[-1]
            exists = key in parent if isinstance(parent, dict) else isinstance(key, int) and 0 <= key < len(parent)
            wanted = change[expected]
            if exists != wanted['exists'] or (exists and not _equal(parent[key], wanted['value'])):
                raise HistoryConflict('The edited value changed outside this undo history')
        result = copy.deepcopy(document)
        for change in changes:
            path, value = change['path'], change[target]
            parent, key = _at(result, path[:-1]), path[-1]
            if value['exists']:
                parent[key] = copy.deepcopy(value['value'])
            elif isinstance(parent, dict):
                del parent[key]
            else:
                raise HistoryConflict('An individual list element cannot be removed by this history format')
        return result
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise HistoryConflict('The history no longer matches this project') from error
