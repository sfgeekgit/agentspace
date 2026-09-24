#!/usr/bin/env python3
"""Build a Borgo temperament root without changing the original baked runtime.

Only NPC/reserve ROLE.md files are changed in a pristine recess_borgo:1.0
image. The authored variant directory is archived in its new OCI labels.
This builds locally; it does not start agents or publish anything.
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agentspace import audit, builder, db, docker_host, oci, registry, versioning
from agentspace.runtimes import pi

BASE_SNAP = "ff18746f70634f43b782cc5bfc45876d"
SEED = 1077061960
MODEL = "deepseek/deepseek-v4.1-flash"

FINGERPRINT = """
import hashlib,json,pathlib
assert not pathlib.Path('/dispatch/state.json').exists(), 'Source contains a played game'
assert not pathlib.Path('/run/svc/openrouter_key').exists(), 'Source contains a key'
assert not list(pathlib.Path('/agents').glob('*/sessions/**/*.jsonl')), 'Source contains sessions'
out={}
for root in ['/agents','/world','/runtime_pi','/dispatch']:
 for p in sorted(pathlib.Path(root).rglob('*')):
  if p.is_file():
   s=p.stat()
   out[str(p)]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                'uid':s.st_uid,'gid':s.st_gid,'mode':s.st_mode}
print(json.dumps(out))
"""

PATCH_ROLES = """
import json,pathlib,sys
for change in json.load(sys.stdin):
 p=pathlib.Path(change['path'])
 assert p.read_text()==change['before'], 'Unexpected source role: '+str(p)
 p.write_text(change['after'])
"""


def build(tone):
    baseline = db.get_snap_by_id(BASE_SNAP)
    assert baseline and baseline['scenario'] == 'recess_borgo' and baseline['version'] == '1.0'
    assert baseline['runtime'] == 'pi' and not baseline.get('parent_snap_id')
    assert baseline['model'] == MODEL
    roster = baseline['roster']
    assert len(roster) == 14
    assert all(a['model'] == MODEL and a['persona'] == 'blank' for a in roster)
    source = REPO / 'scenarios' / 'recess_borgo'
    name = f'recess_borgo_{tone}'
    dest = REPO / 'scenarios' / name
    scen = registry.load_scen(name)
    params = registry.validate_params(scen['params_schema'], {})
    seed, ids, roles = builder.plan_roster(name, len(roster), params, seed=SEED)
    assert ids == [a['id'] for a in roster] and roles == [a['role'] for a in roster]
    assert params['max_turns'] == 80

    # Establish that today's base content matches the original archived source.
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(baseline['scen_src']))) as archive:
        for member in archive:
            if member.isfile():
                assert (source / member.name).read_bytes() == archive.extractfile(member).read(), member.name
    for rel in ['world.md', 'roles/player.md', 'roles/gm.md']:
        assert (source / rel).read_bytes() == (dest / rel).read_bytes(), rel
    for p in (source / 'dispatch').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            assert p.read_bytes() == (dest / p.relative_to(source)).read_bytes(), str(p)

    image = docker_host.stdout('localhost', 'image', 'inspect', '--format', '{{.Id}}', baseline['ghcr_tag']).strip()
    version = versioning.next_root_version(name)
    snap_id = uuid.uuid4().hex
    tag = versioning.ghcr_tag(name, version)
    container = f'as-borgo-{tone}-{snap_id[:10]}'
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    changes = []
    for a in roster:
        if a['role'].startswith('npc_') or a['role'] == 'reserve':
            before = (source / 'roles' / f"{a['role']}.md").read_text()
            after = (dest / 'roles' / f"{a['role']}.md").read_text()
            assert after.startswith(before.rstrip() + '\n\n') and after != before
            changes.append({'path':f"/agents/{a['id']}/ROLE.md", 'before':before, 'after':after})
    assert len(changes) == 12

    try:
        docker_host.run('localhost', 'run', '-d', '--network', 'none', '--name', container,
                        '--entrypoint', 'sleep', image, 'infinity')
        fingerprint = lambda: json.loads(docker_host.exec_('localhost', container, 'python3', '-c', FINGERPRINT))
        before = fingerprint()
        docker_host.run('localhost', 'exec', '-i', container, 'python3', '-c', PATCH_ROLES,
                        input=json.dumps(changes))
        after = fingerprint()
        expected = {c['path'] for c in changes}
        assert set(before) == set(after)
        assert {p for p in before if before[p] != after[p]} == expected
        for c in changes:
            p = c['path']
            assert after[p]['sha256'] == hashlib.sha256(c['after'].encode()).hexdigest()
            assert all(before[p][k] == after[p][k] for k in ['uid', 'gid', 'mode'])
        config = json.loads(docker_host.exec_('localhost', container, 'cat', '/world/world.json'))
        assert config['params'] == params and config['model'] == MODEL
        assert config['models'] == {a['id']:a['model'] for a in roster}
        assert config['plain'] is True and config['max_tokens'] == 8192 and config['thinking'] == 'low'

        # Keep the base's roster and runtime metadata, but create a distinct root.
        snap = {k:copy.deepcopy(v) for k,v in baseline.items() if k in oci.LABEL_FIELDS}
        snap.update(snap_id=snap_id, scenario=name, version=version,
                    ghcr_tag=tag, created_at=now, parent_snap_id=None, parent_version=None,
                    env_name=None, scen=name,
                    scen_url=f'https://github.com/{versioning.GHCR_REPO_DEFAULT}/tree/main/scenarios/{name}',
                    scen_src=oci.pack_dir(dest), source_image=image, files={},
                    budget_usd=None, budget_used=None,
                    creation_message=f'world root: 14 agents; {tone} NPC temperament; exact original Borgo runtime',
                    notes=[{'ts':now,'text':f'Derived from recess_borgo:1.0 ({BASE_SNAP}), image {image}. '
                                           f'Only the 12 NPC/reserve ROLE.md files changed; seed {SEED}; '
                                           'player, GM, world configuration, runtime, and dispatcher preserved.'}])
        built_image = oci.commit_with_labels('localhost', container, tag, oci.make_labels(snap),
                                              changes=pi.COMMIT_CHANGES)
        snap.update(indexed_at=now, notes_dirty=0)
        db.upsert_snap(snap)
        audit.log('world.create', f'{name}:{version}', args={
            'snap_id':snap_id,'scen':name,'runtime':'pi','source_image':image,
            'source_snap_id':BASE_SNAP,'seed':seed,'params':params,'modules':[],
            'roster':roster,'npc_temperament':tone,'changed_files':sorted(expected),
            'build_method':'pristine snapshot role-only derivation'})
        print(json.dumps({'world':f'{name}:{version}','snap_id':snap_id,'image_id':built_image,
                          'source_image':image,'changed_files':sorted(expected)}, indent=2))
    finally:
        docker_host.run('localhost', 'rm', '-f', container, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tone', choices=['nice','mean'])
    build(parser.parse_args().tone)
