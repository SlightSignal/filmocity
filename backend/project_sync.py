"""Stable project identity and optimistic concurrency for editor requests.

The document's id is not the storage identity: Save As can retain that id.
Hash the complete saved document so legacy writers also invalidate old reads.
Callers must read, compare, and commit under the project-store lock.
"""
import hashlib
import json
import os


def workspace_id(root):
    workspace = os.path.normcase(os.path.realpath(root))
    return hashlib.sha256(workspace.encode('utf-8')).hexdigest()[:24]


def project_context(root, project_id, document):
    payload = json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {
        'workspace': workspace_id(root),
        'project': project_id,
        'revision': hashlib.sha256(payload.encode('utf-8')).hexdigest(),
    }


def matches_context(expected, current):
    # Omitted context preserves the existing agent/SDK contract. A provided
    # but malformed context must never silently fall back to legacy behavior.
    return isinstance(expected, dict) and all(
        expected.get(key) == current[key] for key in ('workspace', 'project', 'revision')
    )
