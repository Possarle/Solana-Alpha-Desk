import {useEffect, useState, type CSSProperties} from 'react';
import {Activity, ArrowUpRight, ChevronRight, CircleHelp, ShieldCheck, Signal, Timer, Workflow} from 'lucide-react';
import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {api} from './api';
import type {AgentActivity, Detail, Performance, State} from './types';
import './desk.css';

const money = (v:number|null|undefined) => v==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(v);
const numeric = (v:number|null|undefined) => v==null?'—':new Intl.NumberFormat('pt-BR',{...(Math.abs(v)>0&&Math.abs(v)<0.001?{maximumSignificantDigits:5}:{maximumFractionDigits:3})}).format(v);
const clock = (v:number|null|undefined) => v?new Date(v*1000).toLocaleTimeString('pt-BR'):'Aguardando';
const short = (v:string) => v.slice(0,6)+'…'+v.slice(-5);
const definitions = [
 {id:'crawler',name:'CRAWLER',role:'Coleta pública',color:'#3B82F6',description:'Busca pools nas fontes disponíveis. Falha de API não aprova um token.'},
 {id:'scan',name:'SCAN',role:'Filtros de mercado',color:'#22C55E',description:'Compara idade, liquidez, capitalização, volume e fluxo com os limites configurados.'},
 {id:'vet',name:'VET',role:'Segurança on-chain',color:'#F97316',description:'Consulta permissões e contas SPL. Dados desconhecidos são apresentados como lacunas.'},
 {id:'social',name:'SOCIAL',role:'Evidências públicas',color:'#EC4899',description:'Consulta evidências cadastradas manualmente. Não faz scraping do X nem assume autenticidade.'},
 {id:'smart',name:'SMART',role:'Carteiras monitoradas',color:'#38BDF8',description:'Detecta swaps prováveis nas transações públicas das carteiras cadastradas.'},
 {id:'judge',name:'JUDGE',role:'Avaliação estruturada',color:'#A78BFA',description:'Calcula scores por regras. Ollama, quando configurado, acrescenta apenas inferências.'},
 {id:'chief',name:'CHIEF',role:'Seleção e revalidação',color:'#8B5CF6',description:'Separa melhor candidato de oportunidade negociável e revalida o escolhido.'},
 {id:'size',name:'SIZE',role:'Orçamento da posição',color:'#FBBF24',description:'Limita a alocação por capital, liquidez e incerteza. Não utiliza alavancagem.'},
 {id:'fills',name:'FILLS',role:'Execução SHADOW',color:'#F472B6',description:'Registra operações hipotéticas após controles de risco e idempotência. Nenhum envio à blockchain.'},
 {id:'risk',name:'RISK',role:'Monitoramento de saídas',color:'#F97316',description:'Acompanha stop, alvo, trailing, tempo, liquidez e perda diária mesmo com descoberta pausada.'},
];
const labels:Record<string,string>={waiting:'AGUARDANDO',running:'EM EXECUÇÃO',completed:'CONCLUÍDO',rejected:'BLOQUEADO',unknown:'EVIDÊNCIA PARCIAL',skipped:'NÃO EXECUTADO',error:'FALHA',idle:'SEM POSIÇÃO',paused:'PAUSADO',blocked:'PENDENTE'};
function SparkChart({data,field,color='#8B5CF6'}:{data:Record<string,unknown>[];field:string;color?:string}) {
 if(data.filter(x=>typeof x[field]==='number').length<2)return <div className="desk-chart-empty"><Activity/><strong>Histórico em formação</strong><span>São necessárias duas observações. Nenhuma curva fictícia é exibida.</span></div>;
 return <ResponsiveContainer width="100%" height={230}><AreaChart data={data}><CartesianGrid stroke="#262638" vertical={false}/><XAxis dataKey="at" tickFormatter={v=>clock(Number(v)).slice(0,5)} minTickGap={50} stroke="#747489"/><YAxis domain={['auto','auto']} width={72} tickFormatter={v=>Number(v).toLocaleString('en-US',{maximumSignificantDigits:4})} stroke="#747489"/><Tooltip contentStyle={{background:'#10101b',border:'1px solid #3c3456'}} labelFormatter={v=>clock(Number(v))}/><Area type="linear" dataKey={field} stroke={color} fill={color} fillOpacity={.12} strokeWidth={2} isAnimationActive={false} connectNulls={false}/></AreaChart></ResponsiveContainer>;
}
export function CommandDesk({state,perf,onSelect,navigate}:{state:State;perf:Performance|null;onSelect:(address:string)=>void;navigate:(page:string)=>void}) {
 const [selectedAgent,setSelectedAgent]=useState('chief');
 const [selectedToken,setSelectedToken]=useState('');
 const [detail,setDetail]=useState<Detail|null>(null),[detailError,setDetailError]=useState('');
 const [wallTime,setWallTime]=useState(Date.now()/1000);
 useEffect(()=>{const timer=setInterval(()=>setWallTime(Date.now()/1000),1000);return()=>clearInterval(timer)},[]);
 const decision=state.decisions[0];
 const candidateKey=decision?.selected||decision?.best_candidate;
 const active=state.positions.find(p=>p.status!=='closed');
 const focus=selectedToken||active?.token_key.replace('solana:','')||candidateKey?.replace('solana:','')||state.tokens[0]?.address||'';
 const token=state.tokens.find(t=>t.address===focus);
 useEffect(()=>{
  setDetail(null);setDetailError(''); if(!focus)return;
  let stopped=false;
  const load=()=>api<Detail>('/tokens/'+focus).then(d=>{if(!stopped){setDetail(d);setDetailError('')}}).catch(e=>{if(!stopped)setDetailError(e.message)});
  void load();const timer=setInterval(()=>void load(),15000);return()=>{stopped=true;clearInterval(timer)};
 },[focus]);
 const fresh=wallTime-state.time<20;
 const agents=state.desk?.agents||[];
 const current=agents.find(a=>a.name===selectedAgent);
 const def=definitions.find(a=>a.id===selectedAgent)!;
 const cycle=state.desk?.cycle||decision?.cycle;
 const checks=detail?.evaluations[0]?.judgment.checks||[];
 const plan=decision?.plan;
 const planToken=plan&&state.tokens.find(t=>t.key===plan.token_key);
 const activity=(id:string):AgentActivity|undefined=>agents.find(a=>a.name===id);
 const blockers=Object.entries(state.tokens.reduce<Record<string,number>>((acc,t)=>{if(t.judgment&&!t.judgment.approved)t.judgment.reasons.forEach(r=>acc[r]=(acc[r]||0)+1);return acc},{})).sort((a,b)=>b[1]-a[1]).slice(0,5);
 return <div className="command-desk">
  <div className="desk-masthead"><div><span className="desk-label">SOLANA ALPHA DESK / AGENT OPERATIONS</span><h2>Uma mesa. Decisões verificáveis.</h2></div><div className={'desk-signal '+(!fresh?'is-old':'')}><Signal size={16}/>{fresh?'TELEMETRIA CONECTADA':'TELEMETRIA DESATUALIZADA'}<time>{clock(state.time)}</time></div></div>
  <div className="desk-kpis"><div><small>Patrimônio SHADOW</small><b>{money(state.account.equity)}</b><span>{state.account.valuation_stale?'Marcação antiga; valor incerto':'Capital fictício · USD'}</span></div><div><small>P&L estimado</small><b className={state.account.pnl<0?'negative':'positive'}>{money(state.account.pnl)}</b><span>Inclui posições abertas</span></div><div><small>Último ciclo / ciclo atual</small><b>{cycle?.evaluated??'—'} <em>avaliações</em></b><span>{cycle?.blocked??0} em bloqueio temporário</span></div><div><small>Execução</small><b>{state.account.halted?'BLOQUEADA':active?'POSIÇÃO ATIVA':'SHADOW'}</b><span>{state.thresholds.auto_shadow?'Automação de simulação ligada':'Entradas automáticas desligadas'}</span></div></div>
  <section className="agent-grid" aria-label="Agentes operacionais">{definitions.map(d=>{
   const a=activity(d.id),running=a?.state==='running'&&fresh;
   return <button type="button" key={d.id} aria-pressed={selectedAgent===d.id} onClick={()=>setSelectedAgent(d.id)} className={'agent-station '+(selectedAgent===d.id?'station-selected ':'')+(running?'station-working':'')} style={{'--agent-color':d.color} as CSSProperties}>
    <div className="station-top"><span className="agent-bot" aria-hidden="true"><i/><i/></span><div><b>{d.name}</b><small>{d.role}</small></div><span className="station-led"/></div>
    <span className={'agent-state state-'+(a?.state||'waiting')}>{!fresh?'SEM ATUALIZAÇÃO':labels[a?.state||'waiting']||a?.state}</span>
    <p>{a?.message||'Aguardando primeira execução deste processo'}</p><div className="station-foot"><span>{a?.token_key?short(a.token_key.replace('solana:','')):'SOLANA'}</span><time>{clock(a?.at)}</time></div><div className="station-track"><i/></div>
   </button>;
  })}</section>
  <div className="desk-focus-grid">
   <section className="desk-panel desk-market"><div className="desk-panel-title"><div><span className="desk-label">MERCADO EM OBSERVAÇÃO</span><h3>{token?.symbol||'Aguardando descoberta'}</h3></div><select aria-label="Token na mesa" value={selectedToken} onChange={e=>setSelectedToken(e.target.value)}><option value="">Seguir candidato / posição</option>{state.tokens.map(t=><option key={t.key} value={t.address}>{t.symbol} · {short(t.address)}</option>)}</select></div>
    {token&&<div className="desk-token-meta"><span className="mono">{short(token.address)}</span><span>{token.source}</span><span className={token.stale?'negative':''}>{token.stale?'DADOS ANTIGOS':'Coletado '+clock(token.observed_at)}</span></div>}
    {detailError?<p role="alert" className="negative">{detailError}</p>:<SparkChart data={detail?.prices||[]} field="price"/>}
    <div className="desk-market-stats"><div><small>Preço observado</small><b>{token?.price==null?'—':'$'+token.price.toPrecision(5)}</b></div><div><small>Liquidez</small><b>{money(token?.liquidity)}</b></div><div><small>Compras / vendas 5m</small><b>{numeric(token?.buys_5m)} / {numeric(token?.sells_5m)}</b></div></div>
    <button disabled={!token} className="text-button" onClick={()=>token&&onSelect(token.address)}>Abrir evidências do token <ArrowUpRight size={14}/></button>
   </section>
   <section className="desk-panel desk-chief"><span className="desk-label">CHIEF / DECISÃO MAIS RECENTE</span><h3>{decision?.execution?.status==='simulated'?'ENTRADA SHADOW':decision?.worth_trading?'CANDIDATO APROVADO':'NO TRADE'}</h3><p>{decision?.message||'Aguardando o primeiro ciclo completo.'}</p>
    <div className="desk-fact"><span>Melhor candidato observado</span><b>{decision?.best_candidate?short(decision.best_candidate.replace('solana:','')):'—'}</b></div><div className="desk-fact"><span>Existe oportunidade aprovada?</span><b>{decision?(decision.worth_trading?'SIM':'NÃO'):'—'}</b></div><div className="desk-fact"><span>Resultado da execução</span><b>{decision?.execution?.status||'Não solicitada'}</b></div>{decision?.execution?.reason&&<p className="desk-callout">{decision.execution.reason}</p>}
    {state.thresholds.require_resolved_concentration&&<div className="desk-callout"><ShieldCheck size={18}/><span>Política estrita: concentração desconhecida bloqueia entradas. A cobertura atual não resolve o top 10 de holders.</span></div>}
    <button className="text-button" onClick={()=>navigate('Settings')}>Inspecionar política e automação <ChevronRight size={14}/></button>
   </section>
  </div>
  <div className="desk-middle-grid">
   <section className="desk-panel"><div className="desk-panel-title"><div><span className="desk-label">TRABALHO DO AGENTE</span><h3 style={{color:def.color}}>{def.name} / {def.role}</h3></div><Workflow size={20}/></div><p>{def.description}</p><blockquote>{current?.message||'Nenhuma execução observada nesta sessão do backend.'}</blockquote><small>{clock(current?.at)} · {current?.token_key?short(current.token_key.replace('solana:','')):'Sem token associado'}</small><div className="desk-agent-log">{state.desk?.events.filter(e=>e.name===selectedAgent).slice(0,5).map(e=><div key={e.id}><time>{clock(e.at)}</time><span>{e.message}</span></div>)}</div></section>
   <section className="desk-panel"><span className="desk-label">SIZE / ÚLTIMO PLANO CALCULADO</span><h3>{plan?`${planToken?.symbol||plan.symbol} · ${money(plan.budget)}`:'Sem alocação autorizada'}</h3>{plan?<><p className="mono">{short(plan.token_key.replace('solana:',''))} · avaliação #{plan.evaluation_id} · {clock(plan.observed_at)}</p><div className="desk-fact"><span>Incerteza / fator de tamanho</span><b>{plan.uncertainty_factor}×</b></div><div className="desk-fact"><span>Slippage estimado</span><b>{plan.slippage_bps} bps</b></div><div className="desk-fact"><span>Taxa estimada de entrada</span><b>{money(plan.estimated_entry_fee)}</b></div><div className="desk-fact"><span>Stop / alvo (gatilhos)</span><b>{plan.stop_trigger.toPrecision(4)} / {plan.take_trigger.toPrecision(4)}</b></div><p className="fine-print">{plan.method} Plano histórico; não representa ordem em andamento.</p></>:<p>O orçamento só é calculado depois que um candidato passa pela revalidação. Nenhuma operação é forçada.</p>}</section>
   <section className="desk-panel"><span className="desk-label">RISK / POSIÇÃO ATUAL</span><h3>{active?active.data.symbol:'Nenhuma posição aberta'}</h3>{active?<><div className="desk-fact"><span>Estado</span><b>{active.status}</b></div><div className="desk-fact"><span>Capital alocado</span><b>{money(active.cost)}</b></div><div className="desk-fact"><span>P&L estimado</span><b>{money(active.pnl)}</b></div><p>{active.data.pending_reason||'Monitorando gatilhos configurados.'}</p></>:<p>O risco verifica posições independentemente do ciclo de descoberta.</p>}<div className="desk-fact"><span>Stop / alvo configurados</span><b>{state.thresholds.stop_loss_pct}% / {state.thresholds.take_profit_pct}%</b></div><div className="desk-fact"><span>Limite diário</span><b>{state.thresholds.max_daily_loss_pct}%</b></div><button className="text-button" onClick={()=>navigate('Risk Manager')}>Abrir controles de risco <ChevronRight size={14}/></button></section>
  </div>
  <section className="desk-panel"><div className="desk-panel-title"><div><span className="desk-label">BALANCE / RESULTADOS DA SIMULAÇÃO</span><h3>{money(state.account.equity)}</h3></div><span>{perf?.closed||0} operações encerradas · custos estimados</span></div><SparkChart data={perf?.curve||[]} field="equity" color="#22C55E"/></section>
  <div className="desk-focus-grid"><section className="desk-panel"><div className="desk-panel-title"><div><span className="desk-label">THRESHOLDS / EVIDÊNCIAS DO TOKEN</span><h3>{token?.symbol||'Aguardando avaliação'}</h3></div><CircleHelp size={18}/></div><p className="fine-print">Valores da avaliação de {clock(detail?.evaluations[0]?.at)}. Desconhecido não significa aprovado.</p>{checks.length?<div className="desk-checks">{checks.map((c,i)=><div className="desk-check" key={i}><span className={'check-state check-'+c.state}>{c.state==='pass'?'PASS':c.state==='fail'?'BLOCK':'N/D'}</span><div><strong>{c.label}</strong><small>{c.rule}</small></div><b>{c.observed===null?'—':typeof c.observed==='number'?numeric(c.observed):String(c.observed)}</b></div>)}</div>:<p>Os detalhes estruturados aparecerão após uma nova avaliação.</p>}</section>
   <section className="desk-panel"><span className="desk-label">FUNIL / POR QUE NÃO OPERAR?</span><h3>Bloqueios mais frequentes</h3><p className="fine-print">Últimas avaliações dos tokens carregados; um token pode ter vários motivos.</p>{blockers.length?blockers.map(([reason,count])=><div className="desk-blocker" key={reason}><span>{reason}</span><b>{count}</b></div>):<p>Nenhuma rejeição registrada ainda.</p>}<div className="desk-cycle"><Timer size={18}/><span>{cycle?`${cycle.received} recebidos · ${cycle.approved} aprovados · ${cycle.rejected} rejeitados`:'Aguardando ciclo'}<small>{cycle?.finished_at?'Concluído às '+clock(cycle.finished_at):cycle?'Ciclo em execução':'Nenhum ciclo concluído'}</small></span></div><button className="text-button" onClick={()=>navigate('Alpha Scanner')}>Inspecionar todos os tokens <ChevronRight size={14}/></button></section>
  </div>
  <section className="desk-panel"><span className="desk-label">FILLS & ALERTS / HISTÓRICO PERSISTENTE</span><h3>Eventos da operação</h3><div className="desk-persistent-log">{state.events.slice(0,12).map(e=><div key={e.id}><time>{clock(e.at)}</time><span className={'event-level '+e.level}>{e.kind}</span><p>{e.message}</p></div>)}{!state.events.length&&<p>Nenhum evento operacional registrado.</p>}</div></section>
 </div>;
}
