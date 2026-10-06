"""Focused checks with temporary media/files and controlled frontend/route adapters.

Uses disposable files; native shutdown regressions start their own private
loopback listeners. Requires the Python requirements, Node.js 22+, ffmpeg and ffprobe.
Packaged application/browser acceptance is separate.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    'test_input_options.py', 'test_numbered_import.py', 'test_render_context.py', 'test_render_relative_paths.py', 'test_command_scope_limits.py',
    'test_stabilization_workflow.py', 'test_stabilization_sdk.py', 'test_ffmpeg_graph.py', 'test_font_portability.py',
    'test_http_protocol.py', 'test_request_access.py', 'test_upload_safety.py', 'test_runner_containment.py', 'test_native_shutdown.py', 'test_server_shutdown.py',
    'test_export_safety.py', 'test_png_sequence_publication.py', 'test_render_queue_lifecycle.py',
    'test_clip_attributes.py', 'test_clip_attributes_workflow.py', 'test_clip_attributes_sdk.py', 'test_clip_attributes_composition.py',
    'test_multicam_flatten.py', 'test_multicam_flatten_workflow.py', 'test_multicam_sdk.py', 'test_multicam_composition.py', 'test_source_frame_geometry.py',
    'test_sequence_creation.py', 'test_sequence_creation_workflow.py', 'test_sequence_nesting.py', 'test_sequence_nesting_workflow.py', 'test_sequence_organization_sdk.py', 'test_nested_composition.py',
    'test_source_interpretation.py', 'test_source_interpretation_workflow.py', 'test_source_interpretation_sdk.py', 'test_source_interpretation_delivery.py', 'test_source_timing_render.py',
    'test_source_creation.py', 'test_source_creation_workflow.py', 'test_source_creation_sdk.py', 'test_source_creation_delivery.py', 'test_source_creation_analysis.py',
    'test_source_relink.py', 'test_source_relink_workflow.py', 'test_source_relink_sdk.py', 'test_source_relink_delivery.py',
    'test_graphics_words.py', 'test_editorial_source_commands.py', 'test_editorial_source_sdk.py',
    'test_sequence_recipes.py', 'test_sequence_recipe_workflow.py', 'test_cover_workflow.py', 'test_sequence_recipes_sdk.py', 'test_cover_sdk.py',
    'test_recipe_plans.py', 'test_recipe_workflow.py', 'test_recipe_sdk.py',
    'test_audio_measurement.py', 'test_audio_workflow.py', 'test_audio_workflow_sdk.py',
    'test_audio_sync.py', 'test_source_replacement.py', 'test_sync_sdk.py',
    'test_render_replace.py', 'test_audio_remix.py', 'test_replacement_sdk.py',
    'test_launch_identity.py', 'test_launcher_supervision.py', 'test_bootstrap_install.py', 'test_runtime_policy.py', 'test_workspace_lock.py', 'test_candidate_build.py',
    'test_project_transaction.py', 'test_project_sync.py', 'test_project_lifecycle.py', 'test_project_save.py', 'test_resources.py', 'test_routes.py', 'test_proposal_history.py',
    'test_proposal_preview.py', 'test_agent_context.py', 'test_project_recovery.py', 'test_saved_versions.py',
    'test_restore_transactions.py', 'test_media_collection.py', 'test_collection_workflow.py', 'test_project_package.py', 'test_render_smoke.py', 'test_audio_contract.py', 'test_audio_ducking.py', 'test_nested_audio.py', 'test_mixer_dsp.py', 'test_mixer_edit.py', 'test_clip_split.py', 'test_source_edit.py', 'test_overlap_normalization.py', 'test_timeline_contract.py', 'test_timeline_trim_contract.py', 'test_timeline_range.py', 'test_timeline_range_contract.py', 'test_media_analysis.py', 'test_analysis_edits.py', 'test_analysis_sdk.py', 'test_link_match_contract.py', 'test_source_sync.py', 'test_render_color.py', 'test_source_color.py', 'test_source_color_history.py', 'test_delivery_color.py', 'test_sequence_index.py', 'test_track_mattes.py', 'test_rendered_preview.py',
    'test_encoder_capabilities.py', 'test_export_encoder_routes.py', 'test_export_queue.py', 'test_job_history.py', 'test_editing_workflow.py', 'test_transcript_editing.py', 'test_acceptance_media.py', 'test_work_budget.py', 'test_background_tasks.py', 'test_background_routes.py', 'test_media_metadata.py', 'test_interchange_timing.py', 'test_timecode.py', 'test_timecode_render.py', 'test_exact_frame.py', 'test_proxy_media.py', 'test_media_preview.py', 'test_media_cache.py', 'test_segment_cache.py', 'test_cache_operations.py',
)


def run_checks():
    env = dict(os.environ, PYTHONUTF8='1')
    if (ROOT / 'bin').is_dir(): env['PATH'] = str(ROOT / 'bin') + os.pathsep + env.get('PATH', '')
    node = shutil.which('node', path=env.get('PATH'))
    if not node:
        print('CANNOT_RUN: Node.js 22 or newer is required.', file=sys.stderr); return 2
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name, path=env.get('PATH')):
            print(f'CANNOT_RUN: {name} is required on PATH or in bin/.', file=sys.stderr); return 2
    # Imported server modules can initialize a library before an individual
    # fixture overrides its globals. Never inherit a caller's production root.
    parent = env.get('TEMP') or env.get('TMP') or env.get('TMPDIR') or tempfile.gettempdir()
    try:
        scratch = tempfile.TemporaryDirectory(prefix='filmocity-checks-', dir=parent)
    except OSError as error:
        print(f'CANNOT_RUN: Cannot create private test files: {error}', file=sys.stderr)
        return 2
    result_code = 0
    try:
        scopes = [('frontend', [node, str(ROOT / 'tests/run_frontend.cjs')])]
        scopes.extend((name, [sys.executable, str(ROOT / 'tests' / name)]) for name in SUITES)
        for index, (name, command) in enumerate(scopes):
            scope = Path(scratch.name) / str(index)
            temporary = scope / 'temp'
            temporary.mkdir(parents=True)
            library = scope / 'data'
            child_env = dict(env, FILMOCITY_ROOT=str(library), FILMOCITY_DATA=str(library),
                             TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
            # Preserve an outer captured-run bytecode location; otherwise keep
            # generated bytecode in our own scope rather than the source tree.
            child_env.setdefault('PYTHONPYCACHEPREFIX', str(scope / 'pycache'))
            print(f'Running {name}', flush=True)
            result = subprocess.run(command, cwd=ROOT, env=child_env)
            if result.returncode:
                result_code = result.returncode
                break
    finally:
        try:
            scratch.cleanup()
        except OSError as error:
            print(f'CLEANUP_FAILED: Owned test files remain at {scratch.name}: {error}', file=sys.stderr)
            result_code = 3
    if result_code == 0:
        print('All focused checks passed. Full server/browser/native acceptance remains separate.')
    return result_code


if __name__ == '__main__': raise SystemExit(run_checks())
