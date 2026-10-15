import {useEffect, useMemo, useState} from 'react';
import {api} from './api';
import type {AgentActivity, Detail, GateCheck, Performance, State, Token} from './types';
import './floor.css';

/* Trading Floor: every number, bar, light and animation below comes from the engine.
   Nothing moves unless an agent actually reported activity. */

const compact=(n:number|null|undefined)=>n==null||!Number.isFinite(n)?'—':Math.abs(n)>=1e6?(n/1e6).toFixed(2).replace(/\.?0+$/,'')+'M':Math.abs(n)>=1e3?(n/1e3).toFixed(1).replace(/\.0$/,'')+'K':Math.abs(n)>=100?String(Math.round(n)):Math.abs(n)>0&&Math.abs(n)<0.01?n.toPrecision(3):String(Math.round(n*100)/100);
const usd=(n:number|null|undefined)=>n==null?'—':'$'+compact(n);
const money=(n:number|null|undefined)=>n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const pct=(n:number|null|undefined,signed=true)=>n==null?'—':(signed&&n>0?'+':'')+(Math.round(n*100)/100)+'%';
const hue=(s:string)=>[...s].reduce((a,c)=>a+c.charCodeAt(0),0)%360;
const ago=(t:number|null|undefined,now:number)=>t?now-t:Infinity;

type AgentId='chief'|'scan'|'vet'|'social'|'size'|'fills'|'risk'|'crawler'|'judge'|'smart';
const AGENTS:Record<AgentId,{name:string;role:string;color:string}>={
 chief:{name:'CHIEF',role:'escolhe ou diz NO TRADE',color:'#F5F3FF'},
 scan:{name:'SCAN',role:'filtros de mercado',color:'#22C55E'},
 vet:{name:'VET',role:'risco on-chain',color:'#3B82F6'},
 social:{name:'SOCIAL',role:'evidências públicas',color:'#EF4444'},
 size:{name:'SIZE',role:'quanto alocar',color:'#FBBF24'},
 fills:{name:'FILLS',role:'execução shadow',color:'#F472B6'},
 risk:{name:'RISK',role:'stop, alvo e saída',color:'#F97316'},
 crawler:{name:'CRAWLER',role:'coleta antes da mesa',color:'#A78BFA'},
 judge:{name:'JUDGE',role:'pontua por regras',color:'#8B5CF6'},
 smart:{name:'SMART',role:'carteiras monitoradas',color:'#38BDF8'},
};
const PIPE:AgentId[]=['crawler','scan','vet','social','judge','chief','size','fills'];

/* ---------- original mascots: simple geometric bots, one shape per agent ---------- */
function Bot({id,x,y,state,s=1}:{id:AgentId;x:number;y:number;state:string;s?:number}){
 const c=AGENTS[id].color;const busy=state==='running';const bad=state==='rejected'||state==='error';
 const body=(()=>{switch(id){
  case 'chief':return <><circle r={17} fill={c}/><path d="M-19 -2 a19 19 0 0 1 38 0" fill="none" stroke="#1e1b2e" strokeWidth={4}/><rect x={-23} y={-6} width={7} height={12} rx={3} fill="#1e1b2e"/><rect x={16} y={-6} width={7} height={12} rx={3} fill="#1e1b2e"/></>;
  case 'scan':return <><rect x={-17} y={-14} width={34} height={28} rx={9} fill={c}/><circle cx={0} cy={-1} r={8} fill="#0b2a17"/><circle cx={0} cy={-1} r={3.5} fill="#86efac" className={busy?'fl-blink':''}/></>;
  case 'vet':return <polygon points="0,-18 16,-9 16,9 0,18 -16,9 -16,-9" fill={c}/>;
  case 'social':return <><line x1={0} y1={-15} x2={0} y2={-23} stroke={c} strokeWidth={3}/><circle cx={0} cy={-24} r={3} fill={c}/><rect x={-15} y={-15} width={30} height={30} rx={6} fill={c}/></>;
  case 'size':return <rect x={-13} y={-13} width={26} height={26} rx={5} fill={c} transform="rotate(45)"/>;
  case 'fills':return <path d="M0 -18 L18 14 L-18 14 Z" fill={c} strokeLinejoin="round" stroke={c} strokeWidth={4}/>;
  case 'risk':return <polygon points="-7,-17 7,-17 17,-7 17,7 7,17 -7,17 -17,7 -17,-7" fill={c}/>;
  case 'crawler':return <>{[-1,1].map(d=><g key={d}><path d={`M${d*10} 4 l${d*10} 8 M${d*10} -2 l${d*12} 0 M${d*10} -8 l${d*10} -7`} stroke={c} strokeWidth={2.5} fill="none"/></g>)}<ellipse rx={13} ry={11} fill={c}/></>;
  case 'judge':return <><circle r={16} fill={c}/><rect x={-12} y={-5} width={24} height={7} rx={3} fill="#1e1b2e"/></>;
  case 'smart':return <><circle r={15} fill={c}/><path d="M-9 -12 a14 14 0 0 1 18 0" stroke="#e0f2fe" strokeWidth={2} fill="none"/></>;
 }})();
 const eyes=id==='scan'||id==='judge'?null:<g fill="#151320"><ellipse cx={-5} cy={id==='fills'?5:-1} rx={2.6} ry={3.4}/><ellipse cx={5} cy={id==='fills'?5:-1} rx={2.6} ry={3.4}/></g>;
 return <g transform={`translate(${x} ${y}) scale(${s})`}>
  <ellipse cy={22} rx={18} ry={4} fill="#000" opacity={.35}/>
  {busy&&<circle r={27} fill="none" stroke={c} strokeWidth={2} className="fl-ring"/>}
  <g className={busy?'fl-bob':bad?'fl-shake':''}>{body}{eyes}{bad&&<text y={-26} textAnchor="middle" fontSize={13} fill="#EF4444" fontWeight={800}>!</text>}</g>
 </g>
}

/* ---------- desk with a monitor whose content reflects that agent's state ---------- */
function Desk({x,y,id,a,label}:{x:number;y:number;id:AgentId;a?:AgentActivity;label?:string}){
 const st=a?.state||'waiting';const c=AGENTS[id].color;
 const glow=st==='running'?c:st==='completed'?'#22C55E':st==='rejected'||st==='error'?'#EF4444':'#3a3650';
 const seed=hue(id);
 return <g transform={`translate(${x} ${y})`}>
  <rect x={-52} y={-58} width={104} height={56} rx={4} fill="#0d0c16" stroke={glow} strokeWidth={st==='waiting'||st==='idle'?1:2}/>
  <text x={-44} y={-46} fontSize={7} fill="#8c86a3" fontFamily="monospace">{(label||AGENTS[id].name).toUpperCase()}</text>
  <text x={44} y={-46} fontSize={7} fill={glow} fontFamily="monospace" textAnchor="end">{st==='running'?'●':st==='completed'?'OK':st==='rejected'?'BLOCK':st==='error'?'ERR':''}</text>
  {Array.from({length:11}).map((_,i)=>{const h=4+((seed*(i+3))%17);return <rect key={i} x={-44+i*8} y={-8-h} width={5} height={h} fill={glow} opacity={st==='waiting'||st==='idle'?.25:.8}/>})}
  <rect x={-6} y={-2} width={12} height={8} fill="#24212f"/>
  <path d="M-66 6 h132 l-10 22 h-112z" fill="#1c1a27" stroke="#2b2839"/>
  <rect x={-30} y={9} width={44} height={6} rx={1} fill="#2c2939"/>
  <circle cx={34} cy={12} r={3} fill="#3a3650"/>
 </g>
}

function Plant({x,y,s=1}:{x:number;y:number;s?:number}){return <g transform={`translate(${x} ${y}) scale(${s})`}><path d="M-9 0 h18 l-3 16 h-12z" fill="#22202e"/>{[-34,-14,6,26,-50,44].map((r,i)=><ellipse key={i} cx={0} cy={-12} rx={4} ry={14} fill={i%2?'#2f6b45':'#3d8a57'} transform={`rotate(${r} 0 2)`}/>)}</g>}

/* ---------- small svg charts (no fabricated candles: only collected prices) ---------- */
function Line({values,w,h,color='#22C55E',fill=true}:{values:number[];w:number;h:number;color?:string;fill?:boolean}){
 if(values.length<2)return <text x={w/2} y={h/2} textAnchor="middle" fontSize={11} fill="#6f6a86">Aguardando 2 preços coletados</text>;
 const min=Math.min(...values),max=Math.max(...values),span=max-min||1;
 const pts=values.map((v,i)=>[i/(values.length-1)*w,h-(v-min)/span*(h-8)-4]);
 const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
 return <g>{fill&&<path d={d+` L${w} ${h} L0 ${h}Z`} fill={color} opacity={.12}/>}<path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round"/><circle cx={pts.at(-1)![0]} cy={pts.at(-1)![1]} r={3.5} fill={color} className="fl-blink"/></g>
}

function stageOf(c:GateCheck){return c.stage==='scan'?(c.label.startsWith('Fluxo')?'flow':'market'):c.stage==='vet'?'chain':'score'}
const KEYS:Record<string,string>={'Liquidez':'min_liquidity_usd','Volume 24h':'min_volume_h24','Market cap':'mcap_usd_range','Market cap (FDV)':'fdv_usd_range','Idade do pool (min)':'pool_age_minutes','Transações 24h':'min_trades_h24','Preço':'price_usd','Idade dos dados (s)':'max_data_age_s','Fluxo de compras e vendas':'sell_flow_5m','Mint authority':'mint_authority','Freeze authority':'freeze_authority','Concentração top 10 holders':'max_top_10_pct','Score mínimo':'min_score'};

export function TradingFloor({state,perf,onSelect}:{state:State;perf:Performance|null;onSelect:(address:string)=>void}){
 const [now,setNow]=useState(Date.now()/1000);
 const [picked,setPicked]=useState<string|null>(null);
 const [detail,setDetail]=useState<Detail|null>(null);
 useEffect(()=>{const id=setInterval(()=>setNow(Date.now()/1000),1000);return()=>clearInterval(id)},[]);
 const agents=useMemo(()=>Object.fromEntries((state.desk?.agents||[]).map(a=>[a.name,a])) as Record<string,AgentActivity>,[state.desk]);
 const active=state.positions.find(p=>p.status==='open'||p.status==='exit_pending');
 const lastDecision=state.decisions[0];
 const focus=picked||active?.token_key.replace('solana:','')||lastDecision?.selected?.replace('solana:','')||lastDecision?.best_candidate?.replace('solana:','')||state.tokens[0]?.address||'';
 useEffect(()=>{if(!focus){setDetail(null);return}let live=true;const load=()=>api<Detail>('/tokens/'+focus).then(d=>{if(live)setDetail(d)}).catch(()=>{});load();const id=setInterval(load,10000);return()=>{live=false;clearInterval(id)}},[focus]);

 const token:Token|undefined=state.tokens.find(t=>t.address===focus)||detail?.market;
 const evaluation=detail?.evaluations[0];const j=evaluation?.judgment||token?.judgment||null;const sec=evaluation?.security;
 const checks=(j?.checks||[]) as GateCheck[];
 const prices=(detail?.prices||[]).map(p=>p.price).filter((p):p is number=>p!=null);
 const st=(id:AgentId)=>agents[id]?.state||'waiting';
 const recent=(id:AgentId,s=8)=>ago(agents[id]?.at,now)<s;

 // header numbers
 const acc=state.account;const dayLoss=acc.day_start>0?Math.max(0,(acc.day_start-acc.equity)/acc.day_start*100):0;
 const lossCap=Number(state.thresholds.max_daily_loss_pct)||3;
 const day=perf?.curve.length?Math.floor((now-perf.curve[0].at)/86400)+1:1;
 const wins=perf?.win_rate!=null?Math.round(perf.win_rate*perf.closed/100):0;const losses=(perf?.closed||0)-wins;
 const utc=new Date(now*1000).toISOString().slice(11,16);
 const plan=state.decisions.find(d=>d.plan)?.plan;const exec=state.decisions.find(d=>d.execution)?.execution;
 const cyc=state.desk?.cycle;
 const passed=checks.filter(c=>c.stage==='scan'&&c.state==='pass').length,scanTotal=checks.filter(c=>c.stage==='scan').length;
 const topLimit=Number(state.thresholds.max_top10_pct)||40;
 const symbolOf=(key?:string|null)=>key?state.tokens.find(t=>t.key===key||t.address===key.replace('solana:',''))?.symbol||key.replace('solana:','').slice(0,5):null;
 const runner=[...state.tokens].filter(t=>t.change_24h!=null&&!t.stale).sort((a,b)=>(b.change_24h||0)-(a.change_24h||0))[0];
 const coins=state.tokens.slice(0,6);
 const board=state.tokens.filter(t=>t.change_24h!=null).slice(0,14);
 const lastEvent=state.desk?.events?.[0];
 const bubbleAgent=lastEvent&&ago(lastEvent.at,now)<15?lastEvent.name as AgentId:null;

 const cards:{id:AgentId;metric:string;value:string;bar:number|null;tone?:string}[]=[
  {id:'chief',metric:lastDecision?.worth_trading?'escolha atual':'última decisão',value:lastDecision?(lastDecision.worth_trading?symbolOf(lastDecision.selected)||'—':'NO TRADE'):'aguardando',bar:null},
  {id:'scan',metric:'checks de mercado',value:scanTotal?`${passed}/${scanTotal}`:'—',bar:scanTotal?passed/scanTotal:null},
  {id:'vet',metric:'top 10 holders',value:sec?.concentration_resolved&&sec.top10_pct!=null?pct(sec.top10_pct,false):'não resolvido',bar:sec?.top10_pct!=null?Math.min(1,sec.top10_pct/topLimit):null,tone:sec?.top10_pct!=null&&sec.top10_pct>topLimit?'#EF4444':undefined},
  {id:'social',metric:'evidências manuais',value:String(detail?.social.evidence.length??0),bar:j?.social!=null?j.social/100:null},
  {id:'size',metric:plan?`ticket ${pct(plan.budget/Math.max(acc.equity,1)*100,false)}`:'sem plano',value:plan?money(plan.budget):'—',bar:plan?Math.min(1,plan.budget/Math.max(acc.equity*Number(state.thresholds.max_position_pct||1)/100,1e-9)):null},
  {id:'fills',metric:'última execução',value:exec?({simulated:'simulada',rejected:'bloqueada',skipped:'não enviada',exit_pending:'saída pendente'} as Record<string,string>)[exec.status]||exec.status:'nenhuma',bar:null},
  {id:'risk',metric:active?'posição aberta':'resultado encerrado',value:active?pct((active.mark/active.entry-1)*100):`${wins}W ${losses}L`,bar:active?Math.min(1,Math.abs(active.mark/active.entry-1)*100/Number(state.thresholds.stop_loss_pct||12)):null,tone:active&&active.mark<active.entry?'#EF4444':undefined},
  {id:'crawler',metric:'ciclo atual',value:cyc?`${cyc.received} recebidos`:'aguardando',bar:null},
 ];

 // desk layout (svg units)
 const P:Record<AgentId,[number,number]>={scan:[300,368],vet:[440,368],social:[580,368],judge:[720,368],size:[300,500],fills:[440,500],risk:[580,500],smart:[1065,505],chief:[965,315],crawler:[1110,385]};
 const desks:AgentId[]=['scan','vet','social','judge','size','fills','risk'];
 const links=PIPE.slice(1).map((id,i)=>({from:PIPE[i],to:id,on:recent(id)&&st(id)!=='waiting'}));

 // filter funnel
 const groups=['market','flow','chain','score'] as const;
 const gstate=groups.map(g=>{const cs=checks.filter(c=>stageOf(c)===g);return cs.some(c=>c.state==='fail')?'fail':cs.length&&cs.every(c=>c.state==='pass')?'pass':cs.length?'unknown':'none'});
 const firstKill=gstate.findIndex(s=>s==='fail'||s==='unknown');
 const decisionBars=[...state.decisions].reverse().slice(-48);
 const maxEval=Math.max(1,...decisionBars.map(d=>d.cycle?.evaluated||0));
 const curve=(perf?.curve||[]).map(p=>p.equity);

 return <div className="floor">
  <header className="fl-head">
   <div className="fl-brand"><span className="fl-logo"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M13 2 4 14h6l-1 8 9-12h-6z" fill="currentColor"/></svg></span><div><strong>ALPHA DESK</strong><small>CRAWLER COLETA · CHIEF DECIDE · RISK PROTEGE</small></div></div>
   <div className="fl-progress"><span>PERDA DO DIA</span><div className="fl-bar"><i style={{width:Math.min(100,dayLoss/lossCap*100)+'%'}}/></div><b>{pct(dayLoss,false)} / {lossCap}%</b></div>
   <div className="fl-meta"><span>{state.mode.toUpperCase()} · DIA {day}</span><span className="fl-equity">{money(acc.equity)}</span><span className="fl-clock">{utc} UTC</span></div>
  </header>

  <section className="fl-cards">{cards.map(c=>{const a=agents[c.id];const s=a?.state||'waiting';const col=AGENTS[c.id].color;return <article key={c.id} className={'fl-card s-'+s} style={{['--c' as string]:col}}>
   <div className="fl-card-top"><svg viewBox="-30 -30 60 60" width="34" height="34"><Bot id={c.id} x={0} y={-2} state={s} s={.85}/></svg><div><h3>{AGENTS[c.id].name}</h3><small>{AGENTS[c.id].role}</small></div><span className="fl-tag">{s==='running'?'TRABALHANDO':s==='completed'?'OK':s==='rejected'?'BLOQUEOU':s==='error'?'FALHA':s==='idle'?'OCIOSO':s==='paused'?'PAUSADO':'AGUARDANDO'}</span></div>
   <div className="fl-metric"><span>{c.metric}</span><b>{c.value}</b></div>
   <div className="fl-track">{c.bar!=null&&<i style={{width:Math.max(3,c.bar*100)+'%',background:c.tone||col}}/>}</div>
   <p className="fl-msg" title={a?.message}>{s==='running'?<span className="fl-typing">trabalhando<em>.</em><em>.</em><em>.</em></span>:a?.message?'“'+a.message+'”':'aguardando'}</p>
  </article>})}</section>

  <section className="fl-room">
   <svg viewBox="0 0 1200 640" role="img" aria-label="Sala de operações com o estado de cada agente">
    <defs><linearGradient id="fl-wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#141226"/><stop offset="1" stopColor="#0d0c1a"/></linearGradient><linearGradient id="fl-floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0f0e1d"/><stop offset="1" stopColor="#0a0914"/></linearGradient></defs>
    <rect width="1200" height="640" fill="url(#fl-floor)"/>
    <path d="M0 0 H1200 V250 H0Z" fill="url(#fl-wall)"/><path d="M0 250 H1200" stroke="#26223a"/>
    {Array.from({length:13}).map((_,i)=><path key={i} d={`M${i*100} 250 L${i*100-80+i*14} 640`} stroke="#15132a"/>)}
    {/* ticker */}
    <text x={600} y={22} textAnchor="middle" fontSize={11} fill="#7dd3a8" fontFamily="monospace" letterSpacing={1}>{`${state.counts.tokens} ESCANEADOS · ${state.counts.approved} APROVADOS · ${state.counts.rejected} REJEITADOS · P&L ${money(acc.pnl)} · ${state.positions.filter(p=>p.status!=='closed').length}/1 POSIÇÃO`}</text>
    {/* big wall screen: collected prices of the focus token */}
    <g transform="translate(340 34)">
     <rect width={520} height={200} rx={6} fill="#08070f" stroke="#2c2840"/>
     <text x={14} y={22} fontSize={13} fill="#ece9f6" fontWeight={700}>{token?.symbol||'—'}</text>
     <text x={14} y={38} fontSize={9} fill="#7c7693" fontFamily="monospace">{focus?focus.slice(0,6)+'…'+focus.slice(-5):'sem token'} · {token?.source||''}</text>
     {[[token?.market_cap==null&&token?.fdv!=null?'FDV':'MC',usd(token?.market_cap??token?.fdv)],['PREÇO',token?.price!=null?'$'+compact(token.price):'—'],['LIQ',usd(token?.liquidity)],['24H',pct(token?.change_24h)]].map(([k,v],i)=><g key={k} transform={`translate(${190+i*82} 18)`}><text fontSize={8} fill="#6f6a86">{k}</text><text y={15} fontSize={12} fill={k==='24H'&&token?.change_24h!=null?(token.change_24h>=0?'#22C55E':'#EF4444'):'#e2def0'} fontWeight={600}>{v}</text></g>)}
     <g transform="translate(14 52)"><Line values={prices.slice(-120)} w={492} h={132} color={token?.change_24h!=null&&token.change_24h<0?'#EF4444':'#22C55E'}/>
      {active&&active.token_key==='solana:'+focus&&prices.length>1&&(()=>{const min=Math.min(...prices.slice(-120)),max=Math.max(...prices.slice(-120)),span=max-min||1;const y=(v:number)=>132-(v-min)/span*124-4;return <>{[[active.entry,'#F5F3FF','ENTRADA'],[active.entry*(1-Number(state.thresholds.stop_loss_pct)/100),'#EF4444','STOP'],[active.entry*(1+Number(state.thresholds.take_profit_pct)/100),'#22C55E','ALVO']].map(([v,c,l])=>{const yy=Math.max(0,Math.min(132,y(v as number)));return <g key={l as string}><line x1={0} x2={492} y1={yy} y2={yy} stroke={c as string} strokeDasharray="4 4" opacity={.7}/><text x={490} y={yy-3} textAnchor="end" fontSize={8} fill={c as string}>{l as string}</text></g>})}</>})()}
     </g>
    </g>
    <text x={600} y={250} textAnchor="middle" fontSize={10} fill="#7dd3a8" fontFamily="monospace">{token?`${token.symbol} · ${token.market_cap==null&&token.fdv!=null?'FDV':'MC'} ${usd(token.market_cap??token.fdv)} · VOL 24H ${usd(token.volume_24h)} · C/V 5M ${token.buys_5m??'—'}/${token.sells_5m??'—'}`:'aguardando dados das fontes'}</text>
    {/* left wall board: real 24h changes of recently scanned tokens */}
    <g transform="translate(40 60)"><rect width={220} height={150} rx={6} fill="#0b0a15" stroke="#2c2840"/><text x={12} y={18} fontSize={8} fill="#7c7693" fontFamily="monospace">VARIAÇÃO 24H · ESCANEADOS</text>
     {board.length?board.map((t,i)=>{const v=Math.max(-100,Math.min(300,t.change_24h||0));const h=Math.max(2,Math.abs(v)/300*55);return <rect key={t.key} x={14+i*14} y={v>=0?88-h:88} width={9} height={h} fill={v>=0?'#22C55E':'#EF4444'} opacity={.85}><title>{t.symbol} {pct(t.change_24h)}</title></rect>}):<text x={110} y={90} textAnchor="middle" fontSize={10} fill="#5f5a75">sem tokens ainda</text>}
     <line x1={10} x2={210} y1={88} y2={88} stroke="#2c2840"/><text x={12} y={140} fontSize={8} fill="#5f5a75">fonte: GeckoTerminal / DexScreener</text></g>
    {/* right wall: clock, rules note */}
    <g transform="translate(890 60)"><rect width={110} height={60} rx={5} fill="#08070f" stroke="#2c2840"/><text x={55} y={34} textAnchor="middle" fontSize={22} fill="#7dd3a8" fontFamily="monospace">{utc}</text><text x={55} y={50} textAnchor="middle" fontSize={8} fill="#6f6a86">UTC</text></g>
    <g transform="translate(1012 46) rotate(2)"><rect width={172} height={132} fill="#fde68a" opacity={.92}/><text x={10} y={18} fontSize={9} fontWeight={800} fill="#3b2f05">REGRAS DA MESA</text>{['1 posição por vez',`máx ${state.thresholds.max_position_pct}% por entrada`,`perda diária ${lossCap}%`,'dado desconhecido ≠ aprovado','NO TRADE é resposta válida'].map((l,i)=><text key={l} x={10} y={38+i*18} fontSize={8.5} fill="#4a3c0a">· {l}</text>)}</g>
    {/* scanned token coins: click to focus */}
    {coins.map((t,i)=><g key={t.key} transform={`translate(${900+i*36} 214)`} className="fl-coin" onClick={()=>setPicked(t.address)}><circle r={15} fill={`hsl(${hue(t.address)} 45% 30%)`} stroke={t.address===focus?'#F472B6':'#3a3650'} strokeWidth={t.address===focus?3:1.5}/><text y={4} textAnchor="middle" fontSize={9} fontWeight={700} fill={`hsl(${hue(t.address)} 70% 82%)`}>{t.symbol.slice(0,3)}</text><title>{t.symbol} — clique para focar</title></g>)}
    {/* pipeline data flow: only links whose agent reported in the last seconds animate */}
    {links.map(l=>{const [x1,y1]=P[l.from],[x2,y2]=P[l.to];const mx=(x1+x2)/2,my=Math.min(y1,y2)-60;return <path key={l.from+l.to} d={`M${x1} ${y1+40} Q${mx} ${my} ${x2} ${y2+40}`} fill="none" stroke={l.on?AGENTS[l.to].color:'#221f33'} strokeWidth={l.on?2.5:1} className={l.on?'fl-flow':''} opacity={l.on?.95:.6}/>})}
    {/* desks */}
    {desks.map(id=><g key={id}><Desk x={P[id][0]} y={P[id][1]} id={id} a={agents[id]}/><Bot id={id} x={P[id][0]} y={P[id][1]+52} state={st(id)}/></g>)}
    {/* chief: head of the floor */}
    <g transform="translate(965 300)"><path d="M-80 0 h160 l-12 26 h-136z" fill="#1c1a27" stroke="#2b2839"/><rect x={-60} y={-30} width={46} height={30} rx={3} fill="#0d0c16" stroke="#3a3650"/><rect x={20} y={-16} width={30} height={16} fill="#24212f"/><text y={42} textAnchor="middle" fontSize={8} fill="#9d97b5" fontFamily="monospace">MESA DO CHIEF</text></g>
    <Bot id="chief" x={965} y={268} state={st('chief')} s={1.25}/>
    {/* crawler at the server rack */}
    <g transform="translate(1130 270)"><rect x={-26} y={0} width={52} height={150} rx={3} fill="#121020" stroke="#2c2840"/>{Array.from({length:9}).map((_,i)=><g key={i}><rect x={-18} y={10+i*15} width={36} height={9} fill="#1b1830"/><circle cx={12} cy={14.5+i*15} r={2} fill={st('crawler')==='running'?(i%2?'#22C55E':'#A78BFA'):st('crawler')==='error'?'#EF4444':'#2f2b44'} className={st('crawler')==='running'?'fl-blink':''} style={{animationDelay:i*0.13+'s'}}/></g>)}</g>
    <Bot id="crawler" x={1072} y={410} state={st('crawler')}/>
    {/* smart money radar */}
    <g transform="translate(1000 560)"><path d="M-30 0 a30 30 0 0 1 60 0z" fill="#2a2740" transform="rotate(-25)"/><line x1={0} y1={0} x2={0} y2={30} stroke="#3a3650" strokeWidth={4}/><rect x={-38} y={30} width={76} height={18} rx={3} fill="#151325" stroke="#2c2840"/><text y={42} textAnchor="middle" fontSize={8} fill="#9d97b5" fontFamily="monospace">SMART MONEY</text>{st('smart')==='running'&&<circle r={10} fill="none" stroke="#38BDF8" className="fl-ring"/>}</g>
    <Bot id="smart" x={P.smart[0]} y={P.smart[1]+40} state={st('smart')} s={.9}/>
    {/* top runner: highest real 24h change among scanned tokens */}
    <g transform="translate(860 430)"><path d="M0 -40 C12 -28 12 -6 9 8 H-9 C-12 -6 -12 -28 0 -40Z" fill="#d6d3e3"/><circle cy={-18} r={4} fill="#3B82F6"/><path d="M-9 0 l-8 12 h8z M9 0 l8 12 h-8z" fill="#9b97b0"/>{runner&&<path d="M-5 9 l5 14 l5 -14z" fill="#F97316" className="fl-blink"/>}<rect x={-50} y={30} width={100} height={30} rx={3} fill="#151325" stroke="#2c2840"/><text y={42} textAnchor="middle" fontSize={8} fill="#9d97b5" fontFamily="monospace">TOP RUNNER 24H</text><text y={54} textAnchor="middle" fontSize={9} fill="#22C55E" fontWeight={700}>{runner?`${runner.symbol} ${pct(runner.change_24h)}`:'—'}</text></g>
    {/* paper hands shredder: rejected count */}
    <g transform="translate(110 470)"><rect x={-40} y={-30} width={80} height={70} rx={4} fill="#151325" stroke="#2c2840"/><rect x={-30} y={-38} width={60} height={10} rx={2} fill="#2a2740"/>{Array.from({length:7}).map((_,i)=><line key={i} x1={-24+i*8} x2={-26+i*8} y1={-28} y2={-14} stroke="#cfcadf" strokeWidth={2}/>)}<text y={10} textAnchor="middle" fontSize={8} fill="#9d97b5" fontFamily="monospace">REJEITADOS</text><text y={28} textAnchor="middle" fontSize={15} fill="#EF4444" fontWeight={800}>{state.counts.rejected}</text></g>
    {/* couch + plants */}
    <g transform="translate(470 590)"><rect x={-110} y={-22} width={220} height={30} rx={6} fill="#1d1a2b"/><rect x={-118} y={-30} width={236} height={14} rx={6} fill="#24213a"/></g>
    <Plant x={330} y={590} s={1.1}/><Plant x={610} y={590} s={1.1}/><Plant x={290} y={238}/><Plant x={870} y={238}/><Plant x={1170} y={600} s={1.2}/>
    {/* speech bubble from the most recent real agent event */}
    {bubbleAgent&&P[bubbleAgent]&&lastEvent&&(()=>{const [bx,by]=P[bubbleAgent];const msg=lastEvent.message.length>52?lastEvent.message.slice(0,50)+'…':lastEvent.message;const w=Math.min(300,msg.length*5.6+20);const x=Math.min(1190-w,Math.max(10,bx-w/2));const below=['size','fills','risk','smart'].includes(bubbleAgent);const y=below?by+(bubbleAgent==='smart'?72:80):by+(bubbleAgent==='chief'?-75:-92);return <g className="fl-pop"><rect x={x} y={y} width={w} height={22} rx={5} fill="#f5f3ff"/><text x={x+10} y={y+15} fontSize={10} fill="#1e1b2e">{msg}</text></g>})()}
   </svg>
  </section>

  <p className="fl-swipe">← deslize a sala para os lados →</p>
  <section className="fl-balance">
   <div className="fl-panel-head"><span>SALDO SHADOW · DIA {day}{token?` · ${token.symbol}`:''}</span><span className="fl-pink">{wins}W {losses}L{active?` · ABERTA ${pct((active.mark/active.entry-1)*100)}`:''}</span></div>
   <div className="fl-balance-body">
    <svg viewBox="0 0 900 200" preserveAspectRatio="none" className="fl-balance-chart">
     <g transform="translate(0 6)"><Line values={curve.slice(-400)} w={900} h={130} color="#22C55E"/></g>
     {decisionBars.map((d,i)=>{const h=Math.max(3,(d.cycle?.evaluated||0)/maxEval*46);return <rect key={d.id} x={i*(900/48)+2} y={196-h} width={900/48-5} height={h} fill={d.worth_trading?'#22C55E':'#EF4444'} opacity={.75}><title>{new Date(d.at*1000).toLocaleTimeString('pt-BR')} · {d.cycle?.evaluated??0} avaliados · {d.worth_trading?'candidato':'NO TRADE'}</title></rect>})}
    </svg>
    <div className="fl-balance-kpi"><small>PATRIMÔNIO SIMULADO</small><strong className={acc.pnl>=0?'up':'down'}>{money(acc.equity)}</strong><span>{acc.pnl>=0?'+':''}{money(acc.pnl)} desde o início</span><span className="fl-legend"><i className="g"/>ciclo com candidato <i className="r"/>NO TRADE</span></div>
   </div>
  </section>

  <section className="fl-bottom">
   <div className="fl-panel">
    <div className="fl-panel-head"><span>ANÁLISE · {token?.symbol||'—'}</span><span className="fl-pink">{cyc?.evaluated??0} AVALIADOS NO CICLO</span></div>
    <div className="fl-analysis">
     <div className="fl-stats">{[[token?.market_cap==null&&token?.fdv!=null?'FDV':'Market cap',usd(token?.market_cap??token?.fdv)],['Preço',token?.price!=null?'$'+compact(token.price):'—'],['24h',pct(token?.change_24h)],['Vol 24h',usd(token?.volume_24h)],['Liquidez',usd(token?.liquidity)],['Score',j?String(Math.round(j.score)):'—']].map(([k,v])=><div key={k}><small>{k}</small><b>{v}</b></div>)}</div>
     <svg viewBox="0 0 600 170" className="fl-analysis-chart"><Line values={prices.slice(-200)} w={600} h={170} color="#A78BFA"/></svg>
     <div className="fl-recent">{state.tokens.filter(t=>t.judgment).slice(0,6).map(t=><button key={t.key} onClick={()=>setPicked(t.address)} className={t.address===focus?'on':''}><span className="sym">{t.symbol}</span><span className="why" title={t.judgment!.reasons.join('; ')}>{t.judgment!.reasons[0]}</span><b className={t.judgment!.approved?'ok':'no'}>{t.judgment!.approved?'PASS':'BLOCK'}</b></button>)}</div>
     {token&&<button className="fl-open" onClick={()=>onSelect(token.address)}>Abrir detalhe completo do token →</button>}
    </div>
   </div>
   <div className="fl-panel">
    <div className="fl-panel-head"><span className="fl-code">thresholds.py</span><span className="fl-pink">CADA NÚMERO QUE A MESA USA</span></div>
    {checks.length?<div className="fl-code-body">{([['market','HARD','# mercado, custa nada'],['flow','FLOW','# compras e vendas 5m'],['chain','CHAIN','# on-chain, RPC gratuito'],['score','JUDGE','# score heurístico, não é probabilidade']] as const).map(([g,name,comment])=>{const cs=checks.filter(c=>stageOf(c)===g);if(!cs.length)return null;return <div key={g} className="fl-block"><p><span className="kw">{name}</span> = {'{'} <span className="cm">{comment}</span></p>{cs.map(c=><div key={c.label} className={'fl-row '+c.state}><span className="k">{KEYS[c.label]||c.label}</span><span className="r">{String(c.rule).replace(/\d+(\.\d+)?/g,n=>compact(Number(n)))}</span><span className="v">{typeof c.observed==='number'?compact(c.observed):c.observed==null?'—':String(c.observed).length>10?String(c.observed).slice(0,4)+'…':String(c.observed)}</span><span className="m">{c.state==='pass'?'✓':c.state==='fail'?'✗':'?'}</span></div>)}<p>{'}'}</p></div>})}</div>:<div className="fl-empty">Aguardando a primeira avaliação deste token.</div>}
    <div className="fl-funnel"><span className="fl-code">filter.py · A ORDEM É O QUE IMPORTA</span><div>{['market_kill','flow_kill','chain_kill','score_kill'].map((n,i)=><span key={n} className={'chip '+(firstKill===i?'kill':firstKill===-1||i<firstKill?(gstate[i]==='pass'?'pass':''):'off')}>{n}</span>)}<span className={'chip '+(j?.approved?'pass':'off')}>pick</span></div></div>
   </div>
  </section>
  <WhyNoTrade/>
  <p className="fl-foot">SHADOW · dinheiro fictício · animações só acontecem quando um agente reporta atividade real · preços do gráfico são os coletados pelo robô, não candles reconstruídos</p>
 </div>
}

type Funnel={hours:number;cycles:number;running_hours:number;running_minutes:number;offline_periods:number;longest_offline_hours:number;offline_now_minutes:number|null;tokens_evaluated:number;tokens_approved:number;entries:number;blockers:{reason:string;tokens:number}[];only_blocker:{reason:string;tokens:number}[];entry_blocks:{reason:string;times:number}[];method:string};

function WhyNoTrade(){
 const [f,setF]=useState<Funnel|null>(null);
 useEffect(()=>{let live=true;const load=()=>api<Funnel>('/funnel?hours=48').then(x=>{if(live)setF(x)}).catch(()=>{});load();const id=setInterval(load,60000);return()=>{live=false;clearInterval(id)}},[]);
 if(!f)return null;
 const max=Math.max(1,...f.blockers.map(b=>b.tokens));
 const up=Math.min(100,f.running_hours/f.hours*100);
 return <section className="fl-panel fl-why">
  <div className="fl-panel-head"><span>POR QUE NÃO COMPROU? · ÚLTIMAS {f.hours} H</span><span className="fl-pink">{f.entries} COMPRAS SIMULADAS</span></div>
  <div className="fl-why-body">
   <div className="fl-why-col">
    <small>ROBÔ LIGADO</small>
    <strong>{f.running_minutes<60?f.running_minutes+' min':f.running_hours+' h'} <em>de {f.hours} h</em></strong>
    <div className="fl-uptime"><i style={{width:up+'%'}}/></div>
    <p>{f.offline_periods?`Ficou desligado ${f.offline_periods} vez(es), a maior por ${f.longest_offline_hours} h.`:'Sem interrupções registradas.'}{f.offline_now_minutes!=null&&f.offline_now_minutes>10?` Último ciclo há ${f.offline_now_minutes} min.`:''}</p>
    <div className="fl-why-nums"><div><b>{f.tokens_evaluated}</b><small>avaliados</small></div><div><b>{f.tokens_approved}</b><small>aprovados</small></div><div><b>{f.entries}</b><small>compras</small></div></div>
   </div>
   <div className="fl-why-col">
    <small>REGRAS QUE MAIS BLOQUEARAM</small>
    {f.blockers.length?f.blockers.slice(0,7).map(b=><div className="fl-why-bar" key={b.reason}><span title={b.reason}>{b.reason}</span><div><i style={{width:b.tokens/max*100+'%'}}/></div><b>{b.tokens}</b></div>):<p>Sem avaliações no período.</p>}
    {f.only_blocker.length>0&&<p className="fl-why-hint">Quase aprovados (só uma regra falhou): {f.only_blocker.slice(0,3).map(b=>`${b.reason} (${b.tokens})`).join(' · ')}</p>}
    {f.entry_blocks.length>0&&<p className="fl-why-hint">Aprovados que não viraram compra: {f.entry_blocks.map(b=>`${b.reason} (${b.times})`).join(' · ')}</p>}
   </div>
  </div>
 </section>
}
