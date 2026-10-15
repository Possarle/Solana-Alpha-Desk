import {useEffect, useState} from 'react';
import {Rocket, Radio, ShieldAlert, Square, Check} from 'lucide-react';
import {api} from './api';
import type {Performance} from './types';
import './launch.css';

type Candidate={mint:string;symbol:string;name:string;seen_at:number;checked_at:number;verdict:string;reasons:string[];age_s?:number;dev_buy_pct?:number|null;buyers_sol?:number|null;curve_sol?:number;mcap_usd?:number|null;execution?:{status:string;reason?:string;budget?:number}};
type LaunchState={thresholds:Record<string,number|boolean>;feed:{state:string;requests:number;errors:number;detail:string;last_ok:number|null};since:number;stats:Record<string,number>;top_reasons:{reason:string;count:number}[];recent:Candidate[];account:{initial:number;cash:number;equity:number;pnl:number;halted:boolean}|null;active:{symbol:string;token_key:string;status:string;entry:number;mark:number;cost:number;pnl_pct:number;opened_at:number;entry_age_s:number|null;pending_reason:string|null}|null;closed:{trades:number;pnl:number;wins:number;expectancy:number|null;by_reason:Record<string,number>;last:{symbol:string;pnl:number;reason:string;hold_min:number;closed_at:number}[]};method:string};

const usd=(n:number|null|undefined,d=2)=>n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:d}).format(n);
const num=(n:number|null|undefined,d=2)=>n==null?'—':new Intl.NumberFormat('pt-BR',{maximumFractionDigits:d}).format(n);
const short=(s:string)=>s.slice(0,5)+'…'+s.slice(-4);
const clock=(t:number)=>new Date(t*1000).toLocaleTimeString('pt-BR');
const LABELS:Record<string,string>={enabled:'Modo lançamento ligado',confirm_seconds:'Esperar após criação (s)',min_buyers_sol:'Compra mínima de terceiros (SOL)',max_dev_buy_pct:'Máximo comprado pelo criador (%)',max_entry_mcap_usd:'Market cap máximo na entrada (USD)',max_checks_per_minute:'Verificações por minuto (RPC)',max_position_pct:'Capital por operação (%)',max_price_impact_pct:'Impacto máximo no preço (%)',max_daily_loss_pct:'Perda máxima diária (%)',fee_bps:'Taxa pump.fun por lado (bps)',fixed_cost_usd:'Custo de rede por lado (USD)',stop_loss_pct:'Stop-loss (%)',take_profit_pct:'Take-profit (%)',trailing_activation_pct:'Trailing arma após ganho de (%)',trailing_stop_pct:'Trailing stop (%)',max_hold_minutes:'Tempo máximo de posição (min)',liquidity_drop_pct:'Saída se o SOL da curva cair (%)',max_data_age_seconds:'Validade máxima do preço (s)'};
const VERDICT:Record<string,[string,string]>={bought:['COMPROU','green'],approved:['APROVADO','purple'],rejected:['REJEITADO','orange'],skipped:['PULADO','muted'],error:['ERRO RPC','red']};

export function LaunchView({perf}:{perf:Performance|null}){
 const [s,setS]=useState<LaunchState|null>(null),[err,setErr]=useState(''),[form,setForm]=useState<Record<string,number|boolean>|null>(null),[msg,setMsg]=useState(''),[busy,setBusy]=useState(false);
 const load=()=>api<LaunchState>('/launch').then(x=>{setS(x);setErr('');setForm(f=>f||x.thresholds)}).catch(e=>setErr(e.message));
 useEffect(()=>{load();const id=setInterval(load,3000);return()=>clearInterval(id)},[]);
 if(err&&!s)return <div className="error-banner"><ShieldAlert size={18}/><span>{err}</span></div>;
 if(!s)return <div className="notice">Carregando modo lançamento…</div>;
 const st=s.stats, checked=(st.rejected||0)+(st.approved||0)+(st.bought||0)+(st.error||0);
 const mainWin=perf?.win_rate, launchWin=s.closed.trades?s.closed.wins/s.closed.trades*100:null;
 const save=async()=>{if(!form)return;setBusy(true);try{await api('/launch/thresholds','PUT',form);setMsg('Configuração salva. Vale para as próximas moedas.');load()}catch(e){setMsg((e as Error).message)}finally{setBusy(false)}};
 const close=async()=>{setBusy(true);try{await api('/launch/close','POST');load()}finally{setBusy(false)}};
 const feedTone=s.feed.state==='active'?'green':s.feed.state==='idle'?'muted':'orange';
 return <div className="launch">
  <div className="notice warning"><ShieldAlert size={20}/>Fase de maior risco: no primeiro minuto não existe volume, vendas nem histórico. O robô verifica só o que é possível (permissões, compra do criador, compras de terceiros, quanto já subiu) e opera apenas com dinheiro fictício.</div>
  <div className="metrics">
   <div className="metric"><div className="metric-top"><span>Feed PumpPortal</span><span className={'badge '+feedTone}>{s.feed.state==='active'?'AO VIVO':s.feed.state==='idle'?'AGUARDANDO':'RECONECTANDO'}</span></div><strong>{num(st.received||0,0)}</strong><small>moedas novas recebidas · {s.feed.detail}</small></div>
   <div className="metric"><div className="metric-top"><span>Verificadas na blockchain</span><Radio size={16}/></div><strong>{num(checked,0)}</strong><small>{num(st.skipped||0,0)} puladas por limite de consultas · {st.queued||0} na fila</small></div>
   <div className="metric"><div className="metric-top"><span>Compras simuladas</span><Rocket size={16}/></div><strong>{num(st.bought||0,0)}</strong><small>desde que o robô ligou ({clock(s.since)})</small></div>
   <div className="metric"><div className="metric-top"><span>Patrimônio do modo lançamento</span><span className={'badge '+(s.account?.halted?'red':'purple')}>{s.account?.halted?'PARADO':'SHADOW'}</span></div><strong className={(s.account?.pnl||0)<0?'negative':''}>{usd(s.account?.equity)}</strong><small>{(s.account?.pnl||0)>=0?'+':''}{usd(s.account?.pnl)} · conta separada de US$ 10.000</small></div>
  </div>

  <div className="two-column">
   <section className="panel"><div className="panel-head"><div><small className="eyebrow">QUAL MODO RENDE MAIS?</small><h2>Modo atual × modo lançamento</h2></div></div>
    <div className="table-wrap"><table><thead><tr><th></th><th>Modo atual (15 min – 3 dias)</th><th>Modo lançamento (segundos)</th></tr></thead><tbody>
     <tr><td>Operações encerradas</td><td>{perf?.closed??'—'}</td><td>{s.closed.trades}</td></tr>
     <tr><td>Resultado</td><td className={(perf?.realized_simulated||0)<0?'negative':'positive'}>{usd(perf?.realized_simulated)}</td><td className={s.closed.pnl<0?'negative':'positive'}>{usd(s.closed.pnl)}</td></tr>
     <tr><td>Taxa de acerto</td><td>{mainWin!=null?num(mainWin,1)+'%':'—'}</td><td>{launchWin!=null?num(launchWin,1)+'%':'—'}</td></tr>
     <tr><td>Ganho médio por operação</td><td>{usd(perf?.expectancy)}</td><td>{usd(s.closed.expectancy)}</td></tr>
    </tbody></table></div>
    <p className="fine-print launch-pad">Os dois modos usam contas fictícias separadas de US$ 10.000. Compare depois de pelo menos 20 operações em cada um; com poucas operações, a diferença pode ser sorte.</p>
   </section>
   <section className="panel"><div className="panel-head"><div><small className="eyebrow">POSIÇÃO DO MODO LANÇAMENTO</small><h2>{s.active?s.active.symbol:'Sem posição aberta'}</h2></div>{s.active&&<span className={'badge '+(s.active.status==='exit_pending'?'red':'green')}>{s.active.status==='exit_pending'?'SAÍDA PENDENTE':'ACOMPANHANDO A CADA 8 S'}</span>}</div>
    {s.active?<><div className="position-stats"><div><small>Entrada</small><b>{usd(s.active.entry,10)}</b></div><div><small>Preço agora</small><b>{usd(s.active.mark,10)}</b></div><div><small>Resultado</small><b className={s.active.pnl_pct<0?'negative':'positive'}>{num(s.active.pnl_pct,1)}%</b></div><div><small>Valor</small><b>{usd(s.active.cost)}</b></div><div><small>Comprou com a moeda com</small><b>{num(s.active.entry_age_s,0)} s de vida</b></div></div>
     {s.active.pending_reason&&<p className="fine-print launch-pad">Aguardando preço para sair: {s.active.pending_reason}</p>}
     <div className="action-row"><button className="button danger" disabled={busy} onClick={()=>void close()}><Square size={14}/> Encerrar simulação agora</button></div></>
    :<p className="fine-print launch-pad">O robô abre no máximo uma posição de lançamento por vez. Stop {String(s.thresholds.stop_loss_pct)}%, alvo {String(s.thresholds.take_profit_pct)}%, tempo máximo {String(s.thresholds.max_hold_minutes)} min.</p>}
    {s.closed.last.length>0&&<div className="table-wrap"><table><thead><tr><th>Últimas</th><th>Motivo</th><th>Tempo</th><th>Resultado</th></tr></thead><tbody>{s.closed.last.map((t,i)=><tr key={i}><td>{t.symbol}</td><td>{t.reason}</td><td>{num(t.hold_min,1)} min</td><td className={t.pnl<0?'negative':'positive'}>{usd(t.pnl)}</td></tr>)}</tbody></table></div>}
   </section>
  </div>

  <section className="panel"><div className="panel-head"><div><small className="eyebrow">AO VIVO · MOEDAS NOVAS DO PUMP.FUN</small><h2>Decisões do robô</h2></div><span className="badge">{s.recent.length} recentes</span></div>
   {s.recent.length?<div className="table-wrap"><table className="launch-table"><thead><tr><th>Moeda</th><th>Idade ao verificar</th><th>Compra do criador</th><th>Compra de terceiros</th><th>Market cap</th><th>Decisão</th><th>Motivo</th></tr></thead><tbody>
    {s.recent.slice(0,25).map(c=>{const [label,tone]=VERDICT[c.verdict]||[c.verdict,'muted'];return <tr key={c.mint+c.checked_at}><td><strong>{c.symbol}</strong><small className="cell-note mono">{short(c.mint)}</small></td><td>{c.age_s!=null?num(c.age_s,0)+' s':'—'}</td><td>{c.dev_buy_pct!=null?num(c.dev_buy_pct,1)+'%':'—'}</td><td>{c.buyers_sol!=null?num(c.buyers_sol,2)+' SOL':'—'}</td><td>{usd(c.mcap_usd,0)}</td><td><span className={'badge '+tone}>{label}</span></td><td className="launch-reason" title={c.reasons.join('; ')}>{c.verdict==='bought'?'Entrada de '+usd(c.execution?.budget):c.reasons[0]||'—'}</td></tr>})}
   </tbody></table></div>:<div className="empty"><Radio size={26}/><h3>Aguardando moedas novas</h3><p>Quando o feed estiver ao vivo, cada lançamento do pump.fun aparece aqui alguns segundos depois de criado.</p></div>}
   {s.top_reasons.length>0&&<p className="fine-print launch-pad">Motivos mais comuns: {s.top_reasons.map(r=>`${r.reason} (${r.count})`).join(' · ')}</p>}
  </section>

  {form&&<section className="panel"><div className="panel-head"><div><small className="eyebrow">HIPÓTESES DE TESTE · NÃO CALIBRADAS</small><h2>Regras do modo lançamento</h2></div></div>
   <form onSubmit={e=>{e.preventDefault();void save()}}><div className="settings-grid">{Object.entries(form).map(([k,v])=><label key={k} className={typeof v==='boolean'?'toggle-label':''}><span>{LABELS[k]||k}</span>{typeof v==='boolean'?<input type="checkbox" checked={v} onChange={e=>setForm({...form,[k]:e.target.checked})}/>:<input type="number" step="any" required value={v} onChange={e=>setForm({...form,[k]:Number(e.target.value)})}/>}</label>)}</div>
   <div className="action-row"><button className="button primary" disabled={busy}><Check size={15}/> Salvar regras</button><button type="button" className="button secondary" onClick={()=>setForm(s.thresholds)}>Descartar</button>{msg&&<small className="launch-msg">{msg}</small>}</div></form>
   <p className="fine-print launch-pad">{s.method} O RPC público é compartilhado com o modo atual; para verificar mais moedas por minuto, use um RPC_URL próprio (por exemplo, o plano gratuito da Helius) no .env.</p>
  </section>}
 </div>
}
