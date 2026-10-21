#!/usr/bin/env python3
"""連續 N 天收盤在某根日K實體內的區間研究（獨立腳本，不接 publish）。資料：Bitstamp / Coinbase 公開日K (UTC)。"""
import json, sys, time, urllib.request, datetime as dt
UA={'User-Agent':'Mozilla/5.0'}
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30))
def bitstamp():
    out={}; start=1314835200  # 2011-09
    while True:
        d=get(f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={start}")['data']['ohlc']
        if not d: break
        for r in d: out[int(r['timestamp'])]=(float(r['open']),float(r['close']))
        last=int(d[-1]['timestamp'])
        if last<=start or len(d)<2: break
        start=last+86400; time.sleep(0.3)
    return out
def coinbase():
    out={}; end=int(time.time())//86400*86400
    while True:
        s=end-299*86400
        try: d=get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400&start={dt.datetime.utcfromtimestamp(s).isoformat()}&end={dt.datetime.utcfromtimestamp(end).isoformat()}")
        except Exception as e: print('cb err',e,file=sys.stderr); break
        if not d: break
        for t,lo,hi,o,c,v in d: out[int(t)]=(float(o),float(c))
        end=s-86400; time.sleep(0.35)
    return out
def analyze(rows,N,inclusive=True,fakewin=7):
    ts=sorted(rows); O=[rows[t][0] for t in ts]; C=[rows[t][1] for t in ts]; n=len(ts)
    def inside(x,lo,hi): return lo<=x<=hi if inclusive else lo<x<hi
    ev=[]; i=0
    while i<n-1:
        lo,hi=min(O[i],C[i]),max(O[i],C[i]); j=i+1
        while j<n and inside(C[j],lo,hi): j+=1
        L=j-i-1
        if L>=N:
            e=dict(mother=ts[i],lo=lo,hi=hi,days=L,ongoing=j>=n)
            if j<n:
                b=C[j]; up=b>hi; e.update(brk=ts[j],dir='up' if up else 'down')
                for k in (7,14,30):
                    e[f'r{k}']=round((C[j+k]/b-1)*100,1) if j+k<n else None
                fake=False
                for m in range(j+1,min(n,j+fakewin+1)):
                    if inside(C[m],lo,hi) or (up and C[m]<lo) or (not up and C[m]>hi): fake=True;break
                e['fake']=fake
            ev.append(e); i=j  # 去重：事件被接受後，從突破日之後才找下一根母K
        else: i+=1
    return ev
D=lambda t:dt.datetime.utcfromtimestamp(t).strftime('%Y-%m-%d')
def completed(rows):
    today=int(time.time())//86400*86400
    return {t:v for t,v in rows.items() if t<today}
def current(rows,minN=21):
    ev=analyze(completed(rows),minN)
    return ev[-1] if ev and ev[-1]['ongoing'] else None
def line():
    e=current(bitstamp())
    if not e: return ''
    m=dt.datetime.utcfromtimestamp(e['mother'])
    return f"盤整區 {round(e['lo']):,}～{round(e['hi']):,} 已 {e['days']} 天（母K {m.month}/{m.day}），以日收盤突破判方向；歷史同類 5～6 次，突破後 30 天順勢僅 2 次、假突破 2～4 次，樣本少僅供參考"
def check_breakout(state_path='out/range_state.json',minN=21):
    import os
    st=json.load(open(state_path)) if os.path.exists(state_path) else {}
    bs,cb=completed(bitstamp()),completed(coinbase())
    lt=max(bs); out={'event':'none','date':D(lt),'bitstamp_close':bs[lt][1],'coinbase_close':cb.get(lt,(None,None))[1]}
    def side(c,lo,hi): return None if c is None else ('up' if c>hi else 'down' if c<lo else 'in')
    # 進行中 or 剛結束的區間（Bitstamp 為主）
    ev=[e for e in analyze(bs,minN)]
    if ev and ev[-1]['ongoing']:
        e=ev[-1]; st['active']={'mother':D(e['mother']),'lo':e['lo'],'hi':e['hi'],'days':e['days']}
        out.update(lo=e['lo'],hi=e['hi'],days=e['days'],mother=D(e['mother']))
    elif st.get('active'):
        a=st['active']; lo,hi=a['lo'],a['hi']; out.update(lo=lo,hi=hi,mother=a['mother'])
        sb,sc=side(bs[lt][1],lo,hi),side(out['coinbase_close'],lo,hi)
        key=a['mother']; nt=st.setdefault('notified',{}).setdefault(key,{})
        if sb in('up','down') and sb==sc and 'breakout' not in nt:
            nt['breakout']={'dir':sb,'date':D(lt)}; out['event']='breakout_'+sb
        elif 'breakout' in nt and 'back_inside' not in nt:
            bd=dt.datetime.strptime(nt['breakout']['date'],'%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp()
            if (lt-bd)/86400<=7 and sb=='in' and sc=='in':
                nt['back_inside']=D(lt); out['event']='back_inside'
            elif (lt-bd)/86400>7: st['active']=None
        elif sb!=sc: out['note']='交易所方向不一致，不通知'
    st['last_check']=D(lt); json.dump(st,open(state_path,'w'),ensure_ascii=False,indent=1)
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__' and '--line' in sys.argv: print(line()); sys.exit()
if __name__=='__main__' and '--check-breakout' in sys.argv: check_breakout(); sys.exit()
if __name__=='__main__':
    srcs={'bitstamp':bitstamp(),'coinbase':coinbase()}
    res={}
    for name,rows in srcs.items():
        print(f"== {name} {D(min(rows))}~{D(max(rows))} {len(rows)} bars")
        for N in (14,21,28):
            for inc in (True,False):
                ev=analyze(rows,N,inc); res[f'{name}_N{N}_{"inc" if inc else "exc"}']=ev
                done=[e for e in ev if 'dir' in e]
                print(f"N={N} {'含邊界' if inc else '不含'}: 事件 {len(ev)}（完成 {len(done)}，假突破 {sum(e['fake'] for e in done)}）")
                for e in ev:
                    print(f"  母K {D(e['mother'])} {e['lo']:.2f}~{e['hi']:.2f} {e['days']}天 "+(f"突破 {D(e['brk'])} {e['dir']} 7d {e['r7']} 14d {e['r14']} 30d {e['r30']} 假突破={e['fake']}" if 'dir' in e else '進行中'))
    json.dump({k:[{**e,'mother':D(e['mother']),**({'brk':D(e['brk'])} if 'brk' in e else {})} for e in v] for k,v in res.items()},open('out/range_body.json','w'),ensure_ascii=False,indent=1)
