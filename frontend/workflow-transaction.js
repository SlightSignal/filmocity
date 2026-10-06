/* Guard long-running workflow mutations against stale drafts and uncertain replies. */
(function (global) {
  async function run(deps, request, basis) {
    return deps.withSavedProject(async state => {
      if (!deps.sync.sameProject(basis, state.context) || basis.revision !== state.context.revision || basis.sequence !== deps.sequence()) {
        throw new Error('The edit changed. Refresh the workflow and review your choices before applying.');
      }
      const expected = { ...state.context };
      let result, confirmed = false;
      deps.preserve(state); deps.busy(true);
      try {
        result = await request(expected);
        if (result?.ok !== true || !deps.sync.validContext(result.context) || !deps.sync.sameProject(expected, result.context)) {
          throw new Error('The server did not confirm this workflow action');
        }
        confirmed = true;
        if (!await deps.reload(result.context)) throw new Error('The editor could not refresh');
        deps.clear(state);
        return result;
      } catch (error) {
        if (!result && [400, 401, 403, 404, 409, 422, 501].includes(error.status)) deps.clear(state);
        else {
          state.error = (confirmed ? 'Saved, but the editor could not refresh: ' : 'Workflow outcome not confirmed: ') + (error.message || error);
          deps.changed();
          throw new Error(state.error + '. Open Recovery before continuing; do not repeat the action.');
        }
        throw error;
      } finally { deps.busy(false); deps.changed(); }
    });
  }
  global.FilmocityWorkflowTransaction = { run };
  if (typeof module !== 'undefined') module.exports = { run };
})(typeof window === 'undefined' ? globalThis : window);
