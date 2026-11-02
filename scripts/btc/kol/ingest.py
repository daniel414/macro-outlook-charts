#!/usr/bin/env python3
"""Ingest get_users_posts MCP result files into raw/posts.jsonl and update since_id.
Usage: ingest.py <username> <result_file> [...pairs]
Fetch procedure (via x MCP, not callable from shell): for each top-level account in
kol_accounts.json call get_users_posts id=<id> ...; also for each aliases[] entry that
has an id (e.g. XiaoYao_Capital under xy_xtony). Alias posts are tagged owner=<parent>
and since_id is updated on the alias object. BTC views always fold into the parent
analyst row in latest_view.json.
then run this script."""
import json,re,sys,datetime as dt
acc=json.load(open('kol_accounts.json'))
by={a['username']:a for a in acc}
alias_parent={}
for a in acc:
  for al in a.get('aliases') or []:
    if isinstance(al, dict) and al.get('username'):
      by[al['username']]=al
      alias_parent[al['username']]=a['username']
    elif isinstance(al, str):
      alias_parent[al]=a['username']
seen=set()
try:
  for l in open('raw/posts.jsonl'): seen.add(json.loads(l)['id'])
except FileNotFoundError: pass
out=open('raw/posts.jsonl','a'); args=sys.argv[1:]
for u,f in zip(args[::2],args[1::2]):
  t=open(f).read(); m=re.search(r'^(\{.*\})$',t,re.M)
  q=re.search(r'(\d+) of 1000 today',t)
  if q: print('quota_left_today',q.group(1))
  if not m: print(u,'no data'); continue
  if u not in by:
    print(u,'unknown username (not in kol_accounts.json top-level or aliases)'); continue
  d=json.loads(m.group(1)); n=0
  for p in d.get('data',[]):
    if p['id'] in seen: continue
    p['username']=u; p['fetched_at']=dt.datetime.now().isoformat()
    if u in alias_parent: p['owner']=alias_parent[u]
    out.write(json.dumps(p,ensure_ascii=False)+'\n'); n+=1
  nid=d.get('meta',{}).get('newest_id')
  if nid and (not by[u].get('since_id') or int(nid)>int(by[u]['since_id'])): by[u]['since_id']=nid
  own=alias_parent.get(u)
  print(u,'new',n,'result_count',d.get('meta',{}).get('result_count'), ('owner='+own if own else ''))
json.dump(acc,open('kol_accounts.json','w'),ensure_ascii=False,indent=1)
