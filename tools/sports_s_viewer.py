#!/usr/bin/env python3
"""Package existing frozen S summaries into a standalone offline HTML viewer.

No quote mathematics, fee or P&L replay. Decimal summary strings are retained;
JavaScript converts them to coordinates only. No external scripts or requests.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

METRICS=('s_best_ask','s_mid','s_best_bid','s_ask','s_bid')


def build_data(directory):
    directory=Path(directory)
    universe=json.loads((directory/'GLOBAL_EVENT_FIRST.json').read_text())
    result=json.loads((directory/'RESULT.json').read_text())
    sources=json.loads((directory/'SOURCES.json').read_text())
    games={(r['sport'],str(r['event_id'])):{'sport':r['sport'],'id':str(r['event_id']),'title':None,'first':r['first_valid_observation'],'views':[]}for r in universe}
    source_data={s['id']:{k:s.get(k)for k in ('jenkins_job','strategy','runtime_job','adapter_kind','local_sha256','source_key')}for s in sources}
    with gzip.open(directory/'event-outcomes.jsonl.gz','rt')as handle:
        for line in handle:
            row=json.loads(line);key=(row['sport'],str(row['event']))
            if key in games and not games[key]['title'] and row.get('title'):games[key]['title']=row['title']
    views={};bases=[];base_index={};reason_sets=[];reason_index={};points=0;outside=0
    with gzip.open(directory/'group-states.jsonl.gz','rt')as handle:
        for line in handle:
            row=json.loads(line);key=(row['sport'],str(row['event']))
            if key not in games:outside+=1;continue
            summary=row['summary'];view_key=(key,row['source'],row['cohort'],row['cadence_seconds'])
            if view_key not in views:
                view={'source':row['source'],'cohort':row['cohort'],'cadence':row['cadence_seconds'],'phases':set(),'points':[]}
                views[view_key]=view;games[key]['views'].append(view)
            view=views[view_key];view['phases'].add(row.get('season_phase','UNKNOWN'))
            basis=summary.get('timestamp_basis','UNKNOWN')
            if basis not in base_index:base_index[basis]=len(bases);bases.append(basis)
            reasons=tuple(summary.get('reasons',[]))
            if reasons not in reason_index:reason_index[reasons]=len(reason_sets);reason_sets.append(list(reasons))
            # Exact existing scalar strings, including null. No recomputation.
            view['points'].append([str(summary.get('t') or row['group_time']),*[summary.get(k)for k in METRICS],bool(summary.get('valid')),base_index[basis],reason_index[reasons],row['run_id'],summary.get('common_shares')])
            points+=1
    for view in views.values():
        view['phases']=sorted(view['phases']);view['points'].sort(key=lambda p:float(p[0]))
    ordered=sorted(games.values(),key=lambda g:g['first'],reverse=True)
    assert len(ordered)==result['unique_games']
    assert dict(Counter(g['sport']for g in ordered))==result['unique_games_by_sport']
    return {'schema':'frozen-s-viewer-v1','games':ordered,'sources':source_data,'bases':bases,'reasons':reason_sets,
            'metrics':METRICS,'counts':dict(Counter(g['sport']for g in ordered)),'total':len(ordered),'points':points,
            'outside_declared_universe_rows':outside,'cutoff':json.loads((directory/'RUN.json').read_text())['end_exclusive'],
            'gap_rules':{'60':90,'300':330,'0':None},'title_basis':'Same-event ID titles from frozen outcome rows; no economic values imported.',
            'input_sha256':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest()for name in ('GLOBAL_EVENT_FIRST.json','SOURCES.json','group-states.jsonl.gz','event-outcomes.jsonl.gz')}}


HTML=r'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src data:; font-src 'none'; worker-src 'none'">
<title>전체 경기 S 탐색기</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172438;font:15px system-ui,-apple-system,BlinkMacSystemFont,sans-serif}main{max-width:1460px;margin:auto;padding:28px}h1{font-size:29px;letter-spacing:-1px;margin:4px 0 10px}h2{font-size:20px;margin:0;overflow-wrap:anywhere}.eyebrow{font-size:12px;letter-spacing:1.4px;font-weight:700;color:#527088}.muted{color:#64748b;font-size:13px;line-height:1.6}header{margin-bottom:20px}.counts{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.count{background:#fff;border:1px solid #dce3eb;border-radius:12px;padding:12px 20px;min-width:135px}.count strong{font-size:25px;margin-right:8px}.panel{border:1px solid #dce3eb;background:white;border-radius:16px;padding:20px;margin:14px 0;box-shadow:0 3px 12px #15253a06}.controls{display:grid;grid-template-columns:140px 1fr 2fr;gap:14px}.controls.second{grid-template-columns:2fr 2fr 1fr;margin-top:14px}label{display:block;font-size:12px;font-weight:700;color:#506078;margin-bottom:6px}select,input[type=search]{font:inherit;width:100%;height:42px;padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;background:white;color:#172438;min-width:0}select:focus,input:focus{outline:2px solid #75a9df;outline-offset:1px}.heading{display:flex;justify-content:space-between;gap:15px;align-items:start}.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.chip{font-size:11px;border-radius:6px;padding:5px 8px;background:#edf2f7;color:#46617d;overflow-wrap:anywhere}.legend{display:flex;flex-wrap:wrap;gap:18px;margin:22px 0 8px;font-size:12px}.legend span{display:inline-flex;align-items:center;gap:7px}.swatch{display:inline-block;width:24px;border-top:3px solid}.legend .dash{border-top-style:dashed}.toggles{display:flex;gap:20px;flex-wrap:wrap;margin-top:15px}.toggles label{font-size:13px;font-weight:500;display:inline-flex;align-items:center;gap:5px}canvas{display:block;width:100%;height:440px;touch-action:pan-y}.chartbox{position:relative;margin-top:4px}.tooltip{position:absolute;display:none;pointer-events:none;background:#172438ee;color:#fff;border-radius:9px;padding:12px 14px;font:12px ui-monospace,monospace;max-width:460px;box-shadow:0 6px 18px #17243830;z-index:3;white-space:pre-line;overflow-wrap:anywhere;line-height:1.6}.zoom{display:grid;grid-template-columns:1fr 1fr auto;align-items:center;gap:20px;margin:16px 0 5px}.zoom input{width:100%;accent-color:#336fae}button{border:1px solid #c8d2de;background:white;border-radius:7px;padding:8px 14px;color:#24415e;font:inherit;cursor:pointer}.empty{padding:28px;text-align:center;background:#f8fafc;color:#64748b;border-radius:12px;display:none}.footer-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;font-size:13px;line-height:1.6}.provenance{font:11px ui-monospace,monospace;overflow-wrap:anywhere;color:#64748b}details{font-size:13px;line-height:1.8;color:#506078}summary{cursor:pointer;font-weight:600}#status{font-size:13px;color:#506078}#notice{margin-top:8px;color:#87560b;font-size:13px}a{color:#336fae}@media(max-width:760px){main{padding:15px}h1{font-size:25px}.controls,.controls.second{grid-template-columns:1fr}.counts{gap:6px}.count{padding:9px 12px;min-width:100px}.count strong{font-size:21px}.panel{padding:15px}.heading{display:block}.footer-grid{grid-template-columns:1fr}canvas{height:360px}.zoom{gap:10px;grid-template-columns:1fr 1fr}.zoom button{grid-column:1/-1}.tooltip{max-width:280px;font-size:10px}.legend{gap:11px}}
</style><main>
<header><div class="eyebrow">SPORTS · STORED S VALUES</div><h1>전체 경기 S 탐색기</h1><div class="muted">종목과 경기를 고른 뒤 같은 출처·실험 버전의 관측을 살펴보세요. 저장된 S 값만 표시합니다.</div><div id="counts" class="counts"></div><div id="status">압축된 분석 자료를 불러오는 중…</div></header>
<section class="panel"><div class="controls"><div><label for="sport">종목</label><select id="sport"></select></div><div><label for="search">팀명 / 경기 ID 검색</label><input id="search" type="search" placeholder="예: Austin, Seahawks, 695003"></div><div><label for="game">경기</label><select id="game"></select></div></div><div class="controls second"><div><label for="source">출처 · Jenkins / runtime</label><select id="source"></select></div><div><label for="cohort">실험 버전 · cohort</label><select id="cohort"></select></div><div><label for="cadence">기록된 수집 간격</label><select id="cadence"></select></div></div></section>
<section class="panel"><div class="heading"><div><h2 id="title">자료 준비 중</h2><div id="subtitle" class="muted"></div></div><div id="point-count" class="muted"></div></div><div class="chips" id="chips"></div><div class="toggles"><label><input type="checkbox" id="top" checked> 최우선 호가 S 3선</label><label><input type="checkbox" id="depth" checked> 각 결과 5주 전량 깊이 S 2선</label></div><div class="legend"><span><i class="swatch" style="color:#d87521"></i>S best ask</span><span><i class="swatch" style="color:#223e66"></i>S mid</span><span><i class="swatch" style="color:#2d9d84"></i>S best bid</span><span><i class="swatch dash" style="color:#ae49a4"></i>S ask · 각 결과 5주</span><span><i class="swatch dash" style="color:#367fd4"></i>S bid · 각 결과 5주</span><span><i class="swatch dash" style="color:#aab4c1"></i>기준 S = 1</span></div><div class="chartbox"><canvas id="chart" aria-label="선택한 경기의 UTC 시각별 S 차트"></canvas><div id="tip" class="tooltip"></div></div><div id="empty" class="empty"></div><div class="zoom"><div><label for="from">관측 구간 시작</label><input id="from" type="range" min="0" max="99" value="0"></div><div><label for="to">관측 구간 끝</label><input id="to" type="range" min="1" max="100" value="100"></div><button id="reset">전체 구간</button></div><div id="range" class="muted"></div><div id="notice"></div></section>
<section class="panel footer-grid"><div><strong>표시 기준</strong><p class="muted">실선은 최우선 ask·mid·bid의 S입니다. 점선 2개는 각 결과에서 동일한 <b>5주</b>를 전량 매수·매도할 깊이의 저장된 S이며, $5 주문과 다릅니다. 모든 선은 수수료 전 가격 기준입니다. BBO 자료에는 잔량과 5주 S가 없습니다.</p><p class="muted">완전하지 않은 관측은 선을 끊습니다. 1분 자료는 90초 초과, 5분 자료는 330초 초과 공백을 연결하지 않습니다. 수집 간격이 입증되지 않은 자료는 점만 표시합니다. 서로 다른 출처·cohort를 잇지 않습니다.</p></div><div><strong>자료 범위</strong><p id="scope" class="muted"></p><p class="muted">표시된 경기 수는 전체 역사·진단 자료의 고유 경기입니다. 과거 NFL 자료를 이번 개막 이후의 1분 관측으로 해석하지 않습니다. S는 시장 호가의 합이며 보정된 실제 확률이나 실현손익이 아닙니다.</p><details><summary>선택한 출처의 원본 식별자</summary><div id="provenance" class="provenance"></div></details></div></section>
</main><script id="payload" type="application/octet-stream">__PAYLOAD__</script><script>
'use strict';
let DATA,game,view,chartState;const $=id=>document.getElementById(id);const labels={soccer:'축구',mlb:'야구 · MLB',nfl:'미식축구 · NFL',nba:'농구 · NBA',nhl:'아이스하키 · NHL'};const colors=['#d87521','#223e66','#2d9d84','#ae49a4','#367fd4'];const names=['S best ask','S mid','S best bid','S ask · 5주','S bid · 5주'];
const utc=t=>new Date(Number(t)*1000).toISOString().replace('T',' ').replace('.000Z',' UTC').replace('Z',' UTC');const cadenceLabel=c=>c===60?'1분 · 기록 확인':c===300?'5분 · 역사 감도':c===0?'미입증 · 점으로 표시':String(c)+'초';
function options(el,rows){el.replaceChildren(...rows.map(([value,text])=>new Option(text,value)));}
function selectedGame(){return DATA.games.find(g=>g.sport+'|'+g.id===$('game').value);}
function rebuildGames(){const term=$('search').value.trim().toLowerCase(),sport=$('sport').value;const games=DATA.games.filter(g=>g.sport===sport&&(!term||((g.title||'')+' '+g.id).toLowerCase().includes(term)));options($('game'),games.map(g=>[g.sport+'|'+g.id,(g.title||'경기 ID '+g.id)+' · ID '+g.id]));chooseGame();}
function chooseGame(){game=selectedGame();if(!game){options($('source'),[]);options($('cohort'),[]);options($('cadence'),[]);view=null;draw();return;}const sourceIds=[...new Set(game.views.map(v=>v.source))];const rank={coconut_recorder:0,grid:1,white_pair:2,guava:3,white_legacy:4,white_legacy_eventless:5,coconut_historical:6,watermelon_live_bbo:7};sourceIds.sort((a,b)=>(rank[DATA.sources[a].adapter_kind]??8)-(rank[DATA.sources[b].adapter_kind]??8));options($('source'),sourceIds.map(id=>[id,DATA.sources[id].jenkins_job+' · '+DATA.sources[id].runtime_job]));chooseSource();}
function chooseSource(){if(!game)return;const ids=[...new Set(game.views.filter(v=>v.source===$('source').value).map(v=>v.cohort))];options($('cohort'),ids.map((id,i)=>[id,'버전 '+(i+1)+' · '+(id.length>45?id.slice(-42):id)]));chooseCohort();}
function chooseCohort(){const values=[...new Set(game.views.filter(v=>v.source===$('source').value&&v.cohort===$('cohort').value).map(v=>v.cadence))];options($('cadence'),values.map(c=>[String(c),cadenceLabel(c)]));chooseView();}
function chooseView(){view=game?.views.find(v=>v.source===$('source').value&&v.cohort===$('cohort').value&&String(v.cadence)===$('cadence').value);$('from').value=0;$('to').value=100;$('tip').style.display='none';draw();}
function chip(text){const span=document.createElement('span');span.className='chip';span.textContent=text;return span;}
function draw(){const canvas=$('chart'),rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,rect.width,rect.height);chartState=null;
 if(!view||!view.points.length){$('title').textContent=game?(game.title||'경기 ID '+game.id):'검색 결과가 없습니다';$('empty').style.display='block';$('empty').textContent='선택 가능한 S 관측이 없습니다.';$('subtitle').textContent='';$('chips').replaceChildren();$('point-count').textContent='';$('range').textContent='';$('notice').textContent='';$('provenance').textContent='';return;}
 const pts=view.points,source=DATA.sources[view.source],valid=pts.filter(p=>p[6]).length,grades=[...new Set(pts.map(p=>DATA.bases[p[7]]))];$('title').textContent=game.title||'경기 ID '+game.id;$('subtitle').textContent=labels[game.sport]+' · 경기 ID '+game.id+' · '+source.jenkins_job;$('point-count').textContent=pts.length.toLocaleString()+'개 관측 · 식별·시각·형식 검증 '+valid.toLocaleString()+'개 · S 중간값 계산 가능 '+pts.filter(p=>p[2]!==null).length.toLocaleString()+'개';$('chips').replaceChildren(chip(cadenceLabel(view.cadence)),chip(source.adapter_kind),...grades.map(chip),chip('season: '+view.phases.join(', ')));
 const isBbo=source.adapter_kind==='watermelon_live_bbo';$('depth').disabled=isBbo;$('notice').textContent=isBbo?'BBO 전용: 잔량·5주 깊이·당시 OPEN·수수료는 입증되지 않았습니다.':view.cadence===0?'당시 설정에 수집 간격이 없어 관측점만 표시합니다.':'연결되지 않은 회색 구간은 관측 공백이며 값을 채우지 않았습니다.';
 $('provenance').textContent='source_key: '+source.source_key+'\ncohort: '+view.cohort+'\nruntime: '+source.runtime_job+'\nDB SHA-256: '+source.local_sha256;
 const minT=Number(pts[0][0]),maxT=Number(pts.at(-1)[0]),span=Math.max(1,maxT-minT);let from=Number($('from').value),to=Number($('to').value);if(from>=to){to=Math.min(100,from+1);$('to').value=to;}let low=minT+span*from/100,high=minT+span*to/100;if(high<=low)high=low+1;$('range').textContent=utc(low)+' — '+utc(high);
 const metrics=[];if($('top').checked)metrics.push(0,1,2);if($('depth').checked&&!isBbo)metrics.push(3,4);let vals=[1];for(const p of pts){if(!p[6]||Number(p[0])<low||Number(p[0])>high)continue;for(const k of metrics)if(p[k+1]!==null&&Number.isFinite(Number(p[k+1])))vals.push(Number(p[k+1]));}
 let ymin=vals.reduce((a,b)=>Math.min(a,b),1),ymax=vals.reduce((a,b)=>Math.max(a,b),1),pad=Math.max(.008,(ymax-ymin)*.12);ymin-=pad;ymax+=pad;const L=58,R=18,T=20,B=46,W=rect.width-L-R,H=rect.height-T-B;const x=t=>L+(Number(t)-low)/(high-low)*W,y=v=>T+(ymax-Number(v))/(ymax-ymin)*H;ctx.font='11px system-ui';ctx.strokeStyle='#e8edf3';ctx.fillStyle='#718096';ctx.lineWidth=1;
 for(let i=0;i<=5;i++){const v=ymin+(ymax-ymin)*i/5,yy=y(v);ctx.beginPath();ctx.moveTo(L,yy);ctx.lineTo(L+W,yy);ctx.stroke();ctx.textAlign='right';ctx.fillText(v.toFixed(3),L-9,yy+4);}const ticks=rect.width<600?2:4;for(let i=0;i<=ticks;i++){const t=low+(high-low)*i/ticks;ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(utc(t).slice(5,16),x(t),T+H+25);}ctx.textAlign='left';ctx.fillText('S',12,15);ctx.textAlign='right';ctx.fillText('UTC',L+W,T+H+42);
 ctx.save();ctx.beginPath();ctx.rect(L,T,W,H);ctx.clip();const gap=DATA.gap_rules[String(view.cadence)];if(gap){ctx.fillStyle='#e9edf3aa';for(let i=1;i<pts.length;i++){const a=Number(pts[i-1][0]),b=Number(pts[i][0]);if(b-a>gap&&b>=low&&a<=high)ctx.fillRect(x(a),T,x(b)-x(a),H);}}
 ctx.strokeStyle='#9da8b5';ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(L,y(1));ctx.lineTo(L+W,y(1));ctx.stroke();ctx.setLineDash([]);
 for(const k of metrics){ctx.strokeStyle=colors[k];ctx.fillStyle=colors[k];ctx.lineWidth=k===1?2.2:1.6;ctx.setLineDash(k>=3?[6,4]:[]);let prev=null;ctx.beginPath();for(const p of pts){const t=Number(p[0]),v=p[k+1];if(t<low||t>high){prev=null;continue;}if(!p[6]||v===null||!Number.isFinite(Number(v))){prev=null;continue;}const xx=x(t),yy=y(v);if(gap&&prev&&t>prev[0]&&t-prev[0]<=gap)ctx.lineTo(xx,yy);else ctx.moveTo(xx,yy);ctx.fillRect(xx-1.3,yy-1.3,2.6,2.6);prev=[t,Number(v)];}ctx.stroke();ctx.setLineDash([]);}
 ctx.fillStyle='#c6575744';for(const p of pts)if(!p[6]&&Number(p[0])>=low&&Number(p[0])<=high)ctx.fillRect(x(p[0])-1,T+H-5,2,5);ctx.restore();const hasValues=vals.length>1;$('empty').style.display=hasValues?'none':'block';$('empty').textContent=metrics.length?'이 구간에는 완전한 S 묶음이 없습니다. 개별 token이 관측됐더라도 누락된 결과를 합성하지 않습니다.':'표시할 선을 선택하세요.';chartState={pts,low,high,x,y,L,W,T,H};}
function hover(event){if(!chartState)return;const c=$('chart'),r=c.getBoundingClientRect(),px=event.clientX-r.left;if(px<chartState.L||px>chartState.L+chartState.W){$('tip').style.display='none';return;}const t=chartState.low+(px-chartState.L)/chartState.W*(chartState.high-chartState.low),pts=chartState.pts;let l=0,h=pts.length-1;while(l<h){const m=(l+h)>>1;if(Number(pts[m][0])<t)l=m+1;else h=m;}let i=l;if(i>0&&Math.abs(Number(pts[i-1][0])-t)<Math.abs(Number(pts[i][0])-t))i--;const p=pts[i];let lines=[utc(p[0]),p[6]?'S 묶음: 완전':'S 묶음: 불완전',DATA.bases[p[7]]];for(let k=0;k<5;k++)lines.push(names[k]+': '+(p[k+1]===null?'미확인':p[k+1]));const reasons=DATA.reasons[p[8]];if(reasons.length)lines.push('사유: '+reasons.join(', '));lines.push('run: '+p[9]);$('tip').textContent=lines.join('\n');$('tip').style.display='block';$('tip').style.left=Math.max(0,Math.min(px+15,r.width-300))+'px';$('tip').style.top='25px';}
async function init(){try{const encoded=$('payload').textContent.trim();const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));DATA=JSON.parse(await new Response(stream).text());$('payload').remove();options($('sport'),Object.keys(DATA.counts).sort((a,b)=>['soccer','mlb','nfl','nba','nhl'].indexOf(a)-['soccer','mlb','nfl','nba','nhl'].indexOf(b)).map(s=>[s,labels[s]+' · '+DATA.counts[s]+'경기']));$('counts').replaceChildren(...[['전체',DATA.total],...Object.entries(DATA.counts).map(([k,n])=>[labels[k],n])].map(([name,n])=>{const el=document.createElement('div');el.className='count';const strong=document.createElement('strong');strong.textContent=n;el.append(strong,document.createTextNode(name));return el;}));$('status').textContent=DATA.total+'개 고유 경기 · '+DATA.points.toLocaleString()+'개 저장 관측 · 외부 요청 없음';$('scope').textContent='분석 마감: '+DATA.cutoff+' (UTC). 같은 경기의 여러 수집기는 독립 경기로 더하지 않습니다. 경기명은 같은 event ID의 저장된 전략행을 참조하며 없으면 ID로 표시합니다.';$('sport').onchange=rebuildGames;$('search').oninput=rebuildGames;$('game').onchange=chooseGame;$('source').onchange=chooseSource;$('cohort').onchange=chooseCohort;$('cadence').onchange=chooseView;for(const id of ['top','depth','from','to'])$(id).oninput=draw;$('reset').onclick=()=>{$('from').value=0;$('to').value=100;draw();};$('chart').onpointermove=hover;$('chart').onpointerleave=()=>{$('tip').style.display='none';};window.addEventListener('resize',draw);rebuildGames();document.documentElement.dataset.ready='true';}catch(error){$('status').textContent='자료를 열지 못했습니다: '+error.message+'. DecompressionStream을 지원하는 최신 브라우저에서 열어 주세요.';document.documentElement.dataset.error=error.message;}}
init();
</script></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--analysis-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    data=build_data(args.analysis_dir);raw=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode();packed=gzip.compress(raw,compresslevel=6,mtime=0)
    html=HTML.replace('__PAYLOAD__',base64.b64encode(packed).decode())
    assert 'http://'not in html and 'https://'not in html and 'innerHTML'not in html
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(html,encoding='utf-8')
    print(json.dumps({'output':str(args.output),'games':data['total'],'sports':data['counts'],'points':data['points'],'views':sum(len(g['views'])for g in data['games']),'html_bytes':args.output.stat().st_size,'raw_json_bytes':len(raw),'title_missing_games':sum(not g['title']for g in data['games']),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))

if __name__=='__main__':main()
