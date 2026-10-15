import {useEffect,useState} from 'react';
import {FlaskConical,ShieldCheck} from 'lucide-react';
import {api} from './api';
import {Bar,BarChart,CartesianGrid,Cell,ReferenceLine,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import type {Detail,FilterComparison,CohortGroup} from './types';
import './research.css';

type Security=Detail['evaluations'][number]['security'];
const pct=(n:number|null|undefined,signed=false)=>n==null?'—':(signed&&n>0?'+':'')+new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(n)+'%';
const short=(s:string)=>s.length>14?s.slice(0,6)+'…'+s.slice(-5):s;
const label=(m:number)=>m<60?m+' min':m/60+' h';

const KINDS:Record<string,{text:string;tone:string}>={
 verified_pool_vault:{text:'Cofre do pool (verificado)',tone:'blue'},
 same_dex_program_account:{text:'Conta do mesmo programa DEX',tone:'blue'},
 program_or_pool_candidate:{text:'Endereço de programa não vinculado',tone:'orange'},
 wallet:{text:'Carteira comum',tone:'purple'},
 unclassified:{text:'Não classificado',tone:'muted'},
};
const METHODS:Record<string,string>={
 complete_accounts:'Todas as contas do token (menos de 20 existentes)',
 top20_upper_bound:'Limite superior com as 20 maiores contas',
};

export function HolderBreakdown({sec,limit}:{sec:Security|undefined;limit:number}){
 if(!sec)return null;
 const resolved=Boolean(sec.concentration_resolved&&sec.top10_pct!=null);
 const over=resolved&&sec.top10_pct!>limit;
 return <div className="holder-breakdown">
  <div className="concentration-hero">
   <div><small>Top 10 holders (limite superior)</small><strong className={!resolved?'muted-text':over?'negative':'positive'}>{resolved?pct(sec.top10_pct):'Não resolvido'}</strong><span>Limite da sua configuração: {pct(limit)}</span></div>
   <div><small>Supply nos cofres do pool</small><strong>{pct(sec.pool_pct)}</strong><span>Liquidez, não é pessoa</span></div>
  </div>
  <div className="summary-line"><span>Método</span><b>{sec.concentration_method?METHODS[sec.concentration_method]||sec.concentration_method:'Indisponível'}</b></div>
  <div className="summary-line"><span>Top 10 contas SPL brutas (inclui pools)</span><b>{pct(sec.largest_accounts_pct)}</b></div>
  <div className="holder-list">{sec.holders.map(h=>{const k=KINDS[h.classification]||KINDS.unclassified;const excluded=h.counted===false;return <div className={'holder-line'+(excluded?' excluded':'')} key={(h.account||'')+h.owner}><code title={h.owner}>{short(h.owner)}</code><span className={'kind '+k.tone}>{k.text}</span><small>{excluded?'fora da conta':'contado'}</small><b>{pct(h.sample_pct)}</b></div>})}</div>
  <p className="fine-print"><ShieldCheck size={12}/> Um cofre só é tratado como liquidez quando o endereço dele aparece dentro dos dados da conta do pool. Endereços de programa sem vínculo comprovado (travas, curvas, outros pools) continuam contados, por precaução.</p>
  {sec.notes.length>0&&<ul className="note-list">{sec.notes.map((n,i)=><li key={i}>{n}</li>)}</ul>}
 </div>
}

const GROUPS:[keyof NonNullable<FilterComparison['groups']>,string,string][]=[
 ['approved','Aprovados pelos filtros','#22C55E'],
 ['all_first','Sem filtro (todos avaliados)','#8B5CF6'],
 ['rejected','Rejeitados pelos filtros','#EF4444'],
];

function Cell2({g,h}:{g:CohortGroup;h:string}){const x=g.horizons[h];if(!x)return <td>—</td>;return <td><b className={x.median_pct==null?'muted-text':x.median_pct>=0?'positive':'negative'}>{pct(x.median_pct,true)}</b><small className="cell-note">{x.measured} medidos · {x.missing} sem dado{x.positive_pct!=null?' · '+pct(x.positive_pct)+' subiram':''}</small></td>}

export function FilterComparisonPanel({fc}:{fc:FilterComparison|undefined}){
 if(!fc?.groups)return <section className="panel"><div className="panel-head"><div><small className="eyebrow">COORTE CONTRAFACTUAL</small><h2>Com e sem filtros</h2></div></div><div className="notice"><FlaskConical size={20}/>{fc?.reason||'Aguardando avaliações.'}</div></section>;
 const groups=fc.groups;const hs=(fc.horizons_minutes||[15,60,240]).map(String);
 const chart=GROUPS.map(([k,name,color])=>({name,color,value:groups[k].horizons['60']?.median_pct??null,n:groups[k].horizons['60']?.measured||0}));
 return <section className="panel cohort-panel">
  <div className="panel-head"><div><small className="eyebrow">COORTE CONTRAFACTUAL • PREÇO REAL DO MESMO POOL</small><h2>Com e sem filtros</h2></div><span className={'badge '+(fc.available?'green':'orange')}>{fc.available?'AMOSTRA MÍNIMA ATINGIDA':'AMOSTRA EM FORMAÇÃO'}</span></div>
  <p className="cohort-intro">Todo token avaliado, comprado ou não, tem o preço anotado na hora da decisão e medido de novo depois. Se os filtros funcionam, a linha verde deve ficar acima da roxa e da vermelha.</p>
  <div className="cohort-grid">
   <div className="table-wrap"><table><thead><tr><th>Grupo</th><th>Tokens</th>{hs.map(h=><th key={h}>Mediana {label(Number(h))}</th>)}</tr></thead><tbody>{GROUPS.map(([k,name,color])=><tr key={k}><td><span className="dot" style={{background:color}}/> {name}</td><td>{groups[k].tokens}</td>{hs.map(h=><Cell2 key={h} g={groups[k]} h={h}/>)}</tr>)}</tbody></table></div>
   <div className="cohort-chart"><small>Mediana após 1 h</small>{chart.some(c=>c.value!=null)?<ResponsiveContainer width="100%" height={180}><BarChart data={chart} margin={{top:10,right:10,left:0,bottom:0}}><CartesianGrid stroke="#242431" vertical={false}/><XAxis dataKey="name" hide/><YAxis tickFormatter={v=>v+'%'} width={48} stroke="#6e6e85" tickLine={false} axisLine={false}/><ReferenceLine y={0} stroke="#3a3a4c"/><Tooltip contentStyle={{background:'#181821',border:'1px solid #333344'}} formatter={(v)=>[pct(Number(v),true),'Mediana']}/><Bar dataKey="value" isAnimationActive={false}>{chart.map(c=><Cell key={c.name} fill={c.color}/>)}</Bar></BarChart></ResponsiveContainer>:<div className="cohort-empty">As primeiras medições de 1 h aparecem cerca de uma hora depois que o robô começar a avaliar tokens.</div>}</div>
  </div>
  <div className="notice"><FlaskConical size={20}/>{fc.reason}</div>
  {fc.recent&&fc.recent.length>0&&<details><summary>Últimas avaliações acompanhadas ({fc.recent.length})</summary><div className="table-wrap"><table><thead><tr><th>Token</th><th>Decisão</th><th>Score</th>{hs.map(h=><th key={h}>{label(Number(h))}</th>)}<th>Motivo principal</th></tr></thead><tbody>{fc.recent.slice(0,15).map(r=><tr key={r.token_key+r.approved}><td>{r.symbol} <small className="mono">{short(r.token_key.replace('solana:',''))}</small></td><td><span className={'badge '+(r.approved?'green':'orange')}>{r.approved?'APROVADO':'REJEITADO'}</span></td><td>{Math.round(r.score)}</td>{hs.map(h=>{const v=r.returns[h];return <td key={h} className={v==null?'muted-text':v>=0?'positive':'negative'}>{v==null?'—':pct(v,true)}</td>})}<td className="reason-cell" title={r.reason}>{r.reason}</td></tr>)}</tbody></table></div></details>}
 </section>
}

type Timing={trades:number;pnl:number;entry_latency_cost:number;exit_gap_cost:number;fees_and_slippage:number;median_entry_data_age_s:number|null;median_entry_drift_pct:number|null;median_exit_gap_pct:number|null;median_exit_lag_s:number|null;median_hold_min:number|null;by_reason:{reason:string;trades:number;pnl:number;wins:number}[];verdict:string;method:string};
const usd=(n:number|null|undefined)=>n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const REASONS:Record<string,string>={STOP_LOSS:'Stop-loss',TAKE_PROFIT:'Alvo atingido',TRAILING_STOP:'Trailing stop',MAX_HOLD_TIME:'Tempo máximo',LIQUIDITY_DROP:'Queda de liquidez',MARKET_DETERIORATION:'Mercado piorou',DAILY_LOSS_LIMIT:'Perda diária',EMERGENCY_STOP:'Emergência',STALE_PRICE:'Preço antigo',UNKNOWN_LIQUIDITY:'Liquidez desconhecida',PERSISTENT_PRICE_FAILURE:'Falha de preço',MANUAL_CLOSE:'Encerramento manual'};

export function TimingPanel(){
 const [t,setT]=useState<Timing|null>(null),[err,setErr]=useState('');
 useEffect(()=>{let live=true;const load=()=>api<Timing>('/timing').then(x=>{if(live)setT(x)}).catch(e=>{if(live)setErr(e.message)});load();const id=setInterval(load,30000);return()=>{live=false;clearInterval(id)}},[]);
 const late=(t?.entry_latency_cost||0)+(t?.exit_gap_cost||0);
 return <section className="panel timing-panel">
  <div className="panel-head"><div><small className="eyebrow">AUDITORIA DE TEMPO • OPERAÇÕES SHADOW ENCERRADAS</small><h2>O atraso está custando dinheiro?</h2></div><span className={'badge '+(!t?.trades?'muted':late>0?'orange':'green')}>{!t?.trades?'SEM AMOSTRA':late>0?'ATRASO CUSTA':'SEM CUSTO MEDIDO'}</span></div>
  {err?<div className="notice warning">{err}</div>:!t?<div className="notice">Carregando…</div>:<>
   <p className="cohort-intro">{t.verdict}</p>
   <div className="timing-grid">
    <div><small>Atraso na compra</small><b className={t.entry_latency_cost>0?'negative':'positive'}>{usd(t.entry_latency_cost)}</b><span>preço logo após comprar: {pct(t.median_entry_drift_pct,true)} (mediana)</span></div>
    <div><small>Passou do stop</small><b className={t.exit_gap_cost>0?'negative':'positive'}>{usd(t.exit_gap_cost)}</b><span>abaixo do nível: {pct(t.median_exit_gap_pct,true)} (mediana)</span></div>
    <div><small>Gatilho → venda</small><b>{t.median_exit_lag_s==null?'—':Math.round(t.median_exit_lag_s)+' s'}</b><span>idade do dado na compra: {t.median_entry_data_age_s==null?'—':Math.round(t.median_entry_data_age_s)+' s'}</span></div>
    <div><small>Taxas + slippage</small><b>{usd(t.fees_and_slippage)}</b><span>resultado total: {usd(t.pnl)} em {t.trades} operações</span></div>
   </div>
   {t.by_reason.length>0&&<div className="table-wrap"><table><thead><tr><th>Motivo da venda</th><th>Operações</th><th>Positivas</th><th>Resultado</th></tr></thead><tbody>{t.by_reason.map(r=><tr key={r.reason}><td>{REASONS[r.reason]||r.reason}</td><td>{r.trades}</td><td>{r.wins}</td><td className={r.pnl>=0?'positive':'negative'}>{usd(r.pnl)}</td></tr>)}</tbody></table></div>}
   <p className="fine-print timing-method">{t.method}</p>
  </>}
 </section>
}
