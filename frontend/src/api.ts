export const getToken = () => sessionStorage.getItem('alpha-token') || '';
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const response = await fetch('/api'+path,{method,headers:{'Content-Type':'application/json',Authorization:'Bearer '+getToken()},body:body === undefined ? undefined:JSON.stringify(body),signal:AbortSignal.timeout(100000)});
 if(!response.ok){const data = await response.json().catch(()=>({detail:'Erro de conexão'}));throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
 return response.json();
}
