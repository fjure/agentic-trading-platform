"""Persist verified issue read-backs; CLI mode independently re-reads GitHub."""
import argparse
import hashlib
import json
import runpy
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--complete',action='store_true')
p.add_argument('--transport', choices=['github-mcp','github-cli'], default='github-mcp')
p.add_argument('--write-task', help='Task whose actual mutation was followed by live read-back')
args=p.parse_args()
entries=json.loads((ROOT/'.tooling/output/verified-issue-readbacks.json').read_text(encoding='utf-8'))
mapping_path=ROOT/'docs/development/github-issue-sync.json'
mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
previous={t['taskId']:t for t in mapping.get('tasks',[])}
assert mapping['repository']=='fjure/agentic-trading-platform'
cli_login=None
cli_version=None
if args.transport=='github-cli':
    def gh_json(*command):
        result=subprocess.run(['gh',*command],text=True,encoding='utf-8',capture_output=True)
        if result.returncode:
            raise RuntimeError('Independent CLI verification failed; diagnostics withheld')
        return json.loads(result.stdout)
    cli_login=gh_json('api','user','--hostname','github.com','--jq','{login:.login}')['login']
    assert cli_login=='fjure'
    result=subprocess.run(['gh','--version'],text=True,encoding='utf-8',capture_output=True,check=True)
    cli_version=result.stdout.splitlines()[0].split()[2]
    live=gh_json('issue','list','--repo','github.com/fjure/agentic-trading-platform',
                 '--state','all','--limit','1000','--json','number,title,body,state,labels,url')
    live_by_number={issue['number']:issue for issue in live}
    for entry in entries:
        issue=live_by_number[entry['issueNumber']]
        assert issue['body']==entry['body'] and issue['title']==entry['title']
        assert issue['state'].lower()==entry['state'] and issue['url']==entry['issueUrl']
        assert sorted(label['name'] for label in issue['labels'])==sorted(entry['labels'])
assert len({e['taskId'] for e in entries})==len(entries)
assert len({e['issueNumber'] for e in entries})==len(entries)
tasks=[]
read_tasks=runpy.run_path(str(ROOT/'scripts/validate-plan.py'))['read_tasks']
source_tasks=read_tasks()
source_by_id={t['id']:t for t in source_tasks}
for e in entries:
    assert re.findall(r'<!-- openspec-task-id: (PTV-\d+) -->',e['body'])==[e['taskId']], 'Ambiguous task marker'
    assert e['title']==f"[{e['taskId']}] {source_by_id[e['taskId']]['title']}", 'Task title drift'
    assert [dep['taskId'] for dep in e['dependsOn']]==source_by_id[e['taskId']]['dependsOn'], 'Dependency drift'
    assert e['state']==('closed' if source_by_id[e['taskId']]['done'] else 'open')
    if e['state']=='closed':
        assert e.get('completionEvidence'), 'Closed task requires passed-test evidence'
        assert (ROOT/e['completionEvidence']).is_file(), 'Evidence document missing'
    assert e['issueUrl']==f"https://github.com/fjure/agentic-trading-platform/issues/{e['issueNumber']}"
    assert all(l in e['labels'] for l in ('openspec','change:paper-trading-vertical-slice'))
    assert f"<!-- openspec-task-id: {e['taskId']} -->" in e['body']
    assert e['managed'] in e['body']
    task=dict(taskId=e['taskId'],issueNumber=e['issueNumber'],issueUrl=e['issueUrl'],contentHash=hashlib.sha256(e['managed'].encode('utf-8')).hexdigest(),syncedAt=e['verifiedAt'],dependsOn=e['dependsOn'],state=e['state'])
    if e.get('completionEvidence'): task['completionEvidence']=e['completionEvidence']
    tasks.append(task)
known={e['taskId'] for e in entries}
planned={t['id'] for t in source_tasks}
assert known<=planned
pending=sorted(planned-known)
if args.complete: assert len(entries)==len(source_tasks) and not pending
mapping.update(tasks=tasks,pendingTaskIds=pending,status='synchronized-read-back-verified' if args.complete else 'sync-in-progress-read-back-verified',lastReadBackAt=entries[-1]['verifiedAt'],lastRepositoryReadAt=entries[-1]['verifiedAt'],blockers=[] if args.complete else ['Remaining leaf-task writes and final repository-wide read-back pending.'])
mapping['activeSyncTransport']=args.transport
if args.transport=='github-mcp':
    # A local recorder cannot manufacture new MCP authentication or write proof.
    mapping['readBackProvenance']='caller-provided official MCP tool results'
else:
    prior_cli=mapping.get('githubCliVerification',{})
    mapping['githubCliVerification']={**prior_cli,'version':cli_version,'authenticatedLogin':cli_login,
        'authorizedByUserAt':'2026-10-13','repository':'fjure/agentic-trading-platform',
        'verifiedAt':entries[-1]['verifiedAt'],'readBackIndependentlyVerified':True,
        'readChecks':{'issues':len(entries)}}
    if args.write_task:
        write_entry=next(e for e in entries if e['taskId']==args.write_task)
        write_issue=gh_json('api',f"repos/fjure/agentic-trading-platform/issues/{write_entry['issueNumber']}",
                            '--hostname','github.com')
        assert 'pull_request' not in write_issue
        assert write_issue['body']==write_entry['body'] and write_issue['state']==write_entry['state']
        mapping['githubCliVerification'].update(issueWriteReadBackVerified=True,
            lastVerifiedWriteTask=args.write_task,lastVerifiedWriteAt=entries[-1]['verifiedAt'])
    mapping['readBackProvenance']='independently re-read via user-authorized GH CLI'
mapping.pop('pendingMutation',None)
mapping['applicationProgress']={'complete':sum(t['done'] for t in source_tasks),'total':len(source_tasks),'openIssues':sum(e['state']=='open' for e in entries),'closedIssues':sum(e['state']=='closed' for e in entries)}
if args.complete:
    changed=[t['issueNumber'] for t in tasks if t['taskId'] in previous and
             (t['contentHash'],t['state']) !=
             (previous[t['taskId']]['contentHash'],previous[t['taskId']].get('state'))]
    created=sum(t['taskId'] not in previous for t in tasks)
    mapping['lastSyncRun']={'phase':'apply','updated':len(changed),
        'updatedIssueNumbers':changed,'created':created,
        'unchanged':len(tasks)-len(changed)-created,'duplicates':0,
        'closed':mapping['applicationProgress']['closedIssues'],
        'open':mapping['applicationProgress']['openIssues'],
        'readBackAt':entries[-1]['verifiedAt']}
mapping_path.write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'PASS persisted {len(entries)} verified task mappings; {len(pending)} pending.')
