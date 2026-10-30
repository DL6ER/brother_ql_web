import json
import os

from app import create_app

SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'labeldesigner', 'openapi.json')


def _api_routes(app) -> dict:
    routes = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith('/labeldesigner/api/'):
            routes[rule.rule] = sorted(m.lower() for m in rule.methods - {'HEAD', 'OPTIONS'})
    return routes


def test_every_api_route_is_documented():
    # A new route or method needs to be added to openapi.json
    spec = json.load(open(SPEC))
    documented = {path: sorted(operations) for path, operations in spec['paths'].items()}
    assert documented == _api_routes(create_app())


def test_references_resolve():
    spec = json.load(open(SPEC))
    schemas = spec['components']['schemas']
    refs = set()

    def collect(node):
        if isinstance(node, dict):
            if '$ref' in node:
                refs.add(node['$ref'])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(spec)
    assert refs
    for ref in refs:
        assert ref.startswith('#/components/schemas/') and ref.split('/')[-1] in schemas, ref


def test_spec_is_served(tmp_path):
    client = create_app().test_client()
    response = client.get('/labeldesigner/api/openapi.json')
    assert response.status_code == 200
    assert response.get_json()['openapi'] == '3.1.0'
