"""Connect the real SDK to extracted saved routes, without an HTTP framework."""
import copy

from background_tasks import TaskContext
from filmocity_client import Filmocity, FilmocityError
import test_project_sync as store


def saved_client(fixture):
    class Client(Filmocity):
        def __init__(self):
            super().__init__(); self.calls = []

        def _call(self, path, body=None, method=None):
            self.calls.append((path, copy.deepcopy(body), method))
            try:
                if path == '/api/project/state': return {'project': fixture.project(), 'context': fixture.current()}
                if path == '/api/tasks': return {'context': fixture.current(), 'tasks': [copy.deepcopy(value['record']) for value in fixture.manager.values.values()]}
                if path.startswith('/api/recipes/') or path == '/api/sequences/variants':
                    name = 'sequence_variants' if path.endswith('/variants') else 'recipe_' + path.rsplit('/', 1)[-1]
                    response = fixture.route(name, body); identity = response['task']['id']; value = fixture.manager.values[identity]
                    handler = '_task_cover_workflow' if value['record']['kind'] == 'cover' else '_task_recipe_workflow'
                    fixture.env[handler](value['payload'], TaskContext(fixture.manager, identity))
                    fixture.manager.store.save(value); return response
                if path.startswith('/api/tasks/'):
                    identity, action = path.split('/')[3:5]
                    name = {'recipe': 'background_recipe_review', 'cover': 'background_cover_review', 'apply': 'background_task_apply'}[action]
                    return fixture.route(name, body, identity)
            except store.HTTPError as error: raise FilmocityError(str(error.detail)) from error
            raise AssertionError(path)
    return Client()
